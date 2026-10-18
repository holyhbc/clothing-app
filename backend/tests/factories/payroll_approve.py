"""计件结算单**审核 / 反审核**用例共用的测试世界（T-PAY-005）。

⚠️ 单开一个模块而不是塞进 :mod:`tests.factories.payroll`（已 322 行，逼近 ADR-0030 的
400 行硬线）：审核用例要的东西（「可审核结算单」的组装、按列读 ``payroll_id``、
并发收尾软删）只有审核路径用，塞进通用工厂等于让每个结算用例都拖一份用不上的依赖
（同 :mod:`tests.factories.bundling_approve` 的拆分理由）。

## 「可审核结算单」的口径

``seed_approvable_settlement`` 造的单满足 ``approve`` 的全部前置：① 制单人 ≠ ``REVIEWER_ID``；
② 行逐条对应一条**未占用**流水（``payroll_id IS NULL``）；③ 单头 ``total_qty`` / ``total_amount``
/ ``line_count`` 与行汇总一致。用例只覆盖自己要破坏的那一个字段（10 §7）。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.common.enums import DataScope, DocumentStatus
from app.common.models import DocumentLog
from app.core.permissions import AuthContext
from app.modules.payroll.models import (
    PayrollPeriod,
    PieceworkSettlement,
    PieceworkSettlementLine,
)
from app.modules.piecework.models import PieceworkLog
from tests.factories.payroll import (
    seed_period,
    seed_piecework_log,
    settlement_line_row,
    settlement_row,
)

APPROVE_PERMISSION = "payroll:approve"
REVERSE_PERMISSION = "payroll:reverse"
DOC_TYPE = "PieceworkSettlement"

#: 审核人（与默认 ``settlement_row`` 的随机制单人天然不同）。
REVIEWER_ID = UUID("00000000-0000-0000-0000-000000000002")

#: 固定历史区间（``date(2026,1,1) ~ (2026,1,31)``）与计件归属日 —— 避开真提交的并发用例
#: 落在 ``business_today()`` 的流水（同 ``seed_piecework_log`` 的提醒）。
PERIOD_START = date(2026, 1, 1)
PERIOD_END = date(2026, 1, 31)
WORK_DATE = date(2026, 1, 5)


def reviewer_ctx(
    *,
    permissions: frozenset[str] = frozenset({APPROVE_PERMISSION, REVERSE_PERMISSION}),
    scope: DataScope = DataScope.FACTORY,
) -> AuthContext:
    """审核 / 反审核人上下文（全厂范围，权限含 ``payroll:approve`` + ``payroll:reverse``）。"""
    return AuthContext(
        user_id=REVIEWER_ID,
        name="主管",
        employee_no="A002",
        workshop_id=None,
        group_no=None,
        permissions=permissions,
        data_scope=scope,
    )


async def seed_approvable_settlement(
    session: Any,
    *,
    log_count: int = 3,
    status: DocumentStatus = DocumentStatus.SUBMITTED,
    created_by: UUID | None = None,
    total_amount: Decimal | None = None,
    line_count: int | None = None,
    period: PayrollPeriod | None = None,
    workshop_id: UUID | None = None,
    work_date: date = WORK_DATE,
) -> tuple[PieceworkSettlement, list[PieceworkLog], PayrollPeriod, UUID]:
    """造「可审核的结算单」：N 条未占用流水 + 逐条对应的行 + 汇总一致的单头。

    :returns: ``(结算单, 流水列表, 周期, 车间 id)``。
    """
    if period is None:
        period = await seed_period(
            session,
            period_code=f"AP-{uuid4().hex[:8]}",
            start_date=PERIOD_START,
            end_date=PERIOD_END,
        )
    workshop = workshop_id if workshop_id is not None else uuid4()
    logs = [
        await seed_piecework_log(session, work_date=work_date, workshop_id=workshop, group_no="G1")
        for _ in range(log_count)
    ]
    total_qty = sum((log.qty for log in logs), start=Decimal("0"))
    sum_amount = sum((log.amount for log in logs), start=Decimal("0"))
    settlement = settlement_row(
        period.id,
        workshop,
        doc_no=f"PW-20260131-{uuid4().hex[:6]}",
        status=status,
        created_by=created_by if created_by is not None else uuid4(),
        line_count=line_count if line_count is not None else log_count,
        total_qty=total_qty,
        total_amount=total_amount if total_amount is not None else sum_amount,
        employee_count=1,
        group_count=1,
    )
    session.add(settlement)
    await session.flush()
    for index, log in enumerate(logs, start=1):
        session.add(
            settlement_line_row(
                settlement.id,
                log.id,
                line_no=index,
                qty=log.qty,
                unit_price=log.unit_price,
                amount=log.amount,
                group_no="G1",
                work_date=work_date,
            )
        )
    await session.flush()
    return settlement, logs, period, workshop


async def payroll_ids_of_logs(session: Any, log_ids: list[UUID]) -> dict[UUID, UUID | None]:
    """直接读 ``piecework_logs.payroll_id`` 列（绕开 Core UPDATE 后 identity map 的陈旧值）。"""
    rows = await session.execute(
        select(PieceworkLog.id, PieceworkLog.payroll_id).where(PieceworkLog.id.in_(log_ids))
    )
    return {row.id: row.payroll_id for row in rows}


async def settlement_lines_of(session: Any, doc_id: UUID) -> list[PieceworkSettlementLine]:
    """本单有效行，按 ``line_no``。"""
    rows = await session.execute(
        select(PieceworkSettlementLine)
        .where(
            PieceworkSettlementLine.doc_id == doc_id,
            PieceworkSettlementLine.deleted_at.is_(None),
        )
        .order_by(PieceworkSettlementLine.line_no)
    )
    return list(rows.scalars().all())


async def settlement_logs_of(session: Any, doc_id: UUID) -> list[DocumentLog]:
    """本单 ``document_logs``，按写入顺序。"""
    rows = await session.execute(
        select(DocumentLog)
        .where(DocumentLog.doc_type == DOC_TYPE, DocumentLog.doc_id == doc_id)
        .order_by(DocumentLog.created_at)
    )
    return list(rows.scalars().all())


async def purge_committed_settlement(
    migration_url: str, settlement_id: UUID, period_id: UUID, log_ids: list[UUID]
) -> None:
    """并发用例**真提交**后，用迁移账号（``erp_ddl``）硬删自己造的那几行。

    ⚠️ 必须**硬删**而不是软删：同批跑的 ``test_payroll_settlement.py`` 里有不带
    ``deleted_at IS NULL`` 的 ``COUNT(*)``（``_settlement_count``），软删残留会让那些
    **毫不相干**的用例报「应为 0 实际 2」——报错点与真因隔了一层。erp_app 无 DELETE 权限，
    故走 ``DATABASE_URL_MIGRATION``（同 ``piecework_world_persisted`` / ``bundling_concurrency``
    的收尾口径）。删除顺序按外键：日志 → 行 → 流水 → 结算单 → 周期。
    """
    engine = create_async_engine(migration_url, pool_pre_ping=True)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM document_logs WHERE doc_type = :t AND doc_id = :d"),
                {"t": DOC_TYPE, "d": settlement_id},
            )
            await conn.execute(
                text("DELETE FROM piecework_settlement_lines WHERE doc_id = :d"),
                {"d": settlement_id},
            )
            for log_id in log_ids:
                await conn.execute(
                    text("DELETE FROM piecework_logs WHERE id = :id"), {"id": log_id}
                )
            await conn.execute(
                text("DELETE FROM piecework_settlements WHERE id = :d"), {"d": settlement_id}
            )
            await conn.execute(text("DELETE FROM payroll_periods WHERE id = :p"), {"p": period_id})
    finally:
        await engine.dispose()


__all__ = [
    "APPROVE_PERMISSION",
    "DOC_TYPE",
    "PERIOD_END",
    "PERIOD_START",
    "REVERSE_PERMISSION",
    "REVIEWER_ID",
    "WORK_DATE",
    "payroll_ids_of_logs",
    "purge_committed_settlement",
    "reviewer_ctx",
    "seed_approvable_settlement",
    "settlement_lines_of",
    "settlement_logs_of",
]
