"""工资单**轻状态迁移**编排（T-PAY-006：update/submit/withdraw/reject/cancel）。

迁移表见 :mod:`~app.modules.payroll.service.sheet_state`；本文件只编排 + 校验 + 日志。
08 §1.2 铁律：R1 唯一入口 :meth:`_assert_transition`；R2 每次迁移写 ``document_logs``；
R3 ``WHERE id=? AND version=? AND status=?``，``rowcount==0 → 10003``；R7 状态只由
:meth:`_apply_status` 写。工资单 ``submit`` 只校验「明细非空」（``05 §4.2``），
周期关闭只拦 ``create``（在 ``sheet.py``）。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import DocumentAction, DocumentStatus
from app.core.db import unit_of_work
from app.core.errors import BusinessError, ErrorCode
from app.core.permissions import AuthContext
from app.modules.payroll.models import (
    PayrollDetail,
    PayrollPeriod,
    PayrollPeriodStatus,
    PayrollSheet,
)
from app.modules.payroll.service.common import require_reason
from app.modules.payroll.service.sheet_state import (
    EDITABLE_STATUSES,
    PERMISSION_BY_ACTION,
    TRANSITIONS,
)
from app.modules.payroll.service.sheet_state_support import SheetStoreMixin


class SheetStateMixin(SheetStoreMixin):
    """工资单轻状态动作（由 :class:`~.sheet.PayrollSheetService` 组装）。"""

    session: AsyncSession

    async def update(
        self,
        sheet_id: UUID,
        *,
        expected_version: int,
        operator_id: UUID,
        ctx: AuthContext,
        doc_date: date | None = None,
        remark: str | None = None,
    ) -> PayrollSheet:
        """改单（``DRAFT``/``REJECTED``，状态不变）。白名单 ``doc_date``/``remark``；
        传 ``None`` = 不改。按明细重算表头金额并写字段级 diff（``05 §4.2``）。
        """
        self._assert_can("update", ctx)
        async with unit_of_work(self.session):
            sheet = await self._get_sheet(sheet_id)
            self._assert_updatable(sheet.status)
            changed: dict[str, Any] = {}
            extra: dict[str, Any] = {}
            if doc_date is not None and doc_date != sheet.doc_date:
                changed["doc_date"] = {
                    "from": sheet.doc_date.isoformat(),
                    "to": doc_date.isoformat(),
                }
                extra["doc_date"] = doc_date
            if remark is not None and remark != (sheet.remark or ""):
                changed["remark"] = {"from": sheet.remark, "to": remark}
                extra["remark"] = remark
            changed.update(await self._recalc_header_amounts(sheet, extra))
            if not changed:
                return await self._reloaded(sheet_id)
            await self._write_log(
                sheet,
                operator_id,
                DocumentAction.UPDATE.value,
                sheet.status.value,
                sheet.status.value,
                changed_fields=changed,
            )
            await self._apply_status(
                sheet,
                to_status=sheet.status,
                expected_version=expected_version,
                operator_id=operator_id,
                extra=extra,
            )
            return await self._reloaded(sheet_id)

    async def submit(
        self, sheet_id: UUID, *, expected_version: int, operator_id: UUID, ctx: AuthContext
    ) -> PayrollSheet:
        """提交（``DRAFT``/``REJECTED`` → ``SUBMITTED``）；明细非空（``10008``）。"""
        self._assert_can("submit", ctx)
        async with unit_of_work(self.session):
            sheet = await self._get_sheet(sheet_id)
            self._assert_transition("submit", sheet.status, DocumentStatus.SUBMITTED)
            await self._assert_has_details(sheet)
            await self._write_log(
                sheet,
                operator_id,
                DocumentAction.SUBMIT.value,
                sheet.status.value,
                DocumentStatus.SUBMITTED.value,
            )
            await self._apply_status(
                sheet,
                to_status=DocumentStatus.SUBMITTED,
                expected_version=expected_version,
                operator_id=operator_id,
            )
            return await self._reloaded(sheet_id)

    async def withdraw(
        self, sheet_id: UUID, *, expected_version: int, operator_id: UUID, ctx: AuthContext
    ) -> PayrollSheet:
        """撤回（``SUBMITTED`` → ``DRAFT``）。**撤回人 = 制单人**（08 §1.1）。"""
        self._assert_can("withdraw", ctx)
        async with unit_of_work(self.session):
            sheet = await self._get_sheet(sheet_id)
            self._assert_transition("withdraw", sheet.status, DocumentStatus.DRAFT)
            self._assert_creator(sheet, ctx)
            await self._write_log(
                sheet,
                operator_id,
                DocumentAction.WITHDRAW.value,
                sheet.status.value,
                DocumentStatus.DRAFT.value,
            )
            await self._apply_status(
                sheet,
                to_status=DocumentStatus.DRAFT,
                expected_version=expected_version,
                operator_id=operator_id,
            )
            return await self._reloaded(sheet_id)

    async def reject(
        self,
        sheet_id: UUID,
        *,
        reason: str | None,
        expected_version: int,
        operator_id: UUID,
        ctx: AuthContext,
    ) -> PayrollSheet:
        """驳回（``SUBMITTED`` → ``REJECTED``）。**必填原因**（``10006``）。"""
        self._assert_can("reject", ctx)
        reason_text = require_reason(reason, action="驳回工资单")
        async with unit_of_work(self.session):
            sheet = await self._get_sheet(sheet_id)
            self._assert_transition("reject", sheet.status, DocumentStatus.REJECTED)
            await self._write_log(
                sheet,
                operator_id,
                DocumentAction.REJECT.value,
                sheet.status.value,
                DocumentStatus.REJECTED.value,
                reason_text,
            )
            await self._apply_status(
                sheet,
                to_status=DocumentStatus.REJECTED,
                expected_version=expected_version,
                operator_id=operator_id,
                extra={"rejected_reason": reason_text},
            )
            return await self._reloaded(sheet_id)

    async def cancel(
        self,
        sheet_id: UUID,
        *,
        reason: str | None,
        expected_version: int,
        operator_id: UUID,
        ctx: AuthContext,
    ) -> PayrollSheet:
        """作废（``DRAFT``/``REJECTED`` → ``CANCELLED``）。**必填原因**，终态（08 R6）。"""
        self._assert_can("cancel", ctx)
        reason_text = require_reason(reason, action="作废工资单")
        async with unit_of_work(self.session):
            sheet = await self._get_sheet(sheet_id)
            self._assert_transition("cancel", sheet.status, DocumentStatus.CANCELLED)
            await self._write_log(
                sheet,
                operator_id,
                DocumentAction.CANCEL.value,
                sheet.status.value,
                DocumentStatus.CANCELLED.value,
                reason_text,
            )
            await self._apply_status(
                sheet,
                to_status=DocumentStatus.CANCELLED,
                expected_version=expected_version,
                operator_id=operator_id,
                extra={"cancelled_reason": reason_text},
            )
            return await self._reloaded(sheet_id)

    def _assert_can(self, action: str, ctx: AuthContext) -> None:
        """动作权限点（05 §4.2 / §10）。缺 → ``12001``。"""
        required = PERMISSION_BY_ACTION[action]
        if not ctx.has(required):
            raise BusinessError(
                ErrorCode.PERMISSION_DENIED,
                f"无权限执行 {action}（需 {required}）",
                details={"action": action, "required_permission": required},
            )

    def _assert_transition(
        self, action: str, from_status: DocumentStatus, to_status: DocumentStatus
    ) -> None:
        """08 R1：迁移合法性的**唯一**判定口。非法 / 终态再迁移 → ``10007``。"""
        allowed_from, target = TRANSITIONS[action]
        if from_status in allowed_from and to_status is target:
            return
        raise BusinessError(
            ErrorCode.ILLEGAL_STATUS_TRANSITION,
            f"当前状态 {from_status.value} 不能执行 {action}",
            details={
                "action": action,
                "from_status": from_status.value,
                "to_status": to_status.value,
                "allowed_from": sorted(item.value for item in allowed_from),
            },
        )

    def _assert_updatable(self, status: DocumentStatus) -> None:
        """``update`` 仅限可编辑状态（05 §4.2）。否则 ``10007``。"""
        if status in EDITABLE_STATUSES:
            return
        raise BusinessError(
            ErrorCode.ILLEGAL_STATUS_TRANSITION,
            f"当前状态 {status.value} 不可修改",
            details={"status": status.value},
        )

    def _assert_period_open(self, period: PayrollPeriod) -> None:
        """周期关闭后不可生成工资单（05 §4.2 / BR-PR-19；``33003``）。"""
        if period.status is PayrollPeriodStatus.CLOSED:
            raise BusinessError(
                ErrorCode.PAYROLL_PERIOD_CLOSED,
                "工资结算周期已关闭，不可再生成工资单，请联系计件员重开周期",
                details={"period_id": str(period.id), "period_code": period.period_code},
            )

    def _assert_creator(self, sheet: PayrollSheet, ctx: AuthContext) -> None:
        """撤回人 = 制单人（08 §1.1）。判 ``ctx.user_id`` 而非传入的 ``operator_id``。"""
        if ctx.user_id == sheet.created_by:
            return
        raise BusinessError(
            ErrorCode.PERMISSION_DENIED,
            "只有制单人本人可以撤回本单",
            details={"doc_no": sheet.doc_no},
        )

    async def _assert_has_details(self, sheet: PayrollSheet) -> None:
        """``submit`` 前置：明细非空（05 §4.2）。空单无意义，复用 ``10008``。"""
        count = (
            await self.session.execute(
                select(func.count())
                .select_from(PayrollDetail)
                .where(PayrollDetail.doc_id == sheet.id, PayrollDetail.deleted_at.is_(None))
            )
        ).scalar_one()
        if count > 0:
            return
        raise BusinessError(
            ErrorCode.ILLEGAL_OPERATION,
            "工资单没有任何明细行，不能提交",
            details={"sheet_id": str(sheet.id), "doc_no": sheet.doc_no},
        )

    async def _recalc_header_amounts(
        self, sheet: PayrollSheet, extra: dict[str, Any]
    ) -> dict[str, Any]:
        """按明细重算表头金额；不同则写回 ``extra`` 并返回 diff（``05 §4.2``）。"""
        gross, deduction = (
            await self.session.execute(
                select(
                    func.coalesce(func.sum(PayrollDetail.gross_amount), Decimal("0")),
                    func.coalesce(func.sum(PayrollDetail.deduction_amount), Decimal("0")),
                ).where(PayrollDetail.doc_id == sheet.id, PayrollDetail.deleted_at.is_(None))
            )
        ).one()
        gross = Decimal(gross)
        deduction = Decimal(deduction)
        net = gross - deduction
        if (gross, deduction, net) == (sheet.total_gross, sheet.total_deduction, sheet.total_net):
            return {}
        extra.update(total_gross=gross, total_deduction=deduction, total_net=net)
        return {
            "total_gross": {"from": str(sheet.total_gross), "to": str(gross)},
            "total_deduction": {"from": str(sheet.total_deduction), "to": str(deduction)},
            "total_net": {"from": str(sheet.total_net), "to": str(net)},
        }


__all__ = ["SheetStateMixin"]
