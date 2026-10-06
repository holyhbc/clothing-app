"""打菲四张表的建表测试（T-BUND-001 / 迁移 0014）。

====================  =================================================
TC-B01                四表存在且**列与模型逐个一致**（查 information_schema，
                      非只看 ORM）；``bundle_label_prints`` 是 append-only
TC-B02                ``bundle_status`` 枚举值域 = ``ACTIVE`` / ``VOIDED``
TC-B03                ``uq_bundles_hand`` 真拦重复 ``(doc,color,size,hands)``
TC-B04                ``ck_bundles_qty`` 真拦小数 / 非正 ``bundle_qty``
TC-B05                ``ck_bundles_cnt`` 真拦 ``counted_qty > bundle_qty``
TC-B06                ``bundle_label_prints`` 的部分/排序索引存在
TC-B07                ``alembic downgrade -1`` 干净删四表 + 枚举（真跑迁移）
TC-B08                TD4-01 对 ``bundle_label_prints`` 双向往返
====================  =================================================

⚠️ **TC-B01 查真库而不是 ORM**：迁移漏抄列时 ORM 上的 ``nullable=False`` 两边
会一起安静地通过，只有 ``information_schema`` 能抓出来。TC-B03~B05 同样**真去
INSERT 违反行**，而不是只读 ``pg_constraint`` 定义 —— 「约束文本看着对」与
「违反行真被拒」之间还隔着类型转换与 ``trunc`` 行为。

⚠️ TC-B07 **真跑** downgrade → 断言对象消失 → 再 upgrade 还原。它在同步用例里
用 ``asyncio.run`` 另开连接查库（``alembic`` 的 online 入口自身即 ``asyncio.run``，
不能在已运行的 event loop 里调用），所以配套的 catalog 查询也用同步包装。
"""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.common.enums import DocumentStatus
from app.core.db import unit_of_work
from app.modules.base.models import Operation
from app.modules.bundling.models import (
    Bundle,
    BundlingOrder,
    BundlingOrderLine,
)
from app.modules.cutting.service import CuttingOrderService
from tests.factories.cutting import build_world, payload
from tests.factories.user import OPERATOR_ID

BUNDLING_TABLES = (
    "bundling_orders",
    "bundling_order_lines",
    "bundles",
    "bundle_label_prints",
)
SOFT_DELETE_TABLES = ("bundling_orders", "bundling_order_lines", "bundles")
#: append-only 表**不得**带这四列（04 §7.16 / Q-B14）
_FORBIDDEN_ON_APPEND_ONLY = ("deleted_at", "version", "updated_at", "updated_by")


async def _columns(session, table: str) -> set[str]:
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


async def _world(session):
    """一个可用的打菲单 + 一行明细（含来源裁剪单的尺码明细行）。"""
    world = await build_world(session)
    cutting = await CuttingOrderService(session).create(payload(world), OPERATOR_ID)
    size_line_id = (
        await session.execute(
            text("SELECT id FROM cutting_order_size_lines WHERE deleted_at IS NULL LIMIT 1")
        )
    ).scalar_one()
    session.add(
        Operation(
            operation_no="BUND-OP-1", name="打边", created_by=OPERATOR_ID, updated_by=OPERATOR_ID
        )
    )
    await session.flush()
    order = BundlingOrder(
        doc_no="BD-20991231-999999",
        status=DocumentStatus.DRAFT,
        doc_date=date(2099, 12, 31),
        workshop_id=world["workshop"].id,
        style_no=world["style"].style_no,
        operation_no="BUND-OP-1",
        color_group="A",
        color_code="WHT",
        bundle_qty=60,
        hands_total=1,
        output_qty=Decimal("60.000"),
        source_cutting_order_id=cutting.id,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    session.add(order)
    await session.flush()
    line = BundlingOrderLine(
        doc_id=order.id,
        line_no=1,
        color_code="WHT",
        size_code="L",
        operation_no="BUND-OP-1",
        cutting_size_line_id=size_line_id,
        hands=1,
        planned_qty=Decimal("60.000"),
        available_qty_before=Decimal("60.000"),
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    session.add(line)
    await session.flush()
    return order, line, size_line_id


def _bundle(
    order,
    line,
    size_line_id: UUID,
    *,
    bundle_no: str = "BD-20991231-999999-L01-0001",
    hands: int = 1,
    bundle_qty: Decimal = Decimal("60.000"),
    counted_qty: Decimal = Decimal("0.000"),
    counted_by: UUID | None = None,
) -> Bundle:
    return Bundle(
        doc_id=order.id,
        line_id=line.id,
        bundle_no=bundle_no,
        hands=hands,
        style_no=order.style_no,
        color_code="WHT",
        size_code="L",
        operation_no="BUND-OP-1",
        cutting_size_line_id=size_line_id,
        bundle_qty=bundle_qty,
        counted_qty=counted_qty,
        counted_by=counted_by,
        qr_content=bundle_no,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )


async def _commit(session) -> None:
    async with unit_of_work(session):
        pass


# ------------------------------------------------------------ 表结构 / 枚举


@pytest.mark.parametrize("table", BUNDLING_TABLES)
async def test_tables_and_columns_match_model(db_session, table: str) -> None:
    """TC-B01：真库列集与 ORM 模型逐个一致（不是只看 ORM）。"""
    model_columns = {column.name for column in BundlingOrder.metadata.tables[table].columns}
    assert await _columns(db_session, table) == model_columns, (
        f"{table} 的真实列与模型不一致 —— 迁移漏抄时 ORM 与库会一起安静地通过"
    )


async def test_label_prints_is_append_only(db_session) -> None:
    """TC-B01（续）：``bundle_label_prints`` 只有 id/created_at/created_by。"""
    columns = await _columns(db_session, "bundle_label_prints")
    assert {"id", "created_at", "created_by"} <= columns
    assert not (set(_FORBIDDEN_ON_APPEND_ONLY) & columns), (
        "bundle_label_prints 是 append-only（Q-B14）：不得有 version/deleted_at/updated_*"
    )


@pytest.mark.parametrize("table", SOFT_DELETE_TABLES)
async def test_soft_delete_tables_have_version(db_session, table: str) -> None:
    """前三张是软删 + 乐观锁表，必须有完整公共字段与 ``ck_*_version_positive``。"""
    columns = await _columns(db_session, table)
    for required in (
        "id",
        "created_at",
        "created_by",
        "updated_at",
        "updated_by",
        "deleted_at",
        "version",
        "remark",
    ):
        assert required in columns, f"{table} 缺公共字段 {required}（04 §2）"
    checks = (
        (
            await db_session.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conrelid = to_regclass(:t) AND contype = 'c'"
                ),
                {"t": table},
            )
        )
        .scalars()
        .all()
    )
    assert any("version > 0" in item for item in checks), f"{table} 缺 ck_*_version_positive"


async def test_bundle_status_enum_values(db_session) -> None:
    """TC-B02：``bundle_status`` 值域与顺序 = ``ACTIVE`` / ``VOIDED``。"""
    values = (
        (
            await db_session.execute(
                text(
                    "SELECT enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
                    "WHERE t.typname = 'bundle_status' ORDER BY e.enumsortorder"
                )
            )
        )
        .scalars()
        .all()
    )
    assert list(values) == ["ACTIVE", "VOIDED"], f"bundle_status 值域变了：{list(values)}"


# ------------------------------------------------------------ CHECK 真拦


async def test_uq_bundles_hand_blocks_duplicate(db_session) -> None:
    """TC-B03：同一 (doc,color,size,hands) 的第二手被唯一键拦（ADR-0016 §1）。"""
    order, line, size_line_id = await _world(db_session)
    db_session.add(_bundle(order, line, size_line_id))
    await _commit(db_session)
    db_session.add(_bundle(order, line, size_line_id, bundle_no="BD-20991231-999999-L01-0002"))
    with pytest.raises(IntegrityError):
        await _commit(db_session)


@pytest.mark.parametrize("bundle_qty", [Decimal("60.500"), Decimal("0.000"), Decimal("-3.000")])
async def test_ck_bundles_qty_blocks(db_session, bundle_qty: Decimal) -> None:
    """TC-B04：``ck_bundles_qty`` 真拒小数 / 非正件数（整件口径，09 §4.2）。"""
    order, line, size_line_id = await _world(db_session)
    db_session.add(_bundle(order, line, size_line_id, bundle_qty=bundle_qty))
    with pytest.raises(IntegrityError):
        await _commit(db_session)


async def test_ck_bundles_cnt_blocks_overcount(db_session) -> None:
    """TC-B05：``ck_bundles_cnt`` 真拒 ``counted_qty > bundle_qty``（禁止超产）。"""
    order, line, size_line_id = await _world(db_session)
    db_session.add(
        _bundle(
            order,
            line,
            size_line_id,
            bundle_qty=Decimal("3.000"),
            counted_qty=Decimal("5.000"),
            counted_by=OPERATOR_ID,
        )
    )
    with pytest.raises(IntegrityError):
        await _commit(db_session)


async def test_ck_bundles_one_blocks_counted_without_worker(db_session) -> None:
    """``ck_bundles_one``：计了件却没有计件员工，必须被拒。"""
    order, line, size_line_id = await _world(db_session)
    db_session.add(
        _bundle(
            order,
            line,
            size_line_id,
            bundle_qty=Decimal("60.000"),
            counted_qty=Decimal("10.000"),
            counted_by=None,
        )
    )
    with pytest.raises(IntegrityError):
        await _commit(db_session)


# ------------------------------------------------------------ 索引


async def test_label_print_index_exists(db_session) -> None:
    """TC-B06：``idx_bundle_label_prints_bundle_no (bundle_no, printed_at DESC)``。"""
    indexdef = (
        await db_session.execute(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = :i"),
            {"i": "idx_bundle_label_prints_bundle_no"},
        )
    ).scalar_one()
    assert "bundle_no" in indexdef and "printed_at" in indexdef, indexdef
    assert "DESC" in indexdef, f"重打历史需按 printed_at 倒序：{indexdef}"


# ------------------------------------------------------------ 迁移 downgrade


async def _objects_present(url: str) -> tuple[bool, int]:
    """``(四表是否都在, bundle_status 枚举计数)``。"""
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            all_present = True
            for name in BUNDLING_TABLES:
                present = (
                    await conn.execute(text("SELECT to_regclass(:n) IS NOT NULL"), {"n": name})
                ).scalar_one()
                all_present = all_present and bool(present)
            enum_count = (
                await conn.execute(
                    text("SELECT count(*) FROM pg_type WHERE typname = 'bundle_status'")
                )
            ).scalar_one()
            return all_present, int(enum_count)
    finally:
        await engine.dispose()


def test_migration_downgrade_removes_tables_and_enum(_schema, migration_url: str) -> None:
    """TC-B07：``alembic downgrade -1`` 干净删四表 + ``bundle_status``，再 upgrade 还原。

    ⚠️ 必须**真跑**迁移：只查 ``pg_constraint`` / 只读迁移源码都证明不了
    ``downgrade`` 能执行 —— 而 downgrade 失败会在发布回滚时才发现。
    """
    import os

    from alembic.config import Config

    from alembic import command

    os.environ["DATABASE_URL_MIGRATION"] = migration_url
    config = Config("alembic.ini")
    command.downgrade(config, "-1")
    try:
        present, enum_count = asyncio.run(_objects_present(migration_url))
        assert not present, "downgrade 之后四张打菲表仍在"
        assert enum_count == 0, "downgrade 之后 bundle_status 枚举仍在"
    finally:
        # ⚠️ 无论断言结果如何都必须还原到 head，否则后面的用例全红
        command.upgrade(config, "head")
    present, enum_count = asyncio.run(_objects_present(migration_url))
    assert present and enum_count == 1, "upgrade 还原失败，测试库未回到 head"


# ------------------------------------------------------------ TD4-01 往返


def test_bundle_label_prints_field_table_roundtrips() -> None:
    """TC-B08：``bundle_label_prints`` 的字段表 ↔ 04 §7.16 DDL 双向一致（TD4-01）。"""
    from test_docs_ddl_sync import COMMON_COLUMNS, _documented_ddl
    from test_docs_ddl_sync_fields import FIELD_TABLE_SOURCES, _field_table_columns

    table = "bundle_label_prints"
    ddl = _documented_ddl()[table] - COMMON_COLUMNS
    field = _field_table_columns(FIELD_TABLE_SOURCES[table]) - COMMON_COLUMNS
    assert ddl == field, (
        f"bundle_label_prints 字段表与 04 §7.16 DDL 不一致："
        f"DDL 多 {sorted(ddl - field)}，字段表多 {sorted(field - ddl)}"
    )
