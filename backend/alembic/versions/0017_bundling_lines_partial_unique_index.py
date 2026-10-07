"""Change uq_bundling_order_lines_line from a hard UNIQUE constraint to a partial unique index.

Why: 打菲单明细是**全量替换**语义（PUT /lines = 软删旧行 + 插新行）。硬 `UNIQUE
(doc_id, line_no)` 约束下，「软删旧行 + 插新行且行号相同」必然撞 duplicate key
—— 软删的行仍然占着号。这与 `cutting_orders` 的 `uq_styles_no` /
`material_stocks.uq_material_stocks_lot`（ADR-0025 / 0011 迁移）是同一口径。

部分唯一索引 `WHERE deleted_at IS NULL` 表达的才是业务不变量：**当前有效**的行之间
不重号；软删行已不在业务上，用它占号没有意义。

⚠️ 模型侧（`bundling/models/order.py`）已先改为 Index + `postgresql_where`，
本迁移把数据库对齐到模型，否则 `alembic check` 报漂移。

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-07
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 硬约束（连同其同名 backing index）先删
    op.drop_constraint(
        "uq_bundling_order_lines_line",
        "bundling_order_lines",
        type_="unique",
    )
    # 换成部分唯一索引：仅约束未软删的行
    op.create_index(
        "uq_bundling_order_lines_line",
        "bundling_order_lines",
        ["doc_id", "line_no"],
        unique=True,
        postgresql_where="deleted_at IS NULL",
    )


def downgrade() -> None:
    op.drop_index("uq_bundling_order_lines_line", table_name="bundling_order_lines")
    op.create_unique_constraint(
        "uq_bundling_order_lines_line",
        "bundling_order_lines",
        ["doc_id", "line_no"],
    )
