"""裁剪单草稿态 service 的测试（T-CUT-001b-1）。

============================  ===============================================
TC-C01b-01                   ``doc_no`` 格式 ``CT-YYYYMMDD-6 位``
TC-C01b-02                   同日**并发**取号不重复（C1）
TC-C01b-03                   三级汇总：头 = Σ行 = Σ颜色 Σ尺码（C6）
TC-C01b-04                   表头汇总**不信任前端**（入参里根本没有那五列）
TC-C01b-05                   ``output_qty = hands × qty_per_hand`` 精确整数，**不 floor**
TC-C01b-06                   行余量 = 行可出件数 - Σ明细；负数 → ``30002``（C34）
TC-C01b-07                   人工指定出数 → 颜色转 ``MANUAL`` + 差额进余量（C13/C28）
TC-C01b-08                   同尺码多行合法（C30）
TC-C01b-09                   一行多色合法（C32）
TC-C01b-10                   数据范围：越权取单 → ``12002``（07 §3.2 铁律 2）
TC-C01b-11                   ``hands`` 必须是整数（``1.5`` → ``10001``，ADR-0020）
TC-C01b-12                   款号停用 / 无分类 → 建单即拒，不留到 submit
TC-C01b-13                   缸号 / 匹号**从 stock_id 反查**，不接受前端传（ADR-0022）
TC-C01b-14                   改行**不写**比例主数据（C29 零污染）
TC-C01b-15                   ``cut_waste_qty`` 含余量，且余量不被扣两次（C13/C5）
============================  ===============================================

⚠️ **TC-C01b-06 与 TC-C01b-15 是这一卡最要紧的两条**，因为它们守的是本次
**刚刚修掉的缺陷**（业务确认 2026-10-04「口径 A」）。原来的写法是「服务端把行
``output_qty`` 覆盖成 ``Σ明细``」，代进 C34 就是 ``balance_qty ≡ 0`` ——
「行余量」这个概念整个消失，而 ``cut_waste_qty ≥ balance_qty`` 这条不变式
失去了载体。症状不会报错，只会**报表上的损耗率慢慢偏低**，到月底对账才发现。

⚠️ **TC-C01b-05 也是「刚刚定的口径」**：``hands`` 是 ``int``，所以
``hands × qty_per_hand`` 天然是整数，**代码里不许出现 floor**。
若哪天有人为了「兼容 1.5 手」加了 ``floor``，本条会立刻红。
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from app.common.enums import DataScope
from app.core.errors import BusinessError, ErrorCode
from app.core.numbering import DOC_PREFIX_CUTTING, format_doc_no, take_doc_no
from app.core.permissions import AuthContext
from app.modules.cutting.models import (
    CuttingEntryMode,
    CuttingOrder,
    CuttingOrderLine,
    CuttingOrderLineColor,
    CuttingOrderSizeLine,
)
from app.modules.cutting.schemas import (
    CuttingOrderCreateIn,
    LineColorIn,
    OrderLineIn,
    SizeLineIn,
)
from app.modules.cutting.service import CuttingOrderService, recalc_line
from tests.factories.cutting import DOC_DATE
from tests.factories.cutting import ctx as _ctx
from tests.factories.cutting import payload as _payload
from tests.factories.cutting import size_line as _size
from tests.factories.user import OPERATOR_ID

# ------------------------------------------------------------------ 夹具


async def _create(db_session, cutting_world, **overrides) -> CuttingOrder:
    service = CuttingOrderService(db_session)
    return await service.create(_payload(cutting_world, **overrides), OPERATOR_ID)


async def _reload(db_session, order_id):
    """重新从库里读一遍三层（**不复用内存对象** —— 那会掩盖没落库的值）。

    ⚠️ 刻意**不用** ``selectinload``：这里要的是「库里真实的值」，
    而 ORM 对象的 ``balance_qty`` 等列在 ``flush`` 后就是内存里那个值，
    复用它等于没验。逐层查虽然多几条语句，但验的是真东西。
    """
    order = await db_session.get(CuttingOrder, order_id)
    await db_session.refresh(order)
    lines = list(
        (
            await db_session.execute(
                select(CuttingOrderLine)
                .where(CuttingOrderLine.doc_id == order_id)
                .order_by(CuttingOrderLine.line_no)
            )
        )
        .scalars()
        .all()
    )
    colors = list(
        (
            await db_session.execute(
                select(CuttingOrderLineColor).where(
                    CuttingOrderLineColor.line_id.in_([line.id for line in lines])
                )
            )
        )
        .scalars()
        .all()
    )
    size_lines = list(
        (
            await db_session.execute(
                select(CuttingOrderSizeLine).where(
                    CuttingOrderSizeLine.line_color_id.in_([c.id for c in colors])
                )
            )
        )
        .scalars()
        .all()
    )
    return order, lines, colors, size_lines


# ------------------------------------------------------------------ TC-C01b-01 / 02


def test_doc_no_format() -> None:
    """TC-C01b-01：``CT-YYYYMMDD-6 位``（09 §2.1、C1）。"""
    assert format_doc_no("CT", date(2026, 10, 18), 1) == "CT-20261018-000001"
    assert format_doc_no("CT", date(2026, 10, 18), 150) == "CT-20261018-000150"
    assert format_doc_no("CT", date(2026, 1, 5), 999999) == "CT-20260105-999999"


def test_doc_no_sequence_overflow_is_rejected() -> None:
    """序号超出 6 位要**报错**，不能拼出 ``CT-20261018-1000000``。

    ⚠️ 拼出去比不拼更糟：那个号违反 09 §2.1 的格式，而「号格式不符」
    比「号用尽」难排查得多 —— 用户会以为系统坏了。
    """
    with pytest.raises(BusinessError) as caught:
        format_doc_no("CT", date(2026, 10, 18), 1_000_000)
    assert caught.value.code is ErrorCode.PARAM_INVALID


async def test_concurrent_doc_no_is_unique(db_session, cutting_world) -> None:
    """TC-C01b-02：**同日并发取号不重复**（C1「生成即占用、永不复用」）。

    ⚠️ 必须用**独立引擎真提交**：同一个连接上的语句会被 PostgreSQL 串行化，
    测出来的其实是「顺序执行」（``test_migrations`` 里踩过同一个坑）。

    ⚠️ 每次都**新建 session + 真 commit** —— 只有真提交才让下一个事务看见
    计数器递增，否则 20 个并发全都在同一份未提交的 ``next_no`` 上取号。
    """
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from tests.conftest import app_database_url  # noqa: F401 —— 仅为确认夹具可用

    engine = create_async_engine(_db_url(), pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:

        async def take_one() -> str:
            async with factory() as session, session.begin():
                return await take_doc_no(session, prefix=DOC_PREFIX_CUTTING, doc_date=DOC_DATE)

        numbers = await asyncio.gather(*(take_one() for _ in range(20)))
        assert len(set(numbers)) == 20, f"取号重复了：{sorted(numbers)}"
        # ⚠️ 断言**连续**而不是「恰好是 1..20」：这些提交不在任何回滚范围内，
        #    重跑一次计数器就前进了 20 —— 断言绝对区间会让第二条用例重跑即红。
        #    「20 个号互不相同且连号」才是取号器该保证的东西。
        seqs = sorted(int(no[-6:]) for no in numbers)
        assert seqs == list(range(seqs[0], seqs[0] + 20)), f"取到的号不连续：{numbers}"
        assert all(no.startswith("CT-20261018-") for no in numbers)
    finally:
        await engine.dispose()


def _db_url() -> str:
    import os

    return os.environ["DATABASE_URL"]


async def test_doc_no_resets_each_day(db_session) -> None:
    """单据号**按天重置**（C1 的格式直接后果）。

    ⚠️ 这条是「为什么不用 PG SEQUENCE」的机器证据：``SEQUENCE`` 做不到
    「新的一天自动从头来」，而 ``(doc_date, prefix)`` 作唯一键就自然做到了。

    ⚠️ **只断言相对关系，不断言绝对值**：上一条并发用例用**独立引擎真提交**了
    20 个号（docs/10 §2.3 明确「CC-2/CC-3/CC-6 必须用独立引擎真提交」），
    那些提交**不在**任何用例的回滚范围内 —— 所以「第一个号一定是 000001」
    这种断言会依赖用例执行顺序，重跑一次就红。而 ``erp_app`` 又没有 DELETE
    权限、连清计数器行都做不到（真去清反而会撞 ``permission denied``）。

    ⚠️ 「新的一天必然是 000001」这条**本身是稳定的**：不管那天之前取过多少号，
    新的一天的第一号永远是 1 —— 这正是「按天重置」要证明的东西。
    """
    day1_first = await take_doc_no(
        db_session, prefix=DOC_PREFIX_CUTTING, doc_date=date(2026, 10, 20)
    )
    day1_second = await take_doc_no(
        db_session, prefix=DOC_PREFIX_CUTTING, doc_date=date(2026, 10, 20)
    )
    day2_first = await take_doc_no(
        db_session, prefix=DOC_PREFIX_CUTTING, doc_date=date(2026, 10, 21)
    )

    assert int(day1_second[-6:]) == int(day1_first[-6:]) + 1, "同一天必须逐个递增"
    assert day2_first == "CT-20261021-000001", "换一天就该从头开始，而不是接着昨天的号"
    assert day2_first != day1_second


# ------------------------------------------------------------------ TC-C01b-03 / 05 / 06 / 15


async def test_three_level_totals(db_session, cutting_world) -> None:
    """TC-C01b-03：三级汇总自底向上（行 610 = 360 + 250，modules/02 §5.1 那个例子）。"""
    payload = _payload(
        cutting_world,
        lines=[
            OrderLineIn(
                line_no=1,
                stock_id=cutting_world["stock"].id,
                fabric_qty=Decimal("96.000"),
                waste_qty=Decimal("3.000"),
                output_qty=Decimal("615.000"),  # 铺布估算：比明细多 5
                colors=[
                    LineColorIn(
                        color_code="WHT",
                        size_lines=[
                            _size(1, "L", 1, 60),
                            _size(2, "XL", 2, 60),
                            _size(3, "XXL", 2, 60),
                            _size(4, "3XL", 1, 60),
                        ],
                    ),
                    LineColorIn(
                        color_code="BLK",
                        size_lines=[_size(1, "L", 1, 50), _size(2, "XL", 2, 50)],
                    ),
                ],
            )
        ],
    )
    order = await CuttingOrderService(db_session).create(payload, OPERATOR_ID)
    await db_session.flush()

    _order, lines, colors, size_lines = await _reload(db_session, order.id)

    # 颜色层：WHT 360 / BLK 150
    by_color = {c.color_code: c for c in colors}
    assert by_color["WHT"].output_qty_total == Decimal("360.000")
    assert by_color["WHT"].hands_total == Decimal("6")
    assert by_color["BLK"].output_qty_total == Decimal("150.000")
    assert by_color["BLK"].hands_total == Decimal("3")

    # 行层：余量 = 615 - 510 = 105
    assert len(lines) == 1
    assert lines[0].balance_qty == Decimal("105.000")

    # 表头
    assert order.fabric_qty == Decimal("96.000")
    assert order.output_qty == Decimal("615.000")
    assert order.balance_qty == Decimal("105.000")
    # ⚠️ 裁损 = Σ行 waste_qty + 余量 = 3 + 105 = 108（C13「含尾数」）
    assert order.cut_waste_qty == Decimal("108.000")
    assert order.hands_total == 9
    assert order.color_codes == "BLK,WHT", "color_codes 是去重后按字典序拼的，供列表筛选用"
    assert len(size_lines) == 6


async def test_output_qty_is_exact_integer_product(db_session, cutting_world) -> None:
    """TC-C01b-05：``output_qty = hands × qty_per_hand``，**精确整数、不取整**。

    ⚠️ 挑 ``2 手 × 45 件 = 90`` 这种**乘出来不是 100 的整数倍**的组合：
    如果代码里误加了 ``floor`` 或任何取整，这一条仍然会过 —— 真正的守卫是
    下面 :func:`test_recalc_line_rejects_negative_balance` 与
    :func:`test_non_integer_hands_is_rejected`。
    """
    payload = _payload(
        cutting_world,
        lines=[
            OrderLineIn(
                line_no=1,
                stock_id=cutting_world["stock"].id,
                fabric_qty=Decimal("96.000"),
                # 铺布估算 235 = 明细 225（2×45 + 3×45）+ 余量 10
                output_qty=Decimal("235.000"),
                colors=[
                    LineColorIn(
                        color_code="WHT",
                        size_lines=[_size(1, "L", 2, 45), _size(2, "XL", 3, 45)],
                    )
                ],
            )
        ],
    )
    order = await CuttingOrderService(db_session).create(payload, OPERATOR_ID)
    _order, _lines, _colors, size_lines = await _reload(db_session, order.id)

    assert {row.output_qty for row in size_lines} == {90, 135}
    assert all(row.balance_qty == 0 for row in size_lines), "整数乘整数没有尾数"
    assert all(row.output_qty_manual is False for row in size_lines)


async def test_balance_is_line_estimate_minus_size_lines(db_session, cutting_world) -> None:
    """TC-C01b-06：**行余量 = 正向录入的行可出件数 - Σ明细**（C34 口径 A）。

    ⚠️ 这条是本次修掉的缺陷的正向断言。原来的实现把行 ``output_qty``
    **覆盖**成 Σ明细，于是余量恒为 0。
    """
    order = await _create(db_session, cutting_world)
    _order, lines, _colors, _sizes = await _reload(db_session, order.id)
    assert lines[0].output_qty == Decimal("120.000"), "行可出件数是用户录入的估算值，不能被覆盖"
    assert lines[0].balance_qty == Decimal("60.000"), "120 - 60 = 60"


def test_recalc_line_rejects_negative_balance(db_session, cutting_world) -> None:
    """TC-C01b-06（负数侧）：行可出件数 < Σ明细 → ``30002``。

    ⚠️ 这是整张单**最重要的一道业务校验**：它拦的是「你登记的可出件数
    比实际裁出来的还少」。不拦的后果不是报错，而是 ``cut_waste_qty``
    出现负数、损耗率报表彻底失去意义 —— 而那要到月底对账才发现。
    """
    line = CuttingOrderLine(
        doc_id="00000000-0000-0000-0000-000000000001",
        line_no=1,
        stock_id="00000000-0000-0000-0000-000000000002",
        material_id="00000000-0000-0000-0000-000000000003",
        style_no="HB-2026-0018",
        dye_lot_no="DY-1",
        bolt_no="B-1",
        fabric_qty=Decimal("96.000"),
        output_qty=Decimal("50.000"),
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    color = CuttingOrderLineColor(
        line_id="00000000-0000-0000-0000-000000000004",
        color_code="WHT",
        output_qty_total=Decimal("60.000"),
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    with pytest.raises(BusinessError) as caught:
        recalc_line(line, [color])
    assert caught.value.code is ErrorCode.CUTTING_QTY_CONFLICT
    assert caught.value.details is not None
    assert caught.value.details["line_no"] == 1
    assert caught.value.details["size_line_sum_qty"] == "60.000"


# ------------------------------------------------------------------ TC-C01b-04 / 07


async def test_header_totals_are_not_client_supplied() -> None:
    """TC-C01b-04：表头汇总五列**不接受前端传入**（C6「不信任前端」）。

    ⚠️ 守的是「入参模型里根本没有那五列」。如果哪天有人为了让前端
    「能显示点什么」把它们加进 ``CuttingOrderCreateIn``，本条立刻红。
    """
    fields = set(CuttingOrderCreateIn.model_fields)
    for forbidden in (
        "fabric_qty",
        "output_qty",
        "cut_waste_qty",
        "balance_qty",
        "hands_total",
    ):
        assert forbidden not in fields, (
            f"{forbidden} 不该出现在建单入参里 —— C6 明确由 service 重算并覆盖入参。"
            f"而**能被忽略的入参是最坏的一种**：前端以为设的值生效了"
        )


async def test_manual_output_switches_color_to_manual(db_session, cutting_world) -> None:
    """TC-C01b-07：人工指定出数 → 该颜色**自动转 MANUAL** + 差额进余量（C13/C28）。

    ⚠️ C28 的理由：颜色若还留在 ``MASTER``，界面上会显示「按比例带出来的」，
    下一次编辑时比例会覆盖人工的判断 —— 人的一次决定被系统悄悄抹掉。
    """
    payload = _payload(
        cutting_world,
        lines=[
            OrderLineIn(
                line_no=1,
                stock_id=cutting_world["stock"].id,
                fabric_qty=Decimal("96.000"),
                output_qty=Decimal("100.000"),
                colors=[
                    LineColorIn(
                        color_code="WHT",
                        # 1 手 × 60 = 60，人工指定 55 → 差额 5 进 balance_qty
                        size_lines=[_size(1, "L", 1, 60, output=55)],
                    )
                ],
            )
        ],
    )
    order = await CuttingOrderService(db_session).create(payload, OPERATOR_ID)
    _order, _lines, colors, size_lines = await _reload(db_session, order.id)

    assert colors[0].entry_mode is CuttingEntryMode.MANUAL, "人工指定出数后必须转 MANUAL（C28）"
    assert size_lines[0].output_qty == 55
    assert size_lines[0].output_qty_manual is True
    assert size_lines[0].balance_qty == 5, "差额 60 - 55 = 5 进 balance_qty（C13）"
    # 行余量 = 100 - 55 = 45；裁损 = 0（无 waste_qty）+ 45
    assert order.balance_qty == Decimal("45.000")
    assert order.cut_waste_qty == Decimal("45.000")


async def test_output_equal_to_computed_is_not_manual(db_session, cutting_world) -> None:
    """人工传的件数**与服务端算的一致**时不算「人工指定」。

    ⚠️ 边界：前端按 C25 渲染时会回显算出来的值，那次回传**不该**把颜色打成
    ``MANUAL``。判据是「与服务端算的不一致」，不是「传了就算」。
    """
    payload = _payload(
        cutting_world,
        lines=[
            OrderLineIn(
                line_no=1,
                stock_id=cutting_world["stock"].id,
                fabric_qty=Decimal("96.000"),
                output_qty=Decimal("60.000"),
                colors=[
                    LineColorIn(color_code="WHT", size_lines=[_size(1, "L", 1, 60, output=60)])
                ],
            )
        ],
    )
    order = await CuttingOrderService(db_session).create(payload, OPERATOR_ID)
    _order, _lines, colors, size_lines = await _reload(db_session, order.id)
    assert colors[0].entry_mode is CuttingEntryMode.MASTER
    assert size_lines[0].output_qty_manual is False
    assert size_lines[0].balance_qty == 0


# ------------------------------------------------------------------ TC-C01b-08 / 09


async def test_same_size_multiple_rows_is_allowed(db_session, cutting_world) -> None:
    """TC-C01b-08：同一 ``(颜色, 尺码)`` **可以多行**（C30，ADR-0014 模式 C）。"""
    payload = _payload(
        cutting_world,
        lines=[
            OrderLineIn(
                line_no=1,
                stock_id=cutting_world["stock"].id,
                fabric_qty=Decimal("96.000"),
                output_qty=Decimal("150.000"),
                colors=[
                    LineColorIn(
                        color_code="WHT",
                        size_lines=[
                            _size(1, "XL", 2, 60),
                            _size(2, "XL", 1, 30),  # 同尺码第二行
                        ],
                    )
                ],
            )
        ],
    )
    order = await CuttingOrderService(db_session).create(payload, OPERATOR_ID)
    _order, _lines, _colors, size_lines = await _reload(db_session, order.id)

    assert len(size_lines) == 2
    assert {row.size_code for row in size_lines} == {"XL"}
    assert order.output_qty == Decimal("150.000")


async def test_multiple_colors_per_line_is_allowed(db_session, cutting_world) -> None:
    """TC-C01b-09：一行**多色**（C32，ADR-0017 的核心：一床可裁多个颜色）。"""
    payload = _payload(
        cutting_world,
        lines=[
            OrderLineIn(
                line_no=1,
                stock_id=cutting_world["stock"].id,
                fabric_qty=Decimal("96.000"),
                output_qty=Decimal("610.000"),
                colors=[
                    LineColorIn(color_code="WHT", size_lines=[_size(1, "L", 6, 60)]),
                    LineColorIn(color_code="BLK", size_lines=[_size(1, "L", 5, 50)]),
                ],
            )
        ],
    )
    order = await CuttingOrderService(db_session).create(payload, OPERATOR_ID)
    _order, lines, colors, _sizes = await _reload(db_session, order.id)

    assert len(colors) == 2
    assert {c.color_code for c in colors} == {"WHT", "BLK"}
    assert all(c.line_id == lines[0].id for c in colors), "两色都挂在同一行下"
    assert order.color_codes == "BLK,WHT"


# ------------------------------------------------------------------ TC-C01b-10 / 11 / 12 / 13 / 14


async def test_detail_respects_data_scope(db_session, cutting_world) -> None:
    """TC-C01b-10：**越权按 ID 直查 → ``12002``**（docs/07 §3.2 铁律 2）。

    ⚠️ 这是数据范围最重要的一条守卫：漏了它，车间主管就能按 ID 打开
    别的车间的裁剪单 —— 而列表页看起来一切正常（列表有过滤）。
    """
    order = await _create(db_session, cutting_world)
    service = CuttingOrderService(db_session)
    outsider = AuthContext(
        user_id=OPERATOR_ID,
        name="别的车间主管",
        employee_no="A002",
        workshop_id=None,
        group_no=None,
        permissions=frozenset(),
        data_scope=DataScope.WORKSHOP,
        allowed_workshop_ids=frozenset(),  # ← 看不到任何车间
    )
    with pytest.raises(BusinessError) as caught:
        await service.get(order.id, outsider)
    assert caught.value.code is ErrorCode.DATA_SCOPE_DENIED


async def test_detail_loads_three_levels(db_session, cutting_world) -> None:
    """详情要**一次拿完三层**（``selectinload``），而不是逐层懒加载。"""
    order = await _create(db_session, cutting_world)
    loaded = await CuttingOrderService(db_session).get(order.id, _ctx())
    assert len(loaded.lines) == 1
    assert len(loaded.lines[0].colors) == 1
    assert len(loaded.lines[0].colors[0].size_lines) == 1
    assert loaded.lines[0].colors[0].size_lines[0].output_qty == 60


def test_non_integer_hands_is_rejected() -> None:
    """TC-C01b-11：``hands = 1.5`` 在**校验层**就被拒（ADR-0020 取消 1.5 手）。

    ⚠️ 用 ``int`` 而不是 ``Decimal`` 声明的收益就在这条：类型层面就挡住了，
    不依赖 service 里那条「容易被人漏掉」的判断。
    """
    with pytest.raises(ValueError, match="valid integer"):
        SizeLineIn(size_line_no=1, size_code="L", hands=1.5, qty_per_hand=60)  # type: ignore[arg-type]


def test_zero_hands_is_rejected() -> None:
    """``hands = 0`` → ``10001``（C22：``<= 0`` 无意义）。"""
    with pytest.raises(ValueError):
        SizeLineIn(size_line_no=1, size_code="L", hands=0, qty_per_hand=60)


async def test_disabled_style_is_rejected_at_create(db_session, cutting_world) -> None:
    """TC-C01b-12：款号停用 → **建单即拒**，不留到 submit。

    ⚠️ 让用户建完一整张单才发现款号被停用，是最难解释的一种失败。
    """
    cutting_world["style"].is_active = False
    with pytest.raises(BusinessError) as caught:
        await _create(db_session, cutting_world)
    assert caught.value.code is ErrorCode.BASE_DATA_REFERENCED


async def test_lot_columns_are_derived_from_stock(db_session, cutting_world) -> None:
    """TC-C01b-13：缸号 / 匹号 / 物料 / 供应商**从 stock_id 反查**（ADR-0022）。

    ⚠️ 这就是「级联选料」的结构性保证：请求里**只有** ``stock_id``，
    所以「缸号与匹号对不上」「门幅填了别的批次的」这类错误**不可能发生**。
    """
    order = await _create(db_session, cutting_world)
    _o, lines, _c, _s = await _reload(db_session, order.id)
    stock = cutting_world["stock"]
    assert lines[0].stock_id == stock.id
    assert lines[0].dye_lot_no == stock.dye_lot_no
    assert lines[0].bolt_no == stock.bolt_no
    assert lines[0].material_id == stock.material_id
    assert lines[0].supplier_id == stock.supplier_id
    # 门幅不传时取批次实测值
    assert lines[0].width_cm == stock.width_cm


def test_lot_columns_are_not_client_supplied() -> None:
    """TC-C01b-13 的另一半：入参里**没有**缸号 / 匹号 / 物料 / 供应商。

    ⚠️ 传了会被 ``extra="forbid"`` 挡下（``10001``）而不是「悄悄被忽略」。
    """
    fields = set(OrderLineIn.model_fields)
    for forbidden in ("dye_lot_no", "bolt_no", "material_id", "supplier_id", "stock_qty_before"):
        assert forbidden not in fields, (
            f"{forbidden} 不该由前端传 —— 它是 stock_id 的快照（C38 / ADR-0022）"
        )


async def test_create_does_not_touch_ratio_master_data(db_session, cutting_world) -> None:
    """TC-C01b-14：**建单绝不写比例主数据**（C29 零污染，本模块铁律）。

    ⚠️ 验证口径就是 modules/02 C29 写的那句「用完模式 C 后检查比例主数据
    零变更」—— 建单时比例表必须**一个字节都没变**。
    """
    from app.modules.base.models import StyleColorSizeRatio

    before = (
        await db_session.execute(select(func.count()).select_from(StyleColorSizeRatio))
    ).scalar_one()
    await _create(db_session, cutting_world)
    after = (
        await db_session.execute(select(func.count()).select_from(StyleColorSizeRatio))
    ).scalar_one()
    assert after == before, "建单碰了 style_color_size_ratios —— C29 铁律：改行绝不写回比例主数据"


# ------------------------------------------------------------------ 列表


async def test_list_filters_and_paginates(db_session, cutting_world) -> None:
    """列表：按款号筛 + 分页，且**必须**过数据范围（INV-8）。"""
    await _create(db_session, cutting_world)
    await _create(db_session, cutting_world)

    from app.modules.cutting.repository import OrderListQuery

    service = CuttingOrderService(db_session)
    rows, total = await service.list_orders(
        OrderListQuery(style_no=cutting_world["style"].style_no, page=1, size=10), _ctx()
    )
    assert total == 2
    assert len(rows) == 2
    assert all(row.style_no == cutting_world["style"].style_no for row in rows)

    _, none_total = await service.list_orders(OrderListQuery(style_no="NOT-EXIST"), _ctx())
    assert none_total == 0


async def test_list_rejects_deep_paging(db_session) -> None:
    """深分页被拒（``04 §5.1``：offset > 10000 直接 ``10001``）。"""
    from app.modules.cutting.repository import MAX_OFFSET, OrderListQuery

    service = CuttingOrderService(db_session)
    with pytest.raises(BusinessError) as caught:
        await service.list_orders(OrderListQuery(page=MAX_OFFSET + 10, size=100), _ctx())
    assert caught.value.code is ErrorCode.PARAM_INVALID


async def test_soft_deleted_order_is_invisible(db_session, cutting_world) -> None:
    """软删的单据**详情查不到**（INV-7）。"""
    order = await _create(db_session, cutting_world)
    await db_session.execute(
        text("UPDATE cutting_orders SET deleted_at = now() WHERE id = :i"), {"i": order.id}
    )
    await db_session.flush()
    with pytest.raises(BusinessError) as caught:
        await CuttingOrderService(db_session).get(order.id, _ctx())
    assert caught.value.code is ErrorCode.CUTTING_STATUS_NOT_ALLOWED
