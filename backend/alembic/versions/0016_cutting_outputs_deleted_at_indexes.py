"""Add deleted_at indexes on cutting_outputs and cutting_scrap_records.

T-BASE-009-2 收尾：`SoftDeleteMixin.deleted_at` 带 `index=True`，而迁移 0015 建这两张
表时漏了对应索引。列表查询恒带 `WHERE deleted_at IS NULL`（INV-7），没有索引就是全表扫。

⚠️ **手写而非 autogenerate**：autogenerate 生成的迁移是哈希文件名（`5d8fab9de859`），
本仓迁移一律用可读的日期序号命名（见 0001~0015），否则 `alembic history` 里根本读不出
这条迁移做了什么。

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-07
"""

from __future__ import annotations

from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_cutting_outputs_deleted_at",
        "cutting_outputs",
        ["deleted_at"],
    )
    op.create_index(
        "ix_cutting_scrap_records_deleted_at",
        "cutting_scrap_records",
        ["deleted_at"],
    )
    # 0015 漏了 `style_id` 的列注释（`style_no` 有、`style_id` 没有），补齐到与模型一致
    op.alter_column(
        "cutting_outputs",
        "style_id",
        comment="冗余：历史结转按它引用",
        existing_type=postgresql.UUID(as_uuid=True),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "cutting_outputs",
        "style_id",
        comment=None,
        existing_type=postgresql.UUID(as_uuid=True),
        existing_nullable=False,
    )
    op.drop_index("ix_cutting_scrap_records_deleted_at", table_name="cutting_scrap_records")
    op.drop_index("ix_cutting_outputs_deleted_at", table_name="cutting_outputs")
