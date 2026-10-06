from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import Index


def _trgm_index(name: str, expression: str) -> Index:
    """建 docs/04 §5.1 要求的三元组 GIN 索引。

    ⚠️ 必须用**与候选查询完全相同的表达式**，否则 planner 匹配不上、索引白建。
    所以这个表达式和 :mod:`app.modules.base.repository` 里的候选谓词是同一个
    字符串常量 —— 两处必须同时改。

    表达式索引用 ``sa.text`` 声明：SQLAlchemy 没有"字符串拼接列"的类型化写法。
    """
    return Index(
        name,
        sa.text(expression),
        postgresql_using="gin",
        postgresql_ops={sa.text(expression): "gin_trgm_ops"},
    )


#: 候选搜索的模糊匹配表达式（§5.1：``<编码> || ' ' || <名称>``）。
#: ⚠️ 与 :mod:`app.modules.base.repository` 里的候选谓词是**同一个字符串常量** ——
#: 索引表达式与查询谓词不一致时，planner 匹配不上，GIN 索引等于白建。
TRGM_COLORS = "(color_code || ' ' || name)"
TRGM_SIZES = "(size_code || ' ' || name)"
TRGM_OPERATIONS = "(operation_no || ' ' || name)"
TRGM_CUSTOMERS = "(code || ' ' || name)"
TRGM_STYLES = "(style_no || ' ' || name)"
