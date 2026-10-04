"""裁剪单四张表的建表测试（T-CUT-001a / 迁移 0009）。

======================  =================================================
TC-C01a-01              ``status`` 是 PG 枚举 ``document_status``（不是 varchar）
TC-C01a-02              ``document_status`` 六个值齐全（含 ``PAID``）
TC-C01a-03              四张表都有 ``04 §2`` 公共字段 + ``ck_*_version_positive``
TC-C01a-04              三层外键链完整（size_line → line_color → line → order）
TC-C01a-05              ``workshop_id`` **有** FK 指向 ``workshops``
TC-C01a-06              ``hands`` / ``qty_per_hand`` / ``output_qty`` 是 **integer**
TC-C01a-07              ``ck_cutting_orders_hand`` 是 ``>= 0`` 而非 ``> 0``
TC-C01a-08              ``ck_cutting_size_hands`` 拒 ``hands <= 0``
TC-C01a-09              三个唯一键的列集正确（行 / 颜色 / 尺码明细）
TC-C01a-10              三个列表索引是**部分索引**
TC-C01a-11              ``idx_cutting_size_lines_line`` 存在（冗余外键也要索引）
TC-C01a-12              应用账号**不能** DELETE 四张表
TC-C01a-13              四个模型都在 ``Base.metadata``
TC-C01a-14              ``cutting_orders`` 已不在 ``scope.PENDING_TABLES``
======================  =================================================

⚠️ **TC-C01a-07 是这张卡最关键的一条**，而它的症状在别的卡上已经真实发生过一次：
``hands_total`` 有 ``DEFAULT 0``，而**草稿态的单据还没有尺码明细**，
``hands_total`` 必然是 0。写成 ``CHECK (> 0)` 会让**任何新建的草稿单都插不进去**
—— 报错是 ``violates check constraint``，读的人会以为是 service 层漏传字段，
而真实原因是建表时就写死了矛盾的约束。``bundling_orders.hands_total`` 踩过同一个坑
（04 §7.16 留了记录），所以这条断言守的是「别再踩第二次」。

⚠️ 本模块**大量断言直接查库**（``information_schema`` / ``pg_index`` /
``pg_enum``）而不是看 ORM：ORM 上写了 ``nullable=False`` 不等于迁移建对了 ——
迁移漏抄时两边会**一起安静地通过**。
"""

import re

import pytest
from sqlalchemy import text

from app.core.db import unit_of_work
from app.modules.cutting.models import (
    CuttingOrder,
    CuttingOrderLine,
    CuttingOrderLineColor,
    CuttingOrderSizeLine,
)
from tests.factories.user import OPERATOR_ID, WorkshopFactory

CUTTING_TABLES = (
    "cutting_orders",
    "cutting_order_lines",
    "cutting_order_line_colors",
    "cutting_order_size_lines",
)

#: ``docs/04 §3`` / ``docs/08 §1.1`` 的通用单据状态。``PAID`` 在枚举里但**不在**
#: 裁剪单的值域内 —— 08 §1.1 REV-2026-10 明确「加入通用枚举，仅 PayrollSheet 使用」。
DOCUMENT_STATUS_VALUES = {"DRAFT", "SUBMITTED", "APPROVED", "REJECTED", "CANCELLED", "PAID"}

CUTTING_ENTRY_MODE_VALUES = {"MASTER", "UNIFORM", "MANUAL"}


def _normalized(definition: str) -> str:
    """把 PG 回显的 CHECK 文本归一化成可比对的形状。

    ⚠️ PG 会把 numeric 列的 ``>= 0`` 回显成 ``>= (0)::numeric`` ——
    实测原样比对必然失败，报错 ``'fabric_qty >= 0' in '... >= (0)::numeric ...'``
    完全看不出是「PG 加了类型转换」。所以断言前先剥掉这些转换。
    整数列（``hands_total >= 0``）不会被加转换，但统一剥掉对两者都无害。
    """
    return re.sub(r"\(\s*(-?[\d.]+|\w+)\s*\)::[\w ]+", r"\1", definition)


def _confdeltype(value: object) -> str:
    """把 ``pg_constraint.confdeltype``（``char``）解成可读字符串。

    ⚠️ asyncpg 把 PG 的内部 ``"char"`` 类型（单字节）取成 ``bytes``，
    所以这里拿到的是 ``b'r'`` 而不是 ``'r'``。踩过一次：直接比 ``== "r"``
    会永远失败，而报错 ``b'r' == 'r'`` 完全看不出是类型问题。
    """
    return value.decode() if isinstance(value, bytes | bytearray) else str(value)


async def _columns(session, table: str) -> dict[str, str]:
    """``表名 → {列名: udt_name}``，直接查 ``information_schema``。

    ⚠️ 用 ``udt_name`` 而不是 ``data_type``：``data_type`` 对 ``varchar(32)`` 与
    ``varchar(255)`` 都返回 ``character varying``，那样就分不出长度漂移了；
    而 ``udt_name`` 对枚举返回类型名本身（``document_status``），正好用来断言
    「它真的是枚举而不是 varchar」。
    """
    rows = (
        await session.execute(
            text(
                "SELECT column_name, udt_name FROM information_schema.columns WHERE table_name = :t"
            ),
            {"t": table},
        )
    ).all()
    return {row[0]: row[1] for row in rows}


# ------------------------------------------------------------------ 枚举口径


async def test_status_is_pg_enum_not_varchar(db_session) -> None:
    """TC-C01a-01：``cutting_orders.status`` 必须是 PG 枚举 ``document_status``。

    ⚠️ 守的是「04 §7.7.2 原写的 ``cutting_status`` 那个笔误不会回来」——
    那个类型**全仓从未定义**，照抄建表报 ``type "cutting_status" does not exist``。
    改回 varchar 更隐蔽：能建、能跑，只是枚举值失去数据库层约束。
    """
    columns = await _columns(db_session, "cutting_orders")
    assert columns["status"] == "document_status", (
        f"cutting_orders.status 的类型是 {columns['status']}，"
        f"不是 document_status（04 §3 禁止 VARCHAR 存状态）"
    )
    columns = await _columns(db_session, "cutting_order_line_colors")
    assert columns["entry_mode"] == "cutting_entry_mode", (
        f"cutting_order_line_colors.entry_mode 的类型是 {columns['entry_mode']}，"
        f"不是枚举 cutting_entry_mode"
    )


async def test_document_status_has_all_six_values(db_session) -> None:
    """TC-C01a-02：枚举值集合与 04 §3 / 08 §1.1 **逐个**一致。

    ⚠️ 含 ``PAID``：08 §1.1 REV-2026-10 要求它在**通用**枚举里（工资单那张卡
    直接用，不必再 ``ALTER TYPE ... ADD VALUE``）。少一个值，工资单那张卡
    会卡在「枚举里没有 PAID」上，而那要等 P2 才发现。
    """
    values = (
        (
            await db_session.execute(
                text(
                    "SELECT e.enumlabel FROM pg_enum e "
                    "JOIN pg_type t ON t.oid = e.enumtypid WHERE t.typname = 'document_status'"
                )
            )
        )
        .scalars()
        .all()
    )
    assert set(values) == DOCUMENT_STATUS_VALUES, (
        f"document_status 的值变了：{sorted(values)}（04 §3 / 08 §1.1）"
    )

    modes = (
        (
            await db_session.execute(
                text(
                    "SELECT e.enumlabel FROM pg_enum e "
                    "JOIN pg_type t ON t.oid = e.enumtypid WHERE t.typname = 'cutting_entry_mode'"
                )
            )
        )
        .scalars()
        .all()
    )
    assert set(modes) == CUTTING_ENTRY_MODE_VALUES, (
        f"cutting_entry_mode 的值变了：{sorted(modes)}（04 §7.7.2 / ADR-0014）"
    )


# ------------------------------------------------------------------ 公共字段


@pytest.mark.parametrize("table", CUTTING_TABLES)
async def test_table_has_audit_fields(db_session, table: str) -> None:
    """TC-C01a-03：四张表都是软删表，都要有 04 §2 的公共字段与乐观锁 CHECK。

    ⚠️ 四张表**全部**用 :class:`BaseModel` 而不是 append-only 的 ``IdMixin``：
    裁剪单要反审核、要留痕、要软删历史单据。这条断言的作用是防止后来者
    看到「子表都是流水」就顺手改成 append-only —— 那会让反审核无处落笔。
    """
    columns = await _columns(db_session, table)
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
        assert required in columns, f"{table} 缺公共字段 {required}（docs/04 §2）"
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
    assert any("version > 0" in item for item in checks), (
        f"{table} 缺 ck_*_version_positive（04 §2 要求乐观锁版本号为正）"
    )


# ------------------------------------------------------------------ 外键链


async def test_three_level_foreign_key_chain(db_session) -> None:
    """TC-C01a-04：三层外键链完整，且三根都带 ``ON DELETE RESTRICT``。

    ⚠️ ``RESTRICT`` 是这三张子表**唯一**的删除保护。应用账号本来就没有 DELETE
    权限，但 ``erp_ddl``（运维手工修数据时用的迁移账号）有 ——
    没有 RESTRICT 的话一次误操作就能删掉一整床裁剪明细，而单据还在。

    ⚠️ 同时验证 ``cutting_order_lines`` 的三根**级联选料**外键（ADR-0022）：
    ``stock_id`` → ``material_stocks``、``material_id`` → ``materials``、
    ``supplier_id`` → ``suppliers``。少任何一根，「选了批却查不出用了什么料」。
    """
    rows = (
        await db_session.execute(
            text(
                "SELECT child.relname, parent.relname, fk.confdeltype "
                "FROM pg_constraint fk "
                "JOIN pg_class child ON child.oid = fk.conrelid "
                "JOIN pg_class parent ON parent.oid = fk.confrelid "
                "WHERE fk.contype = 'f' AND child.relname = ANY(:tables) "
                "ORDER BY child.relname, parent.relname"
            ),
            {"tables": list(CUTTING_TABLES)},
        )
    ).all()
    edges = {(row[0], row[1]): _confdeltype(row[2]) for row in rows}

    # ⚠️ 'r' = RESTRICT（PG 的 pg_constraint.confdeltype）
    for child, parent in (
        ("cutting_order_lines", "cutting_orders"),
        ("cutting_order_line_colors", "cutting_order_lines"),
        ("cutting_order_size_lines", "cutting_order_line_colors"),
        ("cutting_order_size_lines", "cutting_order_lines"),
    ):
        assert (child, parent) in edges, f"{child} 缺指向 {parent} 的外键（04 §7.7.2）"
        assert edges[(child, parent)] == "r", (
            f"{child} → {parent} 必须是 ON DELETE RESTRICT，"
            f"实际是 {edges[(child, parent)]}（子表是单据的一部分，不许被级联删）"
        )

    for parent in ("material_stocks", "materials", "suppliers", "styles", "workshops"):
        assert ("cutting_order_lines", parent) in edges or (
            "cutting_orders",
            parent,
        ) in edges, f"级联选料/数据范围的外键缺了 → {parent}"


async def test_workshop_id_has_foreign_key(db_session) -> None:
    """TC-C01a-05：``cutting_orders.workshop_id`` **有** FK 指向 ``workshops``。

    ⚠️ 04 §7.7.2 原先只写 ``uuid NOT NULL``，本卡补上了外键。理由不是「严谨」，
    而是**不加不会报错**，只会静默：能传入一个不存在的车间 id，而
    ``apply_data_scope`` 拿它去 ``IN (...)`` 过滤时只返回空 —— 症状与
    「车间主管看不到数据」完全一样，从日志里看不出根因。
    """
    got = (
        await db_session.execute(
            text(
                "SELECT count(*) FROM pg_constraint fk "
                "JOIN pg_class child ON child.oid = fk.conrelid "
                "JOIN pg_class parent ON parent.oid = fk.confrelid "
                "WHERE fk.contype = 'f' AND child.relname = 'cutting_orders' "
                "AND parent.relname = 'workshops'"
            )
        )
    ).scalar_one()
    assert got == 1, "cutting_orders.workshop_id 没有 FK 指向 workshops（04 §7.7.2 REV 修正 3）"


async def test_business_keys_have_no_foreign_key(db_session) -> None:
    """``style_no`` / ``dye_lot_no`` 等业务键**刻意没有**外键。

    ⚠️ 守的是「别把外键加回 ``style_no``」：``styles.uq_styles_no`` 与
    ``material_stocks.uq_material_stocks_lot`` 都是 ``WHERE deleted_at IS NULL`` 的
    **部分索引**（为了让款号 / 缸号软删后可复用），而 PG 的外键只能引用普通唯一
    约束或主键 —— 加回去建表直接失败，报
    ``there is no unique constraint matching given keys``（T-DOCS-003 实测）。
    """
    offenders = (
        await db_session.execute(
            text(
                "SELECT child.relname, a.attname FROM pg_constraint fk "
                "JOIN pg_class child ON child.oid = fk.conrelid "
                "JOIN pg_attribute a ON a.attrelid = child.oid "
                "AND a.attnum = ANY(fk.conkey) "
                "WHERE fk.contype = 'f' AND child.relname = ANY(:tables) "
                "AND a.attname = ANY(:keys)"
            ),
            {
                "tables": list(CUTTING_TABLES),
                "keys": ["style_no", "dye_lot_no", "bolt_no", "color_code", "size_code"],
            },
        )
    ).all()
    assert not offenders, (
        f"这些业务键上多了外键（外键会指向部分唯一索引，PG 不允许）：{offenders}。"
        f"外键应该落在 style_id / stock_id / line_id 上，业务键只做冗余"
    )


# ------------------------------------------------------------------ 数量与 CHECK


async def test_size_line_quantities_are_integers(db_session) -> None:
    """TC-C01a-06：``hands`` / ``qty_per_hand`` / ``output_qty`` 是 **integer**。

    ⚠️ ADR-0020 的口径：手数是权威输入，件数 = 乘法**精确值**，
    **不再 floor、不允许 1.5 手**。建成 ``numeric`` 的话 1.5 手能存进去，
    而下游打菲按 ``round(hands)`` 生成码 —— 于是「1.5 手」在库里合法、在码上
    只生成 2 个，账实不符且极难排查。
    """
    columns = await _columns(db_session, "cutting_order_size_lines")
    for column in ("hands", "qty_per_hand", "output_qty", "balance_qty"):
        assert columns[column] == "int4", (
            f"cutting_order_size_lines.{column} 的类型是 {columns[column]}，"
            f"应为 integer（ADR-0020：件数 = 乘法精确值，不取整、不允许 1.5 手）"
        )

    # ⚠️ 反过来：比例主数据 style_color_size_ratios.ratio **必须仍是 numeric** ——
    #    1.5 手是合法的**建议值**（ADR-0013），只是不能直接当权威输入。
    #    两处搞反的后果是「按比例带出」永远带不出小数建议。
    ratios = await _columns(db_session, "style_color_size_ratios")
    assert ratios["ratio"] == "numeric", (
        f"style_color_size_ratios.ratio 的类型是 {ratios['ratio']}，应为 numeric"
        f"（ADR-0013：比例可小数，如 1.5 手）"
    )


async def test_hands_total_constraint_allows_zero(db_session) -> None:
    """TC-C01a-07：``ck_cutting_orders_hand`` 是 ``>= 0``，**不能**是 ``> 0``。

    ⚠️ 这条守的是一个**已经真实发生过**的错误（``bundling_orders`` 上同款）：
    ``hands_total`` 有 ``DEFAULT 0``，而草稿态的单据还没有尺码明细，
    ``hands_total`` 必然是 0。写成 ``> 0`` 会让**任何新建的草稿单都插不进去**，
    报错 ``violates check constraint`` 完全看不出根因。
    ``> 0`` 的实际校验在 service 的 ``submit``（modules/02 §2 C8）。
    """
    definition = (
        await db_session.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'ck_cutting_orders_hand'"
            )
        )
    ).scalar_one()
    definition = _normalized(definition)
    assert "hands_total >= 0" in definition, (
        f"ck_cutting_orders_hand 是 {definition} —— 必须是 >= 0。"
        f"> 0 会让草稿态的单据插不进去（草稿态还没有尺码明细，hands_total 必然是 0）"
    )


async def test_qty_constraint_allows_zero(db_session) -> None:
    """``ck_cutting_orders_qty`` 也是 ``>= 0`` —— **本卡实测撞出的真实缺陷**。

    ⚠️⚠️ 这是 T-CUT-001a 写测试时撞出来的，不是预防性加固：
    ``04 §7.7.2`` 原写 ``fabric_qty > 0 AND output_qty > 0``，而这两列
    **同样有 ``DEFAULT 0``**，草稿态也必然是 0 —— 于是
    **任何新建的草稿单都插不进库**（实测报
    ``CheckViolationError: ... violates check constraint "ck_cutting_orders_qty"``）。

    为什么之前没人发现：04 §7.7.2 里那个矛盾**只在 ``hands_total`` 的注释里
    写清楚了**，读的人（包括 0008 与本卡的迁移作者）照着改那一条、
    以为「同类问题已经处理过了」，就漏掉了这一条。所以这条断言与
    :func:`test_hands_total_constraint_allows_zero` 必须**成对存在**：
    单守一条，另一条随时会被改回去。
    """
    definition = (
        await db_session.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'ck_cutting_orders_qty'"
            )
        )
    ).scalar_one()
    definition = _normalized(definition)
    assert "fabric_qty >= 0" in definition, (
        f"ck_cutting_orders_qty 是 {definition} —— fabric_qty / output_qty 必须 >= 0。"
        f"它们有 DEFAULT 0 且草稿态必然是 0，> 0 会让任何草稿单都插不进去"
    )
    assert "output_qty >= 0" in definition, (
        f"ck_cutting_orders_qty 是 {definition} —— output_qty 也必须 >= 0（同上）"
    )


async def test_draft_order_can_be_inserted(db_session) -> None:
    """草稿态的裁剪单**真的能插进库**（TC-C01a-07 的正面验证）。

    ⚠️ 上一条只查了约束定义；这条真去 INSERT。理由是「约束文本看着对」与
    「INSERT 真能过」之间还有 DEFAULT / NOT NULL 的组合 —— 而组合起来失败的话，
    报错信息指向的列往往不是真正的原因。
    """
    from datetime import date

    from app.common.enums import DocumentStatus
    from tests.modules.test_stock_basis import _material, _style

    await _material(db_session)
    style = await _style(db_session)
    workshop = await WorkshopFactory.create(db_session)

    db_session.add(
        CuttingOrder(
            doc_no="CT-20991231-999999",
            workshop_id=workshop.id,
            style_id=style.id,
            style_no=style.style_no,
            color_codes="WHT,BLK",
            # ⚠️ 必须传 ``date`` 对象而不是 ISO 字符串：asyncpg 不做隐式转换，
            #    传字符串报 `invalid input for query argument: '2099-12-31'`
            doc_date=date(2099, 12, 31),
            status=DocumentStatus.DRAFT,
            # ⚠️ 头汇总与 hands_total 全用默认值 0 —— 草稿态就该是 0
            created_by=OPERATOR_ID,
            updated_by=OPERATOR_ID,
        )
    )
    async with unit_of_work(db_session):
        pass


async def test_size_hands_constraint_rejects_zero(db_session) -> None:
    """TC-C01a-08：``ck_cutting_size_hands`` 真的拒 ``hands <= 0``。

    ⚠️ 零手行没有意义（09 §1.5b），会让「这一尺码没裁」和「裁了 0 手」分不清，
    而后者在审核时会被算进 ``hands_total``。
    """
    from sqlalchemy.exc import IntegrityError

    from tests.modules.test_stock_basis import _style

    style = await _style(db_session)
    order = await _make_order(db_session, style)
    line = await _make_line(db_session, order)
    color = await _make_color(db_session, line)

    db_session.add(
        CuttingOrderSizeLine(
            line_color_id=color.id,
            line_id=line.id,
            size_line_no=1,
            size_code="L",
            hands=0,  # ← 违反 ck_cutting_size_hands
            qty_per_hand=60,
            output_qty=0,
            created_by=OPERATOR_ID,
            updated_by=OPERATOR_ID,
        )
    )
    with pytest.raises(IntegrityError):
        async with unit_of_work(db_session):
            pass


# ------------------------------------------------------------------ 唯一键与索引


async def test_three_unique_constraints_have_right_columns(db_session) -> None:
    """TC-C01a-09：三个唯一键的列集正确（层级错位会静默通过）。

    ⚠️ 唯一键是 ADR-0017 三层结构的**数据库层落点**，层级错位不会报错：
    写成 ``UNIQUE (doc_id, color_code)`` 依然能建表，只是「同一匹布上的同一颜色
    只能一行」这条 ADR-0017 的核心语义丢了 —— 而症状是「同一个颜色在两匹布上
    各录一份比例，手数算重」。
    """
    expected = {
        "cutting_order_lines": {"doc_id", "line_no"},
        "cutting_order_line_colors": {"line_id", "color_code"},
        "cutting_order_size_lines": {"line_color_id", "size_line_no"},
    }
    for table, columns in expected.items():
        actual = (
            (
                await db_session.execute(
                    text(
                        "SELECT a.attname FROM pg_index i "
                        "JOIN pg_class c ON c.oid = i.indrelid "
                        "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(i.indkey) "
                        "WHERE c.relname = :t AND i.indisunique AND i.indpred IS NULL "
                        "AND NOT i.indisprimary"
                    ),
                    {"t": table},
                )
            )
            .scalars()
            .all()
        )
        assert set(actual) == columns, (
            f"{table} 的唯一键列集是 {sorted(actual)}，应为 {sorted(columns)}"
        )


async def test_list_indexes_are_partial(db_session) -> None:
    """TC-C01a-10：三个列表索引都是**部分索引**（``WHERE deleted_at IS NULL``）。

    ⚠️ ``04 §5`` 的原话：「部分索引在本项目高频使用，软删后仍能保证索引紧凑」。
    裁剪单的行数会随年限增长，而软删的历史单据占多数 —— 不带条件的索引
    会把已删的行一直挂在索引里。
    """
    for index in (
        "idx_cutting_orders_scope",
        "idx_cutting_orders_trace",
        "idx_cutting_orders_status_workshop",
        "idx_cutting_order_lines_lot",
        "idx_cutting_order_lines_sup",
        "idx_cutting_size_lines_line",
    ):
        indexdef = (
            await db_session.execute(
                text("SELECT indexdef FROM pg_indexes WHERE indexname = :i"), {"i": index}
            )
        ).scalar_one()
        assert "WHERE" in indexdef and "deleted_at IS NULL" in indexdef, (
            f"{index} 必须是部分索引（04 §5）：{indexdef}"
        )


async def test_redundant_foreign_key_is_indexed(db_session) -> None:
    """TC-C01a-11：冗余外键 ``cutting_order_size_lines.line_id`` 有索引。

    ⚠️ ``04 §5`` 要求「所有被用作 JOIN 的外键建索引」。``line_id`` 是冗余列
    （打菲 / 计件按布批反查），而 ``uq_cutting_size_lines`` 只覆盖
    ``line_color_id`` —— 覆盖不到它。而「按布批反查这个布批裁了哪些尺码」
    正是打菲（``bundles.cutting_size_line_id``）与计件的入口查询。
    """
    columns = (
        (
            await db_session.execute(
                text(
                    "SELECT a.attname FROM pg_index i "
                    "JOIN pg_class c ON c.oid = i.indrelid "
                    "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(i.indkey) "
                    "WHERE i.indrelid = 'cutting_order_size_lines'::regclass"
                )
            )
        )
        .scalars()
        .all()
    )
    assert "line_id" in columns, (
        f"cutting_order_size_lines.line_id 没有索引（04 §5 要求所有 JOIN 外键建索引）；"
        f"现有索引覆盖的列：{sorted(columns)}"
    )


# ------------------------------------------------------------------ 权限边界


async def test_app_role_cannot_delete_cutting_tables(app_database_url) -> None:
    """TC-C01a-12：应用账号**不能** DELETE 这四张表。

    ⚠️ 权限边界只有真去试才能确认 —— ORM 里看不到任何痕迹，而 AGENTS §2.1
    禁止物理删除业务数据。裁剪单的删除诱惑比主数据大得多（录错了要能删），
    所以这条边界必须是**数据库层**的，而不是靠 service 层自觉。
    """
    import sqlalchemy as sa
    from sqlalchemy.exc import ProgrammingError
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(app_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            for table in CUTTING_TABLES:
                statement = f"DELETE FROM {table} WHERE false"  # noqa: S608 —— 表名是上面的常量
                with pytest.raises((ProgrammingError, sa.exc.DBAPIError)) as caught:
                    await session.execute(sa.text(statement))
                await session.rollback()
                assert "permission denied" in str(caught.value).lower(), (
                    f"应用账号竟然能对 {table} 执行 DELETE（04 §6.2.1：单据不可硬删）"
                )
    finally:
        await engine.dispose()


# ------------------------------------------------------------------ 注册与清单


def test_models_are_registered() -> None:
    """TC-C01a-13：四个模型都在 ``Base.metadata`` 里，且四张表都齐。

    ⚠️ ORM 没注册的话 ``alembic check`` 会报漂移，但报错是一大段
    ``remove_table``，看不出「哪个模型漏写了」—— 这里能一眼定位。
    """
    registered = set(CuttingOrder.metadata.tables)
    for table in CUTTING_TABLES:
        assert table in registered, f"{table} 不在 Base.metadata 里"
    for model in (
        CuttingOrder,
        CuttingOrderLine,
        CuttingOrderLineColor,
        CuttingOrderSizeLine,
    ):
        assert model.__tablename__ in registered


def test_cutting_orders_left_pending_tables() -> None:
    """TC-C01a-14：``cutting_orders`` 已从 ``scope.PENDING_TABLES`` 移出。

    ⚠️ ``PENDING_TABLES`` 的方向是「建了就删」。留着不删的后果不是报错，
    而是那张表既不在待建清单里、也不在 ``test_scope_specs_keys_are_real_tables``
    的待建分支里 —— 而那条守卫恰恰是为了抓「登记了不存在的表名」，
    清单烂掉它就看不见了（与 ``test_pending_table_has_a_ticket_reference``
    同一个道理）。
    """
    from app.core.scope import PENDING_TABLES, SCOPE_SPECS

    assert "cutting_orders" not in PENDING_TABLES, (
        "cutting_orders 已由迁移 0009 建出，必须从 PENDING_TABLES 移出"
    )
    # ⚠️ 顺带守住另一头：**不能**从 SCOPE_SPECS 里删掉它。
    #    漏登记的症状不是越权而是「界面一片空白」，从日志里看不出来
    #    （test_scope_specs_declares_workshop_resources 守的是「有车间列的表」）
    assert "cutting_orders" in SCOPE_SPECS
    assert SCOPE_SPECS["cutting_orders"].workshop_column == "workshop_id"


# ------------------------------------------------------------------ 建数据辅助


async def _make_order(db_session, style):
    """建一张草稿裁剪单（供子表用例复用）。

    ⚠️ ``workshop_id`` 必须取**真车间**：``04 §7.7.2`` REV-2026-10 补的外键
    ``fk_cutting_orders_workshop`` 会拒一个随手 ``uuid4()``。
    这条外键不是形式 —— 它挡的正是「车间 id 传错却静默查不到数据」那一类。
    """
    from datetime import date

    workshop = await WorkshopFactory.create(db_session)
    order = CuttingOrder(
        doc_no="CT-20991231-999998",
        workshop_id=workshop.id,
        style_id=style.id,
        style_no=style.style_no,
        color_codes="WHT",
        doc_date=date(2099, 12, 31),
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    db_session.add(order)
    await db_session.flush()
    return order


async def _make_line(db_session, order):
    """建一行布批行。⚠️ 先自己造一个 ``material_stocks`` 行 —— 别张望复用别处的。

    ``test_stock_basis`` 的 ``_material()`` 只建物料 / 类目 / 单位 / 仓库，
    **不建物料结存**。所以这里必须自己建一条，否则
    ``SELECT ... FROM material_stocks LIMIT 1`` 返回空，症状是
    ``stock[0]`` 抛 ``TypeError: 'NoneType'``，完全看不出根因是「少造了一张表」。
    """
    from datetime import date
    from decimal import Decimal

    from app.modules.base.models import MaterialStock
    from tests.modules.test_stock_basis import _material

    # ⚠️ `_material()` **不幂等**（每次都 INSERT 一条 code 固定的物料），
    #    所以先查再用 —— 踩过一次：同一个用例里调两次直接撞 uq_materials_code，
    #    而报错指向 materials，与「裁剪单建不出来」毫无关系
    material_id = (
        await db_session.execute(text("SELECT id FROM materials WHERE code = 'F-CT-888801'"))
    ).scalar_one_or_none()
    if material_id is None:
        material_id = (await _material(db_session)).id

    row = (
        await db_session.execute(
            text(
                "SELECT s.id, s.material_id, s.supplier_id, s.width_cm "
                "FROM material_stocks s WHERE s.deleted_at IS NULL LIMIT 1"
            )
        )
    ).first()
    if row is None:
        stock = MaterialStock(
            warehouse_id=(
                await db_session.execute(text("SELECT id FROM warehouses WHERE code = 'FAB'"))
            ).scalar_one(),
            material_id=material_id,
            supplier_id=None,
            width_cm=Decimal("152.00"),
            stock_qty=Decimal("500.000"),
            total_length_m=Decimal("500.000"),
            unit_cost=Decimal("12.500000"),
            in_date=date(2026, 10, 1),
            created_by=OPERATOR_ID,
            updated_by=OPERATOR_ID,
        )
        db_session.add(stock)
        await db_session.flush()
        row = (stock.id, stock.material_id, stock.supplier_id, stock.width_cm)

    line = CuttingOrderLine(
        doc_id=order.id,
        line_no=1,
        stock_id=row[0],
        material_id=row[1],
        supplier_id=row[2],
        style_no=order.style_no,
        dye_lot_no="DY-CUT-TEST",
        bolt_no="B-1",
        width_cm=row[3],
        fabric_qty=Decimal("96.000"),
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    db_session.add(line)
    await db_session.flush()
    return line


async def _make_color(db_session, line):
    color = CuttingOrderLineColor(
        line_id=line.id,
        color_code="WHT",
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    db_session.add(color)
    await db_session.flush()
    return color
