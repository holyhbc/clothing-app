"""结算周期的极简状态机（``OPEN`` ⇄ ``CLOSED``，05 §4.3）。

周期**不是单据**（无 ``doc_no`` / ``doc_date``），但迁移同样遵守 08 §1.2 铁律：

| # | 规则 | 本文件落点 |
| --- | --- | --- |
| R1 | 统一入口 ``service.payroll_period.<action>()`` | :meth:`PeriodStateMixin.close` / :meth:`PeriodStateMixin.reopen` |
| R2 | 每次迁移写 ``document_logs``（含 from/to/reason） | :meth:`PeriodStateMixin._write_log` |
| R3 | ``WHERE id=? AND version=? AND status=?``，``rowcount==0 → 10003`` | :meth:`PeriodStateMixin._apply_status` |
| R7 | 状态只由状态机方法写 | 本文件是唯一写 ``payroll_periods.status`` 的地方 |

## close 的前置为什么也拦「草稿/已提交结算单」

周期关闭后不可再加单（``33003``），但**已经躺在里面还没走完**的结算单若不管，就会出现
「周期 CLOSED 却仍能把它 submit / approve」——与 P6「结算审核 vs 周期关闭」矛盾。
故 ``close`` 前置：区间内不得有 ``DRAFT`` / ``SUBMITTED`` 的结算单（05 §4.3）。
⚠️ ``APPROVED`` 不拦：那是已经生效的快照，关闭只是在它之后冻结新增。

## 加锁顺序（P6 / P7）

:meth:`PeriodStateMixin._locked_period` 对周期行 ``SELECT … FOR UPDATE``：与结算单审核
（T-PAY-005）对同一周期行的加锁串行化，避免「周期已 CLOSED 却新增了 APPROVED 结算单」。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import DocumentStatus
from app.common.models import DocumentLog
from app.core.db import unit_of_work
from app.core.errors import BusinessError, ErrorCode
from app.core.permissions import AuthContext
from app.modules.payroll.models import PayrollPeriod, PayrollPeriodStatus, PieceworkSettlement
from app.modules.payroll.service.common import (
    ACTION_CLOSE,
    ACTION_REOPEN,
    DOC_TYPE_PAYROLL_PERIOD,
    PERIOD_MANAGE_PERMISSION,
    TRANSITION_BY_ACTION,
    require_reason,
)


class PeriodStateMixin:
    """周期状态机动作（由 :class:`~app.modules.payroll.service.period.PayrollPeriodService` 组装）。"""

    session: AsyncSession

    async def close(
        self,
        period_id: UUID,
        *,
        reason: str | None,
        expected_version: int,
        operator_id: UUID,
        ctx: AuthContext,
    ) -> PayrollPeriod:
        """关闭周期（``OPEN → CLOSED``，05 §4.3）。

        前置：区间内**无** ``DRAFT`` / ``SUBMITTED`` 的结算单（有则先处理）。本卡按
        任务卡口径复用 ``33003``（周期相关门户码）；``05 §4.3`` 只写「有则提示先处理」、
        未单列码，若规范维护者另立码则只改 :meth:`_assert_no_open_settlements`。

        :raises BusinessError: ``12001`` 无权限；``10006`` 缺原因；``10007`` 非 OPEN；
            ``33003`` 有未完成结算单；``10003`` 乐观锁。
        """
        self._assert_can_manage(ctx)
        reason_text = require_reason(reason, action="关闭周期")
        async with unit_of_work(self.session):
            period = await self._locked_period(period_id)
            self._assert_transition("close", period.status, PayrollPeriodStatus.CLOSED)
            from_status = period.status.value
            await self._assert_no_open_settlements(period)
            # ⚠️ 日志先写、状态后迁：Core UPDATE 会 expire ORM 行，之后再读
            #    period.period_code 在 async 下会 MissingGreenlet（同打菲）。
            await self._write_log(
                period,
                operator_id,
                ACTION_CLOSE,
                from_status,
                PayrollPeriodStatus.CLOSED.value,
                reason_text,
            )
            await self._apply_status(
                period,
                to_status=PayrollPeriodStatus.CLOSED,
                expected_version=expected_version,
                operator_id=operator_id,
                extra={
                    "closed_by": operator_id,
                    "closed_at": datetime.now(tz=UTC),
                    "close_reason": reason_text,
                },
            )
            return await self._reloaded(period_id)

    async def reopen(
        self,
        period_id: UUID,
        *,
        reason: str | None,
        expected_version: int,
        operator_id: UUID,
        ctx: AuthContext,
    ) -> PayrollPeriod:
        """重开周期（``CLOSED → OPEN``，05 §4.3）。**仅** ``CLOSED → OPEN``，否则 ``10007``。

        ⚠️ 重开后**保留** ``closed_by`` / ``closed_at`` / ``close_reason`` 作为上次关闭的
        痕迹（§5.1），另由 ``document_logs`` 记本次重开；已结算工资数据不变（结算单是快照）。

        :raises BusinessError: ``12001`` 无权限；``10006`` 缺原因；``10007`` 非 CLOSED；
            ``10003`` 乐观锁。
        """
        self._assert_can_manage(ctx)
        reason_text = require_reason(reason, action="重开周期")
        async with unit_of_work(self.session):
            period = await self._locked_period(period_id)
            self._assert_transition("reopen", period.status, PayrollPeriodStatus.OPEN)
            from_status = period.status.value
            await self._write_log(
                period,
                operator_id,
                ACTION_REOPEN,
                from_status,
                PayrollPeriodStatus.OPEN.value,
                reason_text,
            )
            await self._apply_status(
                period,
                to_status=PayrollPeriodStatus.OPEN,
                expected_version=expected_version,
                operator_id=operator_id,
            )
            return await self._reloaded(period_id)

    # ------------------------------------------------------------------ 助手

    def _assert_can_manage(self, ctx: AuthContext) -> None:
        """周期建 / 关 / 重开统一权限点（05 §10）。"""
        if not ctx.has(PERIOD_MANAGE_PERMISSION):
            raise BusinessError(
                ErrorCode.PERMISSION_DENIED,
                f"无权限管理工资结算周期（需 {PERIOD_MANAGE_PERMISSION}）",
                details={"required_permission": PERIOD_MANAGE_PERMISSION},
            )

    def _assert_transition(
        self,
        action: str,
        from_status: PayrollPeriodStatus,
        to_status: PayrollPeriodStatus,
    ) -> None:
        """08 R1：迁移合法性的**唯一**判定口。非法 → ``10007``。"""
        allowed_from, target = TRANSITION_BY_ACTION[action]
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

    async def _locked_period(self, period_id: UUID) -> PayrollPeriod:
        """按 id 取周期并 ``FOR UPDATE``（P6/P7：close/reopen 与结算审核串行化）。"""
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

    async def _assert_no_open_settlements(self, period: PayrollPeriod) -> None:
        """close 前置：区间内不得残留 ``DRAFT`` / ``SUBMITTED`` 结算单（05 §4.3）。"""
        count = (
            await self.session.execute(
                select(func.count(PieceworkSettlement.id)).where(
                    PieceworkSettlement.period_id == period.id,
                    PieceworkSettlement.status.in_(
                        [DocumentStatus.DRAFT, DocumentStatus.SUBMITTED]
                    ),
                    PieceworkSettlement.deleted_at.is_(None),
                )
            )
        ).scalar_one()
        if count:
            raise BusinessError(
                ErrorCode.PAYROLL_PERIOD_CLOSED,
                f"周期内还有 {count} 张未完成的结算单（草稿/已提交），请先处理后再关闭周期",
                details={"period_id": str(period.id), "open_settlement_count": int(count)},
            )

    async def _apply_status(
        self,
        period: PayrollPeriod,
        *,
        to_status: PayrollPeriodStatus,
        expected_version: int,
        operator_id: UUID,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """08 R3 + R7：写状态的**唯一**入口（条件 UPDATE 式乐观锁）。

        ``WHERE id=? AND version=? AND status=?`` 三条件齐全；``rowcount == 0`` → ``10003``。
        只按 id 更新的话，并发的第二个请求会在第一个提交之后再次写下去。
        """
        result = await self.session.execute(
            update(PayrollPeriod)
            .where(
                PayrollPeriod.id == period.id,
                PayrollPeriod.version == expected_version,
                PayrollPeriod.status == period.status,
                PayrollPeriod.deleted_at.is_(None),
            )
            .values(
                status=to_status,
                version=PayrollPeriod.version + 1,
                updated_by=operator_id,
                **(extra or {}),
            )
            .returning(PayrollPeriod.id)
        )
        if len(result.all()) == 0:
            raise BusinessError(
                ErrorCode.OPTIMISTIC_LOCK_CONFLICT,
                "结算周期已被他人修改（版本或状态已变化），请刷新后重试",
                details={
                    "period_id": str(period.id),
                    "expected_version": expected_version,
                    "to_status": to_status.value,
                },
            )

    async def _write_log(
        self,
        period: PayrollPeriod,
        operator_id: UUID,
        action: str,
        from_status: str | None,
        to_status: str,
        reason: str | None = None,
    ) -> None:
        """写 ``document_logs``（05 §4.3：周期迁移同样遵守 08 §1.2 R2）。

        ⚠️ 周期**没有** ``doc_no``，``doc_no`` 列落标签 ``period_code``（可读且唯一）。
        """
        self.session.add(
            DocumentLog(
                doc_type=DOC_TYPE_PAYROLL_PERIOD,
                doc_id=period.id,
                doc_no=period.period_code,
                action=action,
                from_status=from_status,
                to_status=to_status,
                operator_id=operator_id,
                operator_name="系统",  # TODO: 接真实操作人姓名（同打菲 _write_log）
                reason=reason,
                changed_fields=None,
            )
        )

    async def _reloaded(self, period_id: UUID) -> PayrollPeriod:
        """写后重读（``populate_existing`` —— Core UPDATE 后 identity map 里是旧值）。"""
        period = (
            await self.session.execute(
                select(PayrollPeriod)
                .where(PayrollPeriod.id == period_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if period is None:  # pragma: no cover —— 刚写过，行必然还在
            raise BusinessError(ErrorCode.BASE_DATA_NOT_FOUND, "结算周期不存在或已删除")
        return period


__all__ = ["PeriodStateMixin"]
