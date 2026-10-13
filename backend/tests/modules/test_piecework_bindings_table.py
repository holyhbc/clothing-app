"""员工工序绑定表 ``piecework_bindings`` 的建表测试（T-PIECE-001 / 迁移 0019）。

DDL 权威来源：``docs/04 §7.1.1``（搬运自 ``modules/04-计件 §3.2``）。

====================  =================================================
TC-PW-B01             表存在且列与模型逐个一致（查 information_schema）
TC-PW-B02             唯一键含 ``work_date`` —— 绑定当天有效、次日要重扫（BR-PW-04）
TC-PW-B03             同日重复绑定被唯一键拦下（**幂等**由 service 负责，不是报错）
TC-PW-B04             ``bundle_no`` **无**外键（码会被作废、打菲单会被反审核）
TC-PW-B05             ``erp_app`` 对本表**无 DELETE**（解绑走 ``is_active=false``，INV-7）
TC-PW-B06             ``SELF`` 列是 ``user_id``，**不是** ``created_by``
====================  =================================================

⚠️ 为什么与流水表分文件：``04 §7.1`` 的每条约束各要一条用例，加上目录查询与夹具后
一个文件顶破 400 行（ADR-0030）—— 读的人要分两次才拼得出全貌，而中间那两次很容易
漏掉一个约束。

⚠️ TC-PW-B04 守的是「**没建**」：码的合法终结方式是作废（``status='VOIDED'``）或
整单反审核（``03 §4.1``），建 FK 会让这两种合法操作在库层被拒绝。
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app.modules.piecework.models import PieceworkBinding
from tests.factories.piecework import (
    binding_key,
    binding_row,
    column_fk_names,
    columns,
    has_delete_grant,
    index_names,
    table_exists,
)

BINDINGS = "piecework_bindings"


# ------------------------------------------------------------ TC-PW-B01


async def test_piecework_bindings_columns_match_model(db_session) -> None:
    """TC-PW-B01：表存在且列与 ORM 逐个一致（**查真库**）。"""
    assert await table_exists(db_session, BINDINGS), f"{BINDINGS} 没建出来"
    # ⚠️ 一侧来自 information_schema、一侧来自 ORM 元数据，两边都取才叫「一致」
    assert await columns(db_session, BINDINGS) == {
        c.name for c in PieceworkBinding.__table__.columns
    }


async def test_binding_indexes_exist(db_session) -> None:
    """员工端列表恒按 ``user_id + work_date`` 查（04-计件 BR-PW-15），没索引就是全表扫。"""
    names = await index_names(db_session, BINDINGS)
    assert "idx_piecework_bindings_user_date" in names
    assert "idx_piecework_bindings_bundle_no" in names


# ------------------------------------------------------------ TC-PW-B02


async def test_binding_unique_key_includes_work_date(db_session) -> None:
    """TC-PW-B02：唯一键**必须含 work_date**（绑定当天有效、次日要重扫，BR-PW-04）。

    ⚠️ 反过来做（唯一键不含 ``work_date``）的后果是员工第二天来了却看到昨天的绑定，
    于是他以为自己还绑在昨天的款号上 —— 界面正常、数据错，且没有一条报错。
    """
    same = binding_key()
    db_session.add(binding_row(**same, work_date=date(2099, 12, 31)))
    db_session.add(binding_row(**same, work_date=date(2100, 1, 1)))
    await db_session.flush()


# ------------------------------------------------------------ TC-PW-B03


async def test_binding_unique_key_blocks_duplicate_bind(db_session) -> None:
    """TC-PW-B03：同日重复绑定命中唯一键 —— 04 §7.1.1 要点，这是**幂等**不是报错。

    ⚠️ 本用例只断言「数据库层拦得住」；把它变成 200 而不是报错，是 service 的职责
    （下一张卡）。
    """
    same = binding_key()
    db_session.add(binding_row(**same, work_date=date(2099, 12, 31)))
    await db_session.flush()
    db_session.add(binding_row(**same, work_date=date(2099, 12, 31)))
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


# ------------------------------------------------------------ TC-PW-B04


async def test_bundle_no_has_no_foreign_key(db_session) -> None:
    """TC-PW-B04：``bundle_no`` **无**外键。

    ⚠️ 守的是「没建」：码会被作废、打菲单会被反审核（``03 §4.1``），建 FK 会让这两种
    **合法**操作在库层被拒绝。绑定只需要「当时扫的是哪张码」这一条追溯信息。
    """
    assert await column_fk_names(db_session, BINDINGS, "bundle_no") == []


async def test_user_and_workshop_have_no_foreign_keys(db_session) -> None:
    """用户/车间列**不建 FK**：人事离职、车间停用重组是合法的档案维护动作。"""
    assert await column_fk_names(db_session, BINDINGS, "user_id") == []
    assert await column_fk_names(db_session, BINDINGS, "workshop_id") == []


# ------------------------------------------------------------ TC-PW-B05


async def test_app_role_has_no_delete_on_piecework_bindings(db_session) -> None:
    """TC-PW-B05：解绑走 ``is_active=false`` + ``deleted_at``（INV-7），不是物理删。"""
    assert not await has_delete_grant(db_session, BINDINGS), "erp_app 竟然能 DELETE 绑定表"


# ------------------------------------------------------------ TC-PW-B06 数据范围


def test_binding_self_column_is_user_not_creator() -> None:
    """TC-PW-B06：``SELF`` 列是 ``user_id``，**不是** ``created_by``。

    ⚠️ 写错的后果很隐蔽：主管为员工代建绑定后，员工端按数据范围过滤时
    「本人绑定的工序」一条都查不到 —— 界面空白，而越权日志上一条记录都没有。
    """
    from app.core.scope import SCOPE_SPECS

    spec = SCOPE_SPECS["piecework_bindings"]
    assert spec.user_column == "user_id"
    assert spec.workshop_column == "workshop_id"


def test_piecework_bindings_left_pending_tables() -> None:
    """已从 ``scope.PENDING_TABLES`` 移出且仍在 ``SCOPE_SPECS``（同流水表 TC-PW-T10）。"""
    from app.core.scope import PENDING_TABLES, SCOPE_SPECS

    assert BINDINGS not in PENDING_TABLES, f"{BINDINGS} 已由迁移 0019 建出，必须移出 PENDING_TABLES"
    assert BINDINGS in SCOPE_SPECS, f"{BINDINGS} 有 workshop_id 却没登记数据范围"
