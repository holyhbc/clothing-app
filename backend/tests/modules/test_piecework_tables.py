"""计件流水表 ``piecework_logs`` 的建表测试（T-PIECE-001 / 迁移 0019）。

====================  =================================================
TC-PW-T01             表存在且**列与模型逐个一致**（查 information_schema，
                      非只看 ORM）
TC-PW-T02             ``piecework_log_type`` 枚举值域 = 四个
TC-PW-T03             ``uq_piecework_logs_idem`` 真拦重复扫码
TC-PW-T04             ``ck_piecework_logs_sign`` 真拦「非红冲行为负」
                      **以及反向**：红冲行为正也拦（否则红冲就没法写）
TC-PW-T05             ``ck_piecework_logs_whole`` 真拦 28.5 件
TC-PW-T06             ``bundle_id`` 外键存在、指向 ``bundles.id``、RESTRICT，
                      且真拦未知码
TC-PW-T07             ``payroll_id`` **无**外键（``piecework_settlements`` 未建）
TC-PW-T08             ``erp_app`` 对本表**无 DELETE**（04 §6.2.1）
TC-PW-T09             ``alembic downgrade 0018`` 干净删两表 + 枚举（真跑迁移）
TC-PW-T10             本表已从 ``scope.PENDING_TABLES`` 移出，且仍在 ``SCOPE_SPECS``
====================  =================================================

⚠️ **约束用例一律真 INSERT 违反行**，而不只读 ``pg_constraint`` 定义 ——
「约束文本看着对」与「违反行真被拒」之间还隔着类型转换与 ``trunc`` 行为：
``numeric(14,3)`` 存 28.5 到底是被直接拒绝还是先四舍五入，只有真插才知道。

⚠️ **每个破坏用例都配一条反证**（「合法的那个必须能插进去」）。只写破坏用例的话，
把约束写成恒假（比如 ``qty <> qty``）会让全部破坏用例都「通过」，
而表已经彻底不能用了 —— TC-PW-T04 的反向用例就是为这件事准备的。

绑定表（``piecework_bindings``）在 `test_piecework_bindings_table.py`。
"""

from __future__ import annotations

import asyncio
import os
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.modules.piecework.models import PieceworkLog, PieceworkLogType
from tests.factories.piecework import (
    BUNDLE_NO,
    EXPECTED_LOG_TYPES,
    OPERATION_NO,
    PIECEWORK_TABLES,
    WORK_DATE,
    column_fk_names,
    columns,
    constraint_def,
    enum_values,
    has_delete_grant,
    index_def,
    index_names,
    log_row,
    objects_present,
    table_exists,
)

LOG = "piecework_logs"


# ------------------------------------------------------------ TC-PW-T01


async def test_piecework_logs_columns_match_model(db_session) -> None:
    """TC-PW-T01：表存在且列与 ORM 逐个一致（**查真库**）。"""
    assert await table_exists(db_session, LOG), f"{LOG} 没建出来"
    # ⚠️ 一侧来自 information_schema、一侧来自 ORM 元数据，两边都取才叫「一致」
    assert await columns(db_session, LOG) == {c.name for c in PieceworkLog.__table__.columns}

    names = await columns(db_session, LOG)
    # 04-计件 §3.1 标了 `*` 的三列此前只在设计稿里，04 §7.1 没有 —— 建表时漏掉它们
    # 就会静默少三个字段（员工端离线缓存与幂等键都靠它们）
    assert {"scanned_at", "device_id", "idempotency_key"} <= names
    # ⚠️ 结算标记是 payroll_id，**不是** is_settled（04 §7.1 要点：避免双源）
    assert "payroll_id" in names
    assert "is_settled" not in names


# ------------------------------------------------------------ TC-PW-T02


async def test_piecework_log_type_has_exactly_four_values(db_session) -> None:
    """TC-PW-T02：枚举值域 = SCAN / MANUAL / REVERSAL / SUPPLEMENT。"""
    assert await enum_values(db_session, "piecework_log_type") == set(EXPECTED_LOG_TYPES)
    assert {member.value for member in PieceworkLogType} == set(EXPECTED_LOG_TYPES)


# ------------------------------------------------------------ TC-PW-T03


async def test_idempotency_key_blocks_duplicate_scan(db_session) -> None:
    """TC-PW-T03：同一 (码, 工序, 员工, 日期, 类型) 插两次 → 唯一冲突。

    ⚠️ 真 INSERT，不只读约束定义。
    ⚠️ 两次必须**同员工**：员工是幂等键的一部分 —— 只重复 ``bundle_no`` 是**合法**的
    （不同员工扫同一码由 BR-PW-29 在 service 层判 32001，不靠这条唯一键；
    靠它的话换个员工重扫就成了「另一条合法流水」）。
    """
    same = {
        "bundle_no": BUNDLE_NO,
        "operation_no": OPERATION_NO,
        "employee_id": uuid4(),
        "work_date": WORK_DATE,
        "log_type": PieceworkLogType.SCAN,
    }
    db_session.add(log_row(**same))
    await db_session.flush()
    db_session.add(log_row(**same))
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


# ------------------------------------------------------------ TC-PW-T04


@pytest.mark.parametrize(
    ("log_type", "qty", "amount"),
    [
        (PieceworkLogType.SCAN, Decimal("-60"), Decimal("-21")),
        (PieceworkLogType.MANUAL, Decimal("-1"), Decimal("-1")),
        # ⚠️ **反向**：红冲必须为负。为正的话「冲销」就成了「再加一次」，金额翻倍还全绿
        (PieceworkLogType.REVERSAL, Decimal("60"), Decimal("21")),
        (PieceworkLogType.SUPPLEMENT, Decimal("-60"), Decimal("-21")),
    ],
)
async def test_sign_check_rejects_wrong_signed_rows(db_session, log_type, qty, amount) -> None:
    """TC-PW-T04：符号规则 —— 只有 ``REVERSAL`` 为负，其余为正。"""
    db_session.add(log_row(log_type=log_type, qty=qty, amount=amount))
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_sign_check_accepts_valid_reversal(db_session) -> None:
    """TC-PW-T04 反证：合法的红冲行（负数 + 指向原流水）必须能插进去。"""
    original = log_row()
    db_session.add(original)
    await db_session.flush()
    db_session.add(
        log_row(
            log_type=PieceworkLogType.REVERSAL,
            reversal_of_id=original.id,
            qty=Decimal("-60"),
            amount=Decimal("-21.0000"),
            bundle_no=f"{original.bundle_no}-REV",
        )
    )
    await db_session.flush()
    assert (await db_session.get(PieceworkLog, original.id)) is not None


# ------------------------------------------------------------ TC-PW-T05


async def test_whole_check_rejects_fractional_qty(db_session) -> None:
    """TC-PW-T05：整件口径 —— 60 的手可以记 28，**不能**记 28.5（09 §4.2）。"""
    db_session.add(log_row(qty=Decimal("28.5"), amount=Decimal("9.9750")))
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_whole_check_accepts_partial_production_integer(db_session) -> None:
    """TC-PW-T05 反证：部分生产（整数件）必须能插进去 —— ADR-0016 明确允许。"""
    db_session.add(log_row(qty=Decimal("28"), amount=Decimal("9.8000")))
    await db_session.flush()


# ------------------------------------------------------------ TC-PW-T06


async def test_bundle_id_foreign_key_points_at_bundles(db_session) -> None:
    """TC-PW-T06：``bundle_id`` 有 FK 指向 ``bundles.id`` 且是 RESTRICT（BR-PW-30）。"""
    assert await column_fk_names(db_session, LOG, "bundle_id") == ["fk_piecework_logs_bundle"]
    definition = await constraint_def(db_session, "fk_piecework_logs_bundle")
    assert definition is not None
    assert "bundles" in definition
    assert "RESTRICT" in definition.upper()


async def test_bundle_id_rejects_unknown_bundle(db_session) -> None:
    """TC-PW-T06 反证：FK 真的拦得住不存在的码（只读约束定义证明不了这个）。"""
    db_session.add(log_row(bundle_id=uuid4()))
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_reversal_of_id_is_self_referencing_restrict(db_session) -> None:
    """红冲行指向原流水；原流水被物理删会让红冲失去依据 → RESTRICT。"""
    assert await column_fk_names(db_session, LOG, "reversal_of_id") == [
        "fk_piecework_logs_reversal_of"
    ]
    definition = await constraint_def(db_session, "fk_piecework_logs_reversal_of")
    assert definition is not None and "RESTRICT" in definition.upper()


# ------------------------------------------------------------ TC-PW-T07


async def test_payroll_id_deliberately_has_no_foreign_key(db_session) -> None:
    """TC-PW-T07：``payroll_id`` **无** FK —— ``piecework_settlements`` 是工资阶段的表。

    ⚠️ 这条钉死一个「没做」：工资本卡建完那张表后会回来加 FK，届时本条应该改成
    「有 FK 且指向 piecework_settlements」。留着是为了让「暂缓」这件事在测试里可见，
    而不是靠「谁记得」。
    """
    assert await column_fk_names(db_session, LOG, "payroll_id") == []


async def test_employee_and_workshop_have_no_foreign_keys(db_session) -> None:
    """车间/用户列**不建 FK**：人事离职、车间停用重组是合法的档案维护动作。"""
    assert await column_fk_names(db_session, LOG, "employee_id") == []
    assert await column_fk_names(db_session, LOG, "workshop_id") == []


# ------------------------------------------------------------ 索引


async def test_required_indexes_exist(db_session) -> None:
    """``04 §5`` 的工厂典型查询 + ``04-计件 §8`` 的索引建议一个都不能少。"""
    names = await index_names(db_session, LOG)
    for index in (
        "idx_piecework_logs_work_date_workshop_id_style_no",
        "idx_piecework_logs_bundle_id",
        "idx_piecework_logs_employee_work_date",
        "idx_piecework_logs_bundle_no",
        "idx_piecework_logs_unsettled",
        "idx_piecework_logs_station",
    ):
        assert index in names, f"缺索引 {index}"


async def test_unsettled_index_is_partial_on_payroll_id(db_session) -> None:
    """未结算索引必须是 ``payroll_id IS NULL`` 的部分索引 —— 全量索引会让结算扫描扫到已结算行。"""
    definition = await index_def(db_session, "idx_piecework_logs_unsettled")
    assert "WHERE" in definition
    assert "payroll_id IS NULL" in definition
    assert "deleted_at IS NULL" in definition


# ------------------------------------------------------------ TC-PW-T08


async def test_app_role_has_no_delete_on_piecework_logs(db_session) -> None:
    """TC-PW-T08：``erp_app`` 对本表**无 DELETE**（04 §6.2.1；纠错只靠红冲 + 重入）。"""
    assert not await has_delete_grant(db_session, LOG), "erp_app 竟然能 DELETE piecework_logs"


# ------------------------------------------------------------ TC-PW-T09


def test_migration_downgrade_removes_tables_and_enum(_schema, migration_url: str) -> None:
    """TC-PW-T09：``alembic downgrade 0018`` 干净删两表 + 枚举，再 upgrade 还原。

    ⚠️ 必须**真跑**迁移：只读 ``pg_constraint`` / 只读迁移源码都证明不了
    ``downgrade`` 能执行 —— 而 downgrade 失败会在发布回滚时才发现。
    ⚠️ ``finally`` 里无论断言结果如何都要还原到 head，否则后面的用例全红。
    """
    from alembic.config import Config

    from alembic import command

    os.environ["DATABASE_URL_MIGRATION"] = migration_url
    config = Config("alembic.ini")
    command.downgrade(config, "0018")
    try:
        present, enum_count = asyncio.run(objects_present(migration_url))
        assert not present, "downgrade 之后 piecework_logs 仍在"
        assert enum_count == 0, "downgrade 之后 piecework_log_type 枚举仍在"
    finally:
        command.upgrade(config, "head")
    present, enum_count = asyncio.run(objects_present(migration_url))
    assert present and enum_count == len(EXPECTED_LOG_TYPES), "upgrade 还原失败，测试库未回到 head"


# ------------------------------------------------------------ TC-PW-T10 数据范围


def test_piecework_logs_left_pending_tables() -> None:
    """TC-PW-T10：已从 ``scope.PENDING_TABLES`` 移出，且**仍在** ``SCOPE_SPECS``。

    ⚠️ 两头都要守，理由与 ``test_cutting_orders_left_pending_tables`` 同款：

    - **移出** ``PENDING_TABLES``：方向是「建了就删」。留着不删的后果不是报错，
      而是那张表既不在待建清单、也不在
      ``test_scope_specs_keys_are_real_or_declared_pending`` 的待建分支里 ——
      而那条守卫恰恰是为了抓「登记了不存在的表名」，清单烂掉它就看不见了；
    - **不能**从 ``SCOPE_SPECS`` 里删掉：漏登记的症状不是越权而是「界面一片空白」，
      从日志里看不出来
    """
    from app.core.scope import PENDING_TABLES, SCOPE_SPECS

    assert LOG not in PENDING_TABLES, f"{LOG} 已由迁移 0019 建出，必须从 PENDING_TABLES 移出"
    assert LOG in SCOPE_SPECS, f"{LOG} 有 workshop_id 却没登记数据范围"
    assert LOG in PIECEWORK_TABLES  # 顺手守住夹具常量与本测试用的是同一份
