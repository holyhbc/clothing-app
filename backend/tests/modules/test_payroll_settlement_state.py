"""计件结算单**轻状态迁移**用例（T-PAY-004；``modules/05 §11`` TC-25 提交 / TC-PAY-S01/S02）。

================  ==================================================================
TC-25             ``CLOSED`` 周期 ``submit`` → ``33003``
TC-PAY-S01        ``submit`` 金额 ≤ 0 → 拒绝
TC-PAY-S02        ``cancel`` 终态再迁移 → 拒绝（08 R6）
—                 ``reject`` 缺原因 ``10006`` / 乐观锁 ``10003`` / 撤回人 = 制单人
================  ==================================================================

生成与取数用例见同目录 ``test_payroll_settlement.py``（单文件 400 行硬线，拆两份）。

## 本文件刻意不走 HTTP

本卡**没有 router**（归 T-PAY-009），故直接调 service；鉴权用构造的 ``AuthContext``。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.common.enums import DataScope, DocumentStatus
from app.common.models import DocumentLog
from app.core.errors import BusinessError, ErrorCode
from app.core.permissions import AuthContext
from app.modules.payroll.models import PayrollPeriodStatus, PieceworkSettlement
from app.modules.payroll.service import PieceworkSettlementService
from tests.factories.payroll import seed_period, settlement_row
from tests.factories.user import OPERATOR_ID

SETTLE = "payroll:settle"
APPROVE = "payroll:approve"
DOC_TYPE = "PieceworkSettlement"

P_START = date(2026, 1, 1)
P_END = date(2026, 1, 31)


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


async def _logs(session: Any, settlement_id: Any) -> list[DocumentLog]:
    rows = await session.execute(
        select(DocumentLog).where(
            DocumentLog.doc_type == DOC_TYPE, DocumentLog.doc_id == settlement_id
        )
    )
    return list(rows.scalars().all())


async def _seed_settlement(session: Any, **over: Any) -> PieceworkSettlement:
    period = await _period(session)
    settlement = settlement_row(period.id, uuid4(), **over)
    session.add(settlement)
    await session.flush()
    return settlement


# ============================================================== 提交


async def test_tc25_submit_on_closed_period_33003(db_session) -> None:
    period = await _period(db_session, status=PayrollPeriodStatus.CLOSED)
    settlement = settlement_row(period.id, uuid4())
    db_session.add(settlement)
    await db_session.flush()
    with pytest.raises(BusinessError) as exc:
        await PieceworkSettlementService(db_session).submit(
            settlement.id, expected_version=settlement.version, operator_id=OPERATOR_ID, ctx=_ctx()
        )
    assert exc.value.code == ErrorCode.PAYROLL_PERIOD_CLOSED


async def test_submit_success_writes_log(db_session) -> None:
    settlement = await _seed_settlement(db_session)
    original_version = settlement.version
    submitted = await PieceworkSettlementService(db_session).submit(
        settlement.id, expected_version=original_version, operator_id=OPERATOR_ID, ctx=_ctx()
    )
    assert submitted.status is DocumentStatus.SUBMITTED
    assert submitted.version == original_version + 1
    logs = await _logs(db_session, settlement.id)
    assert len(logs) == 1
    assert (logs[0].action, logs[0].from_status, logs[0].to_status) == (
        "SUBMIT",
        "DRAFT",
        "SUBMITTED",
    )


async def test_tc_pay_s01_submit_zero_amount_rejected(db_session) -> None:
    settlement = await _seed_settlement(db_session, total_amount=Decimal("0"))
    with pytest.raises(BusinessError) as exc:
        await PieceworkSettlementService(db_session).submit(
            settlement.id, expected_version=settlement.version, operator_id=OPERATOR_ID, ctx=_ctx()
        )
    assert exc.value.code == ErrorCode.SETTLEMENT_PERIOD_DUPLICATED
    refreshed = await db_session.get(PieceworkSettlement, settlement.id)
    assert refreshed is not None and refreshed.status is DocumentStatus.DRAFT
    assert await _logs(db_session, settlement.id) == []


async def test_submit_optimistic_lock_10003(db_session) -> None:
    settlement = await _seed_settlement(db_session)
    with pytest.raises(BusinessError) as exc:
        await PieceworkSettlementService(db_session).submit(
            settlement.id,
            expected_version=settlement.version + 1,
            operator_id=OPERATOR_ID,
            ctx=_ctx(),
        )
    assert exc.value.code == ErrorCode.OPTIMISTIC_LOCK_CONFLICT


# ============================================================== 撤回


async def test_withdraw_by_creator_success(db_session) -> None:
    settlement = await _seed_settlement(
        db_session, status=DocumentStatus.SUBMITTED, created_by=OPERATOR_ID
    )
    withdrawn = await PieceworkSettlementService(db_session).withdraw(
        settlement.id, expected_version=settlement.version, operator_id=OPERATOR_ID, ctx=_ctx()
    )
    assert withdrawn.status is DocumentStatus.DRAFT


async def test_withdraw_non_creator_12001(db_session) -> None:
    settlement = await _seed_settlement(db_session, status=DocumentStatus.SUBMITTED)
    with pytest.raises(BusinessError) as exc:
        await PieceworkSettlementService(db_session).withdraw(
            settlement.id, expected_version=settlement.version, operator_id=OPERATOR_ID, ctx=_ctx()
        )
    assert exc.value.code == ErrorCode.PERMISSION_DENIED


# ============================================================== 驳回


async def test_reject_requires_reason_10006(db_session) -> None:
    settlement = await _seed_settlement(db_session, status=DocumentStatus.SUBMITTED)
    with pytest.raises(BusinessError) as exc:
        await PieceworkSettlementService(db_session).reject(
            settlement.id,
            reason="   ",
            expected_version=settlement.version,
            operator_id=OPERATOR_ID,
            ctx=_ctx(),
        )
    assert exc.value.code == ErrorCode.REASON_REQUIRED


async def test_reject_success_sets_reason(db_session) -> None:
    settlement = await _seed_settlement(db_session, status=DocumentStatus.SUBMITTED)
    rejected = await PieceworkSettlementService(db_session).reject(
        settlement.id,
        reason="车间报错，重算",
        expected_version=settlement.version,
        operator_id=OPERATOR_ID,
        ctx=_ctx(),
    )
    assert rejected.status is DocumentStatus.REJECTED
    assert rejected.rejected_reason == "车间报错，重算"


# ============================================================== 作废 / 终态


async def test_cancel_success_writes_reason(db_session) -> None:
    settlement = await _seed_settlement(db_session)
    cancelled = await PieceworkSettlementService(db_session).cancel(
        settlement.id,
        reason="周期选错",
        expected_version=settlement.version,
        operator_id=OPERATOR_ID,
        ctx=_ctx(),
    )
    assert cancelled.status is DocumentStatus.CANCELLED
    assert cancelled.cancelled_reason == "周期选错"


async def test_tc_pay_s02_cancel_terminal_rejects(db_session) -> None:
    settlement = await _seed_settlement(
        db_session, status=DocumentStatus.CANCELLED, cancelled_reason="误建"
    )
    service = PieceworkSettlementService(db_session)
    with pytest.raises(BusinessError) as exc:
        await service.submit(
            settlement.id, expected_version=settlement.version, operator_id=OPERATOR_ID, ctx=_ctx()
        )
    assert exc.value.code == ErrorCode.ILLEGAL_STATUS_TRANSITION
    with pytest.raises(BusinessError) as exc2:
        await service.cancel(
            settlement.id,
            reason="再作废",
            expected_version=settlement.version,
            operator_id=OPERATOR_ID,
            ctx=_ctx(),
        )
    assert exc2.value.code == ErrorCode.ILLEGAL_STATUS_TRANSITION
