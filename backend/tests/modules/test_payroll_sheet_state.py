"""工资单**轻状态迁移**用例（T-PAY-006）。

========================  =========================================================
submit 成功               ``DRAFT`` → ``SUBMITTED`` + ``document_logs``
submit 空明细             拒绝 ``10008``
submit 乐观锁             ``version`` 不符 → ``10003``
withdraw                 本人可撤；非制单人 ``12001``
reject                   缺原因 ``10006``；带原因落 ``rejected_reason``
cancel                   带原因落 ``cancelled_reason``；终态再迁移 ``10007``（08 R6）
update                   白名单 ``doc_date``/``remark`` + 字段级 diff 日志；非可编辑态拒绝
========================  =========================================================

生成与取数用例见同目录 ``test_payroll_sheet.py``（ADR-0030 单文件 400 行硬线，拆两份）。
本卡无 router（归 T-PAY-009），故直接调 service。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select, update

from app.common.enums import DataScope, DocumentStatus
from app.common.models import DocumentLog
from app.core.errors import BusinessError, ErrorCode
from app.core.permissions import AuthContext
from app.modules.payroll.models import PayrollSheet
from app.modules.payroll.service import PayrollSheetService
from tests.factories.payroll import seed_approved_settlement, sheet_row
from tests.factories.user import OPERATOR_ID

SETTLE = "payroll:settle"
APPROVE = "payroll:approve"
DOC_DATE = date(2099, 12, 31)
DOC_TYPE = "PayrollSheet"


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


def _spec(employee_id: Any, qty: str = "10", price: str = "0.500000") -> dict[str, Any]:
    return {
        "employee_id": employee_id,
        "style_no": "ST-STATE",
        "operation_no": "01",
        "qty": Decimal(qty),
        "unit_price": Decimal(price),
    }


async def _draft_sheet(session: Any) -> PayrollSheet:
    settlement = await seed_approved_settlement(session, specs=[_spec(uuid4())])
    return await PayrollSheetService(session).create(
        settlement_id=settlement.id, ctx=_ctx(), doc_date=DOC_DATE
    )


async def _logs(session: Any, sheet_id: Any) -> list[DocumentLog]:
    rows = await session.execute(
        select(DocumentLog).where(DocumentLog.doc_type == DOC_TYPE, DocumentLog.doc_id == sheet_id)
    )
    return list(rows.scalars().all())


# ============================================================== submit


async def test_submit_success_writes_log(db_session) -> None:
    sheet = await _draft_sheet(db_session)
    original_version = sheet.version
    submitted = await PayrollSheetService(db_session).submit(
        sheet.id, expected_version=original_version, operator_id=OPERATOR_ID, ctx=_ctx()
    )
    assert submitted.status is DocumentStatus.SUBMITTED
    assert submitted.version == original_version + 1
    logs = await _logs(db_session, sheet.id)
    assert [(item.action, item.from_status, item.to_status) for item in logs] == [
        ("CREATE", None, "DRAFT"),
        ("SUBMIT", "DRAFT", "SUBMITTED"),
    ]


async def test_submit_empty_details_rejected(db_session) -> None:
    settlement = await seed_approved_settlement(db_session, specs=[])
    sheet = await PayrollSheetService(db_session).create(
        settlement_id=settlement.id, ctx=_ctx(), doc_date=DOC_DATE
    )
    with pytest.raises(BusinessError) as exc:
        await PayrollSheetService(db_session).submit(
            sheet.id, expected_version=sheet.version, operator_id=OPERATOR_ID, ctx=_ctx()
        )
    assert exc.value.code == ErrorCode.ILLEGAL_OPERATION
    refreshed = await db_session.get(PayrollSheet, sheet.id, populate_existing=True)
    assert refreshed is not None and refreshed.status is DocumentStatus.DRAFT


async def test_submit_optimistic_lock_10003(db_session) -> None:
    sheet = await _draft_sheet(db_session)
    with pytest.raises(BusinessError) as exc:
        await PayrollSheetService(db_session).submit(
            sheet.id, expected_version=sheet.version + 1, operator_id=OPERATOR_ID, ctx=_ctx()
        )
    assert exc.value.code == ErrorCode.OPTIMISTIC_LOCK_CONFLICT


async def test_submit_requires_permission_12001(db_session) -> None:
    sheet = await _draft_sheet(db_session)
    with pytest.raises(BusinessError) as exc:
        await PayrollSheetService(db_session).submit(
            sheet.id,
            expected_version=sheet.version,
            operator_id=OPERATOR_ID,
            ctx=_ctx(permissions=frozenset()),
        )
    assert exc.value.code == ErrorCode.PERMISSION_DENIED


# ============================================================== withdraw


async def test_withdraw_by_creator_success(db_session) -> None:
    sheet = await _draft_sheet(db_session)
    service = PayrollSheetService(db_session)
    await service.submit(
        sheet.id, expected_version=sheet.version, operator_id=OPERATOR_ID, ctx=_ctx()
    )
    current = await db_session.get(PayrollSheet, sheet.id, populate_existing=True)
    assert current is not None
    withdrawn = await service.withdraw(
        sheet.id, expected_version=current.version, operator_id=OPERATOR_ID, ctx=_ctx()
    )
    assert withdrawn.status is DocumentStatus.DRAFT


async def test_withdraw_non_creator_12001(db_session) -> None:
    settlement = await seed_approved_settlement(db_session, specs=[_spec(uuid4())])
    sheet = sheet_row(
        settlement, status=DocumentStatus.SUBMITTED, created_by=uuid4(), doc_no="PR-20991231-900001"
    )
    db_session.add(sheet)
    await db_session.flush()
    with pytest.raises(BusinessError) as exc:
        await PayrollSheetService(db_session).withdraw(
            sheet.id, expected_version=sheet.version, operator_id=OPERATOR_ID, ctx=_ctx()
        )
    assert exc.value.code == ErrorCode.PERMISSION_DENIED


# ============================================================== reject / cancel


async def test_reject_requires_reason_10006(db_session) -> None:
    sheet = await _draft_sheet(db_session)
    service = PayrollSheetService(db_session)
    await service.submit(
        sheet.id, expected_version=sheet.version, operator_id=OPERATOR_ID, ctx=_ctx()
    )
    current = await db_session.get(PayrollSheet, sheet.id, populate_existing=True)
    assert current is not None
    with pytest.raises(BusinessError) as exc:
        await service.reject(
            sheet.id,
            reason="   ",
            expected_version=current.version,
            operator_id=OPERATOR_ID,
            ctx=_ctx(),
        )
    assert exc.value.code == ErrorCode.REASON_REQUIRED


async def test_reject_success_sets_reason(db_session) -> None:
    sheet = await _draft_sheet(db_session)
    service = PayrollSheetService(db_session)
    await service.submit(
        sheet.id, expected_version=sheet.version, operator_id=OPERATOR_ID, ctx=_ctx()
    )
    current = await db_session.get(PayrollSheet, sheet.id, populate_existing=True)
    assert current is not None
    rejected = await service.reject(
        sheet.id,
        reason="银行资料缺失",
        expected_version=current.version,
        operator_id=OPERATOR_ID,
        ctx=_ctx(),
    )
    assert rejected.status is DocumentStatus.REJECTED
    assert rejected.rejected_reason == "银行资料缺失"


async def test_cancel_terminal_rejects_further_transitions(db_session) -> None:
    sheet = await _draft_sheet(db_session)
    service = PayrollSheetService(db_session)
    cancelled = await service.cancel(
        sheet.id,
        reason="误生成",
        expected_version=sheet.version,
        operator_id=OPERATOR_ID,
        ctx=_ctx(),
    )
    assert cancelled.status is DocumentStatus.CANCELLED
    assert cancelled.cancelled_reason == "误生成"
    with pytest.raises(BusinessError) as exc:
        await service.submit(
            sheet.id, expected_version=cancelled.version, operator_id=OPERATOR_ID, ctx=_ctx()
        )
    assert exc.value.code == ErrorCode.ILLEGAL_STATUS_TRANSITION


# ============================================================== update


async def test_update_whitelist_writes_diff_log(db_session) -> None:
    sheet = await _draft_sheet(db_session)
    updated = await PayrollSheetService(db_session).update(
        sheet.id,
        expected_version=sheet.version,
        operator_id=OPERATOR_ID,
        ctx=_ctx(),
        doc_date=date(2099, 12, 30),
        remark="工资单备注",
    )
    assert updated.status is DocumentStatus.DRAFT
    assert updated.doc_date == date(2099, 12, 30)
    assert updated.remark == "工资单备注"
    logs = await _logs(db_session, sheet.id)
    upd = [item for item in logs if item.action == "UPDATE"]
    assert len(upd) == 1
    assert upd[0].changed_fields is not None
    assert set(upd[0].changed_fields) == {"doc_date", "remark"}


async def test_update_on_submitted_rejected_10007(db_session) -> None:
    sheet = await _draft_sheet(db_session)
    service = PayrollSheetService(db_session)
    await service.submit(
        sheet.id, expected_version=sheet.version, operator_id=OPERATOR_ID, ctx=_ctx()
    )
    current = await db_session.get(PayrollSheet, sheet.id, populate_existing=True)
    assert current is not None
    with pytest.raises(BusinessError) as exc:
        await service.update(
            sheet.id,
            expected_version=current.version,
            operator_id=OPERATOR_ID,
            ctx=_ctx(),
            remark="改不了",
        )
    assert exc.value.code == ErrorCode.ILLEGAL_STATUS_TRANSITION


async def test_submit_unknown_sheet_20001(db_session) -> None:
    with pytest.raises(BusinessError) as exc:
        await PayrollSheetService(db_session).submit(
            uuid4(), expected_version=1, operator_id=OPERATOR_ID, ctx=_ctx()
        )
    assert exc.value.code == ErrorCode.BASE_DATA_NOT_FOUND


async def test_update_without_changes_is_noop(db_session) -> None:
    """无实际变更 → 不写日志、不 +1 版本。"""
    sheet = await _draft_sheet(db_session)
    same = await PayrollSheetService(db_session).update(
        sheet.id, expected_version=sheet.version, operator_id=OPERATOR_ID, ctx=_ctx()
    )
    assert same.version == sheet.version
    assert [item for item in await _logs(db_session, sheet.id) if item.action == "UPDATE"] == []


async def test_update_recalculates_header_amounts(db_session) -> None:
    """带外篡改表头金额后 ``update`` 按明细重算并记入 diff（05 §4.2）。"""
    sheet = await _draft_sheet(db_session)
    await db_session.execute(
        update(PayrollSheet)
        .where(PayrollSheet.id == sheet.id)
        .values(
            total_gross=Decimal("999.0000"),
            total_deduction=Decimal("0.0000"),
            total_net=Decimal("999.0000"),
        )
    )
    await db_session.flush()
    current = await db_session.get(PayrollSheet, sheet.id, populate_existing=True)
    assert current is not None

    updated = await PayrollSheetService(db_session).update(
        sheet.id,
        expected_version=current.version,
        operator_id=OPERATOR_ID,
        ctx=_ctx(),
        remark="修复表头",
    )
    assert updated.total_gross == Decimal("5.0000")
    upd = [item for item in await _logs(db_session, sheet.id) if item.action == "UPDATE"]
    assert len(upd) == 1
    assert "total_gross" in (upd[0].changed_fields or {})
