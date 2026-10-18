"""结算周期状态机用例（T-PAY-003；``modules/05 §11`` TC-27）。

``close`` / ``reopen`` 的权限、必填原因、前置与乐观锁；``document_logs`` 的
``from_status`` / ``to_status`` / ``reason`` 三件套。与预演 / 建法的用例分开一个文件
（ADR-0030 的 400 行硬线，且「状态机」与「建法」是两件独立可验收的事）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.common.enums import DataScope
from app.common.models import DocumentLog
from app.core.errors import BusinessError, ErrorCode
from app.core.permissions import AuthContext
from app.modules.payroll.models import PayrollPeriodStatus
from app.modules.payroll.service import PayrollPeriodService
from tests.factories.payroll import seed_period, settlement_row
from tests.factories.user import OPERATOR_ID

PERIOD_MANAGE = "payroll:period:manage"
DOC_TYPE = "PayrollPeriod"


def _ctx(
    *,
    permissions: frozenset[str] = frozenset({PERIOD_MANAGE}),
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


async def _logs(session: Any, period_id: Any) -> list[DocumentLog]:
    rows = await session.execute(
        select(DocumentLog).where(DocumentLog.doc_type == DOC_TYPE, DocumentLog.doc_id == period_id)
    )
    return list(rows.scalars().all())


# ============================================================== TC-27


async def test_tc27_reopen_requires_reason(db_session) -> None:
    period = await seed_period(db_session, period_code="P-CL1", status=PayrollPeriodStatus.CLOSED)
    with pytest.raises(BusinessError) as exc:
        await PayrollPeriodService(db_session).reopen(
            period.id,
            reason="   ",
            expected_version=period.version,
            operator_id=OPERATOR_ID,
            ctx=_ctx(),
        )
    assert exc.value.code == ErrorCode.REASON_REQUIRED


async def test_tc27_reopen_requires_permission(db_session) -> None:
    period = await seed_period(db_session, period_code="P-CL2", status=PayrollPeriodStatus.CLOSED)
    with pytest.raises(BusinessError) as exc:
        await PayrollPeriodService(db_session).reopen(
            period.id,
            reason="边界设错",
            expected_version=period.version,
            operator_id=OPERATOR_ID,
            ctx=_ctx(permissions=frozenset()),
        )
    assert exc.value.code == ErrorCode.PERMISSION_DENIED


async def test_tc27_reopen_success_writes_log(db_session) -> None:
    period = await seed_period(
        db_session,
        period_code="P-CL3",
        status=PayrollPeriodStatus.CLOSED,
        closed_by=OPERATOR_ID,
        closed_at=datetime(2026, 9, 1, 9, 0, tzinfo=UTC),
        close_reason="上次关闭",
    )
    reopened = await PayrollPeriodService(db_session).reopen(
        period.id,
        reason="边界设错，漏了 8 月最后 3 天",
        expected_version=period.version,
        operator_id=OPERATOR_ID,
        ctx=_ctx(),
    )
    assert reopened.status == PayrollPeriodStatus.OPEN
    # §4.3：重开保留上次关闭痕迹
    assert reopened.close_reason == "上次关闭"

    logs = await _logs(db_session, period.id)
    assert len(logs) == 1
    assert logs[0].action == "REOPEN"
    assert logs[0].from_status == "CLOSED"
    assert logs[0].to_status == "OPEN"
    assert logs[0].reason == "边界设错，漏了 8 月最后 3 天"


async def test_tc27_reopen_on_open_period_10007(db_session) -> None:
    period = await seed_period(db_session, period_code="P-OPEN1", status=PayrollPeriodStatus.OPEN)
    with pytest.raises(BusinessError) as exc:
        await PayrollPeriodService(db_session).reopen(
            period.id,
            reason="不该能重开",
            expected_version=period.version,
            operator_id=OPERATOR_ID,
            ctx=_ctx(),
        )
    assert exc.value.code == ErrorCode.ILLEGAL_STATUS_TRANSITION


async def test_close_success_sets_fields_and_log(db_session) -> None:
    period = await seed_period(db_session, period_code="P-CLOSE", status=PayrollPeriodStatus.OPEN)
    closed = await PayrollPeriodService(db_session).close(
        period.id,
        reason="本期结算完毕",
        expected_version=period.version,
        operator_id=OPERATOR_ID,
        ctx=_ctx(),
    )
    assert closed.status == PayrollPeriodStatus.CLOSED
    assert closed.close_reason == "本期结算完毕"
    assert closed.closed_by == OPERATOR_ID
    assert closed.closed_at is not None

    logs = await _logs(db_session, period.id)
    assert len(logs) == 1
    assert logs[0].action == "CLOSE"
    assert logs[0].from_status == "OPEN"
    assert logs[0].to_status == "CLOSED"
    assert logs[0].reason == "本期结算完毕"


async def test_close_blocked_by_draft_settlement(db_session) -> None:
    """``close`` 前置：区间内仍有草稿/已提交结算单时拒绝（05 §4.3）。"""
    period = await seed_period(db_session, period_code="P-CLOSE2", status=PayrollPeriodStatus.OPEN)
    db_session.add(settlement_row(period.id, uuid4()))
    await db_session.flush()
    with pytest.raises(BusinessError) as exc:
        await PayrollPeriodService(db_session).close(
            period.id,
            reason="结算完再关",
            expected_version=period.version,
            operator_id=OPERATOR_ID,
            ctx=_ctx(),
        )
    assert exc.value.code == ErrorCode.PAYROLL_PERIOD_CLOSED


async def test_close_optimistic_lock_10003(db_session) -> None:
    """乐观锁：过期 ``expected_version`` → ``10003``（08 R3）。"""
    period = await seed_period(db_session, period_code="P-CLOSE3", status=PayrollPeriodStatus.OPEN)
    with pytest.raises(BusinessError) as exc:
        await PayrollPeriodService(db_session).close(
            period.id,
            reason="并发关闭",
            expected_version=period.version + 1,
            operator_id=OPERATOR_ID,
            ctx=_ctx(),
        )
    assert exc.value.code == ErrorCode.OPTIMISTIC_LOCK_CONFLICT
