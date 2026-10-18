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

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select

from app.common.enums import DocumentStatus
from app.modules.payroll.models import (
    BankPaymentFile,
    PayrollDetail,
    PayrollPeriod,
    PayrollPeriodStatus,
    PayrollSheet,
    PieceworkSettlement,
    PieceworkSettlementLine,
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


def settlement_line_row(
    doc_id: UUID, piecework_log_id: UUID, **over: Any
) -> PieceworkSettlementLine:
    """一条**合法**的结算行（一流水一行；`piecework_log_id` 唯一）。

    `group_no` 在行表 NOT NULL，故默认给 `"G1"`；`qty`/`unit_price`/`amount` 默认与
    :func:`tests.factories.piecework.log_row` 一致（60 件 × 0.35 = 21）。
    """
    base: dict[str, Any] = {
        "doc_id": doc_id,
        "line_no": 1,
        "piecework_log_id": piecework_log_id,
        "employee_id": uuid4(),
        "group_no": "G1",
        "style_no": "E2E-2026-0001",
        "operation_no": "01",
        "work_date": PERIOD_END,
        "log_type": "SCAN",
        "qty": Decimal("60"),
        "unit_price": Decimal("0.350000"),
        "amount": Decimal("21.0000"),
        "created_by": uuid4(),
        "updated_by": uuid4(),
    }
    base.update(over)
    return PieceworkSettlementLine(**base)


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


# ------------------------------------------------------------ 周期 service 用例（T-PAY-003）

#: ``cutting_boundary`` 用例的固定款号 / 工序（每个用例独立事务、跑完回滚，故不撞唯一键）
BOUNDARY_STYLE_NO = "PAY-BOUND-1"
BOUNDARY_OPERATION_NO = "OP01"


async def seed_period(session: Any, **over: Any) -> PayrollPeriod:
    """建一条周期并 flush（默认区间 ``2099-12`` —— 与 ``period_row`` 一致）。"""
    period = period_row(**over)
    session.add(period)
    await session.flush()
    return period


async def seed_piecework_log(
    session: Any, *, work_date: date, workshop_id: UUID, **over: Any
) -> Any:
    """灌一条计件流水（区间取数核对用例用）。

    ⚠️ 复用 :func:`tests.factories.piecework.log_row`：它已给出「一条合法流水」的
    全部默认值（``qty=60`` / ``amount=21.0000`` / ``payroll_id=None`` / ``SCAN``），
    用例只覆盖自己要破坏 / 指定的字段。⚠️ 用**历史区间**（2026-07/08）而不是今天，
    避免与真提交的并发用例（它们的 ``work_date`` 是 ``business_today()``）互相污染。
    """
    from tests.factories.piecework import log_row

    log = log_row(work_date=work_date, workshop_id=workshop_id, **over)
    session.add(log)
    await session.flush()
    return log


async def seed_boundary_orders(
    session: Any,
    *,
    style_no: str = BOUNDARY_STYLE_NO,
    cutting_dates: list[date],
    bundling_dates: list[date],
) -> dict[str, Any]:
    """造「车间 + 款号 + 工序 + 若干已审核裁剪/打菲单」，用于 ``cutting_boundary``。

    ⚠️ **完成日列取 ``approved_at``**（见 ``service/common.py`` 的 Q-PL11 说明）：
    裁剪单只需表头即可（边界查询只读 ``style_no`` / ``status`` / ``approved_at``），
    不必铺三层明细；打菲单需要一张真实的来源裁剪单满足它自己的外键。
    """
    from app.modules.base.models import ProductCategory, Style
    from app.modules.bundling.models import BundlingOrder
    from app.modules.cutting.models import CuttingOrder
    from tests.factories.bundling import ensure_operation
    from tests.factories.user import OPERATOR_ID, WorkshopFactory

    workshop = await WorkshopFactory.create(session)
    category = (
        await session.execute(select(ProductCategory).where(ProductCategory.code == "SET"))
    ).scalar_one()
    style = Style(
        style_no=style_no,
        name="周期边界款",
        category_id=category.id,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    session.add(style)
    # ⚠️ **get-or-create，不能盲目 INSERT**：``operations.operation_no`` 是**全局唯一**
    # （``uq_operations_no``），而真提交的持久化世界（``tests/factories/
    # piecework_world_persisted.py``）会往共享测试库留下一条 ``OP01``（L-099）。
    # 直接 INSERT 会在全量跑（前面跑过持久化世界）时撞唯一约束，而**隔离子跑却绿** ——
    # 这正是 L-094「测试库污染、报错点与真因隔三层」的那一类。复用 bundling 工厂的
    # ``ensure_operation``（先查后建）即对残留 ``OP01`` 免疫。
    await ensure_operation(session, BOUNDARY_OPERATION_NO)
    await session.flush()

    cutting_rows = [
        CuttingOrder(
            doc_no=f"CT-{day:%Y%m%d}-{index:06d}",
            workshop_id=workshop.id,
            style_id=style.id,
            style_no=style_no,
            color_codes="WHT",
            doc_date=day,
            status=DocumentStatus.APPROVED,
            approved_by=OPERATOR_ID,
            approved_at=datetime.combine(day, time(10, 0), tzinfo=UTC),
            created_by=OPERATOR_ID,
            updated_by=OPERATOR_ID,
        )
        for index, day in enumerate(cutting_dates, start=1)
    ]
    session.add_all(cutting_rows)
    await session.flush()
    source_cutting_order_id = cutting_rows[0].id

    bundling_rows = [
        BundlingOrder(
            doc_no=f"BD-{day:%Y%m%d}-{index:06d}",
            status=DocumentStatus.APPROVED,
            doc_date=day,
            workshop_id=workshop.id,
            style_no=style_no,
            operation_no=BOUNDARY_OPERATION_NO,
            color_group="WHT",
            color_code="WHT",
            bundle_qty=60,
            output_qty=Decimal("60"),
            source_cutting_order_id=source_cutting_order_id,
            approved_by=OPERATOR_ID,
            approved_at=datetime.combine(day, time(10, 0), tzinfo=UTC),
            created_by=OPERATOR_ID,
            updated_by=OPERATOR_ID,
        )
        for index, day in enumerate(bundling_dates, start=1)
    ]
    session.add_all(bundling_rows)
    await session.flush()
    return {
        "workshop": workshop,
        "style": style,
        "cutting_orders": cutting_rows,
        "bundling_orders": bundling_rows,
    }


# ------------------------------------------------------------ 工资单生成用例（T-PAY-006）


async def seed_approved_settlement(
    session: Any,
    *,
    specs: Sequence[Mapping[str, Any]],
    period: PayrollPeriod | None = None,
    workshop_id: UUID | None = None,
    created_by: UUID | None = None,
) -> PieceworkSettlement:
    """造一张 **APPROVED** 结算单 + 逐行（``specs`` 每项 = 一条结算行 = 一条真实流水）。

    ``specs`` 每项键：``employee_id`` / ``style_no`` / ``operation_no`` / ``qty`` /
    ``unit_price``；``amount`` 缺省 = ``qty × unit_price``，``group_no`` 缺省 ``"G1"``。
    供 T-PAY-006 工资单汇总用例按任意 ``(员工, 款号, 工序)`` 组合造数。
    """
    if period is None:
        period = await seed_period(session, period_code=f"SH-{uuid4().hex[:8]}")
    workshop = workshop_id if workshop_id is not None else uuid4()
    logs: list[Any] = []
    for index, spec in enumerate(specs, start=1):
        qty = Decimal(spec["qty"])
        unit_price = Decimal(spec["unit_price"])
        logs.append(
            await seed_piecework_log(
                session,
                work_date=PERIOD_END,
                workshop_id=workshop,
                # 唯一 bundle_no：``uq_piecework_logs_idem`` 不含 style_no，默认值会撞键
                bundle_no=f"BD-20991231-{index:06d}",
                employee_id=spec["employee_id"],
                style_no=spec["style_no"],
                operation_no=spec["operation_no"],
                group_no=spec.get("group_no", "G1"),
                qty=qty,
                unit_price=unit_price,
                amount=Decimal(spec.get("amount", qty * unit_price)),
            )
        )
    total_qty = sum((log.qty for log in logs), start=Decimal("0"))
    total_amount = sum((log.amount for log in logs), start=Decimal("0"))
    settlement = settlement_row(
        period.id,
        workshop,
        doc_no=f"PW-20991231-{uuid4().hex[:6]}",
        status=DocumentStatus.APPROVED,
        approved_by=created_by if created_by is not None else uuid4(),
        approved_at=datetime(2099, 12, 31, 9, 0, tzinfo=UTC),
        created_by=created_by if created_by is not None else uuid4(),
        line_count=len(logs),
        total_qty=total_qty,
        total_amount=total_amount,
        employee_count=len({log.employee_id for log in logs}),
        group_count=len({log.group_no or "" for log in logs}),
    )
    session.add(settlement)
    await session.flush()
    for index, log in enumerate(logs, start=1):
        session.add(
            settlement_line_row(
                settlement.id,
                log.id,
                line_no=index,
                employee_id=log.employee_id,
                style_no=log.style_no,
                operation_no=log.operation_no,
                work_date=log.work_date,
                qty=log.qty,
                unit_price=log.unit_price,
                amount=log.amount,
            )
        )
    await session.flush()
    return settlement
