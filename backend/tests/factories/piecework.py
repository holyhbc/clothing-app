"""计件两表（``piecework_logs`` / ``piecework_bindings``）的建表夹具与目录查询。

⚠️ **为什么单独一个文件**（ADR-0030 的 400 行硬线）：`test_piecework_tables.py`
照抄 `04 §7.1` 的每一条约束都要一条用例，加上目录查询辅助函数后顶破了 400 行。
拆成「夹具 + 目录查询」与「用例」两个文件，两边都能一次读完。

⚠️ **`log_row` 刻意返回一组合法数据**，各用例只覆盖自己要破坏的那一个字段 ——
「破坏用例」里手写一整行字段，抽出来的字段名一旦与模型不同步，报错会变成
「TypeError: unexpected keyword argument」而不是「约束没拦住」，把排查方向带偏。

⚠️ 时间/工号一律用**固定值**（`2099-12-31` / `BD-20991231-…`）：跨月跨年失败的
教训见 `tests/factories/__init__.py`。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text

from app.modules.piecework.models import PieceworkBinding, PieceworkLog, PieceworkLogType

PIECEWORK_TABLES: tuple[str, ...] = ("piecework_logs", "piecework_bindings")

#: 契约枚举值（``04 §7.1`` 的 ``CREATE TYPE piecework_log_type``）
EXPECTED_LOG_TYPES: tuple[str, ...] = ("SCAN", "MANUAL", "REVERSAL", "SUPPLEMENT")

#: 固定标识 —— 用例之间不共享，唯一键冲突才有意义
BUNDLE_NO = "BD-20991231-999999-XL01-0001"
STYLE_NO = "E2E-2026-0001"
OPERATION_NO = "01"
WORK_DATE = date(2099, 12, 31)
SCANNED_AT = datetime(2099, 12, 31, 8, 0, tzinfo=UTC)


def log_row(**over: Any) -> PieceworkLog:
    """一条**合法**的计件流水。

    ⚠️ ``qty`` 默认 ``60``：一码一手、一码一件数（ADR-0016），60 的手记 60 件。
    「这一手总共多少件」在 ``bundles.bundle_qty``，不在这里（``04-计件 §3.1``）。
    """
    base: dict[str, Any] = {
        "bundle_no": BUNDLE_NO,
        "style_no": STYLE_NO,
        "operation_no": OPERATION_NO,
        "employee_id": uuid4(),
        "workshop_id": uuid4(),
        "group_no": None,
        "work_date": WORK_DATE,
        "bundle_id": None,
        "qty": Decimal("60"),
        "unit_price": Decimal("0.350000"),
        "amount": Decimal("21.0000"),
        "log_type": PieceworkLogType.SCAN,
        "payroll_id": None,
        "reversal_of_id": None,
        "source_station": "ST-01",
        "scanned_at": SCANNED_AT,
        "device_id": None,
        "idempotency_key": None,
        "created_by": uuid4(),
        "updated_by": uuid4(),
    }
    base.update(over)
    return PieceworkLog(**base)


def binding_row(**over: Any) -> PieceworkBinding:
    """一条**合法的**员工工序绑定（``04 §7.1.1``）。"""
    base: dict[str, Any] = {
        "user_id": uuid4(),
        "style_no": STYLE_NO,
        "operation_no": OPERATION_NO,
        "bundle_no": BUNDLE_NO,
        "workshop_id": uuid4(),
        "work_date": WORK_DATE,
        "is_active": True,
        "created_by": uuid4(),
        "updated_by": uuid4(),
    }
    base.update(over)
    return PieceworkBinding(**base)


def binding_key(**over: Any) -> dict[str, Any]:
    """唯一键覆盖到的四个字段（换 ``work_date`` 即换一天 —— BR-PW-04）。"""
    base: dict[str, Any] = {
        "user_id": uuid4(),
        "style_no": STYLE_NO,
        "operation_no": OPERATION_NO,
        "bundle_no": BUNDLE_NO,
        "workshop_id": uuid4(),
    }
    base.update(over)
    return base


# ------------------------------------------------------------ 目录查询
#
# ⚠️ 全部走 ``information_schema`` / ``pg_catalog`` 而**不读 ORM**：迁移漏抄列时
# ORM 上的 ``nullable=False`` 两边会一起安静地通过，只有真库能抓出来
# （同 `test_bundling_tables.py` TC-B01 的理由）。


async def columns(session: Any, table: str) -> set[str]:
    return set(
        (
            await session.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"),
                {"t": table},
            )
        )
        .scalars()
        .all()
    )


async def enum_values(session: Any, name: str) -> set[str]:
    rows = (
        await session.execute(
            text(
                "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
                "WHERE t.typname = :n"
            ),
            {"n": name},
        )
    ).scalars()
    return set(rows.all())


async def constraint_def(session: Any, name: str) -> str | None:
    return (
        await session.execute(
            text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = :n"),
            {"n": name},
        )
    ).scalar_one_or_none()


async def table_exists(session: Any, table: str) -> bool:
    return bool(
        (
            await session.execute(
                text("SELECT 1 FROM information_schema.tables WHERE table_name = :t"),
                {"t": table},
            )
        )
        .scalars()
        .first()
    )


async def column_fk_names(session: Any, table: str, column: str) -> list[str]:
    """某列上的外键约束名列表。⚠️ 读 ``pg_constraint`` 而非 ``information_schema``：
    后者按「约束」列而 PG 16 才加 ``generated`` 维度，跨版本不稳。"""
    return list(
        (
            await session.execute(
                text(
                    "SELECT c.conname FROM pg_constraint c "
                    "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1] "
                    "WHERE c.conrelid = to_regclass(:t) AND a.attname = :col AND c.contype = 'f'"
                ),
                {"t": table, "col": column},
            )
        )
        .scalars()
        .all()
    )


async def index_names(session: Any, table: str) -> set[str]:
    return set(
        (
            await session.execute(
                text("SELECT indexname FROM pg_indexes WHERE tablename = :t"), {"t": table}
            )
        )
        .scalars()
        .all()
    )


async def index_def(session: Any, name: str) -> str:
    return (
        await session.execute(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = :n"), {"n": name}
        )
    ).scalar_one()


async def has_delete_grant(session: Any, table: str) -> bool:
    return bool(
        (
            await session.execute(
                text(
                    "SELECT 1 FROM information_schema.role_table_grants "
                    "WHERE grantee = 'erp_app' AND table_name = :t AND privilege_type = 'DELETE'"
                ),
                {"t": table},
            )
        )
        .scalars()
        .first()
    )


async def objects_present(url: str) -> tuple[bool, int]:
    """``(piecework_logs 是否还在, piecework_log_type 的枚举值个数)``。

    ⚠️ 同步入口：``alembic`` 的 online 入口自身就是 ``asyncio.run``，
    不能在已运行的 event loop 里调用它，所以配套的目录查询也得用同步包装
    （同 ``test_bundling_tables.py`` TC-B07 的理由）。
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(url, pool_pre_ping=True)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            return await table_exists(session, "piecework_logs"), len(
                await enum_values(session, "piecework_log_type")
            )
    finally:
        await engine.dispose()


def new_uuid() -> UUID:
    return uuid4()
