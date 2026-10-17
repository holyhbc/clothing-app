"""工资单域三表（``payroll_sheets`` / ``payroll_details`` / ``bank_payment_files``）
的建表测试（T-PAY-002 / 迁移 0021）。

====================  =================================================
TC-PAY-T10            三表存在且**列与模型逐个一致**（查 information_schema）
TC-PAY-T10b           ``payroll_details`` 含代发快照列 + ``deduction_type``（Q-PL01 只留列）
TC-PAY-T10c           两张软删表公共字段齐全；``bank_payment_files`` 是 append-only
TC-PAY-T11            ``payroll_sheets`` 含 ``04 §7.3`` 单据公共列（含 ``doc_date``）
TC-PAY-T12            ``uq_payroll_sheets_settlement_id`` 挡一结算单出两工资单
TC-PAY-T13            ``ck_payroll_sheets_net`` / ``ck_payroll_details_net`` 真拦
TC-PAY-T14            ``uq_bank_payment_files_sheet_id`` 挡重复导出
TC-PAY-T08x           软删表 ``erp_app`` 无 DELETE；append-only 表无 UPDATE/DELETE
TC-PAY-T15            ``downgrade 0020`` / ``upgrade`` 往返（真跑迁移）
====================  =================================================

⚠️ **约束用例一律真 INSERT 违反行**，且每个破坏用例都配一条**合法反证** ——
否则把约束写成恒假也会让全部破坏用例「通过」，而表已经彻底不能用（照
``test_payroll_tables.py`` 的口径）。
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.modules.payroll.models import BankPaymentFile, PayrollDetail, PayrollSheet
from tests.factories.payroll import (
    bank_payment_file_row,
    detail_row,
    period_row,
    seed_sheet,
    settlement_row,
    sheet_row,
)
from tests.factories.piecework import (
    columns,
    constraint_def,
    enum_values,
    has_delete_grant,
    table_exists,
)

SHEET = "payroll_sheets"
DETAIL = "payroll_details"
BANK = "bank_payment_files"
TABLES = (SHEET, DETAIL, BANK)
SOFT_DELETE_TABLES = (SHEET, DETAIL)


# ------------------------------------------------------------ 夹具


async def _seed_two_settlements(session: Any) -> tuple[Any, Any]:
    """同周期、不同车间的两张结算单（合法），供 1:1 唯一键用例的正反证使用。"""
    period = period_row()
    session.add(period)
    await session.flush()
    first = settlement_row(period.id, uuid4(), doc_no="PW-20991231-000001")
    second = settlement_row(period.id, uuid4(), doc_no="PW-20991231-000002")
    session.add_all([first, second])
    await session.flush()
    return first, second


async def _has_grant(session: Any, table: str, privilege: str) -> bool:
    return bool(
        (
            await session.execute(
                text(
                    "SELECT 1 FROM information_schema.role_table_grants "
                    "WHERE grantee = 'erp_app' AND table_name = :t AND privilege_type = :p"
                ),
                {"t": table, "p": privilege},
            )
        )
        .scalars()
        .first()
    )


# ------------------------------------------------------------ TC-PAY-T10


@pytest.mark.parametrize(
    ("table", "model"),
    [
        (SHEET, PayrollSheet),
        (DETAIL, PayrollDetail),
        (BANK, BankPaymentFile),
    ],
)
async def test_table_columns_match_model(db_session, table: str, model: type) -> None:
    """TC-PAY-T10：三表列与模型逐个一致（**查真库**，非只看 ORM）。"""
    model_columns = {column.name for column in model.__table__.columns}
    assert await columns(db_session, table) == model_columns, (
        f"{table} 的真实列与模型不一致 —— 迁移漏抄时 ORM 与库会一起安静地通过"
    )


async def test_payroll_details_has_bank_snapshot_columns(db_session) -> None:
    """TC-PAY-T10b：``payroll_details`` 含代发快照列与 ``deduction_type``（Q-PL01 只留列）。"""
    detail_columns = await columns(db_session, DETAIL)
    expected = {
        "deduction_type",
        "id_card",
        "bank_name",
        "bank_account",
        "gross_amount",
        "deduction_amount",
        "net_amount",
    }
    assert expected <= detail_columns, f"payroll_details 缺列：{expected - detail_columns}"


async def test_soft_delete_tables_have_common_fields(db_session) -> None:
    """TC-PAY-T10c：``payroll_sheets`` / ``payroll_details`` 是软删 + 乐观锁表（04 §2）。"""
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
    for table in SOFT_DELETE_TABLES:
        table_columns = await columns(db_session, table)
        assert set(required) <= table_columns, f"{table} 缺公共字段（04 §2）"
        assert (await constraint_def(db_session, f"ck_{table}_version_positive")) is not None, (
            f"{table} 缺 ck_*_version_positive"
        )


async def test_bank_payment_files_is_append_only(db_session) -> None:
    """TC-PAY-T10c：``bank_payment_files`` 是 append-only，**不得**有软删/乐观锁列。"""
    bank_columns = await columns(db_session, BANK)
    expected = {
        "id",
        "created_at",
        "created_by",
        "payroll_sheet_id",
        "file_name",
        "row_count",
        "total_amount",
        "content_hash",
        "exported_by",
        "exported_at",
    }
    assert bank_columns == expected, f"bank_payment_files 列不符：{bank_columns ^ expected}"
    assert not (({"version", "deleted_at", "updated_at", "updated_by", "remark"}) & bank_columns)


# ------------------------------------------------------------ TC-PAY-T11


async def test_payroll_sheet_has_document_common_columns(db_session) -> None:
    """TC-PAY-T11：``payroll_sheets`` 必须含 ``04 §7.3`` 单据公共列（含 ``doc_date``）。

    ⚠️ 这条守卫来自一次**真实漏列**（R11）：``modules/05 §3.4`` 的字段表漏了
    ``doc_date``，而 ``05 §4.2 update`` 白名单、``05 §6.1 sort``、``05 §8 S5`` 排序都
    依赖它 —— 只对「库 == ORM」比对抓不到（两边一起漏），故按规范列名**独立断言**。
    """
    sheet_columns = await columns(db_session, SHEET)
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
    assert document_common <= sheet_columns, (
        f"payroll_sheets 缺 04 §7.3 单据公共列：{document_common - sheet_columns}"
    )


# ------------------------------------------------------------ TC-PAY-T12


async def test_settlement_unique_blocks_two_sheets(db_session) -> None:
    """TC-PAY-T12：同一结算单出第二张工资单被唯一键拦（阶段一 1:1，Q-PL06）。"""
    first, _ = await _seed_two_settlements(db_session)
    db_session.add(sheet_row(first, doc_no="PR-20991231-000001"))
    await db_session.flush()
    db_session.add(sheet_row(first, doc_no="PR-20991231-000002"))
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_settlement_unique_allows_distinct_settlements(db_session) -> None:
    """TC-PAY-T12 反证：两张不同结算单各出一张工资单必须能插进去。"""
    first, second = await _seed_two_settlements(db_session)
    db_session.add(sheet_row(first, doc_no="PR-20991231-000001"))
    await db_session.flush()
    db_session.add(sheet_row(second, doc_no="PR-20991231-000002"))
    await db_session.flush()


# ------------------------------------------------------------ TC-PAY-T13


async def test_sheet_net_check_rejects_mismatch(db_session) -> None:
    """TC-PAY-T13：``ck_payroll_sheets_net`` 拦 ``total_net ≠ gross - deduction``。"""
    first, _ = await _seed_two_settlements(db_session)
    db_session.add(
        sheet_row(
            first,
            total_gross="21.0000",
            total_deduction="0.0000",
            total_net="20.0000",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_sheet_net_check_accepts_balanced_row(db_session) -> None:
    """TC-PAY-T13 反证：``total_net = gross - deduction`` 的合法行能插进去。"""
    first, _ = await _seed_two_settlements(db_session)
    db_session.add(sheet_row(first))
    await db_session.flush()


async def test_sheet_amount_check_rejects_negative_deduction(db_session) -> None:
    """TC-PAY-T13：``ck_payroll_sheets_amount`` 拦负的应发 / 扣款合计。"""
    first, _ = await _seed_two_settlements(db_session)
    db_session.add(
        sheet_row(
            first,
            total_gross="21.0000",
            total_deduction="-1.0000",
            total_net="22.0000",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_detail_net_check_rejects_mismatch(db_session) -> None:
    """TC-PAY-T13：``ck_payroll_details_net`` 拦 ``net_amount ≠ gross - deduction``。"""
    sheet = await seed_sheet(db_session)
    db_session.add(
        detail_row(
            sheet,
            gross_amount="21.0000",
            deduction_amount="0.0000",
            net_amount="19.0000",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_detail_net_check_accepts_balanced_row(db_session) -> None:
    """TC-PAY-T13 反证：工资单行合法金额能插进去。"""
    sheet = await seed_sheet(db_session)
    db_session.add(detail_row(sheet))
    await db_session.flush()


# ------------------------------------------------------------ TC-PAY-T14


async def test_bank_file_unique_blocks_duplicate_export(db_session) -> None:
    """TC-PAY-T14：``uq_bank_payment_files_sheet_id`` 挡同一工资单的第二次导出。"""
    sheet = await seed_sheet(db_session)
    db_session.add(bank_payment_file_row(sheet))
    await db_session.flush()
    db_session.add(bank_payment_file_row(sheet, file_name="again.csv"))
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_bank_file_unique_allows_distinct_sheets(db_session) -> None:
    """TC-PAY-T14 反证：两张不同工资单各有一条代发文件记录必须能插进去。"""
    first, second = await _seed_two_settlements(db_session)
    sheet_a = sheet_row(first, doc_no="PR-20991231-000001")
    sheet_b = sheet_row(second, doc_no="PR-20991231-000002")
    db_session.add_all([sheet_a, sheet_b])
    await db_session.flush()
    db_session.add(bank_payment_file_row(sheet_a, file_name="one.csv"))
    await db_session.flush()
    db_session.add(bank_payment_file_row(sheet_b, file_name="two.csv"))
    await db_session.flush()


# ------------------------------------------------------------ 授权（04 §6.2.1）


@pytest.mark.parametrize("table", SOFT_DELETE_TABLES)
async def test_app_role_has_no_delete_on_soft_delete_tables(db_session, table: str) -> None:
    """软删表 ``erp_app`` 无 DELETE（04 §6.2.1 双保险，纠错只走红冲/反审核）。"""
    assert not await has_delete_grant(db_session, table), f"erp_app 竟然能 DELETE {table}"


async def test_app_role_cannot_mutate_append_only_table(db_session) -> None:
    """append-only 表 ``erp_app`` 既无 UPDATE 也无 DELETE（同 §7.16 留痕表）。"""
    assert not await _has_grant(db_session, BANK, "UPDATE"), "erp_app 竟然能 UPDATE 代发留痕"
    assert not await _has_grant(db_session, BANK, "DELETE"), "erp_app 竟然能 DELETE 代发留痕"


# ------------------------------------------------------------ TC-PAY-T15


async def _tables_present_and_paid(url: str) -> tuple[bool, bool]:
    """``(三表是否都在, document_status 是否仍含 PAID)``（同步入口包装，理由同 0020）。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(url, pool_pre_ping=True)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            present = True
            for name in TABLES:
                present = present and await table_exists(session, name)
            paid = "PAID" in await enum_values(session, "document_status")
            return present, paid
    finally:
        await engine.dispose()


def test_migration_downgrade_removes_tables_and_keeps_paid(_schema, migration_url: str) -> None:
    """TC-PAY-T15：``alembic downgrade 0020`` 干净删三表，再 upgrade 还原。

    ⚠️ 必须**真跑**迁移（只读迁移源码证明不了 downgrade 能执行）。
    ⚠️ ``document_status``（含 ``PAID``）由迁移 0009 建，**不得**被本迁移的 downgrade
    误删 —— 故顺带断言 PAID 仍在。
    """
    import os

    from alembic.config import Config

    from alembic import command

    os.environ["DATABASE_URL_MIGRATION"] = migration_url
    config = Config("alembic.ini")
    command.downgrade(config, "0020")
    try:
        present, paid = asyncio.run(_tables_present_and_paid(migration_url))
        assert not present, "downgrade 之后工资单域三表仍在"
        assert paid, "downgrade 误删了 document_status 的 PAID"
    finally:
        command.upgrade(config, "head")
    present, paid = asyncio.run(_tables_present_and_paid(migration_url))
    assert present and paid, "upgrade 还原失败，测试库未回到 head"
