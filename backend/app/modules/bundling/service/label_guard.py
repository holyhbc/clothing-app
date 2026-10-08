"""标签的手号区间与「共 M 手」快照校验（T-BUND-006，**纯函数**）。

照 :mod:`.state_guard` 的做法：这些判断既不查库也不写库，所以单独放一个模块，
service 的两个方法各自调用它 —— 判断只有一份，「导出按什么区间、登记就按什么区间」。

⚠️ 这里**不允许任何"取其一"的兜底**：``hands_seq`` 与 ``from_hands`` 矛盾时必须报错。
悄悄挑一个会让留痕里的「第 N 手」与实际印的那一手对不上，而那正是留痕存在的意义。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from app.common.enums import DocumentStatus
from app.core.errors import BusinessError, ErrorCode
from app.modules.bundling.label_repository import LabelBundleRow
from app.modules.bundling.models import BundleStatus, BundlingOrder


def assert_hand_range(from_hands: int | None, to_hands: int | None) -> None:
    """手号区间合法（≥ 1 且不颠倒）。倒过来写（3~1）→ ``10001``，不是空结果。"""
    for name, value in (("from_hands", from_hands), ("to_hands", to_hands)):
        if value is not None and value < 1:
            raise BusinessError(
                ErrorCode.PARAM_INVALID, f"{name} 必须 ≥ 1（手号从 1 起），收到 {value}"
            )
    if from_hands is not None and to_hands is not None and from_hands > to_hands:
        raise BusinessError(
            ErrorCode.PARAM_INVALID,
            f"手号区间颠倒：from_hands={from_hands} > to_hands={to_hands}",
            details={"from_hands": from_hands, "to_hands": to_hands},
        )


def resolve_hand_span(
    hands_seq: int | None, from_hands: int | None, to_hands: int | None
) -> tuple[int, int]:
    """把 ``hands_seq`` / ``from_hands`` / ``to_hands`` 归一成 ``(起点, 终点)``。

    :param hands_seq: 本次打印的第几手（03 §6 **必传**）。
    :raises BusinessError: ``10002`` 缺 ``hands_seq``；``10001`` 与 ``from_hands`` 矛盾
        或区间颠倒。
    """
    if hands_seq is None:
        raise BusinessError(
            ErrorCode.MISSING_BUSINESS_PARAM,
            "必须带 hands_seq（本次打印第几手，03 §6），空白不算",
        )
    start = from_hands if from_hands is not None else hands_seq
    if start != hands_seq:
        raise BusinessError(
            ErrorCode.PARAM_INVALID,
            f"hands_seq（本次第 {hands_seq} 手）与 from_hands（区间起点 {start}）矛盾",
            details={"hands_seq": hands_seq, "from_hands": from_hands},
        )
    end = to_hands if to_hands is not None else start
    assert_hand_range(start, end)
    return start, end


def assert_hands_total_snapshot(
    row: LabelBundleRow, totals: Mapping[tuple[str, str], int], claimed: int | None
) -> None:
    """前端传的「共 M 手」与库内权威值不符 → ``10001``。

    ⚠️ 落库的永远是**权威值**；但对不上必须报错而不是照抄 —— 照抄会把一个错的
    「共 M 手」永久写进留痕，而留痕是日后核对「标签与实物」的依据（B15）。
    """
    if claimed is None:
        return
    actual = totals.get((row.color_code, row.size_code), row.hands_seq)
    if claimed != actual:
        raise BusinessError(
            ErrorCode.PARAM_INVALID,
            f"{row.size_code} 尺码实际共 {actual} 手，收到 {claimed}（请刷新后重试）",
            details={"size_code": row.size_code, "expected": actual, "received": claimed},
        )


def assert_printable_order(order: BundlingOrder) -> None:
    """只有**已审核**（已出码）的单能出标签 / 印标签。

    ⚠️ 草稿态压根没有 ``bundles`` 行，返回空数组会让用户以为「这单没有手」；反审核后
    码全 ``VOIDED``，印出去的标签是废标签（B10 作废是业务终态）。两种情况都该说清
    「为什么不能印」，而不是给一个空结果。
    """
    if order.status is not DocumentStatus.APPROVED:
        raise BusinessError(
            ErrorCode.CUTTING_STATUS_NOT_ALLOWED,
            f"打菲单 {order.doc_no} 当前状态 {order.status.value} 不能打印标签"
            "（标签要在审核出码之后才能印）",
            details={"status": order.status.value},
        )


def assert_printable(rows: list[LabelBundleRow], start: int, end: int) -> list[LabelBundleRow]:
    """区间内必须有码，且**逐手 ACTIVE** —— 区分 ``31001`` 与 ``31002``。

    ⚠️ 两种情况的处置完全不同：没有码是「手号填错了 / 还没出码」，码已作废是「这手已经
    作废了，别印」。报同一个码的话，用户会去查手号，而真正的原因是作废。
    """
    if not rows:
        raise BusinessError(
            ErrorCode.BUNDLE_NO_NOT_FOUND,
            f"打菲单第 {start}~{end} 手没有对应的码（手号超出该尺码范围，请核对区间）",
            details={"from_hands": start, "to_hands": end},
        )
    voided = [row.bundle_no for row in rows if row.status != BundleStatus.ACTIVE]
    if voided:
        raise BusinessError(
            ErrorCode.BUNDLE_ALREADY_VOIDED,
            f"打菲号 {'、'.join(voided)} 已作废，不能再印标签（如需补打请先作废重开）",
            details={"bundle_nos": voided},
        )
    return rows


#: 单次导出 / 登记的**手上限**（防一次拉 20 万手把响应撑爆，05 §2「批量上限」）。
#:
#: ⚠️ 打菲单本身的手数上限是 20000（modules/03 §5.3），而标签导出的实际用法是
#: 「按手号区间印一叠」（几十~几百张）。1 万已远超日常用量，又在单据上限之内。
MAX_LABEL_HANDS: Final[int] = 10_000


def assert_within_max_hands(count: int) -> None:
    """区间内的手数超上限 → ``10001``（``11011`` 是「导出行数超 10 万」，口径不同）。"""
    if count > MAX_LABEL_HANDS:
        raise BusinessError(
            ErrorCode.PARAM_INVALID,
            f"一次最多导出/登记 {MAX_LABEL_HANDS} 手，收到 {count} 手，请按手号区间分批",
            details={"hands_count": count, "max_hands": MAX_LABEL_HANDS},
        )


__all__ = [
    "MAX_LABEL_HANDS",
    "assert_hand_range",
    "assert_hands_total_snapshot",
    "assert_printable",
    "assert_printable_order",
    "assert_within_max_hands",
    "resolve_hand_span",
]
