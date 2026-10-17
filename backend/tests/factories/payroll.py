"""结算域三表的夹具（T-PAY-001 / 迁移 0020）。

## 为什么需要它

``piecework_logs.payroll_id`` 在迁移 0020 起**有外键**指向 ``piecework_settlements``。
计件侧用例此前习惯用「随便一个 ``uuid4()`` 填 ``payroll_id``」伪造「已结算」——
FK 建了之后那会直接 ``ForeignKeyViolationError``。本模块提供「建一条真实结算单」
的最小夹具，让这些用例改为指向真实行，而不是放松 FK。

⚠️ 夹具一律用固定值（``2099-12-31`` / ``PT-2099-12``），跨月跨年失败的教训见
``tests/factories/__init__.py``。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from app.modules.payroll.models import (
    BankPaymentFile,
    PayrollDetail,
    PayrollPeriod,
    PayrollPeriodStatus,
    PayrollSheet,
    PieceworkSettlement,
)

#: 固定周期区间 —— 与 piecework 夹具的 ``WORK_DATE``（2099-12-31）落在同一区间内。
PERIOD_START = date(2099, 12, 1)
PERIOD_END = date(2099, 12, 31)


def period_row(**over: Any) -> PayrollPeriod:
    """一条**合法**的结算周期。"""
    base: dict[str, Any] = {
        "period_code": "PT-2099-12",
        "start_date": PERIOD_START,
        "end_date": PERIOD_END,
        "status": PayrollPeriodStatus.OPEN,
        "source": "MANUAL",
        "created_by": uuid4(),
        "updated_by": uuid4(),
    }
    base.update(over)
    return PayrollPeriod(**base)


def settlement_row(period_id: UUID, workshop_id: UUID, **over: Any) -> PieceworkSettlement:
    """一条**合法**的结算单（``doc_no`` 可覆盖以避免唯一冲突）。"""
    base: dict[str, Any] = {
        "doc_no": "PW-20991231-000001",
        "doc_date": PERIOD_END,
        "period_id": period_id,
        "period_code": "PT-2099-12",
        "period_start_date": PERIOD_START,
        "period_end_date": PERIOD_END,
        "workshop_id": workshop_id,
        "group_count": 1,
        "total_qty": Decimal("60"),
        "total_amount": Decimal("21.0000"),
        "employee_count": 1,
        "line_count": 1,
        "created_by": uuid4(),
        "updated_by": uuid4(),
    }
    base.update(over)
    return PieceworkSettlement(**base)


async def seed_settlement(session: Any, *, workshop_id: UUID | None = None) -> PieceworkSettlement:
    """建「周期 + 结算单」并把结算单返回（``workshop_id`` 缺省随机）。

    先 ``flush`` 周期再建结算单：两表没有 relationship，SQLAlchemy 的单位工作
    不保证跨表 flush 顺序，显式分步最稳。
    """
    period = period_row()
    session.add(period)
    await session.flush()
    settlement = settlement_row(period.id, workshop_id if workshop_id is not None else uuid4())
    session.add(settlement)
    await session.flush()
    return settlement


# ------------------------------------------------------------ 工资单域（T-PAY-002）

#: 工资单号固定值（唯一键；用例需换号时显式覆盖）。
SHEET_DOC_NO = "PR-20991231-000001"


def sheet_row(settlement: PieceworkSettlement, **over: Any) -> PayrollSheet:
    """一条**合法**的工资单，来源 ``settlement``（1:1）。

    ``period_id`` / ``period_code`` / ``workshop_id`` 从结算单快照；``doc_no`` 可覆盖。
    ``total_net = total_gross - total_deduction``（``ck_payroll_sheets_net``）。
    """
    base: dict[str, Any] = {
        "doc_no": SHEET_DOC_NO,
        "doc_date": PERIOD_END,
        "period_id": settlement.period_id,
        "period_code": settlement.period_code,
        "workshop_id": settlement.workshop_id,
        "employee_count": 1,
        "detail_count": 1,
        "total_gross": Decimal("21.0000"),
        "total_deduction": Decimal("0.0000"),
        "total_net": Decimal("21.0000"),
        "settlement_id": settlement.id,
        "created_by": uuid4(),
        "updated_by": uuid4(),
    }
    base.update(over)
    return PayrollSheet(**base)


def detail_row(sheet: PayrollSheet, **over: Any) -> PayrollDetail:
    """一条**合法**的工资单行（``net_amount = gross - deduction``）。"""
    base: dict[str, Any] = {
        "doc_id": sheet.id,
        "line_no": 1,
        "employee_id": uuid4(),
        "style_no": "E2E-2026-0001",
        "operation_no": "01",
        "qty": Decimal("60"),
        "unit_price": Decimal("0.350000"),
        "gross_amount": Decimal("21.0000"),
        "deduction_amount": Decimal("0.0000"),
        "net_amount": Decimal("21.0000"),
        "deduction_type": None,
        "id_card": None,
        "bank_name": None,
        "bank_account": None,
        "created_by": uuid4(),
        "updated_by": uuid4(),
    }
    base.update(over)
    return PayrollDetail(**base)


def bank_payment_file_row(sheet: PayrollSheet, **over: Any) -> BankPaymentFile:
    """一条**合法**的代发文件记录（append-only，只有 ``id`` + ``created_at`` + ``created_by``）。"""
    base: dict[str, Any] = {
        "payroll_sheet_id": sheet.id,
        "file_name": "PR-20991231-000001.csv",
        "row_count": 1,
        "total_amount": Decimal("21.0000"),
        "content_hash": "a" * 64,
        "exported_by": uuid4(),
        "exported_at": datetime(2099, 12, 31, 9, 0, tzinfo=UTC),
        "created_by": uuid4(),
    }
    base.update(over)
    return BankPaymentFile(**base)


async def seed_sheet(
    session: Any, *, settlement: PieceworkSettlement | None = None
) -> PayrollSheet:
    """建「周期 + 结算单 + 工资单」并把工资单返回（``settlement`` 缺省现建）。"""
    if settlement is None:
        settlement = await seed_settlement(session)
    sheet = sheet_row(settlement)
    session.add(sheet)
    await session.flush()
    return sheet
