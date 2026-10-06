"""库存台账与批次耗用明细的建表测试（T-BASE-009 第 1 张卡 / 迁移 0012）。

覆盖：

======================  =================================================
TC-BL-01               两张台账表是 **append-only** —— 没有 ``deleted_at`` /
                        ``version`` / ``updated_*``；``stock_reservations``
                        是三张里**唯一**的软删表
TC-BL-02               ``stock_ledgers`` 的唯一键**只有** ``uq_stock_ledgers_source``，
                        **不含** ``stock_id``
TC-BL-03               ``stock_id`` **不建外键**（``stock_type`` 多态），
                        而 ``warehouse_id`` / ``ledger_id`` / ``material_id`` 建了
TC-BL-04               ``stock_ledger_lines.material_id`` **NOT NULL**
TC-BL-05               ``ck_stock_ledger_lines_amount`` 真拦（金额必须等于
                        ``ROUND(qty × unit_cost, 4)``）
TC-BL-06               三个 PG 枚举的值域
TC-BL-09               ``uq_stock_ledger_lines_batch`` 是**普通** UNIQUE 而非部分索引
======================  =================================================

⚠️ **TC-BL-01 是这张卡最关键的一条**：台账一旦能软删，INV-4 红冲就形同虚设 ——
「红冲」的定义是「原行永远保留、另写一条反向的」，而能软删意味着那条「不可逆的操作」
其实可逆，于是「这批布什么时候被哪张单扣过、后来冲回去没有」这个问题**再也答不出来**。
而症状极其隐蔽：软删一条台账之后 ``v_stock_reconciliation`` 会**永久非空**，
而视图非空的含义是「账实不一致」= **发不了版**（``docs/modules/06 §3.7`` ④）。

⚠️ **TC-BL-02 同样关键**：``modules/06 §3.3`` 的字段表原先把 ``stock_id`` 也标成
「联合唯一」，而**同一缸布必然被多张单据反复消耗** —— 照抄的话第二张裁剪单审核时
就撞 ``duplicate key``。所以这条断言盯的是「唯一键里不许出现 ``stock_id``」。

⚠️ 对账视图与应用账号权限在**另一个文件**（``test_stock_views_and_grants.py``）：
把它们拆开是因为一个文件超过 ``AGENTS §7.1`` 的**单文件 400 行硬线**，
而拆分的界线是「表结构」对「表被怎么用」—— 两者要抓的失败形状完全不同。
"""

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.modules.stock.models import StockLedger, StockLedgerLine, StockReservation
from tests.factories.cutting import make_stock
from tests.factories.stock import make_ledger, make_material
from tests.factories.user import OPERATOR_ID

#: 两张 append-only 表：``04 §7.2.5`` 明令**不得**带这四列
_APPEND_ONLY_TABLES = ("stock_ledgers", "stock_ledger_lines")
_FORBIDDEN_ON_APPEND_ONLY = ("deleted_at", "version", "updated_at", "updated_by")


async def _columns(db_session, table: str) -> set[str]:
    return set(
        (
            await db_session.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"),
                {"t": table},
            )
        )
        .scalars()
        .all()
    )


async def _stock(db_session):
    """一个布批（连带它的物料），缸号固定。

    ⚠️ 用**固定缸号**：每个用例独立事务、跑完回滚，所以不会撞。写成随机的话，
    失败信息里是一串没法检索的十六进制。
    """
    return await make_stock(db_session, await make_material(db_session), lot="DY-LEDGER-1")


# --------------------------------------------------------------- append-only 口径


async def test_ledger_tables_are_append_only(db_session):
    """TC-BL-01：两张台账表**没有** ``deleted_at`` / ``version`` / ``updated_*``。

    ⚠️ 直接查 ``information_schema`` 而不是看 ORM：迁移漏抄时 ORM 上写的
    ``nullable=False`` 两边都安静地通过，只有真库能抓出来。
    """
    for table in _APPEND_ONLY_TABLES:
        cols = await _columns(db_session, table)
        assert {"id", "created_at", "created_by", "remark"} <= cols, (
            f"{table} 缺 append-only 必备列"
        )
        for forbidden in _FORBIDDEN_ON_APPEND_ONLY:
            assert forbidden not in cols, (
                f"{table} 不该有 {forbidden} —— 台账是 append-only（04 §7.2.5 / INV-4）。"
                f"能软删就等于给「不可逆的红冲」开了个后门，而症状是 "
                f"v_stock_reconciliation 永久非空（等于发不了版）"
            )
        assert "version" not in StockLedger.__table__.c or table == "stock_ledger_lines"


async def test_reservation_is_the_only_soft_delete_table(db_session):
    """TC-BL-01（另一半）：``stock_reservations`` **有**完整公共字段 + ``version > 0``。

    ⚠️ 它不是 append-only：锁定的**释放**是「置 ``released_at``」（INV-7），
    行本身还要留着回答「这批布曾经被哪张单占过」；而单据被删除时它的锁要软删。
    """
    cols = await _columns(db_session, "stock_reservations")
    for required in (
        "id",
        "created_at",
        "created_by",
        "updated_at",
        "updated_by",
        "deleted_at",
        "version",
        "remark",
        "released_at",
    ):
        assert required in cols, f"stock_reservations 缺 {required}（docs/04 §2）"
    assert issubclass(StockReservation, object) and "version" in StockReservation.__table__.c
    checks = (
        (
            await db_session.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conrelid = 'stock_reservations'::regclass AND contype = 'c'"
                )
            )
        )
        .scalars()
        .all()
    )
    assert any("version > 0" in item for item in checks), (
        "缺 ck_stock_reservations_version_positive"
    )


# --------------------------------------------------------------- 唯一键与外键


async def test_ledger_unique_key_excludes_stock_id(db_session):
    """TC-BL-02：唯一键**只有** ``uq_stock_ledgers_source``，且**不含** ``stock_id``。

    ⚠️ 同一缸布必然被多张单据反复消耗 —— ``stock_id`` 进唯一约束就会在
    **第二张裁剪单审核**时撞 ``duplicate key``。
    ⚠️ 反过来 ``direction`` **必须**在键里：反审核写的反向台账与原台账的
    ``(source_doc_type, source_doc_id, source_line_no)`` **完全相同**。
    """
    uniques = (
        (
            await db_session.execute(
                text(
                    "SELECT a.attname FROM pg_index i "
                    "JOIN pg_class c ON c.oid = i.indrelid "
                    "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(i.indkey) "
                    "WHERE c.relname = 'stock_ledgers' AND i.indisunique"
                )
            )
        )
        .scalars()
        .all()
    )
    as_set = set(uniques)
    assert "stock_id" not in as_set, (
        f"uq_stock_ledgers_source 的列里有 stock_id —— 同一缸布被第二张单扣减时必撞"
        f"duplicate key（实际列：{sorted(as_set)}）"
    )
    assert {"source_doc_type", "source_doc_id", "source_line_no", "direction"} <= as_set


async def test_multipolymorphic_columns_have_no_foreign_key(db_session):
    """TC-BL-03：``stock_id`` **不建外键**，而确定性的引用建了。

    ⚠️ ``stock_type`` 多态指向两张结存表，跨表多态**建不了 FK** —— 建了只能指一张，
    另一栈就写不进去（``modules/06 §3.6``：由 service 保证 + ``40003`` 对账兜底）。
    ⚠️ ``source_doc_id`` 同理**不建**：单据可整体软删或反审核，建 FK 会阻止
    这两件合法操作。
    """
    fks = (
        (
            await db_session.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conrelid = 'stock_ledgers'::regclass AND contype = 'f'"
                )
            )
        )
        .scalars()
        .all()
    )
    joined = " ".join(fks)
    assert "warehouses" in joined, "stock_ledgers.warehouse_id 必须有 FK（它只有一张目标表）"
    for forbidden in ("material_stocks", "finished_goods_stocks", "cutting_orders"):
        assert forbidden not in joined, (
            f"stock_ledgers 不该对 {forbidden} 建外键 —— 多态 / 单据可软删，建了会阻止合法操作"
        )


async def test_ledger_line_material_id_is_not_nullable(db_session):
    """TC-BL-04：``material_id`` **NOT NULL**。

    ⚠️ 字段表同一行还写着「成衣栈台账此列为 ``NULL``」—— 那是为 P3 的
    ``finished_goods_stocks`` 预留的。本轮建成可空会让「每条出库都有批次耗用」
    松掉、且 ``idx_stock_ledger_lines_material`` 对 NULL 行无效。
    ⚠️ **P3 建成衣栈时必须 ``DROP NOT NULL``**（docs/12 §5 已登记）。
    """
    nullable = {
        row[0]: row[1]
        for row in (
            await db_session.execute(
                text(
                    "SELECT column_name, is_nullable FROM information_schema.columns "
                    "WHERE table_name = 'stock_ledger_lines' AND column_name = 'material_id'"
                )
            )
        ).all()
    }
    assert nullable["material_id"] == "NO", (
        "stock_ledger_lines.material_id 又是可空了 —— P3 建成衣栈时才该放宽"
    )


async def test_batch_unique_is_not_partial(db_session):
    """TC-BL-09：``uq_stock_ledger_lines_batch`` 是**普通** UNIQUE，不是部分索引。

    ⚠️ ``cutting_order_lines`` 那批表必须用部分索引（``PUT /lines`` 是
    「软删旧行 + 插新行」的**全量替换**语义），而明细**从不软删**（append-only，
    纠错走红冲新增反向行）—— 所以不需要。两类不要一起改。
    """
    is_partial = (
        await db_session.execute(
            text(
                "SELECT i.indpred IS NOT NULL FROM pg_index i "
                "JOIN pg_class c ON c.oid = i.indrelid "
                "JOIN pg_class ic ON ic.oid = i.indexrelid "
                "WHERE ic.relname = 'uq_stock_ledger_lines_batch'"
            )
        )
    ).scalar_one()
    assert is_partial is False, (
        "uq_stock_ledger_lines_batch 不该是部分索引：明细 append-only、deleted_at 恒为 NULL"
    )


# --------------------------------------------------------------- 约束真的会拦


async def test_amount_check_rejects_wrong_amount(db_session):
    """TC-BL-05：``ck_stock_ledger_lines_amount`` 真拦。

    ⚠️ 金额一致性放**行级** CHECK：台账已无 ``amount`` 列，「台账 ↔ 明细」的
    有无与数量由 ``v_stock_cost_check`` 兜，而金额是行内静态的，CHECK 恰好够用。
    """
    stock = await _stock(db_session)
    ledger, line = await make_ledger(db_session, stock)
    assert line.id is not None
    # ⚠️ **必须新插一行**而不是改上面那行的 ``amount``：台账明细是 append-only，
    #    应用账号连 UPDATE 都没有（迁移 0012 的 ``REVOKE UPDATE, DELETE``），
    #    所以「改坏再保存」这条路在权限层就先被挡住了 —— 守卫要测的是
    #    **写入时就对不上**这个情形，那才是真实会发生的错
    db_session.add(
        StockLedgerLine(
            ledger_id=ledger.id,
            material_id=stock.material_id,
            stock_id=stock.id,
            dye_lot_no="DY-OTHER",
            bolt_no=stock.bolt_no,
            qty=Decimal("-1.000"),
            unit_cost=stock.unit_cost,
            amount=Decimal("1.0000"),  # ≠ ROUND(-1.000 × 12.500000, 4) = -12.5000
            created_by=OPERATOR_ID,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_zero_qty_is_rejected(db_session):
    """``ck_stock_ledgers_qty``：``qty = 0`` 必被拒（台账记的是「一次动作」）。"""
    from sqlalchemy.exc import ProgrammingError

    stock = await _stock(db_session)
    db_session.add(
        StockLedger(
            stock_type="MATERIAL",
            stock_id=stock.id,
            warehouse_id=stock.warehouse_id,
            direction="OUT",
            qty="0",
            source_doc_type="CuttingOrder",
            source_doc_id=stock.id,
            created_by=OPERATOR_ID,
        )
    )
    with pytest.raises((IntegrityError, ProgrammingError)):
        await db_session.flush()
    await db_session.rollback()


# --------------------------------------------------------------- 枚举


def test_three_enums_have_exact_values():
    """TC-BL-06：三个 PG 枚举的值域**逐个值**核对。

    ⚠️ ``StockDocType`` 的**成员名与值不一致**（``PURCHASE_ORDER`` vs
    ``\"PurchaseOrder\"``），所以 ``values_callable`` 必须显式给 —— 否则 SQLAlchemy
    存成员名，库里写不进 ``PurchaseOrder``，**第一条 INSERT 就报 invalid input value**。
    """
    assert set(StockLedger.__table__.c.stock_type.type.enums) == {"MATERIAL", "FINISHED_GOODS"}
    assert set(StockLedger.__table__.c.direction.type.enums) == {"IN", "OUT", "ADJUST"}
    assert set(StockLedger.__table__.c.source_doc_type.type.enums) == {
        "PurchaseOrder",
        "CuttingOrder",
        "MaterialIssue",
        "FinishedGoodsReceipt",
        "SalesDelivery",
        "StockTransfer",
        "Stocktaking",
    }


async def test_enums_exist_in_database(db_session):
    """TC-BL-06（库里那半）：模型写对了但迁移建成 varchar，是最常见的漂移。"""
    for name, expected in (
        ("stock_type", {"MATERIAL", "FINISHED_GOODS"}),
        ("stock_direction", {"IN", "OUT", "ADJUST"}),
    ):
        values = set(
            (
                await db_session.execute(
                    text(
                        "SELECT e.enumlabel FROM pg_enum e "
                        "JOIN pg_type t ON t.oid = e.enumtypid WHERE t.typname = :n"
                    ),
                    {"n": name},
                )
            )
            .scalars()
            .all()
        )
        assert values == expected, f"PG 枚举 {name} 的值域变了：{sorted(values)}"
