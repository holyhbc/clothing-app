"""计件结算单的**轻状态迁移**（T-PAY-004；``submit`` / ``withdraw`` / ``reject`` / ``cancel``）。

迁移表在 :mod:`app.modules.payroll.state`（声明式，08 §3.1），本文件只做编排：

============  ===========================  ================  ==================================
动作          迁移                         权限点            副作用
============  ===========================  ================  ==================================
``submit``    DRAFT/REJECTED → SUBMITTED   ``payroll:settle`` 前置 ``total_amount > 0`` + 二次校验周期仍 ``OPEN``
``withdraw``  SUBMITTED → DRAFT            ``payroll:settle`` 撤回人 = 制单人
``reject``    SUBMITTED → REJECTED         ``payroll:approve`` **必填原因**（落 ``rejected_reason``）
``cancel``    DRAFT/REJECTED → CANCELLED   ``payroll:settle`` **必填原因**（落 ``cancelled_reason``）
============  ===========================  ================  ==================================

## 08 §1.2 铁律落点
R1 统一入口 + ``_assert_transition``；R2 每次迁移写 ``document_logs``；
R3 ``WHERE id=? AND version=? AND status=?`` 条件 UPDATE，``rowcount==0 → 10003``；
R7 状态字段**只**由 :meth:`SettlementStateMixin._apply_status` 写。

``approve`` / ``reverse`` 归 T-PAY-005（:mod:`.settlement_approve`，要回写 / 清空计件流水）。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import DocumentStatus
from app.common.models import DocumentLog
from app.core.db import unit_of_work
from app.core.errors import BusinessError, ErrorCode
from app.core.permissions import AuthContext
from app.modules.payroll.models import PayrollPeriod, PayrollPeriodStatus, PieceworkSettlement
from app.modules.payroll.service.common import require_reason
from app.modules.payroll.state import (
    DOC_TYPE_PIECEWORK_SETTLEMENT,
    LOG_ACTION_BY_ACTION,
    PERMISSION_BY_ACTION,
    SETTLEMENT_TRANSITIONS,
)


class SettlementStateMixin:
    """结算单轻状态动作（由 :class:`~app.modules.payroll.service.settlement.PieceworkSettlementService` 组装）。"""

    session: AsyncSession

    # ------------------------------------------------------------------ 动作

    async def submit(
        self,
        settlement_id: UUID,
        *,
        expected_version: int,
        operator_id: UUID,
        ctx: AuthContext,
    ) -> PieceworkSettlement:
        """提交（``DRAFT``/``REJECTED`` → ``SUBMITTED``）。

        前置：① 状态可提交（否则 ``10007``）；② **二次校验周期仍 ``OPEN``** ——
        期间被关 → ``33003``（05 §4.1）；③ ``total_amount > 0``（空结算单无意义）。

        ⚠️ 周期行 ``SELECT … FOR UPDATE``：与周期 ``close``（T-PAY-003 亦锁周期行）
        串行化，避免「周期已 CLOSED 却把结算单提交成 SUBMITTED」（P6 同源）。

        ⚠️ ``total_amount <= 0`` 的拒绝错误码：``05 §4`` / §9 未为这条前置单列码。
        本卡复用 ``33002``（结算域唯一的「结算内容」相关码），并记入
        ``docs/modules/05-工资.md §12`` 待规范确认。

        :raises BusinessError: ``12001`` 无权限；``20001`` 单据/周期不存在；
            ``10007`` 非法迁移；``33003`` 周期已关闭；``33002`` 金额 ≤ 0；
            ``10003`` 乐观锁。
        """
        self._assert_can("submit", ctx)
        async with unit_of_work(self.session):
            settlement = await self._get_settlement(settlement_id)
            self._assert_transition("submit", settlement.status, DocumentStatus.SUBMITTED)
            period = await self._locked_period(settlement.period_id)
            self._assert_period_open(period)
            self._assert_submittable(settlement)
            from_status = settlement.status.value
            await self._write_log(
                settlement,
                operator_id,
                LOG_ACTION_BY_ACTION["submit"],
                from_status,
                DocumentStatus.SUBMITTED.value,
            )
            await self._apply_status(
                settlement,
                to_status=DocumentStatus.SUBMITTED,
                expected_version=expected_version,
                operator_id=operator_id,
            )
            return await self._reloaded(settlement_id)

    async def withdraw(
        self,
        settlement_id: UUID,
        *,
        expected_version: int,
        operator_id: UUID,
        ctx: AuthContext,
    ) -> PieceworkSettlement:
        """撤回（``SUBMITTED`` → ``DRAFT``）。**撤回人 = 制单人**（08 §1.1）。

        :raises BusinessError: ``12001`` 无权限 / 非制单人；``20001`` 单据不存在；
            ``10007`` 非法迁移；``10003`` 乐观锁。
        """
        self._assert_can("withdraw", ctx)
        async with unit_of_work(self.session):
            settlement = await self._get_settlement(settlement_id)
            self._assert_transition("withdraw", settlement.status, DocumentStatus.DRAFT)
            self._assert_creator(settlement, ctx)
            from_status = settlement.status.value
            await self._write_log(
                settlement,
                operator_id,
                LOG_ACTION_BY_ACTION["withdraw"],
                from_status,
                DocumentStatus.DRAFT.value,
            )
            await self._apply_status(
                settlement,
                to_status=DocumentStatus.DRAFT,
                expected_version=expected_version,
                operator_id=operator_id,
            )
            return await self._reloaded(settlement_id)

    async def reject(
        self,
        settlement_id: UUID,
        *,
        reason: str | None,
        expected_version: int,
        operator_id: UUID,
        ctx: AuthContext,
    ) -> PieceworkSettlement:
        """驳回（``SUBMITTED`` → ``REJECTED``）。**必填原因**（``10006``），落 ``rejected_reason``。

        :raises BusinessError: ``12001`` 无权限；``10006`` 缺原因；``20001`` 单据不存在；
            ``10007`` 非法迁移；``10003`` 乐观锁。
        """
        self._assert_can("reject", ctx)
        reason_text = require_reason(reason, action="驳回结算单")
        async with unit_of_work(self.session):
            settlement = await self._get_settlement(settlement_id)
            self._assert_transition("reject", settlement.status, DocumentStatus.REJECTED)
            from_status = settlement.status.value
            await self._write_log(
                settlement,
                operator_id,
                LOG_ACTION_BY_ACTION["reject"],
                from_status,
                DocumentStatus.REJECTED.value,
                reason_text,
            )
            await self._apply_status(
                settlement,
                to_status=DocumentStatus.REJECTED,
                expected_version=expected_version,
                operator_id=operator_id,
                extra={"rejected_reason": reason_text},
            )
            return await self._reloaded(settlement_id)

    async def cancel(
        self,
        settlement_id: UUID,
        *,
        reason: str | None,
        expected_version: int,
        operator_id: UUID,
        ctx: AuthContext,
    ) -> PieceworkSettlement:
        """作废（``DRAFT``/``REJECTED`` → ``CANCELLED``）。**必填原因**，终态不可再迁移（08 R6）。

        ⚠️ 本卡**没有** ``APPROVED → CANCELLED``（要先把已审核的结算单 ``reverse``，
        归 T-PAY-005），故 ``cancel`` 无任何计件流水副作用 —— 走到这里时本单手里
        没有任何 ``payroll_id`` 占用。

        :raises BusinessError: ``12001`` 无权限；``10006`` 缺原因；``20001`` 单据不存在；
            ``10007`` 非法迁移 / 终态再迁移；``10003`` 乐观锁。
        """
        self._assert_can("cancel", ctx)
        reason_text = require_reason(reason, action="作废结算单")
        async with unit_of_work(self.session):
            settlement = await self._get_settlement(settlement_id)
            self._assert_transition("cancel", settlement.status, DocumentStatus.CANCELLED)
            from_status = settlement.status.value
            await self._write_log(
                settlement,
                operator_id,
                LOG_ACTION_BY_ACTION["cancel"],
                from_status,
                DocumentStatus.CANCELLED.value,
                reason_text,
            )
            await self._apply_status(
                settlement,
                to_status=DocumentStatus.CANCELLED,
                expected_version=expected_version,
                operator_id=operator_id,
                extra={"cancelled_reason": reason_text},
            )
            return await self._reloaded(settlement_id)

    # ------------------------------------------------------------------ 校验

    def _assert_can(self, action: str, ctx: AuthContext) -> None:
        """动作权限点（05 §10 / 08 §1.1）。缺 → ``12001``。"""
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
        allowed_from, target = SETTLEMENT_TRANSITIONS[action]
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

    def _assert_period_open(self, period: PayrollPeriod) -> None:
        """周期关闭后不可加单 / 提交（05 §4 BR-PR-19；``33003``）。"""
        if period.status is PayrollPeriodStatus.CLOSED:
            raise BusinessError(
                ErrorCode.PAYROLL_PERIOD_CLOSED,
                "工资结算周期已关闭，不可再结算或加单，请联系计件员重开周期",
                details={"period_id": str(period.id), "period_code": period.period_code},
            )

    def _assert_submittable(self, settlement: PieceworkSettlement) -> None:
        """``submit`` 前置：汇总金额 > 0（05 §4.1；空结算单无意义）。"""
        if settlement.total_amount <= 0:
            raise BusinessError(
                ErrorCode.SETTLEMENT_PERIOD_DUPLICATED,
                "结算单汇总金额为 0（没有可结算的流水），不能提交",
                details={
                    "settlement_id": str(settlement.id),
                    "doc_no": settlement.doc_no,
                    "total_amount": str(settlement.total_amount),
                },
            )

    def _assert_creator(self, settlement: PieceworkSettlement, ctx: AuthContext) -> None:
        """撤回人 = 制单人（08 §1.1）。不符 → ``12001``。

        判 ``ctx.user_id``（认证身份）而不是 ``operator_id``：后者由调用方传入，
        用它判会把「传错身份」变成静默放过。
        """
        if ctx.user_id == settlement.created_by:
            return
        raise BusinessError(
            ErrorCode.PERMISSION_DENIED,
            "只有制单人本人可以撤回本单",
            details={"doc_no": settlement.doc_no},
        )

    # ------------------------------------------------------------------ 取数 / 写

    async def _get_settlement(self, settlement_id: UUID) -> PieceworkSettlement:
        """按 id 取结算单（不过滤状态）。不存在 / 软删 → ``20001``。"""
        settlement = (
            await self.session.execute(
                select(PieceworkSettlement).where(
                    PieceworkSettlement.id == settlement_id,
                    PieceworkSettlement.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if settlement is None:
            raise BusinessError(
                ErrorCode.BASE_DATA_NOT_FOUND,
                "结算单不存在或已删除",
                details={"settlement_id": str(settlement_id)},
            )
        return settlement

    async def _locked_period(self, period_id: UUID) -> PayrollPeriod:
        """按 id 取周期并 ``FOR UPDATE``（与周期 close 串行化，P6）。不存在 → ``20001``。"""
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
        settlement: PieceworkSettlement,
        *,
        to_status: DocumentStatus,
        expected_version: int,
        operator_id: UUID,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """08 R3 + R7：写状态的**唯一**入口（条件 UPDATE 式乐观锁）。

        ``WHERE id=? AND version=? AND status=?`` 三条件齐全；``rowcount == 0`` → ``10003``。
        只按 id 更新的话，并发的第二个请求会在第一个提交之后再次写下去。
        """
        result = await self.session.execute(
            update(PieceworkSettlement)
            .where(
                PieceworkSettlement.id == settlement.id,
                PieceworkSettlement.version == expected_version,
                PieceworkSettlement.status == settlement.status,
                PieceworkSettlement.deleted_at.is_(None),
            )
            .values(
                status=to_status,
                version=PieceworkSettlement.version + 1,
                updated_by=operator_id,
                **(extra or {}),
            )
            .returning(PieceworkSettlement.id)
        )
        if len(result.all()) == 0:
            raise BusinessError(
                ErrorCode.OPTIMISTIC_LOCK_CONFLICT,
                "结算单已被他人修改（版本或状态已变化），请刷新后重试",
                details={
                    "settlement_id": str(settlement.id),
                    "expected_version": expected_version,
                    "to_status": to_status.value,
                },
            )

    async def _write_log(
        self,
        settlement: PieceworkSettlement,
        operator_id: UUID,
        action: str,
        from_status: str | None,
        to_status: str,
        reason: str | None = None,
        changed_fields: dict[str, Any] | None = None,
    ) -> None:
        """写 ``document_logs``（08 R2：谁、何时、从什么状态到什么状态、为什么）。

        ⚠️ 必须在 ``_apply_status`` **之前**调用：Core UPDATE 会 ``expire`` ORM 行，
        之后再读 ``settlement.doc_no`` 在 async 下会 ``MissingGreenlet``（同打菲/周期）。
        ``changed_fields`` 供 T-PAY-005 记 ``occupied_log_count`` / ``released_log_count``。
        """
        self.session.add(
            DocumentLog(
                doc_type=DOC_TYPE_PIECEWORK_SETTLEMENT,
                doc_id=settlement.id,
                doc_no=settlement.doc_no,
                action=action,
                from_status=from_status,
                to_status=to_status,
                operator_id=operator_id,
                operator_name="系统",  # TODO: 接真实操作人姓名（同打菲/周期 _write_log）
                reason=reason,
                changed_fields=changed_fields,
            )
        )

    async def _reloaded(self, settlement_id: UUID) -> PieceworkSettlement:
        """写后重读（``populate_existing`` —— Core UPDATE 后 identity map 里是旧值）。"""
        settlement = (
            await self.session.execute(
                select(PieceworkSettlement)
                .where(PieceworkSettlement.id == settlement_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if settlement is None:  # pragma: no cover —— 刚写过，行必然还在
            raise BusinessError(ErrorCode.BASE_DATA_NOT_FOUND, "结算单不存在或已删除")
        return settlement


__all__ = ["SettlementStateMixin"]
