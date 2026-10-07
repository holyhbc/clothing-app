"""打菲拆分算法**纯函数** + 只读预演（T-BUND-004）。

============================  =================================================
TC-SP-01  ``hands=2, qty_per_hand=60`` → 2 码各 60，余 0
TC-SP-02  ``hands=5, output=12`` → 5 码 × 2、**余 2 且无第 6 码**（B18 / 09 §4.2）
TC-SP-03  1:2:2:1 → 6 手，手号 ``L01/XL01/XL02/XXL01/XXL02/3XL01``
TC-SP-04  同尺码多行（ADR-0017 / Q-B13）：**接着上一个起编**
TC-SP-05  ``hands=0`` → ``10001``，**不静默取整**；非整件每手件数 → ``31003``
TC-SP-07  ``conflicts[]`` 列出手序号冲突（``31005`` 的前置），且**不抛错**
TC-SP-08  预演读库内已有 ACTIVE 码 → ``conflicts[]`` 真列出来；越权 → ``12002``
TC-SP-09  **零写库**：调用前后 ``bundles`` / ``cutting_outputs`` / ``document_logs``
          的**每一行每一格**完全不变
============================  =================================================

⚠️ **TC-SP-09 是这一卡的命门**。``/split`` 的全部价值就是「先看后提交」：一旦预演写库
（预占 / 生成码 / 写日志），主管「核对一下」这个动作本身就会改变数据，而审核时还要重算
一遍 —— 两份口径迟早分叉，用户看到的现象是「我只是点了一下预览，单子就变了」。
"""

from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import Table, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import DataScope
from app.common.models import DocumentLog
from app.core.errors import BusinessError, ErrorCode
from app.core.permissions import AuthContext
from app.modules.bundling.models import Bundle, BundlingOrder, BundlingOrderLine
from app.modules.bundling.service import BundlingOrderService
from app.modules.bundling.service.split import (
    SizeLineSplit,
    SplitLineInput,
    assert_whole,
    preview_order,
    split_size_line,
)
from app.modules.cutting.models import CuttingOutput
from tests.factories.bundling import ctx, payload
from tests.factories.user import OPERATOR_ID

DOC_NO = "BD-20261018-000031"
CUT_LINE = UUID("11111111-1111-1111-1111-111111111111")


def _line(
    size_code: str = "XL",
    hands: int = 2,
    qty_per_hand: int | Decimal = 60,
    color_code: str = "WHT",
    output_qty: int | Decimal | None = None,
) -> SplitLineInput:
    """一条尺码明细入参。``output_qty`` 只在「裁剪侧人工指定出数」时传。"""
    return SplitLineInput(
        color_code=color_code,
        size_code=size_code,
        hands=hands,
        qty_per_hand=Decimal(qty_per_hand),
        cutting_size_line_id=CUT_LINE,
        output_qty=None if output_qty is None else Decimal(output_qty),
    )


def _split(line: SplitLineInput, start_hand_seq: int = 1) -> SizeLineSplit:
    """只把一个尺码明细行喂给 :func:`split_size_line`。"""
    return split_size_line(
        doc_no=DOC_NO,
        color_code=line.color_code,
        size_code=line.size_code,
        hands=line.hands,
        qty_per_hand=line.qty_per_hand,
        cutting_size_line_id=line.cutting_size_line_id,
        start_hand_seq=start_hand_seq,
        output_qty=line.output_qty,
    )


def _assert_code(code: ErrorCode, line: SplitLineInput, *, needle: str = "") -> None:
    with pytest.raises(BusinessError) as caught:
        _split(line)
    assert caught.value.code is code
    if needle:
        assert needle in str(caught.value)


# ------------------------------------------------------------------ TC-SP-01


def test_tc_sp_01_two_hands_two_codes_of_60() -> None:
    """TC-SP-01：``hands=2, qty_per_hand=60`` → **2 个码各 60 件**，余 0（B20）。"""
    result = _split(_line("XL", hands=2, qty_per_hand=60))
    assert [b.bundle_no for b in result.bundles] == [
        "BD-20261018-000031-XL01-0001",
        "BD-20261018-000031-XL02-0001",
    ]
    assert [b.hands for b in result.bundles] == [1, 2]
    assert [b.bundle_qty for b in result.bundles] == [Decimal("60"), Decimal("60")]
    assert result.output_qty == Decimal("120")
    assert result.remainder_qty == 0
    assert result.start_hand_seq == 1


def test_tc_sp_01b_no_rounding_needed_for_exact_products() -> None:
    """``hands=3, qty_per_hand=33`` → 3 码各 33。

    Q-B15 之后**不存在「100÷3 除不尽」**：每手件数直接取 ``qty_per_hand``，
    ``output_qty`` 恒等于 ``hands × qty_per_hand``（ADR-0020）。"""
    result = _split(_line("M", hands=3, qty_per_hand=33))
    assert len(result.bundles) == 3
    assert {b.bundle_qty for b in result.bundles} == {Decimal("33")}
    assert result.output_qty == Decimal("99")
    assert result.remainder_qty == 0


# ------------------------------------------------------------------ TC-SP-02


def test_tc_sp_02_remainder_never_gets_a_code() -> None:
    """TC-SP-02：``hands=5, output=12`` → **5 个码 × 2 件，余 2，无第 6 码**。"""
    result = _split(_line("L", hands=5, qty_per_hand=2, output_qty=12))
    assert len(result.bundles) == 5, "余数不出码：只有 5 个码"
    assert [b.hands for b in result.bundles] == [1, 2, 3, 4, 5]
    assert all(b.bundle_qty == Decimal("2") for b in result.bundles)
    assert result.remainder_qty == Decimal("2")
    assert result.output_qty == Decimal("12")
    assert result.bundles[-1].bundle_no.endswith("-L05-0001")
    assert all("L06" not in b.bundle_no for b in result.bundles)


def test_tc_sp_02b_output_below_hands_times_qty_is_rejected() -> None:
    """人工指定出数**小于** ``hands × qty_per_hand`` → 10001（装不下，不静默出码）。"""
    _assert_code(ErrorCode.PARAM_INVALID, _line("L", hands=5, qty_per_hand=2, output_qty=9))


# ------------------------------------------------------------------ TC-SP-03


def test_tc_sp_03_ratio_1_2_2_1_gives_six_continuous_hands() -> None:
    """TC-SP-03：1:2:2:1 → 6 手，手号 ``L01/XL01/XL02/XXL01/XXL02/3XL01``。"""
    preview = preview_order(
        doc_no=DOC_NO,
        lines=[
            _line("L", hands=1),
            _line("XL", hands=2),
            _line("XXL", hands=2),
            _line("3XL", hands=1),
        ],
    )
    assert [b.bundle_no for p in preview.lines for b in p.bundles] == [
        "BD-20261018-000031-L01-0001",
        "BD-20261018-000031-XL01-0001",
        "BD-20261018-000031-XL02-0001",
        "BD-20261018-000031-XXL01-0001",
        "BD-20261018-000031-XXL02-0001",
        "BD-20261018-000031-3XL01-0001",
    ]
    assert preview.hands_total == 6
    assert preview.planned_qty == Decimal("360")
    assert preview.balance_qty == 0
    assert preview.conflicts == ()


# ------------------------------------------------------------------ TC-SP-04


def test_tc_sp_04_same_size_across_batches_continues_hand_seq() -> None:
    """TC-SP-04：同尺码来自多条裁剪明细行 → **接着上一个起编**（Q-B13 / ADR-0017 §4）。

    §5.3 的例子：行A ``XL hands=2 qty_per_hand=60`` → XL01/XL02；行B
    ``XL hands=1 qty_per_hand=30`` → **XL03**（不是重新从 XL01 编），
    天然合规 ``uq_bundles_hand(doc_id, color_code, size_code, hands)``。
    """
    preview = preview_order(
        doc_no=DOC_NO,
        lines=[_line("XL", hands=2, qty_per_hand=60), _line("XL", hands=1, qty_per_hand=30)],
    )
    bundles = [b for p in preview.lines for b in p.bundles]
    assert [b.hands for b in bundles] == [1, 2, 3]
    assert [b.bundle_no for b in bundles][-1] == "BD-20261018-000031-XL03-0001"
    assert [b.bundle_qty for b in bundles] == [Decimal("60"), Decimal("60"), Decimal("30")]
    assert preview.hands_total == 3
    assert preview.planned_qty == Decimal("150")

    # 不同色的同尺码**各自从 1 起**（唯一键含 color_code，B22）
    two_colors = preview_order(
        doc_no=DOC_NO,
        lines=[_line("XL", hands=2, color_code="WHT"), _line("XL", hands=1, color_code="BLK")],
    )
    assert {p.color_code: [b.hands for b in p.bundles] for p in two_colors.lines} == {
        "WHT": [1, 2],
        "BLK": [1],
    }


# ------------------------------------------------------------------ TC-SP-05 / 06


def test_tc_sp_05_zero_hands_is_rejected_not_rounded() -> None:
    """TC-SP-05：``hands=0`` → ``10001``，**不静默取整**（B18 ④ / §11 TC-21）。"""
    _assert_code(ErrorCode.PARAM_INVALID, _line("XL", hands=0), needle="手数")


def test_tc_sp_05b_negative_hands_and_zero_qty_are_rejected() -> None:
    """``hands=-1`` 与 ``qty_per_hand=0`` 都是 ``10001``（§9）。"""
    _assert_code(ErrorCode.PARAM_INVALID, _line("XL", hands=-1))
    _assert_code(ErrorCode.PARAM_INVALID, _line("XL", hands=2, qty_per_hand=0))


def test_tc_sp_06_non_integer_qty_is_31003() -> None:
    """TC-SP-06：非整件每手件数 → ``31003``（09 §4.2 整件口径，DB CHECK 同款）。"""
    _assert_code(ErrorCode.BUNDLE_QTY_MUST_BE_INTEGER, _line("XL", hands=2, qty_per_hand="60.5"))


def test_tc_sp_06b_assert_whole_normalises_and_guards() -> None:
    """``assert_whole`` 是整件口径的**唯一**断言点：``<= 0`` → 10001，非整数 → 31003。"""
    assert assert_whole(Decimal("60")) == Decimal("60")
    assert assert_whole(60) == Decimal("60")
    _assert_code(ErrorCode.PARAM_INVALID, _line("XL", hands=1, qty_per_hand=0))
    with pytest.raises(BusinessError) as caught:
        assert_whole(Decimal("2.5"), label="尺码 XL 的每手件数")
    assert caught.value.code is ErrorCode.BUNDLE_QTY_MUST_BE_INTEGER
    assert "XL" in str(caught.value)


def test_tc_sp_06c_hand_seq_beyond_two_digits_is_rejected() -> None:
    """手序号固定 2 位（Q-B18）→ 单尺码 > 99 手必须拆单。

    ⚠️ 这条挡的是一个**静默**损坏：``XL100-0001`` 能通过 DB 那条正则
    （``[A-Z0-9]{1,3}[0-9]{2}`` 会把 ``XL1`` 当尺码码），但
    :func:`parse_bundle_no` 解析回来是 ``size_code='XL1' / hands=00``。
    """
    _assert_code(ErrorCode.PARAM_INVALID, _line("XL", hands=100), needle="拆单")


def test_tc_sp_06d_illegal_size_code_is_rejected() -> None:
    """尺码码超过 3 位 → 10001（``bundle_no`` 格式非法，§9）。"""
    _assert_code(ErrorCode.PARAM_INVALID, _line("XLONG", hands=1))


# ------------------------------------------------------------------ TC-SP-07


def test_tc_sp_07_conflicts_are_listed_not_raised() -> None:
    """TC-SP-07：手序号与库内已有 ACTIVE 码冲突 → 进 ``conflicts[]``，**不抛错**。

    预演是给主管「核对」用的：冲突要**展示出来**，不能把请求打回（提交 / 审核才报 31005）。
    """
    preview = preview_order(
        doc_no=DOC_NO,
        lines=[_line("XL", hands=2)],
        existing_hands={("WHT", "XL", 2): "BD-20261018-000001-XL02-0001"},
    )
    assert len(preview.conflicts) == 1
    conflict = preview.conflicts[0]
    assert conflict.code == "31005"
    assert (conflict.color_code, conflict.size_code, conflict.hands) == ("WHT", "XL", 2)
    assert conflict.bundle_no == "BD-20261018-000031-XL02-0001"
    assert conflict.existing_bundle_no == "BD-20261018-000001-XL02-0001"


# ------------------------------------------------------------------ DB 用例


async def _make_order(db_session: AsyncSession, world: dict) -> BundlingOrder:
    xl = next(sl for sl in world["cutting_size_lines"] if sl.size_code == "XL")
    return await BundlingOrderService(db_session).create(
        payload(world, lines=[{"cutting_size_line_id": xl.id, "size_code": "XL", "hands": 2}]),
        OPERATOR_ID,
    )


async def _add_bundle(db_session: AsyncSession, order: BundlingOrder, hands: int) -> None:
    stmt = select(BundlingOrderLine).where(BundlingOrderLine.doc_id == order.id)
    line = (await db_session.execute(stmt)).scalars().one()
    bundle_no = f"{order.doc_no}-XL{hands:02d}-0001"
    db_session.add(
        Bundle(
            doc_id=order.id,
            line_id=line.id,
            bundle_no=bundle_no,
            hands=hands,
            style_no=order.style_no,
            color_code="WHT",
            size_code="XL",
            operation_no="OP01",
            cutting_size_line_id=line.cutting_size_line_id,
            bundle_qty=Decimal("60"),
            qr_content=bundle_no,
            created_by=OPERATOR_ID,
            updated_by=OPERATOR_ID,
        )
    )
    await db_session.flush()


#: 本卡承诺「零变动」的三张表。⚠️ 比对的是**整行**（``select(*table.c)``）而不是手挑
#: 几列 —— 手挑一旦漏了 ``version`` 之类，「被改过但没被看见」就会漏测。
_ZERO_TOUCH_TABLES: dict[str, Table] = {
    "bundles": Bundle.__table__,
    "cutting_outputs": CuttingOutput.__table__,
    "document_logs": DocumentLog.__table__,
}


async def _snapshot(db_session: AsyncSession) -> dict[str, list[tuple[object, ...]]]:
    """把三张表的全部行抓下来，用于预演前后逐格比对。"""
    snapshot: dict[str, list[tuple[object, ...]]] = {}
    for name, table in _ZERO_TOUCH_TABLES.items():
        stmt = select(*table.c).order_by(table.c[0])
        snapshot[name] = [tuple(row) for row in (await db_session.execute(stmt)).all()]
    return snapshot


async def test_tc_sp_08_preview_reads_existing_active_codes(db_session, bundling_world) -> None:
    """TC-SP-08：库内已有 ``(doc, WHT, XL, 2)`` → 预演的 ``conflicts[]`` 列出来。"""
    order = await _make_order(db_session, bundling_world)
    await _add_bundle(db_session, order, hands=2)

    preview = await BundlingOrderService(db_session).preview_split(order.id, ctx())
    assert len(preview.lines) == 1
    assert preview.hands_total == 2
    assert preview.planned_qty == Decimal("120")
    assert [c.hands for c in preview.conflicts] == [2]
    assert preview.conflicts[0].code == "31005"


async def test_tc_sp_09_preview_writes_nothing(db_session, bundling_world) -> None:
    """TC-SP-09：**零写库** —— 预演前后三张表的**值**（不只是行数）完全不变。"""
    order = await _make_order(db_session, bundling_world)
    await _add_bundle(db_session, order, hands=2)
    service = BundlingOrderService(db_session)

    before = await _snapshot(db_session)
    assert len(before["bundles"]) == 1, "夹具应已有 1 条码，否则本用例证不了任何事"

    preview = await service.preview_split(order.id, ctx())
    outputs = await service.available_outputs(order.id, ctx())
    assert preview.hands_total == 2
    assert [row.size_code for row in outputs] == ["S", "M", "L", "XL"], "来源裁剪单该色全部尺码"
    assert all(row.available_qty == 0 for row in outputs), "本用例没建结转行 → 全为 0"

    after = await _snapshot(db_session)
    for table in ("bundles", "cutting_outputs", "document_logs"):
        assert after[table] == before[table], f"{table} 在只读预演后被改动了"
    assert await db_session.scalar(text("SELECT count(*) FROM bundles")) == 1


async def test_tc_sp_09b_available_outputs_reads_view(db_session, bundling_world) -> None:
    """``available_outputs`` 读 ``v_cutting_output_available``（04 §7.7.5）。"""
    world = bundling_world
    db_session.add(
        CuttingOutput(
            style_id=world["style"].id,
            style_no=world["style"].style_no,
            color_code="WHT",
            size_code="XL",
            workshop_id=world["workshop"].id,
            output_qty=Decimal("1000"),
            balance_qty=Decimal("0"),
            bundled_qty=Decimal("120"),
            reserved_qty=Decimal("0"),
            cut_waste_qty=Decimal("0"),
            created_by=OPERATOR_ID,
            updated_by=OPERATOR_ID,
        )
    )
    await db_session.flush()
    order = await _make_order(db_session, world)

    rows = await BundlingOrderService(db_session).available_outputs(order.id, ctx())
    by_size = {row.size_code: row for row in rows}
    assert set(by_size) == {"S", "M", "L", "XL"}, "来源裁剪单的每个尺码都该出现"
    assert by_size["XL"].available_qty == Decimal("880"), "1000 - 120 已打菲"
    assert (by_size["XL"].hands, by_size["XL"].qty_per_hand) == (1, Decimal("60"))
    assert by_size["L"].available_qty == Decimal("0"), "没有结转行的尺码可用量为 0"


async def test_tc_sp_10_preview_out_of_data_scope_is_denied(db_session, bundling_world) -> None:
    """TC-SP-10：越权预演 → ``12002``（只读也要过数据范围，07 §3.2 铁律 2）。"""
    order = await _make_order(db_session, bundling_world)
    outsider = AuthContext(
        user_id=OPERATOR_ID,
        name="他车间主管",
        employee_no="A002",
        workshop_id=None,
        group_no=None,
        permissions=frozenset(),
        data_scope=DataScope.WORKSHOP,
        allowed_workshop_ids=frozenset(),
    )
    with pytest.raises(BusinessError) as caught:
        await BundlingOrderService(db_session).preview_split(order.id, outsider)
    assert caught.value.code is ErrorCode.DATA_SCOPE_DENIED
