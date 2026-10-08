"""打菲拆分的**只读**预演（T-BUND-004 / docs/modules/03-打菲.md §5.5）。

## 本文件的硬边界：不写库

``preview_split`` / ``available_outputs`` **只 SELECT**：不插码、不预占、不写
``document_logs``。理由不是「性能」，而是**语义**：预演的价值在于让主管在提交前核对，
一旦它改了数据，这个动作就变成了「改单」而不是「看单」（而且审核还要重算一遍，
两份口径迟早分叉）。所以它们**不开事务**（``unit_of_work``），只读路径上
由 session 的隐式事务兜底即可 —— 与本模块的 :meth:`~.bundling_order_service.
BundlingOrderService.get` / ``list_orders`` 同款。

真正的落库发生在审核（T-BUND-005b）：**重算一遍**并跑 §5.3 的 4 条断言，
**预演结果不作为审核依据**（TOCTOU，§5.5 明写）。

数据范围：两个方法都先 ``assert_in_scope(order, ctx)``（07 §3.2 铁律 2），
后续查询都以「已通过范围校验的单据」为根 —— 越权与不存在报**不同的码**。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessError, ErrorCode
from app.core.permissions import AuthContext
from app.core.scope import assert_in_scope
from app.modules.bundling.models import BundlingOrder
from app.modules.bundling.repository import (
    get_cutting_size_lines_by_cutting_order,
    get_cutting_size_lines_by_ids,
    get_order_with_lines,
    list_active_bundle_hands,
    list_available_outputs,
)
from app.modules.bundling.schemas import SplitLineIn
from app.modules.bundling.service.split import (
    OrderSplitPreview,
    SplitLineInput,
    preview_order,
)

ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class AvailableOutput:
    """一条**可打菲来源**明细（modules/03 §6 的 ``available-outputs`` 行）。

    ⚠️ 同时带「裁剪侧说了有多少手」与「现在还能打多少」：前者是手数的权威
    （``hands`` / ``qty_per_hand``），后者是余量的权威（``available_qty``，
    由 ``v_cutting_output_available`` 算）。两者不是一回事，合成一列就丢了一份口径。
    """

    cutting_size_line_id: UUID
    size_code: str
    hands: int
    qty_per_hand: Decimal
    output_qty: Decimal
    available_qty: Decimal


class PreviewMixin:
    """拆分预演与可打菲来源（由 :class:`BundlingOrderService` 组装）。"""

    session: AsyncSession

    async def _scoped_order(self, order_id: UUID, ctx: AuthContext) -> BundlingOrder:
        """取单据并校验数据范围（只读也要过范围，07 §3.2 铁律 2）。

        顺序刻意是「先查存在性再判范围」，与 :meth:`BundlingOrderService.get` 一致：
        越权（``12002``）与不存在（``30001``）要报**不同的码**，否则前端无法区分
        「你没权限」与「这单被删了」。
        """
        order = await get_order_with_lines(self.session, order_id)
        if order is None:
            raise BusinessError(ErrorCode.CUTTING_STATUS_NOT_ALLOWED, "打菲单不存在或已删除")
        assert_in_scope(order, ctx)
        return order

    async def preview_split(
        self,
        order_id: UUID,
        ctx: AuthContext,
        *,
        lines: Sequence[SplitLineIn] | None = None,
    ) -> OrderSplitPreview:
        """预演「这张单提交后会长出哪些码」（**只读**，不落库）。

        算法全部委托 :func:`~app.modules.bundling.service.split.preview_order`
        —— 审核复用同一份，本方法只负责把库里的数据喂进去。

        :param ctx: 数据范围按单据的 ``workshop_id`` 判定（07 §3.2）。**传了 ``lines``
            也一样**：请求体只影响「算哪几行」，不影响「能看见哪张单」。
        :param lines: 不传 = 用当前单据的行（modules/03 §5.5）；传了就按这几行试算
            （「我改了手数，先看看会变成什么样」）。
        :returns: 逐手 ``bundle_no`` / ``hands`` / ``bundle_qty`` + 单据级汇总 +
            ``conflicts[]``（手序号与库内已有 ACTIVE 码冲突，提交后会报 ``31005``）。
        :raises BusinessError: ``30001`` 单不存在 / ``12002`` 越权；``20001`` 请求体
            里的 ``cutting_size_line_id`` 查无此行；明细本身的非法由 :func:`split_size_line`
            报（``10001`` / ``31003``）。
        """
        order = await self._scoped_order(order_id, ctx)
        inputs = (
            await self._inputs_from_order_lines(order)
            if lines is None
            else await self._inputs_from_body(order, lines)
        )
        existing = await list_active_bundle_hands(self.session, order.id)
        return preview_order(
            doc_no=order.doc_no,
            lines=inputs,
            existing_hands={(h.color_code, h.size_code, h.hands): h.bundle_no for h in existing},
        )

    async def _inputs_from_order_lines(self, order: BundlingOrder) -> list[SplitLineInput]:
        """用**本单当前明细**算（不带 body 的那条路径）。"""
        size_lines = await get_cutting_size_lines_by_ids(
            self.session, [line.cutting_size_line_id for line in order.lines]
        )
        inputs: list[SplitLineInput] = []
        for line in order.lines:
            source = size_lines.get(line.cutting_size_line_id)
            if source is None:
                raise BusinessError(
                    ErrorCode.BASE_DATA_NOT_FOUND,
                    f"第 {line.line_no} 行引用的裁剪尺码明细行不存在或已删除",
                    details={
                        "line_no": line.line_no,
                        "cutting_size_line_id": str(line.cutting_size_line_id),
                    },
                )
            inputs.append(
                SplitLineInput(
                    color_code=line.color_code,
                    size_code=line.size_code,
                    hands=line.hands,
                    qty_per_hand=source.qty_per_hand,
                    cutting_size_line_id=line.cutting_size_line_id,
                )
            )
        return inputs

    async def _inputs_from_body(
        self, order: BundlingOrder, lines: Sequence[SplitLineIn]
    ) -> list[SplitLineInput]:
        """用**请求体**的几行算（试算路径）。

        ⚠️ **色码一律取本单**：ADR-0016 一码一色，让前端指定色码等于让它挑一个服务端
        不认的口径，而预演出来的 ``bundle_no`` 会带着那个假色码。
        """
        size_lines = await get_cutting_size_lines_by_ids(
            self.session, [line.cutting_size_line_id for line in lines]
        )
        inputs: list[SplitLineInput] = []
        for index, line in enumerate(lines, start=1):
            source = size_lines.get(line.cutting_size_line_id)
            if source is None:
                raise BusinessError(
                    ErrorCode.BASE_DATA_NOT_FOUND,
                    f"预演第 {index} 行的裁剪尺码明细行不存在或已删除",
                    details={
                        "line_index": index,
                        "cutting_size_line_id": str(line.cutting_size_line_id),
                    },
                )
            inputs.append(
                SplitLineInput(
                    color_code=order.color_code,
                    size_code=line.size_code,
                    hands=line.hands,
                    qty_per_hand=source.qty_per_hand,
                    cutting_size_line_id=line.cutting_size_line_id,
                )
            )
        return inputs

    async def available_outputs(
        self, order_id: UUID, ctx: AuthContext, *, color_code: str | None = None
    ) -> list[AvailableOutput]:
        """可打菲来源明细（**只读**）：来源裁剪单该色下每个尺码的手数与可用量。

        :param color_code: 不传则用本单的 ``color_code``。ADR-0016 一码一色，
            所以按色过滤是硬约束而不是可选筛选。
        :returns: 按尺码明细行序返回；**没有结转行的尺码 ``available_qty = 0``**
            （不是不返回）—— 主管要看见「这个尺码一件都打不了」这件事。
        """
        order = await self._scoped_order(order_id, ctx)
        target_color = color_code or order.color_code
        availability = {
            row.size_code: row.available_qty
            for row in await list_available_outputs(
                self.session, ctx, style_no=order.style_no, color_code=target_color
            )
        }
        size_lines = await get_cutting_size_lines_by_cutting_order(
            self.session, order.source_cutting_order_id, color_code=target_color
        )
        return [
            AvailableOutput(
                cutting_size_line_id=line.id,
                size_code=line.size_code,
                hands=line.hands,
                qty_per_hand=Decimal(line.qty_per_hand),
                output_qty=Decimal(line.output_qty),
                available_qty=availability.get(line.size_code, ZERO),
            )
            for line in size_lines
        ]


__all__ = ["AvailableOutput", "PreviewMixin"]
