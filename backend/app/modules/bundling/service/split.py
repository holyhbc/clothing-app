"""打菲拆分算法 —— **纯函数**（T-BUND-004 / docs/modules/03-打菲.md §5.3）。

审核（T-BUND-005b）与预演（:mod:`.preview`）**共用本文件**：同一份算法算两遍的地方，
两边迟早分叉，而分叉的表现是「预演说 6 个码、审核生成 5 个」—— 用户只会认为是审核算错了。

## 权威口径（Q-B15 / Q-B16 / Q-B13 / Q-B18 均 2026-10-06 闭环）

| 量 | 口径 | 出处 |
| --- | --- | --- |
| 每手件数 ``bundle_qty`` | **直接取**裁剪尺码明细行的 ``qty_per_hand``，**不 floor** | B18 / B20 / ADR-0020 |
| 码数 | 该行 ``hands``（整数） | B21 |
| ``output_qty`` | ``hands × qty_per_hand``（精确整数） | ADR-0020 |
| 余数 | ``output_qty - hands × qty_per_hand``，**不出码**；草稿 / 正常态恒为 0 | B18 / 09 §4.2 |
| 手序号 | 同一打菲单内**同尺码全局连续编到 N**，跨布批行**接着上一个起编** | Q-B13（暂定默认） |
| ``bundle_no`` | ``{单号}-{尺码码 1~3 位}{手序号 2 位}-0001`` | ADR-0016 §1 / Q-B18 |

## 本文件**没有**的东西

无 DB、无 session、无 ORM 引用 —— 所以它能被单测直接调用，也能被审核事务内复用。
读库（取 ``qty_per_hand``、查已有 ACTIVE 码、读可用出数）全在
:mod:`app.modules.bundling.repository` 与 :mod:`.preview`。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from app.core.errors import BusinessError, ErrorCode

from .numbering import HANDS_SEQ_DIGITS, build_bundle_no, is_valid_bundle_no

ZERO = Decimal("0")

#: 手序号可表达的最大值（``bundle_no`` 里固定 2 位，Q-B18）。
#:
#: ⚠️ 这条不是「顺手加的限制」，而是**挡一个静默损坏**：第 100 手会拼出
#: ``XL100-0001``，它**能通过** DB 那条 CHECK（``[A-Z0-9]{1,3}[0-9]{2}`` 会把 ``XL1``
#: 当成尺码码），但 ``parse_bundle_no`` 解析回来是 ``size_code='XL1' / hands=00`` ——
#: 码能扫、扫出来却是另一个尺码的第 0 手。所以宁可拒单让人拆单。
#: TODO(业务待确认: 单尺码手数上限) 文档只写了「单张打菲单 hands_total ≤ 20000」，
#: 未写「单尺码 ≤ 99 手」。若业务确实要单尺码过百手，需要改 ``bundle_no`` 格式
#: （手序号 3 位）而不是放宽这里。
MAX_HAND_SEQ = 10**HANDS_SEQ_DIGITS - 1

#: 冲突项的错误码文案（``31005``）。⚠️ 这里是**字符串**而不是
#: :class:`~app.core.errors.ErrorCode`：``conflicts[]`` 是给主管看的**清单**，
#: 不是异常 —— 预演遇到冲突必须**展示**而不是把请求打回（§5.5）。
HAND_DUPLICATED_CODE = "31005"


# =================================================================== 数据结构


@dataclass(frozen=True, slots=True)
class SplitBundle:
    """预演出的**一手**（将来落 ``bundles`` 的一行）。"""

    bundle_no: str
    hands: int
    bundle_qty: Decimal


@dataclass(frozen=True, slots=True)
class SizeLineSplit:
    """一条裁剪尺码明细行 → 若干手的拆分结果。"""

    color_code: str
    size_code: str
    cutting_size_line_id: UUID
    hands: int
    qty_per_hand: Decimal
    output_qty: Decimal
    bundle_qty: Decimal
    remainder_qty: Decimal
    start_hand_seq: int
    bundles: tuple[SplitBundle, ...]


@dataclass(frozen=True, slots=True)
class SplitLineInput:
    """:func:`split_size_line` / :func:`preview_order` 的**纯数据**入参。

    ⚠️ 用它而不是 ORM 实体，是因为「算法能被单独测」比「算法能少写几行 import」
    重要得多：审核路径上传 ORM 实体、预演路径上传本结构，两者算出来必须一致。
    """

    color_code: str
    size_code: str
    hands: int
    qty_per_hand: Decimal | int
    cutting_size_line_id: UUID
    #: 裁剪侧**人工指定出数**时的实际出数；``None`` 表示按 ``hands × qty_per_hand``。
    output_qty: Decimal | int | None = None


@dataclass(frozen=True, slots=True)
class HandConflict:
    """一个手序号冲突（提交 / 审核时会变成 ``31005``）。"""

    code: str
    color_code: str
    size_code: str
    hands: int
    bundle_no: str
    existing_bundle_no: str
    message: str


@dataclass(frozen=True, slots=True)
class OrderSplitPreview:
    """单据级预演结果（``03 §5.5`` 的响应体雏形，T-BUND-007 才落 router）。"""

    lines: tuple[SizeLineSplit, ...]
    hands_total: int
    planned_qty: Decimal
    balance_qty: Decimal
    conflicts: tuple[HandConflict, ...]


# =================================================================== 断言


def assert_whole(bundle_qty: Decimal | int, *, label: str = "每手件数") -> Decimal:
    """整件口径断言（09 §4.2 / B18）：**必须是正整数**。

    与 ``bundles`` 的 DB CHECK ``ck_bundles_qty`` 同款 —— 库拦的是「已经写进去的」，
    这里拦的是「正要写进去的」，错误信息才能带尺码 / 行号定位。

    :param label: 报错文案里的字段定位，如 ``"尺码 XL 的每手件数"``。
    :returns: 归一化后的 ``Decimal``（调用方直接用它算 ``hands × bundle_qty``）。
    :raises BusinessError: ``<= 0`` → ``10001``；非整数 → ``31003``。
    """
    value = Decimal(bundle_qty)
    if value != value.to_integral_value():
        raise BusinessError(
            ErrorCode.BUNDLE_QTY_MUST_BE_INTEGER,
            f"{label}必须是整数（余料计入裁剪损耗，不生成尾数码），收到 {value}",
            details={"bundle_qty": str(value)},
        )
    if value <= ZERO:
        raise BusinessError(
            ErrorCode.PARAM_INVALID,
            f"{label}必须大于 0，收到 {value}",
            details={"bundle_qty": str(value)},
        )
    return value


def _norm(value: str, field: str) -> str:
    """尺码码 / 色码统一大写去空白（与 :func:`build_bundle_no` 同一口径）。"""
    code = value.strip().upper()
    if not code:
        raise BusinessError(ErrorCode.PARAM_INVALID, f"{field} 不能为空")
    return code


def _bundle_no(doc_no: str, size_code: str, hands_seq: int) -> str:
    """拼 ``bundle_no`` 并校验格式；非法一律 ``10001``（§9「尺码码非法」）。"""
    try:
        bundle_no = build_bundle_no(doc_no, size_code, hands_seq)
    except ValueError as exc:
        raise BusinessError(ErrorCode.PARAM_INVALID, f"生成打菲号失败：{exc}") from exc
    if hands_seq > MAX_HAND_SEQ:
        raise BusinessError(
            ErrorCode.PARAM_INVALID,
            f"尺码 {size_code} 第 {hands_seq} 手超出打菲号可表达范围（手序号固定 "
            f"{HANDS_SEQ_DIGITS} 位，上限 {MAX_HAND_SEQ}），请拆单重开",
            details={"size_code": size_code, "hands_seq": hands_seq, "max_hand_seq": MAX_HAND_SEQ},
        )
    if not is_valid_bundle_no(bundle_no):
        raise BusinessError(
            ErrorCode.PARAM_INVALID,
            f"打菲号 {bundle_no} 不符合格式：尺码码只允许 1~3 位大写字母或数字"
            f"（{size_code} 超长），请核对尺码档案",
            details={"bundle_no": bundle_no, "size_code": size_code},
        )
    return bundle_no


# =================================================================== 拆分


def split_size_line(
    *,
    doc_no: str,
    color_code: str,
    size_code: str,
    hands: int,
    qty_per_hand: Decimal | int,
    cutting_size_line_id: UUID,
    start_hand_seq: int = 1,
    output_qty: Decimal | int | None = None,
) -> SizeLineSplit:
    """一条裁剪尺码明细行 → 逐手的 ``bundle_no`` / ``hands`` / ``bundle_qty``。

    :param hands: 手数（整数）= **码数**。``<= 0`` → ``10001``，**不静默取整**。
    :param qty_per_hand: 每手件数，**直接取**裁剪尺码明细的该值（B20），不 floor。
    :param start_hand_seq: 本行第一手的序号（Q-B13：同尺码跨行连续编）。
    :param output_qty: 裁剪侧**人工指定出数**时的实际出数；``None`` → 按
        ``hands × qty_per_hand``。余数 ``output_qty - hands × qty_per_hand`` **不出码**。
    :raises BusinessError: ``10001``（零手 / 非正整数 / 尺码码非法 / 出数装不下）、
        ``31003``（每手件数非整数）。
    """
    if hands < 1:
        raise BusinessError(
            ErrorCode.PARAM_INVALID,
            f"手数必须是正整数（零手行对生产无意义），收到 {hands}；本系统不静默取整",
            details={"hands": hands, "cutting_size_line_id": str(cutting_size_line_id)},
        )
    if start_hand_seq < 1:
        raise BusinessError(ErrorCode.PARAM_INVALID, f"起始手序号必须 ≥ 1，收到 {start_hand_seq}")

    color = _norm(color_code, "color_code")
    size = _norm(size_code, "size_code")
    bundle_qty = assert_whole(qty_per_hand, label=f"尺码 {size} 的每手件数")
    planned_qty = bundle_qty * hands
    final_output = planned_qty if output_qty is None else Decimal(output_qty)
    remainder = final_output - planned_qty
    if remainder < ZERO:
        raise BusinessError(
            ErrorCode.PARAM_INVALID,
            f"尺码 {size}：裁剪出数 {final_output} 件装不下 {hands} 手 × 每手 {bundle_qty} 件"
            f" = {planned_qty} 件，请核对裁剪尺码明细",
            details={
                "hands": hands,
                "qty_per_hand": str(bundle_qty),
                "output_qty": str(final_output),
            },
        )

    bundles = tuple(
        SplitBundle(
            bundle_no=_bundle_no(doc_no, size, hands_seq),
            hands=hands_seq,
            bundle_qty=bundle_qty,
        )
        for hands_seq in range(start_hand_seq, start_hand_seq + hands)
    )
    return SizeLineSplit(
        color_code=color,
        size_code=size,
        cutting_size_line_id=cutting_size_line_id,
        hands=hands,
        qty_per_hand=bundle_qty,
        output_qty=final_output,
        bundle_qty=bundle_qty,
        remainder_qty=remainder,
        start_hand_seq=start_hand_seq,
        bundles=bundles,
    )


def preview_order(
    *,
    doc_no: str,
    lines: Sequence[SplitLineInput],
    existing_hands: Mapping[tuple[str, str, int], str] | None = None,
) -> OrderSplitPreview:
    """多行 → 单据级预演：``hands_total`` / ``planned_qty`` / ``balance_qty`` + ``conflicts[]``。

    :param existing_hands: 库内已有 ACTIVE 码，键为 ``(color_code, size_code, hands)``，
        值为已有 ``bundle_no``。命中即产出一条 :class:`HandConflict`（**不抛错**）。
    :raises BusinessError: 任一行非法即整单失败 —— 预演要的是「这份明细能不能过」，
        而不是「哪些行不行」。
    """
    cursors: dict[tuple[str, str], int] = {}
    previews: list[SizeLineSplit] = []
    for index, line in enumerate(lines, start=1):
        key = (line.color_code.strip().upper(), line.size_code.strip().upper())
        start = cursors.get(key, 1)
        try:
            split = split_size_line(
                doc_no=doc_no,
                color_code=line.color_code,
                size_code=line.size_code,
                hands=line.hands,
                qty_per_hand=line.qty_per_hand,
                cutting_size_line_id=line.cutting_size_line_id,
                start_hand_seq=start,
                output_qty=line.output_qty,
            )
        except BusinessError as exc:
            raise BusinessError(
                exc.code,
                f"第 {index} 行（{line.size_code}）：{exc.message}",
                details={**exc.details, "line_index": index},
            ) from exc
        cursors[key] = start + line.hands
        previews.append(split)

    return OrderSplitPreview(
        lines=tuple(previews),
        hands_total=sum(split.hands for split in previews),
        planned_qty=sum((split.bundle_qty * split.hands for split in previews), start=ZERO),
        balance_qty=sum((split.remainder_qty for split in previews), start=ZERO),
        conflicts=_detect_conflicts(previews, existing_hands or {}),
    )


def _detect_conflicts(
    previews: Sequence[SizeLineSplit], existing: Mapping[tuple[str, str, int], str]
) -> tuple[HandConflict, ...]:
    """比对预演手号与库内已有 ACTIVE 码（B22 → ``31005`` 的前置）。

    ⚠️ **返回而不抛错**：预演的用途是让主管核对，冲突要出现在清单里；
    真正拦截发生在 submit / approve（那里有唯一索引兜底）。
    """
    conflicts: list[HandConflict] = []
    for split in previews:
        for bundle in split.bundles:
            key = (split.color_code, split.size_code, bundle.hands)
            existing_no = existing.get(key)
            if existing_no is None:
                continue
            conflicts.append(
                HandConflict(
                    code=HAND_DUPLICATED_CODE,
                    color_code=split.color_code,
                    size_code=split.size_code,
                    hands=bundle.hands,
                    bundle_no=bundle.bundle_no,
                    existing_bundle_no=existing_no,
                    message=(
                        f"{split.color_code}/{split.size_code} 第 {bundle.hands} 手已存在打菲码"
                        f"{existing_no}，提交后会报 31005"
                    ),
                )
            )
    return tuple(conflicts)


__all__ = [
    "HAND_DUPLICATED_CODE",
    "MAX_HAND_SEQ",
    "HandConflict",
    "OrderSplitPreview",
    "SizeLineSplit",
    "SplitBundle",
    "SplitLineInput",
    "assert_whole",
    "preview_order",
    "split_size_line",
]
