"""迁移与数据库权限隔离测试（T-INFRA-004）。

覆盖 TC-I10~I13、I16、I17：
    - 迁移产物结构（扩展 / 枚举 / 表列 / 索引）与 docs/04 对齐
    - ``erp_app`` 的权限边界：能写审计、不能改审计、不能硬删、不能建表
    - 应用连接串必须是**受限账号**，否则权限测试全部失真

⚠️ 所有用例都必须**顺序无关**（docs/10 §2.1）。因此回滚验证采用"同一用例内
开两个会话"的写法，不依赖任何跨用例的执行顺序。
"""

import re
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.factories import FIXED_DATETIME, DocumentLogFactory

DOC_04 = Path(__file__).resolve().parents[2] / "docs" / "04-数据库规范.md"

_INSERT_LOG = text(
    "INSERT INTO document_logs (id, doc_type, doc_id, doc_no, action, "
    "operator_id, operator_name) VALUES (:id, :doc_type, :doc_id, :doc_no, "
    ":action, :operator_id, :operator_name)"
)


# ---------------------------------------------------------------------------
# 迁移产物结构
# ---------------------------------------------------------------------------


async def test_pg_trgm_extension_installed(db_session: AsyncSession) -> None:
    """TC-I10：迁移第一条就装 pg_trgm（docs/04 §5.1 强制）。"""
    extname = await db_session.scalar(
        text("SELECT extname FROM pg_extension WHERE extname = 'pg_trgm'")
    )
    assert extname == "pg_trgm"


async def test_data_scope_enum_matches_07(db_session: AsyncSession) -> None:
    """data_scope 枚举值与顺序必须与 docs/07 §2.1 一致。"""
    labels = (
        (
            await db_session.execute(
                text(
                    "SELECT enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
                    "WHERE t.typname = 'data_scope' ORDER BY e.enumsortorder"
                )
            )
        )
        .scalars()
        .all()
    )
    assert list(labels) == ["SELF", "GROUP", "WORKSHOP", "FACTORY"]


async def test_document_logs_columns_match_04(db_session: AsyncSession) -> None:
    """TC-I11：document_logs 列集合逐列等于 docs/04 §7.9。"""
    columns = (
        (
            await db_session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'document_logs' ORDER BY ordinal_position"
                )
            )
        )
        .scalars()
        .all()
    )
    assert list(columns) == [
        "id",
        "doc_type",
        "doc_id",
        "doc_no",
        "action",
        "from_status",
        "to_status",
        "operator_id",
        "operator_name",
        "reason",
        "changed_fields",
        "created_at",
    ]


async def test_document_logs_has_required_index(db_session: AsyncSession) -> None:
    """详情页「变更历史」依赖 idx_document_logs_doc。"""
    index_names = (
        (
            await db_session.execute(
                text("SELECT indexname FROM pg_indexes WHERE tablename = 'document_logs'")
            )
        )
        .scalars()
        .all()
    )
    assert "idx_document_logs_doc" in index_names


def test_docs_04_section7_9_column_list_is_unchanged() -> None:
    """守卫：docs/04 §7.9 若增删列，本测试失败，提醒同步迁移脚本（docs/12 §9 检查项 1）。"""
    section = DOC_04.read_text(encoding="utf-8").split("### 7.9 操作日志")[1]
    sql_block = section.split("```sql")[1].split("```")[0]
    documented = re.findall(r"^\s{4}(\w+)\s+\w", sql_block, re.MULTILINE)
    assert documented == [
        "id",
        "doc_type",
        "doc_id",
        "doc_no",
        "action",
        "from_status",
        "to_status",
        "operator_id",
        "operator_name",
        "reason",
        "changed_fields",
        "created_at",
    ], "docs/04 §7.9 的列定义已变，必须同步 0001 迁移与本测试"


# ---------------------------------------------------------------------------
# 权限隔离（docs/04 §6.2.1）
# ---------------------------------------------------------------------------


async def test_app_role_can_insert_audit_record(db_session: AsyncSession) -> None:
    """审计必须能写：``erp_app`` 对 document_logs 有 INSERT 权限。"""
    await db_session.execute(_INSERT_LOG, DocumentLogFactory.build(doc_type="PermissionProbe"))
    count = await db_session.scalar(
        text("SELECT count(*) FROM document_logs WHERE doc_type = 'PermissionProbe'")
    )
    assert count == 1


@pytest.mark.parametrize(
    "statement",
    [
        pytest.param("UPDATE document_logs SET reason = '篡改'", id="update"),
        pytest.param("DELETE FROM document_logs", id="delete"),
        pytest.param("TRUNCATE document_logs", id="truncate"),
        pytest.param("CREATE TABLE probe_forbidden (id int)", id="create_table"),
    ],
)
async def test_app_role_cannot_tamper_or_drop(db_session: AsyncSession, statement: str) -> None:
    """TC-I12：受限账号不得篡改审计、不得硬删、不得建表。"""
    with pytest.raises(ProgrammingError):
        await db_session.execute(text(statement))


def test_app_database_url_uses_restricted_account(app_database_url: str) -> None:
    """TC-I17：应用连接串必须是 ``erp_app``。

    若测试用超级账号连接，上面几条权限断言全部失真（超级账号绕过一切授权）。
    """
    assert "erp_app" in app_database_url
    assert "erp_ddl" not in app_database_url


# ---------------------------------------------------------------------------
# 事务回滚隔离（TC-I14）：同一用例内开两个会话，不依赖用例顺序
# ---------------------------------------------------------------------------


async def test_db_session_rollback_discards_writes(db_session: AsyncSession) -> None:
    """TC-I14：``db_session`` 内写入的数据在事务回滚后不可见（隔离语义核心）。

    自包含：写入 → 断言事务内可见 → 显式回滚 → 断言不可见。
    不依赖任何其他用例的执行顺序（docs/10 §2.1）。
    """
    payload = DocumentLogFactory.build(doc_type="RollbackProbe")
    await db_session.execute(_INSERT_LOG, payload)

    inside = await db_session.scalar(
        text("SELECT count(*) FROM document_logs WHERE doc_type = 'RollbackProbe'")
    )
    assert inside == 1, "写入后在同一事务内应可见"

    await db_session.rollback()

    after = await db_session.scalar(
        text("SELECT count(*) FROM document_logs WHERE doc_type = 'RollbackProbe'")
    )
    assert after == 0, "回滚后仍可见，说明隔离未生效"


async def test_fresh_db_session_does_not_see_previous_test_data(
    db_session: AsyncSession, app_database_url: str
) -> None:
    """TC-I14b：新会话看不到上一个用例经 ``db_session`` 写入的行。

    直接开一个**独立 engine**（等于下一个用例的连接）来读，从而不依赖用例顺序。
    """
    engine = create_async_engine(app_database_url, pool_pre_ping=True)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        async with factory() as reader:
            count = await reader.scalar(
                text("SELECT count(*) FROM document_logs WHERE doc_type = 'RollbackProbe'")
            )
        assert count == 0, "上个用例的数据泄漏到了新连接，事务隔离失效"
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# 工厂（docs/10 §7）
# ---------------------------------------------------------------------------


def test_factory_defaults_are_fixed_and_complete() -> None:
    """工厂默认值固定时间与完整字段，测试里不手写字典。"""
    payload = DocumentLogFactory.build()
    assert payload["created_at"] == FIXED_DATETIME
    assert payload["created_at"].tzinfo is not None
    assert set(DocumentLogFactory.defaults) == {
        "id",
        "doc_type",
        "doc_id",
        "doc_no",
        "action",
        "from_status",
        "to_status",
        "operator_id",
        "operator_name",
        "reason",
        "changed_fields",
        "created_at",
    }


def test_factory_overrides_win() -> None:
    payload = DocumentLogFactory.build(doc_type="Custom", action="UPDATE")
    assert payload["doc_type"] == "Custom"
    assert payload["action"] == "UPDATE"
