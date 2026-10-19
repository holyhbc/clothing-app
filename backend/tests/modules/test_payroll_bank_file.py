"""工资单**代发文件导出**的边界分支（T-PAY-008；T-PAY-009 路由的前置 service 行为）。

===============  ====================================================================
—                权限 ``12001``：无 ``payroll:pay`` / ``payroll:export`` 直接拒绝
—                数据范围 ``12002``：越权导出不生成代发文件
—                草稿导出 ``10008``：金额未冻结，不出代发文件
—                唯一冲突回退：并发撞 ``uq_bank_payment_files_sheet_id`` 时重查既有行
===============  ====================================================================

主用例（TC-07/09、TC-PAY-PAY01/02）见 ``test_payroll_pay.py``；共用造数见
``tests/factories/payroll_pay.py``（ADR-0030 单文件 400 行硬线，拆两份）。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from app.common.enums import DataScope
from app.core.errors import BusinessError, ErrorCode
from app.modules.payroll.models import BankPaymentFile
from app.modules.payroll.service import PayrollSheetService
from tests.factories.payroll import seed_approved_settlement
from tests.factories.payroll_pay import (
    DOC_DATE,
    EXPORTER_ID,
    PAYER_ID,
    approved_sheet,
    bank_file_count,
    export_ctx,
    pay_ctx,
    settle_ctx,
    spec,
    user_with_bank,
)


async def test_pay_requires_permission_12001(db_session) -> None:
    employee = await user_with_bank(db_session, employee_no="A905", name="孙八")
    sheet = await approved_sheet(
        db_session, specs=[spec(employee.id, "ST-PAY-1", "01", "10", "0.500000")]
    )
    with pytest.raises(BusinessError) as exc:
        await PayrollSheetService(db_session).pay(
            sheet.id,
            expected_version=sheet.version,
            operator_id=PAYER_ID,
            ctx=pay_ctx(permissions=frozenset()),
        )
    assert exc.value.code == ErrorCode.PERMISSION_DENIED


async def test_export_requires_permission_12001(db_session) -> None:
    employee = await user_with_bank(db_session, employee_no="A906", name="周九")
    sheet = await approved_sheet(
        db_session, specs=[spec(employee.id, "ST-PAY-1", "01", "10", "0.500000")]
    )
    no_permission = export_ctx(permissions=frozenset())
    with pytest.raises(BusinessError) as exc:
        await PayrollSheetService(db_session).bank_file_export(sheet.id, ctx=no_permission)
    assert exc.value.code == ErrorCode.PERMISSION_DENIED


async def test_export_out_of_scope_12002(db_session) -> None:
    employee = await user_with_bank(db_session, employee_no="A907", name="吴十")
    sheet = await approved_sheet(
        db_session, specs=[spec(employee.id, "ST-PAY-1", "01", "10", "0.500000")]
    )
    out_of_scope = export_ctx(scope=DataScope.WORKSHOP, workshop_id=None)
    with pytest.raises(BusinessError) as exc:
        await PayrollSheetService(db_session).bank_file_export(sheet.id, ctx=out_of_scope)
    assert exc.value.code == ErrorCode.DATA_SCOPE_DENIED
    assert await bank_file_count(db_session, sheet.id) == 0, "越权导出不得生成代发文件"


async def test_export_draft_sheet_10008(db_session) -> None:
    """草稿（未审核）工资单不能出代发文件：金额未冻结（``10008``），且不落记录。"""
    employee = await user_with_bank(db_session, employee_no="A908", name="郑十一")
    settlement = await seed_approved_settlement(
        db_session,
        specs=[spec(employee.id, "ST-PAY-1", "01", "10", "0.500000")],
        created_by=uuid4(),
    )
    sheet = await PayrollSheetService(db_session).create(
        settlement_id=settlement.id, ctx=settle_ctx(), doc_date=DOC_DATE
    )
    with pytest.raises(BusinessError) as exc:
        await PayrollSheetService(db_session).bank_file_export(sheet.id, ctx=export_ctx())
    assert exc.value.code == ErrorCode.ILLEGAL_OPERATION
    assert await bank_file_count(db_session, sheet.id) == 0


async def test_ensure_bank_file_integrity_conflict_falls_back(db_session, monkeypatch: Any) -> None:
    """并发下撞 ``uq_bank_payment_files_sheet_id``：重查返回既有行，不抛 500。"""
    employee = await user_with_bank(db_session, employee_no="A909", name="冯十二")
    sheet = await approved_sheet(
        db_session, specs=[spec(employee.id, "ST-PAY-1", "01", "10", "0.500000")]
    )
    service = PayrollSheetService(db_session)
    details = await service._load_details(sheet.id)
    existing, content = await service._ensure_bank_file(sheet, details, EXPORTER_ID)
    real_find = service._find_bank_file
    calls = {"n": 0}

    async def flaky_find(sheet_id: UUID) -> BankPaymentFile | None:
        calls["n"] += 1
        if calls["n"] == 1:
            return None  # 模拟并发：第一次查不到 → 去插入 → 撞唯一
        return await real_find(sheet_id)

    monkeypatch.setattr(service, "_find_bank_file", flaky_find)
    record, content2 = await service._ensure_bank_file(sheet, details, EXPORTER_ID)
    assert record.id == existing.id, "撞唯一后必须重查返回既有行"
    assert content2 == content
    assert calls["n"] == 2


async def test_load_users_empty_set_returns_empty(db_session) -> None:
    """``_load_users(set())`` 不得退回「查全表」（防御性早返回）。"""
    assert await PayrollSheetService(db_session)._load_users(set()) == {}
