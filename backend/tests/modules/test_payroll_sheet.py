"""工资单**生成**用例（T-PAY-006；``modules/05 §11`` TC-04/05 + TC-PAY-P01/P02）。

============  ==================================================================
TC-05         同一员工 2 款号 3 工序（6 行）→ 生成 6 行；Σgross = 结算行金额
TC-04         生成后改基础资料单价 → 工资行 ``unit_price`` 不变（INV-3）
TC-PAY-P01    来源结算单未 ``APPROVED`` → 拒绝（``33003``）
TC-PAY-P02    同结算单重复生成 → 幂等返回既有单，不重复写行
—             ``unit_price`` 多值取加权平均（Q-PL13）/ 缺银行资料不阻断 / 权限与数据范围
============  ==================================================================

轻状态迁移（``submit`` / ``withdraw`` / ``reject`` / ``cancel`` / ``update``）见同目录
``test_payroll_sheet_state.py``（ADR-0030 单文件 400 行硬线，拆两份）。

## 本文件刻意不走 HTTP

本卡**没有 router**（归 T-PAY-009），故直接调 service；鉴权用构造的 ``AuthContext``。
区间用固定 ``2099-12``（``seed_approved_settlement`` 默认），与真提交的并发用例隔离。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.common.enums import DataScope, DocumentStatus
from app.core.errors import BusinessError, ErrorCode
from app.core.permissions import AuthContext
from app.modules.base.models import OperationRate
from app.modules.payroll.models import PayrollDetail, PayrollPeriodStatus, PayrollSheet
from app.modules.payroll.service import PayrollSheetService
from app.modules.payroll.service.sheet import (
    _bank_snapshot,
    _GroupAgg,
    _resolve_unit_price,
    _str_attr,
)
from tests.factories.bundling import ensure_operation
from tests.factories.payroll import seed_approved_settlement, seed_period, settlement_row
from tests.factories.user import OPERATOR_ID, UserFactory

SETTLE = "payroll:settle"
APPROVE = "payroll:approve"
DOC_DATE = date(2099, 12, 31)


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


def _spec(
    employee_id: Any, style_no: str, operation_no: str, qty: str, price: str
) -> dict[str, Any]:
    return {
        "employee_id": employee_id,
        "style_no": style_no,
        "operation_no": operation_no,
        "qty": Decimal(qty),
        "unit_price": Decimal(price),
    }


async def _details(session: Any, doc_id: Any) -> list[PayrollDetail]:
    rows = await session.execute(
        select(PayrollDetail).where(PayrollDetail.doc_id == doc_id).order_by(PayrollDetail.line_no)
    )
    return list(rows.scalars().all())


async def _sheet_count(session: Any) -> int:
    return (await session.execute(select(func.count(PayrollSheet.id)))).scalar_one()


# ============================================================== TC-05


async def test_tc05_generate_groups_by_employee_style_operation(db_session) -> None:
    """同一员工 2 款号 × 3 工序 = 6 行；数量/金额 = 结算行汇总。"""
    emp = uuid4()
    specs = [
        _spec(emp, style, operation, "10", "0.500000")
        for style in ("ST-PAY-A", "ST-PAY-B")
        for operation in ("01", "02", "03")
    ]
    settlement = await seed_approved_settlement(db_session, specs=specs)

    sheet = await PayrollSheetService(db_session).create(
        settlement_id=settlement.id, ctx=_ctx(), doc_date=DOC_DATE
    )

    assert sheet.doc_no.startswith("PR-20991231-")
    assert sheet.status is DocumentStatus.DRAFT
    assert (sheet.detail_count, sheet.employee_count) == (6, 1)
    details = await _details(db_session, sheet.id)
    assert [item.line_no for item in details] == list(range(1, 7))
    assert {item.style_no for item in details} == {"ST-PAY-A", "ST-PAY-B"}
    assert sum((item.qty for item in details), start=Decimal("0")) == Decimal("60")
    assert (
        sum((item.gross_amount for item in details), start=Decimal("0")) == settlement.total_amount
    )
    for item in details:
        assert item.unit_price == Decimal("0.500000")
        assert item.deduction_amount == Decimal("0")
        assert item.net_amount == item.gross_amount - item.deduction_amount
    assert sheet.total_gross == settlement.total_amount
    assert sheet.total_deduction == Decimal("0")
    assert sheet.total_net == sheet.total_gross - sheet.total_deduction


# ============================================================== TC-04


async def test_tc04_unit_price_snapshot_survives_rate_change(db_session) -> None:
    """生成后改基础资料单价，工资行 ``unit_price`` 不变（INV-3）。"""
    emp = uuid4()
    settlement = await seed_approved_settlement(
        db_session, specs=[_spec(emp, "ST-SNAP", "01", "10", "0.350000")]
    )
    sheet = await PayrollSheetService(db_session).create(
        settlement_id=settlement.id, ctx=_ctx(), doc_date=DOC_DATE
    )
    assert (await _details(db_session, sheet.id))[0].unit_price == Decimal("0.350000")

    await ensure_operation(db_session, "01")
    db_session.add(
        OperationRate(
            operation_no="01",
            style_no="ST-SNAP",
            unit_price=Decimal("9.990000"),
            effective_from=date(2099, 1, 1),
            created_by=OPERATOR_ID,
            updated_by=OPERATOR_ID,
        )
    )
    await db_session.flush()

    refreshed = (await _details(db_session, sheet.id))[0]
    assert refreshed.unit_price == Decimal("0.350000")


# ============================================================== Q-PL13


async def test_mixed_unit_prices_use_weighted_average(db_session) -> None:
    """同一 (员工, 款号, 工序) 多条不同单价 → 取加权平均（Q-PL13）。"""
    emp = uuid4()
    settlement = await seed_approved_settlement(
        db_session,
        specs=[
            _spec(emp, "ST-MIX", "01", "10", "0.300000"),
            _spec(emp, "ST-MIX", "01", "30", "0.500000"),
        ],
    )
    sheet = await PayrollSheetService(db_session).create(
        settlement_id=settlement.id, ctx=_ctx(), doc_date=DOC_DATE
    )
    detail = (await _details(db_session, sheet.id))[0]
    assert detail.qty == Decimal("40")
    assert detail.gross_amount == Decimal("18.0000")
    assert detail.unit_price == Decimal("0.450000")  # (3 + 15) / 40


# ============================================================== TC-PAY-P01/P02


async def test_tc_pay_p01_source_not_approved_rejected(db_session) -> None:
    period = await seed_period(db_session, period_code=f"NA-{uuid4().hex[:8]}")
    settlement = settlement_row(
        period.id, uuid4(), doc_no=f"PW-20991231-{uuid4().hex[:6]}", status=DocumentStatus.DRAFT
    )
    db_session.add(settlement)
    await db_session.flush()

    with pytest.raises(BusinessError) as exc:
        await PayrollSheetService(db_session).create(
            settlement_id=settlement.id, ctx=_ctx(), doc_date=DOC_DATE
        )
    assert exc.value.code == ErrorCode.PAYROLL_PERIOD_CLOSED
    assert await _sheet_count(db_session) == 0


async def test_tc_pay_p02_idempotent_returns_existing(db_session) -> None:
    settlement = await seed_approved_settlement(
        db_session, specs=[_spec(uuid4(), "ST-IDEM", "01", "10", "0.500000")]
    )
    service = PayrollSheetService(db_session)
    first = await service.create(settlement_id=settlement.id, ctx=_ctx(), doc_date=DOC_DATE)
    again = await service.create(settlement_id=settlement.id, ctx=_ctx(), doc_date=DOC_DATE)

    assert again.id == first.id
    assert again.doc_no == first.doc_no
    assert len(await _details(db_session, first.id)) == 1
    assert await _sheet_count(db_session) == 1


# ============================================================== 周期关闭 / 缺银行资料


async def test_create_on_closed_period_33003(db_session) -> None:
    period = await seed_period(
        db_session, period_code=f"CL-{uuid4().hex[:8]}", status=PayrollPeriodStatus.CLOSED
    )
    settlement = await seed_approved_settlement(
        db_session, period=period, specs=[_spec(uuid4(), "ST-CLOSED", "01", "10", "0.500000")]
    )
    with pytest.raises(BusinessError) as exc:
        await PayrollSheetService(db_session).create(
            settlement_id=settlement.id, ctx=_ctx(), doc_date=DOC_DATE
        )
    assert exc.value.code == ErrorCode.PAYROLL_PERIOD_CLOSED
    assert await _sheet_count(db_session) == 0


async def test_missing_bank_data_does_not_block_generation(db_session) -> None:
    """员工无银行卡资料仍能生成（缺失不阻断，发放时才拦 ``10002``）。"""
    settlement = await seed_approved_settlement(
        db_session, specs=[_spec(uuid4(), "ST-NOBANK", "01", "10", "0.500000")]
    )
    sheet = await PayrollSheetService(db_session).create(
        settlement_id=settlement.id, ctx=_ctx(), doc_date=DOC_DATE
    )
    detail = (await _details(db_session, sheet.id))[0]
    assert (detail.id_card, detail.bank_name, detail.bank_account) == (None, None, None)


# ============================================================== 权限 / 数据范围


async def test_create_requires_settle_permission_12001(db_session) -> None:
    settlement = await seed_approved_settlement(
        db_session, specs=[_spec(uuid4(), "ST-PERM", "01", "10", "0.500000")]
    )
    with pytest.raises(BusinessError) as exc:
        await PayrollSheetService(db_session).create(
            settlement_id=settlement.id, ctx=_ctx(permissions=frozenset()), doc_date=DOC_DATE
        )
    assert exc.value.code == ErrorCode.PERMISSION_DENIED


async def test_create_workshop_out_of_scope_12002(db_session) -> None:
    settlement = await seed_approved_settlement(
        db_session, specs=[_spec(uuid4(), "ST-SCOPE", "01", "10", "0.500000")]
    )
    with pytest.raises(BusinessError) as exc:
        await PayrollSheetService(db_session).create(
            settlement_id=settlement.id,
            ctx=_ctx(scope=DataScope.WORKSHOP),
            doc_date=DOC_DATE,
        )
    assert exc.value.code == ErrorCode.DATA_SCOPE_DENIED


async def test_create_unknown_settlement_20001(db_session) -> None:
    with pytest.raises(BusinessError) as exc:
        await PayrollSheetService(db_session).create(
            settlement_id=uuid4(), ctx=_ctx(), doc_date=DOC_DATE
        )
    assert exc.value.code == ErrorCode.BASE_DATA_NOT_FOUND


# ============================================================== 并发兜底 / 快照 / 单价回退


async def test_concurrent_duplicate_returns_existing(db_session, monkeypatch) -> None:
    """预检落空、唯一约束兜底 → 返回既有单（05 §6.3）。"""
    settlement = await seed_approved_settlement(
        db_session, specs=[_spec(uuid4(), "ST-RACE", "01", "10", "0.500000")]
    )
    service = PayrollSheetService(db_session)
    first = await service.create(settlement_id=settlement.id, ctx=_ctx(), doc_date=DOC_DATE)

    original = PayrollSheetService._find_existing
    calls = {"n": 0}

    async def _miss_once(self, settlement_id):
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return await original(self, settlement_id)

    monkeypatch.setattr(PayrollSheetService, "_find_existing", _miss_once)
    again = await service.create(settlement_id=settlement.id, ctx=_ctx(), doc_date=DOC_DATE)
    assert again.id == first.id
    assert len(await _details(db_session, first.id)) == 1


async def test_concurrent_conflict_without_existing_33002(db_session, monkeypatch) -> None:
    """唯一冲突且查不到既有单（非 settlement_id 冲突）→ ``33002``。"""
    settlement = await seed_approved_settlement(
        db_session, specs=[_spec(uuid4(), "ST-RACE2", "01", "10", "0.500000")]
    )
    service = PayrollSheetService(db_session)
    await service.create(settlement_id=settlement.id, ctx=_ctx(), doc_date=DOC_DATE)

    async def _always_none(self, settlement_id):
        return None

    monkeypatch.setattr(PayrollSheetService, "_find_existing", _always_none)
    with pytest.raises(BusinessError) as exc:
        await service.create(settlement_id=settlement.id, ctx=_ctx(), doc_date=DOC_DATE)
    assert exc.value.code == ErrorCode.SETTLEMENT_PERIOD_DUPLICATED


async def test_create_soft_deleted_period_20001(db_session) -> None:
    period = await seed_period(db_session, period_code=f"DEL-{uuid4().hex[:8]}")
    settlement = await seed_approved_settlement(
        db_session, period=period, specs=[_spec(uuid4(), "ST-DELPERIOD", "01", "10", "0.500000")]
    )
    period.deleted_at = datetime(2099, 12, 1, tzinfo=UTC)
    await db_session.flush()
    with pytest.raises(BusinessError) as exc:
        await PayrollSheetService(db_session).create(
            settlement_id=settlement.id, ctx=_ctx(), doc_date=DOC_DATE
        )
    assert exc.value.code == ErrorCode.BASE_DATA_NOT_FOUND


async def test_bank_snapshot_reads_user_row(db_session) -> None:
    """员工在 ``users`` 中存在时走快照读取路径（字段缺失 → ``None``，不阻断）。"""
    user = await UserFactory.create(db_session, employee_no=f"B{uuid4().hex[:7].upper()}")
    settlement = await seed_approved_settlement(
        db_session, specs=[_spec(user.id, "ST-BANK", "01", "10", "0.500000")]
    )
    sheet = await PayrollSheetService(db_session).create(
        settlement_id=settlement.id, ctx=_ctx(), doc_date=DOC_DATE
    )
    detail = (await _details(db_session, sheet.id))[0]
    assert detail.bank_account == getattr(user, "bank_account", None)


def test_resolve_unit_price_falls_back_when_qty_zero() -> None:
    """``Σqty == 0``（全额红冲净额）退回首条快照单价（Q-PL13）。"""
    agg = _GroupAgg(qty=Decimal("0"), amount=Decimal("0"), first_unit_price=Decimal("0.350000"))
    assert _resolve_unit_price(agg) == Decimal("0.350000")


def test_bank_snapshot_handles_missing_user() -> None:
    assert _bank_snapshot(None) == {"id_card": None, "bank_name": None, "bank_account": None}
    assert _str_attr(None, "id_card") is None
