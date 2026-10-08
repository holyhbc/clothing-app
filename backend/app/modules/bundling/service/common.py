"""打菲单 service 的公共助手（ADR-0031：从 `bundling_order_service.py` 拆出）。

只放**私有助手**：校验、取单、乐观锁、明细落库、汇总重算、写日志。公开方法
（`create` / `patch` / `put_lines` / `get` / `list_orders`）留在
:mod:`app.modules.bundling.service.bundling_order_service`，由组合类组装。

分层：`common` 不依赖任何 mixin，也不反向 import 组合类。
"""

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import DocumentStatus
from app.core.errors import BusinessError, ErrorCode
from app.core.permissions import AuthContext
from app.core.scope import assert_in_scope
from app.modules.base.models import Operation, Style
from app.modules.bundling.models import BundlingOrder, BundlingOrderLine
from app.modules.bundling.repository import get_order_for_update, get_order_with_lines
from app.modules.bundling.schemas import LineIn
from app.modules.cutting.models import CuttingOrder, CuttingOrderSizeLine

#: ``document_logs.doc_type``（docs/08 §1.1 的单据类型名）。**只有这里写这个字面量** ——
#: 写日志的（:meth:`CommonMixin._write_log`）与读日志的（``list_logs`` 查历史）必须同值，
#: 两处各写一份的话，改了一处就会得到「日志写得进、查不出来」，而那个现象在界面上是
#: 变更历史永远空白。
DOC_TYPE_BUNDLING_ORDER = "BundlingOrder"


class CommonMixin:
    """打菲单 service 的私有助手集合（由 :class:`BundlingOrderService` 组装）。"""

    session: AsyncSession

    async def _reloaded(self, order_id: UUID) -> BundlingOrder:
        """写后重读单据（表头 + 明细）。

        ⚠️ **每一次写之后都要走这里**：Core ``UPDATE`` 会 ``expire`` identity map
        里的行，而响应序列化在同步上下文里，访问被 expire 的列就是
        ``MissingGreenlet`` 500（与 ``cutting`` 同款，见其 ``_reloaded``）。
        """
        order = await get_order_with_lines(self.session, order_id)
        if order is None:  # pragma: no cover —— 刚写过，行必然还在
            raise BusinessError(ErrorCode.CUTTING_STATUS_NOT_ALLOWED, "打菲单不存在或已删除")
        return order

    async def _assert_style_usable(self, style_no: str) -> Style:
        """款号必须存在且启用（草稿态就拦，不留到 submit）。"""
        style = (
            await self.session.execute(select(Style).where(Style.style_no == style_no))
        ).scalar_one_or_none()
        if style is None:
            raise BusinessError(ErrorCode.BASE_DATA_NOT_FOUND, "款号不存在，请先建款号档案")
        if not style.is_active:
            raise BusinessError(
                ErrorCode.BASE_DATA_REFERENCED, f"款号 {style.style_no} 已停用，不能开打菲单"
            )
        return style

    async def _assert_operation_usable(self, operation_no: str) -> Operation:
        """工序必须存在且启用。"""
        op = (
            await self.session.execute(
                select(Operation).where(Operation.operation_no == operation_no)
            )
        ).scalar_one_or_none()
        if op is None:
            raise BusinessError(ErrorCode.BASE_DATA_NOT_FOUND, f"工序 {operation_no} 不存在")
        if not op.is_active:
            raise BusinessError(
                ErrorCode.BASE_DATA_REFERENCED, f"工序 {operation_no} 已停用，不能开打菲单"
            )
        return op

    async def _assert_cutting_approved(self, cutting_order_id: UUID) -> CuttingOrder:
        """来源裁剪单必须 APPROVED（B8）。"""
        cutting = await self.session.get(CuttingOrder, cutting_order_id)
        if cutting is None:
            raise BusinessError(ErrorCode.BASE_DATA_NOT_FOUND, "来源裁剪单不存在")
        if cutting.status != DocumentStatus.APPROVED:
            raise BusinessError(
                ErrorCode.BASE_DATA_REFERENCED,
                f"来源裁剪单 {cutting.doc_no} 状态为 {cutting.status.value}，必须是 APPROVED",
            )
        return cutting

    async def _insert_lines(
        self, order: BundlingOrder, lines_in: Sequence[LineIn], operator_id: UUID
    ) -> list[BundlingOrderLine]:
        """落库明细行，逐行校验 cutting_size_line_id 与尺码匹配、hands > 0。"""
        built: list[BundlingOrderLine] = []
        for idx, line_in in enumerate(lines_in, start=1):
            cutting_size_line = await self.session.get(
                CuttingOrderSizeLine, line_in.cutting_size_line_id
            )
            if cutting_size_line is None:
                raise BusinessError(
                    ErrorCode.BASE_DATA_NOT_FOUND,
                    f"第 {idx} 行：裁剪尺码明细行 {line_in.cutting_size_line_id} 不存在",
                )
            if cutting_size_line.size_code != line_in.size_code:
                raise BusinessError(
                    ErrorCode.PARAM_INVALID,
                    f"第 {idx} 行：尺码 {line_in.size_code} 与裁剪明细行的 {cutting_size_line.size_code} 不匹配",
                )
            if line_in.hands <= 0:
                raise BusinessError(
                    ErrorCode.PARAM_INVALID, f"第 {idx} 行：手数必须 > 0，收到 {line_in.hands}"
                )

            qty_per_hand = cutting_size_line.qty_per_hand
            if qty_per_hand <= 0:
                raise BusinessError(
                    ErrorCode.PARAM_INVALID,
                    f"第 {idx} 行：裁剪尺码明细的 qty_per_hand 必须 > 0，当前 {qty_per_hand}",
                )

            planned_qty = line_in.hands * qty_per_hand
            available_qty_before = Decimal("0")

            line = BundlingOrderLine(
                doc_id=order.id,
                line_no=line_in.line_no,
                color_code=line_in.color_code,
                size_code=line_in.size_code,
                operation_no=line_in.operation_no,
                cutting_size_line_id=line_in.cutting_size_line_id,
                hands=line_in.hands,
                planned_qty=planned_qty,
                available_qty_before=available_qty_before,
                group_no=line_in.group_no,
                workstation_no=line_in.workstation_no,
                remark=line_in.remark,
                created_by=operator_id,
                updated_by=operator_id,
            )
            self.session.add(line)
            built.append(line)
        return built

    def _recalc_header(self, order: BundlingOrder, lines: Sequence[BundlingOrderLine]) -> None:
        """重算表头汇总字段（纯内存，不碰 DB）。

        ⚠️ 汇总全在 service 重算，**不信任前端**。
        """
        zero = Decimal("0")
        order.hands_total = sum(line.hands for line in lines)
        order.output_qty = sum((line.planned_qty for line in lines), start=zero)
        order.balance_qty = zero  # 草稿态余数为 0，余数仅裁剪侧人工指定出数时产生

    async def _bump_header(
        self,
        order_id: UUID,
        expected_version: int,
        operator_id: UUID,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """条件 UPDATE 式乐观锁：WHERE version = :expected + rowcount == 0 判冲突。"""
        values: dict[str, Any] = {
            "version": BundlingOrder.version + 1,
            "updated_by": operator_id,
            **(extra or {}),
        }
        result = await self.session.execute(
            update(BundlingOrder)
            .where(BundlingOrder.id == order_id, BundlingOrder.version == expected_version)
            .values(**values)
            .returning(BundlingOrder.id)
        )
        if len(result.all()) == 0:
            current = await self.session.get(BundlingOrder, order_id)
            raise BusinessError(
                ErrorCode.OPTIMISTIC_LOCK_CONFLICT,
                f"打菲单已被他人修改（当前版本 {current.version if current else '已删除'}），请刷新后重试",
                details={
                    "expected": expected_version,
                    "current": current.version if current else None,
                },
            )

    async def _editable_order(
        self, order_id: UUID, version: int, ctx: AuthContext
    ) -> BundlingOrder:
        """取可编辑的单据：存在 + 在数据范围内 + 版本对 + 状态可改。"""
        order = await get_order_for_update(self.session, order_id)
        if order is None:
            raise BusinessError(ErrorCode.CUTTING_STATUS_NOT_ALLOWED, "打菲单不存在或已删除")
        assert_in_scope(order, ctx)
        if order.version != version:
            raise BusinessError(
                ErrorCode.OPTIMISTIC_LOCK_CONFLICT,
                f"打菲单已被他人修改（当前版本 {order.version}），请刷新后重试",
                details={"expected": version, "current": order.version},
            )
        if order.status not in (DocumentStatus.DRAFT, DocumentStatus.REJECTED):
            raise BusinessError(
                ErrorCode.CUTTING_STATUS_NOT_ALLOWED,
                f"当前状态 {order.status.value} 不允许修改（只有草稿与已驳回可改）",
                details={"status": order.status.value},
            )
        return order

    async def _write_log(
        self,
        order: BundlingOrder,
        operator_id: UUID,
        action: str,
        from_status: str | None,
        to_status: str,
        reason: str | None = None,
        changed_fields: Mapping[str, Any] | None = None,
    ) -> None:
        """写 document_logs（谁、何时、从什么状态到什么状态、为什么、改了什么）。

        ⚠️ ``changed_fields`` 记的是**重算前后的值**（03 §5.3「取整前后值」）：审核会重算
        ``hands_total`` / ``output_qty`` / ``balance_qty``，而这三个数在单据上只有一个
        「当前值」—— 不留前后值，「申请 2000 手、实得 1998 个码」就无人知晓（§5.3 原文
        明确要求写日志）。留痕表只追加不修改，所以这是**唯一**能追溯那组数的地方。
        """
        from app.common.models import DocumentLog

        log = DocumentLog(
            doc_type=DOC_TYPE_BUNDLING_ORDER,
            doc_id=order.id,
            doc_no=order.doc_no,
            action=action,
            from_status=from_status,
            to_status=to_status,
            operator_id=operator_id,
            operator_name="系统",  # TODO: 获取真实操作人姓名
            reason=reason,
            changed_fields=changed_fields,
        )
        self.session.add(log)
