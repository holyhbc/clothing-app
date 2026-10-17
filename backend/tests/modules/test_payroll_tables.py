"""结算域三表（``payroll_periods`` / ``piecework_settlements`` /
``piecework_settlement_lines``）的建表测试（T-PAY-001 / 迁移 0020）。

====================  =================================================
TC-PAY-T01            三表存在且**列与模型逐个一致**（查 information_schema，非只看 ORM）
TC-PAY-T02            ``payroll_period_status`` = ``OPEN`` / ``CLOSED``
TC-PAY-T03            ``uq_..._period_workshop`` 真拦「同周期同车间」重复结算
TC-PAY-T04            ``piecework_log_id`` 唯一真拦「一条流水被两单占用」
TC-PAY-T05            ``payroll_id`` **有** FK → ``piecework_settlements.id``，
                      不加 ``ON DELETE``，且真拦未知 id
TC-PAY-T06            ``ck_payroll_periods_range`` 真拦 ``end_date < start_date``
TC-PAY-T07            ``alembic downgrade 0019`` 干净删三表 + 枚举（真跑迁移）
TC-PAY-T08            三张软删表 ``erp_app`` 均无 DELETE（04 §6.2.1）
TC-PAY-T09            ``piecework_settlements`` 含 ``04 §7.3`` 单据公共列（含 ``doc_date``）
====================  =================================================

⚠️ **约束用例一律真 INSERT 违反行**，而不只读 ``pg_constraint`` 定义 ——
「约束文本看着对」与「违反行真被拒」之间还隔着类型转换与 flush 顺序：
``numeric(14,3)`` 存负值、FK 指向不存在的 uuid，只有真插才知道。
⚠️ **每个破坏用例都配一条反证**（合法的那个必须能插进去），否则把约束写成恒假
也会让全部破坏用例「通过」，而表已经彻底不能用。

目录查询辅助（``columns`` / ``constraint_def`` / ``column_fk_names`` / …）复用
``tests/factories/piecework.py``（照 ``test_piecework_tables.py`` 的写法，不重抄一份）。
"""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.modules.payroll.models import (
    PayrollPeriod,
    PayrollPeriodStatus,
    PieceworkSettlement,
    PieceworkSettlementLine,
)
from tests.factories.piecework import (
    column_fk_names,
    columns,
    constraint_def,
    enum_values,
    has_delete_grant,
    log_row,
    table_exists,
)

PERIOD = "payroll_periods"
SETTLEMENT = "piecework_settlements"
LINE = "piecework_settlement_lines"
TABLES = (PERIOD, SETTLEMENT, LINE)
ENUM = "payroll_period_status"


# ------------------------------------------------------------ 合法行夹具


def _period(**over: Any) -> PayrollPeriod:
    base: dict[str, Any] = {
        "period_code": "PT-2099-12",
        "start_date": date(2099, 12, 1),
        "end_date": date(2099, 12, 31),
        "status": PayrollPeriodStatus.OPEN,
        "source": "MANUAL",
        "created_by": uuid4(),
        "updated_by": uuid4(),
    }
    base.update(over)
    return PayrollPeriod(**base)


def _settlement(period_id: UUID, workshop_id: UUID, **over: Any) -> PieceworkSettlement:
    base: dict[str, Any] = {
        "doc_no": "PW-20991231-000001",
        "doc_date": date(2099, 12, 31),
        "period_id": period_id,
        "period_code": "PT-2099-12",
        "period_start_date": date(2099, 12, 1),
        "period_end_date": date(2099, 12, 31),
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


def _line(doc_id: UUID, piecework_log_id: UUID, **over: Any) -> PieceworkSettlementLine:
    base: dict[str, Any] = {
        "doc_id": doc_id,
        "line_no": 1,
        "piecework_log_id": piecework_log_id,
        "employee_id": uuid4(),
        "group_no": "A",
        "style_no": "E2E-2026-0001",
        "operation_no": "01",
        "work_date": date(2099, 12, 31),
        "log_type": "SCAN",
        "qty": Decimal("60"),
        "unit_price": Decimal("0.350000"),
        "amount": Decimal("21.0000"),
        "created_by": uuid4(),
        "updated_by": uuid4(),
    }
    base.update(over)
    return PieceworkSettlementLine(**base)


async def _seed_settlement(db_session: Any) -> PieceworkSettlement:
    period = _period()
    db_session.add(period)
    await db_session.flush()
    settlement = _settlement(period.id, uuid4())
    db_session.add(settlement)
    await db_session.flush()
    return settlement


# ------------------------------------------------------------ TC-PAY-T01


@pytest.mark.parametrize(
    ("table", "model"),
    [
        (PERIOD, PayrollPeriod),
        (SETTLEMENT, PieceworkSettlement),
        (LINE, PieceworkSettlementLine),
    ],
)
async def test_table_columns_match_model(db_session, table: str, model: type) -> None:
    """TC-PAY-T01：三表列与模型逐个一致（**查真库**，非只看 ORM）。"""
    model_columns = {column.name for column in model.__table__.columns}
    assert await columns(db_session, table) == model_columns, (
        f"{table} 的真实列与模型不一致 —— 迁移漏抄时 ORM 与库会一起安静地通过"
    )


async def test_soft_delete_tables_have_common_fields(db_session) -> None:
    """三张都是软删 + 乐观锁表，公共字段与 ``ck_*_version_positive`` 必须齐全（04 §2）。"""
    required = (
        "id",
        "created_at",
        "created_by",
        "updated_at",
        "updated_by",
        "deleted_at",
        "version",
        "remark",
    )
    for table in TABLES:
        table_columns = await columns(db_session, table)
        assert set(required) <= table_columns, f"{table} 缺公共字段（04 §2）"
        assert (await constraint_def(db_session, f"ck_{table}_version_positive")) is not None, (
            f"{table} 缺 ck_*_version_positive"
        )


async def test_settlement_has_document_common_columns(db_session) -> None:
    """``piecework_settlements`` 必须含 ``04 §7.3`` 单据公共列。

    ⚠️ 这条守卫来自一次**真实漏列**：``modules/05 §3.2`` 的字段表漏了 ``doc_date``，
    而 ``05 §4.1`` 的 ``update`` 白名单、``05 §6.1`` 的 ``sort=-doc_date``、``05 §8 S3``
    的排序都依赖它 —— 只对「库 == ORM」比对抓不到（两边一起漏），故按规范列名**独立断言**。
    """
    settlement_columns = await columns(db_session, SETTLEMENT)
    document_common = {
        "doc_no",
        "status",
        "doc_date",
        "workshop_id",
        "approved_by",
        "approved_at",
        "rejected_reason",
        "cancelled_reason",
    }
    assert document_common <= settlement_columns, (
        f"piecework_settlements 缺 04 §7.3 单据公共列：{document_common - settlement_columns}"
    )


# ------------------------------------------------------------ TC-PAY-T02


async def test_payroll_period_status_has_two_values(db_session) -> None:
    """TC-PAY-T02：``payroll_period_status`` = ``OPEN`` / ``CLOSED``。"""
    assert await enum_values(db_session, ENUM) == {"OPEN", "CLOSED"}
    assert {member.value for member in PayrollPeriodStatus} == {"OPEN", "CLOSED"}


# ------------------------------------------------------------ TC-PAY-T03


async def test_period_workshop_unique_blocks_duplicate(db_session) -> None:
    """TC-PAY-T03：同一「周期 + 车间」的第二张结算单被唯一键拦（BR-PR-01 / P1）。"""
    period = _period()
    db_session.add(period)
    await db_session.flush()
    workshop_id = uuid4()
    db_session.add(_settlement(period.id, workshop_id, doc_no="PW-20991231-000001"))
    await db_session.flush()
    db_session.add(_settlement(period.id, workshop_id, doc_no="PW-20991231-000002"))
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_period_workshop_unique_allows_other_workshop(db_session) -> None:
    """TC-PAY-T03 反证：同周期**不同车间**必须能各出一张（跨车间共用一个周期）。"""
    period = _period()
    db_session.add(period)
    await db_session.flush()
    db_session.add(_settlement(period.id, uuid4(), doc_no="PW-20991231-000001"))
    await db_session.flush()
    db_session.add(_settlement(period.id, uuid4(), doc_no="PW-20991231-000002"))
    await db_session.flush()


# ------------------------------------------------------------ TC-PAY-T04


async def test_piecework_log_id_unique_blocks_double_charge(db_session) -> None:
    """TC-PAY-T04：同一条流水出现在两行（哪怕行号不同）被唯一键拦（P3 / INV-2）。"""
    settlement = await _seed_settlement(db_session)
    log = log_row()
    db_session.add(log)
    await db_session.flush()
    db_session.add(_line(settlement.id, log.id, line_no=1))
    await db_session.flush()
    db_session.add(_line(settlement.id, log.id, line_no=2))
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_line_unique_allows_distinct_logs(db_session) -> None:
    """TC-PAY-T04 反证：两条不同流水各自成行必须能插进去。"""
    settlement = await _seed_settlement(db_session)
    for line_no in (1, 2):
        log = log_row(bundle_no=f"BD-20991231-999999-XL01-{line_no:04d}")
        db_session.add(log)
        await db_session.flush()
        db_session.add(_line(settlement.id, log.id, line_no=line_no))
        await db_session.flush()


# ------------------------------------------------------------ TC-PAY-T05


async def test_payroll_id_foreign_key_points_at_settlements(db_session) -> None:
    """TC-PAY-T05：``piecework_logs.payroll_id`` 有 FK → ``piecework_settlements.id``。

    ⚠️ 不加 ``ON DELETE``（结算单只软删，INV-7；PG 默认 ``NO ACTION``）。
    """
    assert await column_fk_names(db_session, "piecework_logs", "payroll_id") == [
        "fk_piecework_logs_payroll"
    ]
    definition = await constraint_def(db_session, "fk_piecework_logs_payroll")
    assert definition is not None
    assert "piecework_settlements" in definition
    assert "ON DELETE" not in definition.upper()


async def test_payroll_id_foreign_key_rejects_unknown_id(db_session) -> None:
    """TC-PAY-T05 反证：FK 真拦未知结算单 id（只读约束定义证明不了这个）。"""
    db_session.add(log_row(payroll_id=uuid4()))
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


# ------------------------------------------------------------ TC-PAY-T06


async def test_period_range_check_rejects_reversed_dates(db_session) -> None:
    """TC-PAY-T06：``ck_payroll_periods_range`` 真拦 ``end_date < start_date``（BR-PR-21）。"""
    db_session.add(_period(start_date=date(2099, 12, 31), end_date=date(2099, 12, 1)))
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_period_range_check_accepts_same_day(db_session) -> None:
    """TC-PAY-T06 反证：单日区间（``end == start``）是合法的（CHECK 是 ``>=``）。"""
    db_session.add(_period(start_date=date(2099, 12, 31), end_date=date(2099, 12, 31)))
    await db_session.flush()


# ------------------------------------------------------------ TC-PAY-T08


@pytest.mark.parametrize("table", TABLES)
async def test_app_role_has_no_delete(db_session, table: str) -> None:
    """TC-PAY-T08：``erp_app`` 对三张软删表均无 DELETE（04 §6.2.1 双保险）。"""
    assert not await has_delete_grant(db_session, table), f"erp_app 竟然能 DELETE {table}"


# ------------------------------------------------------------ TC-PAY-T07


async def _tables_present_and_enum_count(url: str) -> tuple[bool, int]:
    """``(三表是否都在, payroll_period_status 枚举值个数)``。

    ⚠️ 同步入口 ``asyncio.run`` 包装：``alembic`` 的 online 入口自身即 ``asyncio.run``，
    不能在已运行的 event loop 里调用（同 ``tests/factories/piecework.py``）。
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(url, pool_pre_ping=True)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            present = True
            for name in TABLES:
                present = present and await table_exists(session, name)
            enum_count = len(await enum_values(session, ENUM))
            return present, enum_count
    finally:
        await engine.dispose()


def test_migration_downgrade_removes_tables_and_enum(_schema, migration_url: str) -> None:
    """TC-PAY-T07：``alembic downgrade 0019`` 干净删三表 + 枚举，再 upgrade 还原。

    ⚠️ 必须**真跑**迁移：只读 ``pg_constraint`` / 只读迁移源码都证明不了
    ``downgrade`` 能执行 —— 而 downgrade 失败会在发布回滚时才发现。
    ⚠️ ``finally`` 里无论断言结果如何都要还原到 head，否则后面的用例全红。
    """
    import os

    from alembic.config import Config

    from alembic import command

    os.environ["DATABASE_URL_MIGRATION"] = migration_url
    config = Config("alembic.ini")
    command.downgrade(config, "0019")
    try:
        present, enum_count = asyncio.run(_tables_present_and_enum_count(migration_url))
        assert not present, "downgrade 之后三张结算域表仍在"
        assert enum_count == 0, "downgrade 之后 payroll_period_status 枚举仍在"
    finally:
        command.upgrade(config, "head")
    present, enum_count = asyncio.run(_tables_present_and_enum_count(migration_url))
    assert present and enum_count == 2, "upgrade 还原失败，测试库未回到 head"
