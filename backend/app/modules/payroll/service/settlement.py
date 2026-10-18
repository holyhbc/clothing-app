"""计件结算单生成（T-PAY-004）。

按「周期区间 + 车间」把**未结算**的计件流水汇成结算单（单头 + 逐条行）。
设计权威：``docs/modules/05-工资.md`` §2（BR-PR-01/02/14/20/22）、§2.1、§3.2/§3.3、
§4.1、§5.2、§6.3、§8（S1/S2/S4）；轻状态迁移见
:mod:`~app.modules.payroll.service.settlement_state`。

## 行的粒度：**一流水一行**（不是聚合行）

``05 §2.1`` 的取数 SQL 写了 ``GROUP BY employee_id, group_no, style_no, operation_no``
（聚合），而 ``§3.3`` / ``§5.2`` / §11 TC-01 要求「每条流水一行」，且迁移 0020 的
``uq_piecework_settlement_lines_piecework_log_id`` 是**唯一约束** —— 三者指向
「一流水一行」。本卡以 ``§3.3`` + DB 唯一约束 + TC-01 为准：**不做 GROUP BY**，
单头合计 = 对行求和（结构上不可能与行汇总不一致）。该冲突登记为
``docs/modules/05-工资.md §12`` 的 **Q-PL12**，**不改 §2.1 正文**。

## 取数日期从周期表读（BR-PR-20）

``create`` **不收前端日期**：``work_date >= period.start_date AND <= period.end_date``
（闭区间，TC-20）；``period_start_date`` / ``period_end_date`` 只是冗余快照列。
标签 ``period_code`` **只给人看，永不解析**。

## 幂等

业务唯一键「周期 + 车间」命中 → **返回既有单**（``code=0``，不报 ``33002``，05 §6.3）；
并发下的最后一道防线是 ``uq_piecework_settlements_period_workshop``，撞上翻译成 ``33002``。
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import DataScope, DocumentStatus
from app.core.db import unit_of_work
from app.core.errors import BusinessError, ErrorCode
from app.core.numbering import business_today, take_doc_no
from app.core.permissions import AuthContext
from app.core.scope import visible_workshops
from app.modules.payroll.models import (
    PayrollPeriod,
    PieceworkSettlement,
    PieceworkSettlementLine,
)
from app.modules.payroll.service.settlement_state import SettlementStateMixin
from app.modules.payroll.state import SETTLEMENT_DOC_PREFIX
from app.modules.piecework.models import PieceworkLog

logger = logging.getLogger("app.payroll")


class PieceworkSettlementService(SettlementStateMixin):
    """结算单 service：``create`` 生成 + 轻状态迁移（继承状态 Mixin）。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ================================================================== 生成

    async def create(
        self,
        *,
        period_id: UUID,
        workshop_id: UUID,
        ctx: AuthContext,
        doc_date: date | None = None,
    ) -> PieceworkSettlement:
        """按「周期 + 车间」生成结算单（05 §4.1 / §5.2）。

        步骤：① 校验周期存在且 ``OPEN``（``CLOSED`` → ``33003``，周期行 ``FOR UPDATE``
        与 ``close`` 串行化）；② 业务唯一键命中 → **幂等返回既有单**；③ 从周期表读区间
        取未结算流水（``payroll_id IS NULL AND deleted_at IS NULL``，一流水一行）；
        ④ 写单头（汇总由 service **重算**）+ 行；⑤ 写 ``document_logs``。

        ⚠️ **不回写** ``piecework_logs.payroll_id``（那是审核副作用，T-PAY-005）。

        :param doc_date: 单据日期，默认业务今天（工厂当地日历）。测试显式传入以避免
            跨零点失败（``docs/10`` §10）。
        :raises BusinessError: ``12001`` 无权限；``12002`` 车间越权；``20001`` 周期不存在；
            ``33003`` 周期已关闭；``33002`` 并发重复结算。
        """
        self._assert_can("create", ctx)
        self._assert_workshop_in_scope(workshop_id, ctx)
        async with unit_of_work(self.session):
            period = await self._locked_period(period_id)
            self._assert_period_open(period)

            existing = await self._find_existing(period_id, workshop_id)
            if existing is not None:
                logger.info(
                    "结算单幂等命中，返回既有单",
                    extra={"doc_no": existing.doc_no, "period_id": str(period_id)},
                )
                return existing

            logs = await self._fetch_unsettled_logs(period, workshop_id)
            resolved_date = doc_date if doc_date is not None else business_today()
            doc_no = await take_doc_no(
                self.session, prefix=SETTLEMENT_DOC_PREFIX, doc_date=resolved_date
            )
            header = self._build_header(
                period, workshop_id, doc_no, resolved_date, logs, ctx.user_id
            )
            self.session.add(header)
            await self._flush_or_duplicated(header, period_id, workshop_id)

            lines = self._build_lines(header.id, logs, ctx.user_id)
            self.session.add_all(lines)
            await self._flush_or_duplicated(header, period_id, workshop_id)

            await self._write_log(
                header,
                ctx.user_id,
                "CREATE",
                None,
                DocumentStatus.DRAFT.value,
            )
            logger.info(
                "计件结算单已生成",
                extra={
                    "doc_no": doc_no,
                    "period_id": str(period_id),
                    "line_count": header.line_count,
                    "total_amount": str(header.total_amount),
                },
            )
            return await self._reloaded(header.id)

    # ================================================================== 取数

    async def _find_existing(
        self, period_id: UUID, workshop_id: UUID
    ) -> PieceworkSettlement | None:
        """业务唯一键「周期 + 车间」查既有单（S1；幂等命中返回它，不报错）。"""
        return (
            await self.session.execute(
                select(PieceworkSettlement).where(
                    PieceworkSettlement.period_id == period_id,
                    PieceworkSettlement.workshop_id == workshop_id,
                    PieceworkSettlement.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()

    async def _fetch_unsettled_logs(
        self, period: PayrollPeriod, workshop_id: UUID
    ) -> list[PieceworkLog]:
        """05 §2.1 的取数条件，但**不聚合**（一流水一行，见模块 docstring）。

        ``work_date >= start AND <= end``（闭区间）+ 车间 + ``payroll_id IS NULL``
        + ``deleted_at IS NULL``；``ORDER BY work_date, id`` 保证 ``line_no`` 稳定。
        """
        rows = await self.session.execute(
            select(PieceworkLog)
            .where(
                PieceworkLog.work_date >= period.start_date,
                PieceworkLog.work_date <= period.end_date,
                PieceworkLog.workshop_id == workshop_id,
                PieceworkLog.payroll_id.is_(None),
                PieceworkLog.deleted_at.is_(None),
            )
            .order_by(PieceworkLog.work_date, PieceworkLog.id)
        )
        return list(rows.scalars().all())

    # ================================================================== 组装

    def _build_header(
        self,
        period: PayrollPeriod,
        workshop_id: UUID,
        doc_no: str,
        doc_date: date,
        logs: list[PieceworkLog],
        operator_id: UUID,
    ) -> PieceworkSettlement:
        """建单头；汇总字段由 service **重算**（不信前端，05 §4.1）。"""
        total_qty, total_amount, employee_count, group_count, line_count = self._recalc(logs)
        return PieceworkSettlement(
            doc_no=doc_no,
            status=DocumentStatus.DRAFT,
            doc_date=doc_date,
            period_id=period.id,
            period_code=period.period_code,
            period_start_date=period.start_date,
            period_end_date=period.end_date,
            workshop_id=workshop_id,
            group_count=group_count,
            total_qty=total_qty,
            total_amount=total_amount,
            employee_count=employee_count,
            line_count=line_count,
            created_by=operator_id,
            updated_by=operator_id,
        )

    def _build_lines(
        self, doc_id: UUID, logs: list[PieceworkLog], operator_id: UUID
    ) -> list[PieceworkSettlementLine]:
        """每条流水一行（``line_no`` 从 1 起），快照单价与金额（BR-PR-03）。

        ⚠️ ``group_no`` 在 ``piecework_logs`` 可空、在行表**必填**，故 ``None`` 落空串；
        组别是行级维度（``group_count`` 按去重后统计）。
        """
        return [
            PieceworkSettlementLine(
                doc_id=doc_id,
                line_no=index,
                piecework_log_id=log.id,
                employee_id=log.employee_id,
                group_no=log.group_no or "",
                style_no=log.style_no,
                operation_no=log.operation_no,
                work_date=log.work_date,
                log_type=log.log_type.value,
                qty=log.qty,
                unit_price=log.unit_price,
                amount=log.amount,
                created_by=operator_id,
                updated_by=operator_id,
            )
            for index, log in enumerate(logs, start=1)
        ]

    def _recalc(self, logs: list[PieceworkLog]) -> tuple[Decimal, Decimal, int, int, int]:
        """从流水重算 ``(total_qty, total_amount, employee_count, group_count, line_count)``。

        ``total_qty`` 恒为整数（BR-PR-22：源头流水只允许正整数件）。
        """
        total_qty = sum((log.qty for log in logs), start=Decimal("0"))
        total_amount = sum((log.amount for log in logs), start=Decimal("0"))
        return (
            total_qty,
            total_amount,
            len({log.employee_id for log in logs}),
            len({log.group_no or "" for log in logs}),
            len(logs),
        )

    async def _flush_or_duplicated(
        self, header: PieceworkSettlement, period_id: UUID, workshop_id: UUID
    ) -> None:
        """flush；撞唯一约束（P1 / P3）翻译成 ``33002``。"""
        try:
            await self.session.flush()
        except IntegrityError:
            raise BusinessError(
                ErrorCode.SETTLEMENT_PERIOD_DUPLICATED,
                "该「周期 + 车间」已有结算单（或同一流水已被占用），请刷新后查看",
                details={
                    "doc_no": header.doc_no,
                    "period_id": str(period_id),
                    "workshop_id": str(workshop_id),
                },
            ) from None

    def _assert_workshop_in_scope(self, workshop_id: UUID, ctx: AuthContext) -> None:
        """建单车间必须在数据范围内（05 §4.1 前置 ②；越权 → ``12002``）。"""
        if ctx.data_scope == DataScope.FACTORY or workshop_id in visible_workshops(ctx):
            return
        raise BusinessError(
            ErrorCode.DATA_SCOPE_DENIED,
            "该车间超出你的数据范围，如需结算请联系管理员",
            details={"workshop_id": str(workshop_id)},
        )


__all__ = ["PieceworkSettlementService"]
