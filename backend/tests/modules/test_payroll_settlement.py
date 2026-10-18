"""计件结算单**生成与取数**用例（T-PAY-004；``modules/05 §11`` TC-01/02/25）。

======================================  ==================================================
TC-01                                   跨 2 组别 10 条未结算流水 → 1 张 PW、一流水一行
TC-02                                   幂等返回既有单 / 并发唯一约束 → ``33002``
TC-25                                   ``CLOSED`` 周期 ``create`` → ``33003``
======================================  ==================================================

轻状态迁移（``submit`` / ``withdraw`` / ``reject`` / ``cancel``）见同目录
``test_payroll_settlement_state.py``（ADR-0030 的单文件 400 行硬线，拆两份）。

## 本文件刻意不走 HTTP

本卡**没有 router**（归 T-PAY-009），故直接调 service。鉴权用构造的 ``AuthContext``。

## 为什么区间用 2026-01 而不是今天

真提交的并发用例（``test_piecework_count_concurrency``）落的是 ``business_today()``
的流水且不一定清理（L-035）。取数按 ``work_date``，改用确定的历史区间即可隔离。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.common.enums import DataScope, DocumentStatus
from app.core.errors import BusinessError, ErrorCode
from app.core.permissions import AuthContext
from app.modules.payroll.models import (
    PayrollPeriodStatus,
    PieceworkSettlement,
    PieceworkSettlementLine,
)
from app.modules.payroll.service import PieceworkSettlementService
from tests.factories.payroll import (
    seed_period,
    seed_piecework_log,
    settlement_line_row,
    settlement_row,
)
from tests.factories.user import OPERATOR_ID

SETTLE = "payroll:settle"
APPROVE = "payroll:approve"

P_START = date(2026, 1, 1)
P_END = date(2026, 1, 31)
DOC_DATE = date(2026, 1, 31)


def _ctx(
    *,
    permissions: frozenset[str] = frozenset({SETTLE, APPROVE}),
    scope: DataScope = DataScope.FACTORY,
) -> AuthContext:
    return AuthContext(
        user_id=OPERATOR_ID,
        name="计件员",
        employee_no="A001",
        workshop_id=None,
        group_no=None,
        permissions=permissions,
        data_scope=scope,
    )


async def _period(session: Any, **over: Any) -> Any:
    base: dict[str, Any] = {
        "period_code": f"P-{uuid4().hex[:8]}",
        "start_date": P_START,
        "end_date": P_END,
    }
    base.update(over)
    return await seed_period(session, **base)


async def _lines(session: Any, doc_id: UUID) -> list[PieceworkSettlementLine]:
    rows = await session.execute(
        select(PieceworkSettlementLine)
        .where(PieceworkSettlementLine.doc_id == doc_id)
        .order_by(PieceworkSettlementLine.line_no)
    )
    return list(rows.scalars().all())


async def _settlement_count(session: Any) -> int:
    return (await session.execute(select(func.count(PieceworkSettlement.id)))).scalar_one()


async def _seed_amount_log(session: Any, workshop_id: UUID, **over: Any) -> Any:
    """一条落在 P_START~P_END、件数/金额干净的流水（10 件 × 0.5 = 5 元）。"""
    base: dict[str, Any] = {
        "work_date": date(2026, 1, 5),
        "workshop_id": workshop_id,
        "group_no": "G1",
        "qty": Decimal("10"),
        "unit_price": Decimal("0.500000"),
        "amount": Decimal("5.0000"),
    }
    base.update(over)
    return await seed_piecework_log(session, **base)


# ============================================================== TC-01


async def test_tc01_generate_settlement_cross_two_groups(db_session) -> None:
    """跨 2 组别 10 条未结算流水 → 1 张 PW、行数=10、单头合计=行求和。"""
    period = await _period(db_session)
    workshop = uuid4()
    emp_a, emp_b = uuid4(), uuid4()
    for day in range(5, 11):  # 6 条 G1
        await _seed_amount_log(
            db_session, workshop, work_date=date(2026, 1, day), group_no="G1", employee_id=emp_a
        )
    for day in range(5, 9):  # 4 条 G2
        await _seed_amount_log(
            db_session, workshop, work_date=date(2026, 1, day), group_no="G2", employee_id=emp_b
        )
    # 干扰项：区间外 1 条 + 软删 1 条 + 已结算 1 条（都不该被取到）
    await _seed_amount_log(db_session, workshop, work_date=date(2025, 12, 31))
    await _seed_amount_log(
        db_session,
        workshop,
        work_date=date(2026, 1, 15),
        deleted_at=datetime(2026, 1, 16, tzinfo=UTC),
    )
    # 已结算的流水指向「同周期、另一车间」的结算单（唯一键是 周期+车间，故可并存）
    other_settlement = settlement_row(period.id, uuid4())
    db_session.add(other_settlement)
    await db_session.flush()
    await _seed_amount_log(
        db_session, workshop, work_date=date(2026, 1, 20), payroll_id=other_settlement.id
    )

    header = await PieceworkSettlementService(db_session).create(
        period_id=period.id, workshop_id=workshop, ctx=_ctx(), doc_date=DOC_DATE
    )
    assert header.doc_no.startswith("PW-20260131-")
    assert header.status is DocumentStatus.DRAFT
    assert header.line_count == 10
    assert header.group_count == 2
    assert header.employee_count == 2
    assert header.total_qty == Decimal("100")
    assert header.total_amount == Decimal("50.0000")
    assert (header.period_code, header.period_start_date, header.period_end_date) == (
        period.period_code,
        P_START,
        P_END,
    )

    lines = await _lines(db_session, header.id)
    assert [line.line_no for line in lines] == list(range(1, 11))
    assert len({line.piecework_log_id for line in lines}) == 10
    assert sum((line.qty for line in lines), start=Decimal("0")) == header.total_qty
    assert sum((line.amount for line in lines), start=Decimal("0")) == header.total_amount
    for line in lines:
        assert line.group_no in {"G1", "G2"}
        assert line.unit_price == Decimal("0.500000")
        assert line.amount == line.qty * line.unit_price


# ============================================================== TC-02


async def test_tc02_idempotent_returns_existing_without_new_lines(db_session) -> None:
    """同「周期+车间」再 ``create`` → 幂等返回既有单，不重复写行。"""
    period = await _period(db_session)
    workshop = uuid4()
    await _seed_amount_log(db_session, workshop)
    service = PieceworkSettlementService(db_session)

    first = await service.create(
        period_id=period.id, workshop_id=workshop, ctx=_ctx(), doc_date=DOC_DATE
    )
    again = await service.create(
        period_id=period.id, workshop_id=workshop, ctx=_ctx(), doc_date=DOC_DATE
    )
    assert again.id == first.id
    assert again.doc_no == first.doc_no
    assert len(await _lines(db_session, first.id)) == 1
    assert await _settlement_count(db_session) == 1


async def test_tc02_duplicate_race_translated_to_33002(db_session, monkeypatch) -> None:
    """并发下 ``_find_existing`` 落空、唯一约束兜底 → ``33002``（P1）。"""
    period = await _period(db_session)
    workshop = uuid4()
    await _seed_amount_log(db_session, workshop)
    service = PieceworkSettlementService(db_session)
    await service.create(period_id=period.id, workshop_id=workshop, ctx=_ctx(), doc_date=DOC_DATE)

    async def _none(self: PieceworkSettlementService, period_id: UUID, workshop_id: UUID) -> None:
        return None

    monkeypatch.setattr(PieceworkSettlementService, "_find_existing", _none)
    with pytest.raises(BusinessError) as exc:
        await service.create(
            period_id=period.id, workshop_id=workshop, ctx=_ctx(), doc_date=DOC_DATE
        )
    assert exc.value.code == ErrorCode.SETTLEMENT_PERIOD_DUPLICATED


# ============================================================== TC-25


async def test_tc25_create_on_closed_period_33003(db_session) -> None:
    period = await _period(db_session, status=PayrollPeriodStatus.CLOSED)
    with pytest.raises(BusinessError) as exc:
        await PieceworkSettlementService(db_session).create(
            period_id=period.id, workshop_id=uuid4(), ctx=_ctx(), doc_date=DOC_DATE
        )
    assert exc.value.code == ErrorCode.PAYROLL_PERIOD_CLOSED
    assert await _settlement_count(db_session) == 0


# ============================================================== 权限 / 数据范围


async def test_create_requires_settle_permission_12001(db_session) -> None:
    period = await _period(db_session)
    with pytest.raises(BusinessError) as exc:
        await PieceworkSettlementService(db_session).create(
            period_id=period.id,
            workshop_id=uuid4(),
            ctx=_ctx(permissions=frozenset()),
            doc_date=DOC_DATE,
        )
    assert exc.value.code == ErrorCode.PERMISSION_DENIED


async def test_create_workshop_out_of_scope_12002(db_session) -> None:
    period = await _period(db_session)
    with pytest.raises(BusinessError) as exc:
        await PieceworkSettlementService(db_session).create(
            period_id=period.id,
            workshop_id=uuid4(),
            ctx=_ctx(scope=DataScope.WORKSHOP),
            doc_date=DOC_DATE,
        )
    assert exc.value.code == ErrorCode.DATA_SCOPE_DENIED


# ============================================================== P3 兜底


async def test_line_piecework_log_id_unique(db_session) -> None:
    """一条流水只能被一张结算单占用（迁移 0020 的唯一约束，INV-2）。"""
    period = await _period(db_session)
    workshop = uuid4()
    log = await _seed_amount_log(db_session, workshop)
    settlement = settlement_row(period.id, workshop)
    db_session.add(settlement)
    await db_session.flush()
    db_session.add(settlement_line_row(settlement.id, log.id, line_no=1))
    await db_session.flush()
    db_session.add(settlement_line_row(settlement.id, log.id, line_no=2))
    with pytest.raises(IntegrityError):
        await db_session.flush()
