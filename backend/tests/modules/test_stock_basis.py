"""库存基础表的建表测试（T-BASE-006 / 迁移 0008）。

覆盖：

======================  =================================================
TC-B06-01               ``dye_lot_no`` / ``bolt_no`` **NOT NULL** ——
                        可空会让「无缸号」的批次重复插入任意多次
TC-B06-02               唯一键不含 ``batch_no``（业务：不存在同缸不同价）
TC-B06-03               选批索引是**部分索引**且只含「还有可用量」的批次
TC-B06-04               ``wip_ledger_lines`` 是 append-only —— 没有
                        ``deleted_at`` / ``version`` / ``updated_*``
TC-B06-05               应用账号**不能** UPDATE/DELETE ``wip_ledger_lines``
TC-B06-06               三张表都有 ``04 §2`` 公共字段 + ``version > 0``
TC-B06-07               ``purpose`` 是 PG 枚举，含 ADR-0012 的四个值
======================  =================================================

⚠️ **TC-B06-01 是这张卡最关键的一条**：``dye_lot_no`` 可空 + 在唯一键里，
症状**极其隐蔽** —— PG 的 NULL 不参与唯一判定，所以「无缸号」的辅料批次插 10 次
也不报错。等到发现「同一个缸号的库存变成了 10 条」时，数据已经写脏了，
而报错完全看不出根因是唯一键里有个可空列。
"""

import pytest
from sqlalchemy import select, text

from app.core.db import unit_of_work
from app.modules.base.models import MaterialStock, WipLedgerLine, WipStock
from tests.factories.user import OPERATOR_ID

STOCK_TABLES = ("material_stocks", "wip_stocks", "wip_ledger_lines")


async def _material(db_session):
    """建一条物料 + 它的类目 / 单位 / 仓库，返回 ``material``。

    ⚠️ 已下沉到 :func:`tests.factories.cutting.make_material`：原先它住在本测试
    模块里，而 ``tests/factories/bundling.py`` 反过来 import 本模块的私有函数 ——
    那是**跨文件顺序依赖**（docs/10 §2.2 禁止），库一清空就失败且报错点离真因很远。
    本函数保留为薄壳，只为不改动本文件其余调用点。
    """
    from tests.factories.cutting import make_material

    return await make_material(db_session)


async def _style(db_session):
    """建一条款号（WIP 测试要用）。

    ⚠️ 分类取 ``ProductCategory``（内置 ``SET``）而不是 ``MaterialCategory`` ——
    两张表都叫「分类」，而 ``styles.category_id`` 外键指向的是**前者**。
    传错时报 ``violates foreign key constraint "fk_styles_product_categories"``：
    能看出是外键，但看不出「你把物料类目当成了商品分类」。
    """
    from app.modules.base.models import ProductCategory, Style

    category = (
        await db_session.execute(select(ProductCategory).where(ProductCategory.code == "SET"))
    ).scalar_one()
    style = Style(
        style_no="E2E-WIP-1",
        name="WIP 测试款",
        category_id=category.id,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    db_session.add(style)
    await db_session.flush()
    return style


async def test_lot_columns_are_not_nullable(db_session):
    """TC-B06-01：``dye_lot_no`` / ``bolt_no`` 必须 NOT NULL。

    ⚠️ 直接查 ``information_schema`` 而不是看 ORM：ORM 上写了 ``nullable=False``
    不等于迁移建对了 —— 迁移漏抄时会两边都安静地通过。
    """
    nullable = {
        row[0]: row[1]
        for row in (
            await db_session.execute(
                text(
                    "SELECT column_name, is_nullable FROM information_schema.columns "
                    "WHERE table_name = 'material_stocks'"
                )
            )
        ).all()
    }
    for column in ("dye_lot_no", "bolt_no"):
        assert nullable[column] == "NO", (
            f"material_stocks.{column} 又是可空了 —— 它在唯一键里，"
            f"而 PG 的 NULL 不参与唯一判定，'无缸号'的批次会重复插入任意多次"
        )


async def test_unique_index_excludes_batch_no(db_session):
    """TC-B06-02：唯一键是 ``(warehouse, material, dye_lot, bolt)``，**不含 batch_no**。

    业务确认「不存在同缸不同价」，所以同缸同匹只有一行；``batch_no`` 是展示编号。
    """
    cols = (
        (
            await db_session.execute(
                text(
                    "SELECT a.attname FROM pg_index i "
                    "JOIN pg_class c ON c.oid = i.indrelid "
                    "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(i.indkey) "
                    "WHERE c.relname = 'material_stocks' AND i.indisunique AND i.indpred IS NOT NULL"
                )
            )
        )
        .scalars()
        .all()
    )
    assert set(cols) == {"warehouse_id", "material_id", "dye_lot_no", "bolt_no"}, (
        f"uq_material_stocks_lot 的列变了：{sorted(cols)}（batch_no 不该在内）"
    )


async def test_pick_index_is_partial_and_excludes_locked(db_session):
    """TC-B06-03：选批索引是部分索引，且条件含 ``stock_qty > locked_qty``。

    ⚠️ BR-ST-17：选批只查「还有可用量」的批次。索引条件里少了这个比较，
    索引会大很多 —— 而库存行数是仓库里最多的（一个缸号一个匹就是一行）。
    """
    indexdef = (
        await db_session.execute(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = 'idx_material_stocks_pick'")
        )
    ).scalar_one()
    assert "WHERE" in indexdef, "idx_material_stocks_pick 必须是部分索引"
    assert "stock_qty > locked_qty" in indexdef, (
        "选批索引必须排除「已被锁满」的批次（BR-ST-17），否则索引会随行数膨胀"
    )


async def test_wip_ledger_line_is_append_only(db_session):
    """TC-B06-04：append-only 表没有 ``deleted_at`` / ``version`` / ``updated_*``。

    ⚠️ 这三个字段在 append-only 表上**永远是空值**，而空值比没有更糟：
    读的人看到 ``updated_at IS NULL`` 会以为「没人改过」，而不是「这表不能改」。
    口径与 ``document_logs`` 一致：只留 ``id`` + ``created_at`` + ``created_by``。
    """
    cols = {
        row[0]
        for row in (
            await db_session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'wip_ledger_lines'"
                )
            )
        ).all()
    }
    assert {"created_at", "created_by"} <= cols
    for forbidden in ("deleted_at", "version", "updated_at", "updated_by", "remark_x"):
        assert forbidden not in cols, (
            f"wip_ledger_lines 不该有 {forbidden}（append-only，见 04 §2 的例外）"
        )
    assert not issubclass(WipLedgerLine, object) or "version" not in WipLedgerLine.__table__.c


async def test_app_role_cannot_modify_wip_ledger(app_database_url):
    """TC-B06-05：应用账号**不能** UPDATE/DELETE append-only 流水。

    ⚠️ 权限边界只有真去试才能确认 —— ORM 里看不到任何痕迹，而 04 §2 说
    「append-only 表必须由应用账号无 UPDATE/DELETE」，这条只有一次真 DELETE 才算数。
    """
    import sqlalchemy as sa
    from sqlalchemy.exc import ProgrammingError
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(app_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            for statement in (
                "UPDATE wip_ledger_lines SET qty = 0",
                "DELETE FROM wip_ledger_lines",
            ):
                with pytest.raises((ProgrammingError, sa.exc.DBAPIError)) as caught:
                    await session.execute(sa.text(statement))
                await session.rollback()
                assert "permission denied" in str(caught.value).lower(), (
                    f"应用账号竟然能对 append-only 流水执行 {statement!r}"
                )
    finally:
        await engine.dispose()


async def test_three_tables_have_audit_fields(db_session):
    """TC-B06-06：三张表满足 ``04 §2`` 的公共字段约定（append-only 表除外，见上）。"""
    for table in ("material_stocks", "wip_stocks"):
        rows = (
            (
                await db_session.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns WHERE table_name = :t"
                    ),
                    {"t": table},
                )
            )
            .scalars()
            .all()
        )
        for required in (
            "id",
            "created_at",
            "created_by",
            "updated_at",
            "updated_by",
            "deleted_at",
            "version",
            "remark",
        ):
            assert required in rows, f"{table} 缺公共字段 {required}（docs/04 §2）"
        checks = (
            (
                await db_session.execute(
                    text(
                        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                        "WHERE conrelid = to_regclass(:t) AND contype = 'c'"
                    ),
                    {"t": table},
                )
            )
            .scalars()
            .all()
        )
        assert any("version > 0" in item for item in checks), f"{table} 缺 ck_*_version_positive"


async def test_purpose_is_pg_enum(db_session):
    """TC-B06-07：``purpose`` 是 PG 枚举，含 ADR-0012 的四个值。

    ⚠️ 守卫「防止有人把它改成 varchar」：改动本身编译得过、测试也全绿，
    而后果是「返修布成本天然分开」（BR-ST-25）这条依据失效 —— 表现为
    返修布的成本混进正常布料，成衣毛利虚高，而症状要到看报表才发现。
    """
    enum_type = MaterialStock.__table__.c.purpose.type
    assert enum_type.name == "purchase_purpose"
    assert set(enum_type.enums) == {"NORMAL", "REWORK_RECEIPT", "RETURN", "SAMPLE"}
    # 库里也要真的是这个枚举（模型上写对了但迁移建成 varchar，是最常见的漂移）
    udt = (
        await db_session.execute(
            text(
                "SELECT udt_name FROM information_schema.columns "
                "WHERE table_name = 'material_stocks' AND column_name = 'purpose'"
            )
        )
    ).scalar_one()
    assert udt == "purchase_purpose", f"库里 purpose 的类型是 {udt}，不是枚举"


async def test_wip_qty_constraint_blocks_out_gt_in(db_session):
    """``out_qty <= in_qty``：转出的件数不能超过转入的。

    ⚠️ WIP 的账实一致全靠这一条 —— 没有它，「转出比转入多」这种说不通的状态
    能被存进去，而报表照样出得来，要到月底盘点才发现。
    """
    from sqlalchemy.exc import IntegrityError

    from app.modules.base.models import Warehouse

    await _material(db_session)
    warehouse = (
        await db_session.execute(select(Warehouse).where(Warehouse.code == "FAB"))
    ).scalar_one()
    style = await _style(db_session)

    db_session.add(
        WipStock(
            workshop_id=warehouse.id,
            style_id=style.id,
            style_no=style.style_no,
            color_code="NVY",
            size_code="L",
            qty=10,
            in_qty=5,
            out_qty=8,  # ← 8 > 5，违反 ck_wip_qty
            source_doc_type="CUTTING_ORDER",
            source_doc_id="00000000-0000-0000-0000-000000000009",
            created_by=OPERATOR_ID,
            updated_by=OPERATOR_ID,
        )
    )
    with pytest.raises(IntegrityError):
        async with unit_of_work(db_session):
            pass


def test_models_are_registered() -> None:
    """两张软删表 + 一张 append-only 表都在 ``Base.metadata`` 里。

    ⚠️ 这条看着多余：ORM 没注册的话 ``alembic check`` 会报漂移。但漂移的报错是
    一大段 ``remove_table``，看不出「哪个模型漏写了」—— 而这里能一眼定位。
    """
    for model in (MaterialStock, WipStock, WipLedgerLine):
        assert model.__tablename__ in MaterialStock.metadata.tables
