"""0001 扩展与操作日志表

Revision ID: 0001
Revises:
Create Date: 2026-10-03

内容（全部对应 docs 规范，不含业务表）：
    1. ``pg_trgm`` 扩展 —— docs/04 §5.1 要求**第一条迁移**就建，
       后续所有主数据的模糊候选检索都依赖它
    2. ``data_scope`` 枚举 —— docs/07 §2.1
    3. ``document_logs`` —— docs/04 §7.9，全模块 append-only 审计表
    4. ``document_logs`` 的写保护 —— 应用账号只能读

⚠️ 破坏性 DDL 标注（docs/04 §6.2 要求危险操作写明理由与回滚方式）：
    ``downgrade()`` 会 DROP TABLE / DROP TYPE / DROP EXTENSION。
    回滚代价：``document_logs`` 里的审计记录一并消失。
    因此**生产环境执行 downgrade 前必须先备份**（docs/11 §5 第 3 步）。
    P1 起会有模块依赖 ``pg_trgm`` 建的 GIN 索引，届时不允许再回滚到本版本
    （DROP EXTENSION 会因依赖失败，属预期行为）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: 应用运行账号（docs/04 §6.2.1）
APP_ROLE = "erp_app"


def _app_role_exists() -> bool:
    """应用运行账号是否已存在（角色由 docker/postgres/init 创建）。"""
    return (
        op.get_bind()
        .execute(
            sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"),
            {"role": APP_ROLE},
        )
        .scalar()
        is not None
    )


def upgrade() -> None:
    # 1) 模糊检索扩展：docs/04 §5.1「迁移第一条建 pg_trgm，闸门 4 需在 PG 16 验证」
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # 2) 数据范围枚举
    postgresql.ENUM("SELF", "GROUP", "WORKSHOP", "FACTORY", name="data_scope").create(
        op.get_bind(), checkfirst=True
    )

    # 3) 操作日志表：逐列照抄 docs/04 §7.9
    #    注意：append-only 日志表**豁免** 04 §2 公共字段（无 version / deleted_at /
    #    updated_*），先例即本表；已登记待回写 04 §2（设计稿 §10.1 W2）
    op.create_table(
        "document_logs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "doc_type",
            sa.String(length=32),
            nullable=False,
            comment="CuttingOrder / BundlingOrder / ...",
        ),
        sa.Column("doc_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("doc_no", sa.String(length=32), nullable=False),
        sa.Column(
            "action",
            sa.String(length=32),
            nullable=False,
            comment="CREATE/UPDATE/SUBMIT/APPROVE/...",
        ),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("to_status", sa.String(length=32), nullable=True),
        sa.Column("operator_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "operator_name",
            sa.String(length=64),
            nullable=False,
            comment="冗余姓名：用户改名后日志仍可读",
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "changed_fields",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="字段级 diff",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_document_logs"),
        comment="全模块 append-only 审计表（docs/04 §7.9）",
    )

    # 按单据查历史变更：详情页「变更历史」抽屉的唯一支撑索引
    op.create_index(
        "idx_document_logs_doc",
        "document_logs",
        ["doc_type", "doc_id", sa.text("created_at DESC")],
        unique=False,
    )

    # 4) 审计表写保护（docs/04 §6.2.1）：应用账号连 UPDATE 都没有
    #    角色可能不存在（本地手工起的库跳过了 init 脚本），故先用绑定参数判断，
    #    再执行字面量 REVOKE —— 全程不拼接 SQL 文本（也避开 ruff S608）。
    if _app_role_exists():
        op.execute("REVOKE UPDATE, DELETE ON document_logs FROM erp_app")


def downgrade() -> None:
    # 破坏性 DDL（docs/11 §10 红线 7：需在 ADR 说明回滚方案）
    # 回滚代价：审计记录消失。生产执行前必须已备份（docs/11 §5 第 3 步）。
    if _app_role_exists():
        op.execute("REVOKE UPDATE, DELETE ON document_logs FROM erp_app")
    op.drop_index("idx_document_logs_doc", table_name="document_logs")
    op.drop_table("document_logs")
    postgresql.ENUM(name="data_scope").drop(op.get_bind(), checkfirst=True)
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
