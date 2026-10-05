"""0011 裁剪单三层的唯一键改为**部分唯一索引**（T-CUT-001b-2）。

## 起因：测试撞出来的真实缺陷

`04 §7.7.2` 把三层的唯一键写成**普通 UNIQUE 约束**：

    CONSTRAINT uq_cutting_order_lines     UNIQUE (doc_id, line_no)
    CONSTRAINT uq_cutting_line_colors     UNIQUE (line_id, color_code)
    CONSTRAINT uq_cutting_size_lines      UNIQUE (line_color_id, size_line_no)

而 `modules/02 §6` 的三条 PUT 接口是**全量替换**语义（前端把页面上现有的行原样
带回再改要改的那几个）。这两者**不可兼得**：

    先把旧行软删（``deleted_at = now()``），再插入新行
      → 实测报 duplicate key value violates unique constraint "uq_cutting_order_lines"
      → Key (doc_id, line_no)=(..., 1) already exists

**普通 UNIQUE 不区分软删** —— 软删的行仍然占着号，所以「替换」永远撞号。

## 修法：改为 ``WHERE deleted_at IS NULL`` 的部分唯一索引

这与本仓既有四处**完全同一口径**（`0005` 的 `styles.uq_styles_no`、
`style_color_size_ratios`、`0008` 的 `material_stocks.uq_material_stocks_lot`、
`wip_stocks.uq_wip_stocks_src`）—— 那些表的注释里都写着理由：

> 「唯一键用 ``WHERE deleted_at IS NULL``，为了让款号**软删后可复用**」

裁剪单的三层需要的就是同一条：**草稿态反复编辑时，行号 / 尺码行号必须能重用**。

⚠️ **不是「因为已有先例所以照抄」，而是因为约束的语义就该是这样**：唯一约束要表达
的是「**当前有效的**行之间不能重号」，而软删行已经不在业务上了，用它占号没有意义。

## ⚠️ ``uq_cutting_orders_doc_no`` **故意不动**

单据号**绝对不能**软删后复用 —— C1「生成即占用、**永不复用**」。
它是普通 UNIQUE，这是**对的**，不要「顺手」改成部分索引：
改了之后删掉一张单，那个号会被第二次发给另一张单，而两者的操作日志都指向同一个
``doc_no``，事后完全查不清谁是谁。

## 为什么不需要新迁移来「搬数据」

这三个约束改完之后，``alembic check`` 会报 ``modify_constraint`` →
``drop_constraint`` + ``create_index``，本迁移就是这么干的。
不改 ``04 §7.7.2`` 的 DDL 文本的话，`test_docs_ddl_sync` 的 TD-02 不管约束，
但**下一个照 §7.7.2 建表的人会建成硬约束** —— 所以文档同步改（见 docs/04 §7.7.2）。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: 约束名 → (表名, 列)。降级时按同一份数据还原。
_PARTIAL_UNIQUES: dict[str, tuple[str, tuple[str, ...]]] = {
    "uq_cutting_order_lines": ("cutting_order_lines", ("doc_id", "line_no")),
    "uq_cutting_line_colors": ("cutting_order_line_colors", ("line_id", "color_code")),
    "uq_cutting_size_lines": (
        "cutting_order_size_lines",
        ("line_color_id", "size_line_no"),
    ),
}


def upgrade() -> None:
    for name, (table, columns) in _PARTIAL_UNIQUES.items():
        op.drop_constraint(name, table, type_="unique")
        op.create_index(
            name,
            table,
            list(columns),
            unique=True,
            postgresql_where=sa.text("deleted_at IS NULL"),
        )


def downgrade() -> None:
    for name, (table, columns) in _PARTIAL_UNIQUES.items():
        op.drop_index(name, table_name=table)
        # ⚠️ 还原成硬 UNIQUE **可能失败**：如果库里已经有「软删行 + 新行同号」的数据
        #    （正是本迁移允许的状态），硬约束建不出来。
        #    这正是本迁移存在的理由，所以**不**加 IF NOT EXISTS 之类的东西掩盖它 ——
        #    真要回滚时人必须先决定「这些重复的行怎么处理」。
        op.create_unique_constraint(name, table, list(columns))
