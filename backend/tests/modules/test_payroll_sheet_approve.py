"""工资单**审核 / 反审核 + 计提凭证收敛点**用例（T-PAY-007）。

设计权威：``docs/modules/05-工资.md`` §4.2、§5.4、§5.6、§11（TC-06/TC-08）；
``P2-工资-实施说明 §8 Q-PL09``（选项 A：解耦 + 单一 ``VoucherPort`` 收敛点）。

================  ====================================================================
TC-08             对 ``PAID`` 工资单 ``reverse`` → ``33001``，状态 / 凭证不动
TC-PAY-V01        ``approve`` 调用 ``VoucherPort.create_accrual`` **恰好一次**
TC-PAY-V02        空适配器下 ``accrual_voucher_no`` 为 ``NULL`` 且不抛异常
TC-PAY-V03        ``reverse`` 调用 ``reverse_accrual`` 一次
TC-PAY-V04        并发 ``approve`` vs ``reverse`` → 一成一败 ``10003``，无中间态
—                 自审 ``10005`` / ``total_net`` 不符拒绝 / 缺原因 ``10006`` / 权限 ``12001``
================  ====================================================================

⚠️ **TC-06（凭证借贷平衡）标「待 M9」**：本卡只测 VoucherPort 契约（调用点 / 返回值），
**不**断言真实凭证借贷 —— M9 财务凭证模块尚未建表（Q-PL09 选项 A）。

并发用例用 ``asyncio.Event`` 固定顺序：``approve``（起始 ``SUBMITTED``）先提交成功后，
``reverse`` 带同一 ``expected_version`` 进来被乐观锁挡下（若 reverse 先读会报 ``10007``，
那是另一条防线）。本卡无 router（归 T-PAY-009），直接调 service；鉴权用构造 ``AuthContext``。
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.common.enums import DataScope, DocumentStatus
from app.common.models import DocumentLog
from app.core.errors import BusinessError, ErrorCode
from app.core.permissions import AuthContext
from app.modules.payroll.models import PayrollSheet
from app.modules.payroll.service import PayrollSheetService
from tests.factories.payroll import (
    seed_approved_settlement,
    seed_period,
    settlement_row,
    sheet_row,
)
from tests.factories.payroll_approve import REVIEWER_ID, reviewer_ctx
from tests.factories.user import OPERATOR_ID

DOC_DATE = date(2099, 12, 31)
DOC_TYPE = "PayrollSheet"
SETTLE = "payroll:settle"


class _FakeVoucherPort:
    """计数用 fake：断言「恰好调用一次」，并可控返回凭证号（注入 service 用）。"""

    def __init__(self, *, accrual_no: str | None = None, reverse_no: str | None = None) -> None:
        self.accrual_no = accrual_no
        self.reverse_no = reverse_no
        self.create_calls: list[UUID] = []
        self.reverse_calls: list[UUID] = []

    async def create_accrual(self, sheet: PayrollSheet) -> str | None:
        self.create_calls.append(sheet.id)
        return self.accrual_no

    async def reverse_accrual(self, sheet: PayrollSheet) -> str | None:
        self.reverse_calls.append(sheet.id)
        return self.reverse_no


def _settle_ctx() -> AuthContext:
    """制单人上下文（只用于 ``create`` / ``submit``，含 ``payroll:settle``）。"""
    return AuthContext(
        user_id=OPERATOR_ID,
        name="计件员",
        employee_no="A001",
        workshop_id=None,
        group_no=None,
        permissions=frozenset({SETTLE}),
        data_scope=DataScope.FACTORY,
    )


def _spec(employee_id: Any) -> dict[str, Any]:
    return {
        "employee_id": employee_id,
        "style_no": "ST-APPROVE",
        "operation_no": "01",
        "qty": Decimal("10"),
        "unit_price": Decimal("0.500000"),
    }


async def _submitted_sheet(session: Any) -> PayrollSheet:
    """经 ``create`` + ``submit`` 造一张 ``SUBMITTED`` 工资单（制单人 = ``OPERATOR_ID``）。"""
    settlement = await seed_approved_settlement(session, specs=[_spec(uuid4())], created_by=uuid4())
    service = PayrollSheetService(session)
    sheet = await service.create(settlement_id=settlement.id, ctx=_settle_ctx(), doc_date=DOC_DATE)
    return await service.submit(
        sheet.id, expected_version=sheet.version, operator_id=OPERATOR_ID, ctx=_settle_ctx()
    )


async def _logs(session: Any, sheet_id: UUID) -> list[DocumentLog]:
    rows = await session.execute(
        select(DocumentLog)
        .where(DocumentLog.doc_type == DOC_TYPE, DocumentLog.doc_id == sheet_id)
        .order_by(DocumentLog.created_at)
    )
    return list(rows.scalars().all())


async def test_tc_pay_v01_approve_calls_create_accrual_once(db_session) -> None:
    sheet = await _submitted_sheet(db_session)
    fake = _FakeVoucherPort(accrual_no="VO-TEST-0001")
    approved = await PayrollSheetService(db_session, voucher_port=fake).approve(
        sheet.id,
        expected_version=sheet.version,
        operator_id=REVIEWER_ID,
        ctx=reviewer_ctx(),
        remark="审核通过",
    )
    assert approved.status is DocumentStatus.APPROVED
    assert approved.approved_by == REVIEWER_ID
    assert approved.approved_at is not None
    assert fake.create_calls == [sheet.id], "create_accrual 必须恰好调用一次"
    assert approved.accrual_voucher_no == "VO-TEST-0001"

    approve_logs = [item for item in await _logs(db_session, sheet.id) if item.action == "APPROVE"]
    assert len(approve_logs) == 1
    fields = approve_logs[0].changed_fields or {}
    assert fields.get("accrual_voucher_no") == "VO-TEST-0001"
    assert fields.get("total_net") == str(approved.total_net)
    assert (approve_logs[0].from_status, approve_logs[0].to_status) == ("SUBMITTED", "APPROVED")


async def test_tc_pay_v02_null_adapter_leaves_voucher_null(db_session) -> None:
    """空适配器（默认）：两个动作都返回 ``None``（凭证号落 ``NULL``）且不抛异常。"""
    sheet = await _submitted_sheet(db_session)
    service = PayrollSheetService(db_session)
    approved = await service.approve(
        sheet.id, expected_version=sheet.version, operator_id=REVIEWER_ID, ctx=reviewer_ctx()
    )
    assert approved.status is DocumentStatus.APPROVED
    assert approved.accrual_voucher_no is None
    reversed_ = await service.reverse(
        approved.id,
        reason="空适配器红冲",
        expected_version=approved.version,
        operator_id=REVIEWER_ID,
        ctx=reviewer_ctx(),
    )
    assert reversed_.status is DocumentStatus.SUBMITTED
    assert reversed_.accrual_voucher_no is None


async def test_tc_pay_v03_reverse_calls_reverse_accrual_once(db_session) -> None:
    sheet = await _submitted_sheet(db_session)
    fake = _FakeVoucherPort(accrual_no="VO-TEST-0001", reverse_no="VO-TEST-R001")
    service = PayrollSheetService(db_session, voucher_port=fake)
    approved = await service.approve(
        sheet.id, expected_version=sheet.version, operator_id=REVIEWER_ID, ctx=reviewer_ctx()
    )
    reversed_ = await service.reverse(
        approved.id,
        reason="工价录错，红冲重算",
        expected_version=approved.version,
        operator_id=REVIEWER_ID,
        ctx=reviewer_ctx(),
    )
    assert reversed_.status is DocumentStatus.SUBMITTED
    assert fake.reverse_calls == [sheet.id], "reverse_accrual 必须恰好调用一次"
    assert reversed_.accrual_voucher_no is None, "反审核后计提凭证号清空（红冲）"
    assert reversed_.approved_by is None and reversed_.approved_at is None

    reverse_logs = [item for item in await _logs(db_session, sheet.id) if item.action == "REVERSE"]
    assert len(reverse_logs) == 1
    assert reverse_logs[0].reason == "工价录错，红冲重算"
    assert (reverse_logs[0].changed_fields or {}).get("reversal_voucher_no") == "VO-TEST-R001"


async def test_tc08_reverse_paid_sheet_33001(db_session) -> None:
    settlement = await seed_approved_settlement(db_session, specs=[], created_by=uuid4())
    sheet = sheet_row(
        settlement,
        doc_no=f"PR-{uuid4().hex[:12]}",
        status=DocumentStatus.PAID,
        created_by=uuid4(),
        accrual_voucher_no="VO-ACCRUAL-1",
    )
    db_session.add(sheet)
    await db_session.flush()
    fake = _FakeVoucherPort(reverse_no="VO-SHOULD-NOT")
    with pytest.raises(BusinessError) as exc:
        await PayrollSheetService(db_session, voucher_port=fake).reverse(
            sheet.id,
            reason="已发放试反审核",
            expected_version=sheet.version,
            operator_id=REVIEWER_ID,
            ctx=reviewer_ctx(),
        )
    assert exc.value.code == ErrorCode.PAYROLL_SHEET_ALREADY_PAID
    refreshed = await db_session.get(PayrollSheet, sheet.id, populate_existing=True)
    assert refreshed is not None and refreshed.status is DocumentStatus.PAID
    assert refreshed.accrual_voucher_no == "VO-ACCRUAL-1", "状态/凭证不动"
    assert fake.reverse_calls == [], "被拒的 reverse 不得调用红冲"


async def test_self_approval_10005(db_session) -> None:
    settlement = await seed_approved_settlement(db_session, specs=[], created_by=uuid4())
    sheet = sheet_row(
        settlement,
        doc_no=f"PR-{uuid4().hex[:12]}",
        status=DocumentStatus.SUBMITTED,
        created_by=REVIEWER_ID,
    )
    db_session.add(sheet)
    await db_session.flush()
    fake = _FakeVoucherPort(accrual_no="VO-X")
    with pytest.raises(BusinessError) as exc:
        await PayrollSheetService(db_session, voucher_port=fake).approve(
            sheet.id, expected_version=sheet.version, operator_id=REVIEWER_ID, ctx=reviewer_ctx()
        )
    assert exc.value.code == ErrorCode.SELF_APPROVAL_FORBIDDEN
    refreshed = await db_session.get(PayrollSheet, sheet.id, populate_existing=True)
    assert refreshed is not None and refreshed.status is DocumentStatus.SUBMITTED
    assert fake.create_calls == []


def test_approve_net_mismatch_guard() -> None:
    """``total_net = total_gross - total_deduction`` 的 service 双检（与 DB CHECK 同口径）。

    该不一致在库层被 ``ck_payroll_sheets_net`` 拒绝、正常路径不可达，故直接对守卫方法
    做单元断言（账实不符时绝不带着错金额生成计提）。
    """
    bad = PayrollSheet(
        doc_no="PR-GUARD-1",
        total_gross=Decimal("10.0000"),
        total_deduction=Decimal("1.0000"),
        total_net=Decimal("10.0000"),
    )
    with pytest.raises(BusinessError) as exc:
        PayrollSheetService._assert_net_balanced(bad)
    assert exc.value.code == ErrorCode.ILLEGAL_OPERATION


async def test_reverse_requires_reason_10006(db_session) -> None:
    sheet = await _submitted_sheet(db_session)
    service = PayrollSheetService(db_session)
    approved = await service.approve(
        sheet.id, expected_version=sheet.version, operator_id=REVIEWER_ID, ctx=reviewer_ctx()
    )
    with pytest.raises(BusinessError) as exc:
        await service.reverse(
            approved.id,
            reason="   ",
            expected_version=approved.version,
            operator_id=REVIEWER_ID,
            ctx=reviewer_ctx(),
        )
    assert exc.value.code == ErrorCode.REASON_REQUIRED
    refreshed = await db_session.get(PayrollSheet, sheet.id, populate_existing=True)
    assert refreshed is not None and refreshed.status is DocumentStatus.APPROVED


async def test_approve_requires_permission_12001(db_session) -> None:
    sheet = await _submitted_sheet(db_session)
    with pytest.raises(BusinessError) as exc:
        await PayrollSheetService(db_session).approve(
            sheet.id,
            expected_version=sheet.version,
            operator_id=REVIEWER_ID,
            ctx=reviewer_ctx(permissions=frozenset()),
        )
    assert exc.value.code == ErrorCode.PERMISSION_DENIED


async def _purge_sheet_world(
    migration_url: str, sheet_id: UUID, settlement_id: UUID, period_id: UUID
) -> None:
    """并发用例真提交后，用迁移账号硬删自己造的那几行（erp_app 无 DELETE 权限）。"""
    engine = create_async_engine(migration_url, pool_pre_ping=True)
    try:
        async with engine.begin() as conn:
            for doc_type in (DOC_TYPE, "PieceworkSettlement"):
                await conn.execute(
                    text("DELETE FROM document_logs WHERE doc_type=:t AND doc_id=:d"),
                    {"t": doc_type, "d": sheet_id if doc_type == DOC_TYPE else settlement_id},
                )
            await conn.execute(text("DELETE FROM payroll_details WHERE doc_id=:d"), {"d": sheet_id})
            await conn.execute(text("DELETE FROM payroll_sheets WHERE id=:d"), {"d": sheet_id})
            await conn.execute(
                text("DELETE FROM piecework_settlement_lines WHERE doc_id=:d"),
                {"d": settlement_id},
            )
            await conn.execute(
                text("DELETE FROM piecework_settlements WHERE id=:d"), {"d": settlement_id}
            )
            await conn.execute(text("DELETE FROM payroll_periods WHERE id=:p"), {"p": period_id})
    finally:
        await engine.dispose()


async def test_tc_pay_v04_concurrent_approve_reverse_one_wins(
    app_database_url, migration_url
) -> None:
    engine = create_async_engine(app_database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    start = date(2099, 1, 1) + timedelta(days=uuid4().int % 2000)
    end = start + timedelta(days=20)
    sheet_id: UUID | None = None
    settlement_id: UUID | None = None
    period_id: UUID | None = None
    version = 0
    try:
        async with factory() as session:
            period = await seed_period(
                session, period_code=f"V04-{uuid4().hex[:8]}", start_date=start, end_date=end
            )
            settlement = settlement_row(
                period.id, uuid4(), doc_no=f"PW-{uuid4().hex[:12]}", status=DocumentStatus.APPROVED
            )
            session.add(settlement)
            await session.flush()
            sheet = sheet_row(
                settlement,
                doc_no=f"PR-{uuid4().hex[:12]}",
                status=DocumentStatus.SUBMITTED,
                created_by=uuid4(),
            )
            session.add(sheet)
            await session.flush()
            await session.commit()
            sheet_id, settlement_id, period_id = sheet.id, settlement.id, period.id
            version = sheet.version

        approve_done = asyncio.Event()

        async def do_approve() -> ErrorCode | None:
            async with factory() as session:
                try:
                    await PayrollSheetService(session).approve(
                        sheet_id,
                        expected_version=version,
                        operator_id=REVIEWER_ID,
                        ctx=reviewer_ctx(),
                    )
                    await session.commit()
                    return None
                except BusinessError as exc:
                    await session.rollback()
                    return exc.code
                finally:
                    approve_done.set()

        async def do_reverse() -> ErrorCode | None:
            await approve_done.wait()
            async with factory() as session:
                try:
                    await PayrollSheetService(session).reverse(
                        sheet_id,
                        reason="并发反审核",
                        expected_version=version,
                        operator_id=REVIEWER_ID,
                        ctx=reviewer_ctx(),
                    )
                    await session.commit()
                    return None
                except BusinessError as exc:
                    await session.rollback()
                    return exc.code

        approve_code, reverse_code = await asyncio.gather(do_approve(), do_reverse())
        assert approve_code is None, f"approve 应成功：{approve_code}"
        assert reverse_code is ErrorCode.OPTIMISTIC_LOCK_CONFLICT, (
            f"reverse 应报 10003：{reverse_code}"
        )

        async with factory() as session:
            fresh = await session.get(PayrollSheet, sheet_id)
            assert fresh is not None
            assert fresh.status is DocumentStatus.APPROVED
            assert fresh.version == version + 1, "version 只应 +1 一次"
            actions = list(
                await session.scalars(
                    select(DocumentLog.action).where(
                        DocumentLog.doc_type == DOC_TYPE, DocumentLog.doc_id == sheet_id
                    )
                )
            )
            assert actions.count("APPROVE") == 1
            assert "REVERSE" not in actions, "失败的 reverse 不应留下日志"
    finally:
        await engine.dispose()
        if sheet_id is not None and settlement_id is not None and period_id is not None:
            await _purge_sheet_world(migration_url, sheet_id, settlement_id, period_id)
