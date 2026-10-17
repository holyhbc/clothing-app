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

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from app.modules.payroll.models import PayrollPeriod, PayrollPeriodStatus, PieceworkSettlement

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
