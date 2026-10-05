"""0010 裁剪单取号计数器 `cutting_doc_no_sequences`（T-CUT-001b-1）。

唯一理由：C1 要求 ``doc_no`` 是 ``CT-{YYYYMMDD}-{6 位序号}``，且
**「生成即占用、永不复用」**。这需要**按天**递增的计数器。

## 为什么单独一张表，而不是 PG 的 SEQUENCE

| | PG ``SEQUENCE`` | 本仓的做法（计数表） |
| --- | --- | --- |
| 按天重置 | 要靠 ``ALTER SEQUENCE`` 或调 ``setval``，**在应用事务里做不了** | ``(doc_date, prefix)`` 就是唯一键，新的一天自然是一行 |
| 回滚语义 | ``nextval`` **不回滚**（刻意的，因为「生成即占用」） | 随事务回滚 |
| 可观测 | 要连系统表查 | 一条 ``SELECT`` |

⚠️ **回滚会不会导致重号**：会 —— 事务回滚时 ``next_no`` 一起退回，下一个事务
拿到同一个号。但那个号**从未出现在任何响应里**（单据没建成），所以「重号」这件事
在业务上不可观测。**这与款号的差别在于**：款号是用户自己填的（Q-P0-04，服务端只
给建议），而单据号是服务端发的 —— 发出去的单号必须唯一，所以走计数表 + 唯一索引
``uq_cutting_orders_doc_no`` 兜底。

## 豁免 ``04 §2`` 公共字段（与 ``style_no_sequences`` 同一理由）

纯计数表没有「谁改的」的概念：它由取号逻辑在事务内 ``UPDATE ... RETURNING``
递增，而 ``created_by`` / ``updated_by`` 是 ``NOT NULL`` 的**操作人**字段 ——
计数行的「创建者」只能是系统，那才是谎话。所以只留 ``id`` + 分组列 + ``next_no``。

⚠️ **唯一性用普通唯一约束而不是复合主键**：与 ``style_no_sequences`` 同款 ——
本表的主键列不需要可空，所以主键其实也行，但**与既有表保持同一形状**更重要，
读代码的人不必重新判断一次。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811 —— 别名照抄 docs/04 §2

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cutting_doc_no_sequences",
        sa.Column(
            "id",
            PgUUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        # ⚠️ 分组列是 `doc_date` 而不是 `year`：C1 的格式里**有日期**，
        # 「每天从 1 开始」是格式的直接后果。用 `date` 而不是 `year` 的好处是
        # 将来若改格式（加前缀、加车间段），分组维度跟着改就行，不必动数据。
        sa.Column(
            "doc_date",
            sa.Date(),
            nullable=False,
            comment="按天分组：单据号 CT-{YYYYMMDD}-{6 位} 的 YYYYMMDD 部分",
        ),
        sa.Column(
            "prefix",
            sa.String(16),
            server_default=sa.text("'CT'"),
            nullable=False,
            comment="单据前缀（09 §2.1：裁剪 = CT）；写成一列是为了将来分单据类型时不必改表",
        ),
        sa.Column(
            "next_no",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
            comment="下一个可用序号，从 1 开始（09 §2.1：6 位）",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_cutting_doc_no_sequences"),
        sa.UniqueConstraint("doc_date", "prefix", name="uq_cutting_doc_no_sequences_date_prefix"),
        sa.CheckConstraint("next_no >= 1", name="ck_cutting_doc_no_sequences_next_no"),
        comment="裁剪单号按天计数器（纯计数表，豁免 04 §2 公共字段，同 style_no_sequences）",
    )


def downgrade() -> None:
    op.drop_table("cutting_doc_no_sequences")
