"""打菲标签**数据导出**与**打印痕迹登记**（T-BUND-006 / modules/03 §5.4、§3.4、B14/B15/B25）。

## 两个能力，**不合并**（03 §6）

| 方法 | 边界 | 事务 |
| --- | --- | --- |
| :meth:`LabelMixin.export_labels` | **只读**：出标签数据（数据数组 / CSV） | 不开（只读路径） |
| :meth:`LabelMixin.register_print` | **只写痕迹**：追加 ``bundle_label_prints`` + 重算 ``label_print_qty`` + 写日志 | ``unit_of_work`` |

⚠️ 为什么不能合成「导出即登记」：实物标签是**先印后核**的 —— 导出 5 手可能只印了 3 手
（卡纸 / 只补两手）。合并之后「导出了 5 手」会被记成「打印了 5 手」，而车间实物只有 3 张：
留痕一旦与实物不符，它就不再是对账证据。

## 三条口径（下面各处注释都指向这里）

1. **「共 M 手」= 该尺码的总手数**，不是全单总手数（Q-B13：同尺码手号连续编到 N）。
   跨尺码取全单总数的话，XL 的标签会印成「第 1 手 / 共 6 手」，而那 6 手里有 5 手是别的
   尺码 —— 员工按标签认领时会数错。
2. **码只有一个来源**：``qr_content`` / ``barcode_content`` 都取库里的
   ``bundles.qr_content``（ADR-0004 + B5/B6），**不在标签里另算一套码**。
3. **幂等**：``04 §7.16`` 的 ``print_seq`` 没有唯一索引，所以靠 ① 登记前 ``FOR UPDATE``
   锁表头（同单并发被串行化）② 同事务内查 ``print_seq`` 用过没有 ③ Router 层
   ``Idempotency-Key`` 短路（05 §5，T-BUND-007 接线）。

校验在 :mod:`.label_guard`、CSV 渲染在 :mod:`.label_csv`（都是纯函数）。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import DocumentAction, LabelExportFormat
from app.core.db import unit_of_work
from app.core.errors import BusinessError, ErrorCode
from app.core.numbering import BUSINESS_TZ
from app.core.permissions import AuthContext
from app.core.scope import assert_in_scope
from app.modules.bundling.label_repository import (
    LabelBundleRow,
    append_label_prints,
    existing_print_seq_hands,
    hands_total_by_size,
    list_label_bundles,
    sum_printed_qty,
)
from app.modules.bundling.models import BundlingOrder
from app.modules.bundling.repository import get_order_for_update
from app.modules.bundling.service.label_csv import render_labels_csv
from app.modules.bundling.service.label_guard import (
    assert_hand_range,
    assert_hands_total_snapshot,
    assert_printable,
    assert_printable_order,
    assert_within_max_hands,
    resolve_hand_span,
)

logger = logging.getLogger("app.bundling")

ZERO: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class LabelItem:
    """**一手**的标签数据（03 §5.4 标签内容清单）。"""

    bundle_no: str
    style_no: str
    color_code: str
    size_code: str
    operation_no: str
    hands_seq: int
    hands_total_of_size: int
    bundle_qty: Decimal
    hands_text: str
    qr_content: str
    barcode_content: str

    def csv_row(self) -> tuple[str, ...]:
        """CSV 数据行（列序照 :data:`~.label_csv.LABEL_CSV_HEADER`）。"""
        return (
            self.style_no,
            self.color_code,
            self.size_code,
            str(self.hands_seq),
            str(self.hands_total_of_size),
            str(self.bundle_qty),
            self.bundle_no,
            self.operation_no,
            self.qr_content,
            self.barcode_content,
        )


@dataclass(frozen=True, slots=True)
class LabelExport:
    """一次导出的结果。

    ⚠️ ``csv_text`` **只在 ``export_format=CSV`` 时有值**：2000 手的大单渲染一遍
    CSV 是白干活，而默认路径根本不请求它。
    """

    doc_no: str
    size_code: str | None
    items: tuple[LabelItem, ...]
    hands_count: int
    total_qty: Decimal
    csv_text: str | None = None


@dataclass(frozen=True, slots=True)
class LabelPrintResult:
    """一次打印登记的结果（03 §6 的响应要点）。

    ⚠️ ``print_ids`` 是**列表**：一次登记按手逐行留痕（哪一手印过要能逐行追溯），
    多手时不存在单一的 ``print_id``。
    """

    doc_no: str
    print_ids: tuple[UUID, ...]
    printed_count: int
    hands_total: int
    label_print_qty: int
    is_reprint: bool
    print_seq: int | None


class LabelMixin:
    """标签导出与打印留痕（由 :class:`~.bundling_order_service.BundlingOrderService` 组装）。"""

    session: AsyncSession

    if TYPE_CHECKING:
        # 照抄 StateMixin 的写法：运行期不 import 兄弟 Mixin，只在类型检查时声明形状。
        # ``_scoped_order`` 在 PreviewMixin（只读路径）、``_bump_header`` / ``_write_log``
        # 在 CommonMixin —— 三者都不 import 本 Mixin，所以不成环。
        async def _scoped_order(self, order_id: UUID, ctx: AuthContext) -> BundlingOrder: ...

        async def _bump_header(
            self,
            order_id: UUID,
            expected_version: int,
            operator_id: UUID,
            extra: dict[str, object] | None = None,
        ) -> None: ...

        async def _write_log(
            self,
            order: BundlingOrder,
            operator_id: UUID,
            action: str,
            from_status: str | None,
            to_status: str,
            reason: str | None = None,
            changed_fields: Mapping[str, object] | None = None,
        ) -> None: ...

    # ================================================================== 导出（只读）

    async def export_labels(
        self,
        order_id: UUID,
        ctx: AuthContext,
        *,
        from_hands: int | None = None,
        to_hands: int | None = None,
        size_code: str | None = None,
        export_format: LabelExportFormat = LabelExportFormat.DATA,
    ) -> LabelExport:
        """取标签数据（``GET /bundling-orders/{id}/labels``，03 §6）。

        ⚠️ **只读**：不开事务、不写库、不写日志（与 :meth:`PreviewMixin.preview_split` 同款）。
        导出与「按手列表」共用同一批查询，不另开一条绕过数据范围的路径（07 §3.2 铁律 3）。

        :param from_hands / to_hands: 手号区间（**每个尺码各自**编号，见模块 docstring）；
            都不传 = 全部手。
        :param size_code: 只导某个尺码；不传 = 本单全部尺码。
        :param export_format: ``DATA`` 出数据数组；``CSV`` 额外出 ``csv_text``。
        :raises BusinessError: ``30001`` 单不存在 / 非 ``APPROVED``；``12002`` 越权；
            ``10001`` 手号区间颠倒或手数超上限。
        """
        assert_hand_range(from_hands, to_hands)
        order = await self._scoped_order(order_id, ctx)
        assert_printable_order(order)
        rows = await list_label_bundles(
            self.session,
            doc_id=order.id,
            size_code=size_code,
            hands_from=from_hands,
            hands_to=to_hands,
        )
        assert_within_max_hands(len(rows))
        totals = await hands_total_by_size(self.session, order.id)
        items = tuple(_to_item(row, totals) for row in rows)
        return LabelExport(
            doc_no=order.doc_no,
            size_code=size_code,
            items=items,
            hands_count=len(items),
            total_qty=sum((item.bundle_qty for item in items), start=ZERO),
            csv_text=render_labels_csv(items) if export_format is LabelExportFormat.CSV else None,
        )

    # ================================================================== 登记（写痕迹）

    async def register_print(
        self,
        order_id: UUID,
        operator_id: UUID,
        ctx: AuthContext,
        *,
        hands_seq: int | None = None,
        size_code: str | None = None,
        hands_total_of_size: int | None = None,
        from_hands: int | None = None,
        to_hands: int | None = None,
        printed_qty: int = 1,
        is_reprint: bool = False,
        print_seq: int | None = None,
        idempotency_key: str | None = None,
    ) -> LabelPrintResult:
        """登记打印痕迹（``POST /bundling-orders/{id}/label-prints``，03 §6 / B15）。

        :param hands_seq: **本次打印的第几手**（03 §6 必传 → 缺失报 ``10002``）。
            与 ``from_hands`` 同时传时必须相等 —— 它就是区间起点，矛盾入参报 ``10001``。
        :param size_code: 只登记某个尺码；不传 = 本单全部尺码（手号区间在每个尺码内各自成立）。
        :param hands_total_of_size: 「共 M 手」的前端快照；**落库取库内权威值**，对不上报
            ``10001``（照抄前端会把错的「共 M 手」永久留在留痕里）。
        :param printed_qty: **每手**本次打印张数（默认 1；一次多打几张备用时 > 1）。
        :param print_seq: 重打批次序号。重打必传（否则重复请求与首次打印无从区分）→ ``10002``。
        :raises BusinessError: ``10002`` 缺 ``hands_seq`` / 重打缺 ``print_seq``；
            ``10001`` 区间矛盾、张数非正、「共 M 手」对不上；``10008`` ``print_seq`` 已用过；
            ``30001`` 单不存在 / 非 ``APPROVED``；``31001`` 区间内无码；``31002`` 码已作废；
            ``12002`` 越权。
        """
        start, end = resolve_hand_span(hands_seq, from_hands, to_hands)
        if printed_qty < 1:
            raise BusinessError(ErrorCode.PARAM_INVALID, f"打印张数必须 > 0，收到 {printed_qty}")
        if is_reprint and print_seq is None:
            raise BusinessError(
                ErrorCode.MISSING_BUSINESS_PARAM,
                "重打必须带 print_seq（重打批次序号），否则重复请求无法与首次打印区分",
            )
        async with unit_of_work(self.session):
            # ⚠️ **先锁表头**：① 挡住同单并发的两次登记（print_seq 去重）；
            # ② 让 label_print_qty 的「重算」落在同一临界区内。⚠️ **不碰
            # cutting_outputs**：打印没有库存副作用，去占别人的行锁只会堵住并发提交者。
            order = await get_order_for_update(self.session, order_id)
            if order is None:
                raise BusinessError(ErrorCode.CUTTING_STATUS_NOT_ALLOWED, "打菲单不存在或已删除")
            assert_in_scope(order, ctx)
            assert_printable_order(order)

            rows = await list_label_bundles(
                self.session,
                doc_id=order.id,
                size_code=size_code,
                hands_from=start,
                hands_to=end,
                active_only=False,
            )
            assert_within_max_hands(len(rows))
            hands = assert_printable(rows, start, end)
            totals = await hands_total_by_size(self.session, order.id)
            for row in hands:
                assert_hands_total_snapshot(row, totals, hands_total_of_size)
            if print_seq is not None:
                await self._assert_print_seq_unused(order.id, hands, print_seq)

            print_ids = await append_label_prints(
                self.session,
                doc_id=order.id,
                rows=[
                    {
                        "bundle_no": row.bundle_no,
                        "hands_seq": row.hands_seq,
                        "hands_total_of_size": totals[(row.color_code, row.size_code)],
                        "printed_qty": printed_qty,
                        "is_reprint": is_reprint,
                        "print_seq": print_seq,
                        "printed_by": operator_id,
                        "printed_at": datetime.now(tz=BUSINESS_TZ),
                    }
                    for row in hands
                ],
                operator_id=operator_id,
            )
            label_print_qty = await sum_printed_qty(self.session, order.id)
            # ⚠️ 日志**先写、表头后 bump**：Core UPDATE 会 expire 表头那一行，之后再读
            # order.doc_no 就是同步 IO（async 下 MissingGreenlet）。两者同事务。
            await self._write_log(
                order,
                operator_id,
                DocumentAction.REPRINT.value if is_reprint else DocumentAction.PRINT.value,
                order.status.value,
                order.status.value,
                changed_fields={
                    "hands": [row.hands_seq for row in hands],
                    "bundle_nos": [row.bundle_no for row in hands],
                    "printed_qty_per_hand": printed_qty,
                    "is_reprint": is_reprint,
                    "print_seq": print_seq,
                    "hands_total_of_size": sorted(
                        {totals[(row.color_code, row.size_code)] for row in hands}
                    ),
                    "label_print_qty": label_print_qty,
                    "idempotency_key": idempotency_key,
                },
            )
            await self._bump_header(
                order_id, order.version, operator_id, {"label_print_qty": label_print_qty}
            )
        logger.info(
            "打菲标签已打印",
            extra={
                "doc_no": order.doc_no,
                "is_reprint": is_reprint,
                "hands": [row.hands_seq for row in hands],
                "printed_qty_per_hand": printed_qty,
                "print_seq": print_seq,
                "label_print_qty": label_print_qty,
            },
        )
        return LabelPrintResult(
            doc_no=order.doc_no,
            print_ids=tuple(print_ids),
            printed_count=printed_qty * len(hands),
            hands_total=len(hands),
            label_print_qty=label_print_qty,
            is_reprint=is_reprint,
            print_seq=print_seq,
        )

    # ================================================================== 私有断言

    async def _assert_print_seq_unused(
        self, doc_id: UUID, hands: list[LabelBundleRow], print_seq: int
    ) -> None:
        """``print_seq`` 批次序号**不得重复使用** → ``10008``（service 侧幂等兜底）。

        ⚠️ 报 ``10008``（非法操作）而不是 ``10003``（乐观锁冲突）：这不是并发覆盖，
        而是「这个批次号已经用过了」—— 重试的正确做法是换一个 ``print_seq`` 或带上同一个
        ``Idempotency-Key``，而 10003 的文案会让用户去刷新单据。
        """
        used = await existing_print_seq_hands(
            self.session,
            doc_id=doc_id,
            bundle_nos=[row.bundle_no for row in hands],
            print_seq=print_seq,
        )
        if used:
            raise BusinessError(
                ErrorCode.ILLEGAL_OPERATION,
                f"打印批次序号 print_seq={print_seq} 已登记过（{used[0]} 等 {len(used)} 手），"
                "请换一个批次序号；若这是同一次请求的重试，请带相同的 Idempotency-Key",
                details={"print_seq": print_seq, "bundle_nos": used},
            )


def _to_item(row: LabelBundleRow, totals: Mapping[tuple[str, str], int]) -> LabelItem:
    """一行码 → 一张标签的数据（**共 M 手**取库内权威值，见 ``hands_total_by_size``）。"""
    total = totals.get((row.color_code, row.size_code))
    if total is None:  # pragma: no cover —— 与 list_label_bundles 同一组过滤条件
        raise BusinessError(ErrorCode.INTERNAL, f"{row.size_code} 尺码总手数缺失，请刷新后重试")
    return LabelItem(
        bundle_no=row.bundle_no,
        style_no=row.style_no,
        color_code=row.color_code,
        size_code=row.size_code,
        operation_no=row.operation_no,
        hands_seq=row.hands_seq,
        hands_total_of_size=total,
        bundle_qty=row.bundle_qty,
        hands_text=f"第 {row.hands_seq} 手 / 共 {total} 手",
        # ⚠️ 二维码与条码内容**都取库里的 qr_content**（ADR-0004：纯 bundle_no），
        #    不在这里重拼码 —— 纸上的码与库里那行必须是同一个字符串。
        qr_content=row.qr_content,
        barcode_content=row.qr_content,
    )


__all__ = [
    "LabelExport",
    "LabelItem",
    "LabelMixin",
    "LabelPrintResult",
]
