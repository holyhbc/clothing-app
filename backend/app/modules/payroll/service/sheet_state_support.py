"""工资单状态机的**持久化助手**（T-PAY-006，从 ``sheet_state_mixin`` 拆出）。

08 §1.2 R3/R7 的落点集中在本文件：:meth:`SheetStoreMixin._apply_status` 是工资单
``status`` / ``version`` 的**唯一**写入口，:meth:`SheetStoreMixin._write_log` 是
``document_logs`` 的唯一写入口。拆出的理由同 ADR-0030/0031：``sheet_state_mixin.py``
加上这些助手后越过单文件 400 行硬线。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import DocumentStatus
from app.common.models import DocumentLog
from app.core.errors import BusinessError, ErrorCode
from app.modules.payroll.models import PayrollPeriod, PayrollSheet
from app.modules.payroll.service.sheet_state import DOC_TYPE_PAYROLL_SHEET


class SheetStoreMixin:
    """工资单的取数 / 状态写 / 日志写（由 :class:`~.sheet_state_mixin.SheetStateMixin` 继承）。"""

    session: AsyncSession

    async def _get_sheet(self, sheet_id: UUID) -> PayrollSheet:
        """按 id 取工资单（不过滤状态）。不存在 / 软删 → ``20001``。"""
        sheet = (
            await self.session.execute(
                select(PayrollSheet).where(
                    PayrollSheet.id == sheet_id, PayrollSheet.deleted_at.is_(None)
                )
            )
        ).scalar_one_or_none()
        if sheet is None:
            raise BusinessError(
                ErrorCode.BASE_DATA_NOT_FOUND,
                "工资单不存在或已删除",
                details={"sheet_id": str(sheet_id)},
            )
        return sheet

    async def _locked_period(self, period_id: UUID) -> PayrollPeriod:
        """按 id 取周期并 ``FOR UPDATE``（与周期关闭串行化）。不存在 → ``20001``。"""
        period = (
            await self.session.execute(
                select(PayrollPeriod)
                .where(PayrollPeriod.id == period_id, PayrollPeriod.deleted_at.is_(None))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if period is None:
            raise BusinessError(
                ErrorCode.BASE_DATA_NOT_FOUND,
                "结算周期不存在或已删除",
                details={"period_id": str(period_id)},
            )
        return period

    async def _apply_status(
        self,
        sheet: PayrollSheet,
        *,
        to_status: DocumentStatus,
        expected_version: int,
        operator_id: UUID,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """08 R3+R7：写状态的**唯一**入口（``rowcount==0 → 10003``）。

        ``WHERE id=? AND version=? AND status=?`` 三条件齐全；只按 id 更新的话，
        并发的第二个请求会在第一个提交之后再次写下去。
        """
        result = await self.session.execute(
            update(PayrollSheet)
            .where(
                PayrollSheet.id == sheet.id,
                PayrollSheet.version == expected_version,
                PayrollSheet.status == sheet.status,
                PayrollSheet.deleted_at.is_(None),
            )
            .values(
                status=to_status,
                version=PayrollSheet.version + 1,
                updated_by=operator_id,
                **(extra or {}),
            )
            .returning(PayrollSheet.id)
        )
        if len(result.all()) == 0:
            raise BusinessError(
                ErrorCode.OPTIMISTIC_LOCK_CONFLICT,
                "工资单已被他人修改（版本或状态已变化），请刷新后重试",
                details={
                    "sheet_id": str(sheet.id),
                    "expected_version": expected_version,
                    "to_status": to_status.value,
                },
            )

    async def _write_log(
        self,
        sheet: PayrollSheet,
        operator_id: UUID,
        action: str,
        from_status: str | None,
        to_status: str,
        reason: str | None = None,
        changed_fields: dict[str, Any] | None = None,
    ) -> None:
        """写 ``document_logs``（08 R2）。必须在 :meth:`_apply_status` **之前**调用。"""
        self.session.add(
            DocumentLog(
                doc_type=DOC_TYPE_PAYROLL_SHEET,
                doc_id=sheet.id,
                doc_no=sheet.doc_no,
                action=action,
                from_status=from_status,
                to_status=to_status,
                operator_id=operator_id,
                operator_name="系统",  # TODO: 接真实操作人姓名（同结算 _write_log）
                reason=reason,
                changed_fields=changed_fields,
            )
        )

    async def _reloaded(self, sheet_id: UUID) -> PayrollSheet:
        """写后重读（``populate_existing`` —— Core UPDATE 后 identity map 里是旧值）。"""
        sheet = (
            await self.session.execute(
                select(PayrollSheet)
                .where(PayrollSheet.id == sheet_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if sheet is None:  # pragma: no cover —— 刚写过，行必然还在
            raise BusinessError(ErrorCode.BASE_DATA_NOT_FOUND, "工资单不存在或已删除")
        return sheet


__all__ = ["SheetStoreMixin"]
