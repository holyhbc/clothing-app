"""裁剪单 schema 的公共片段：类型别名、三层规模上限与乐观锁版本号。

依赖方向 ``common_schemas ← order_schemas ← maintenance_schemas``，
本模块**不**反向 import 任何同包文件。
"""

from typing import Annotated

from pydantic import Field

# ------------------------------------------------------------------ 入参片段

#: 款号（09 §1.1）。``max_length=32`` 与 ``styles.style_no`` 的列宽一致。
StyleNo = Annotated[str, Field(min_length=1, max_length=32, examples=["HB-2026-0018"])]
#: 色码（09 §1.2），与 ``colors.color_code`` 列宽一致。
ColorCode = Annotated[str, Field(min_length=1, max_length=32, examples=["WHT"])]
#: 尺码码（09 §1.3），与 ``sizes.size_code`` 列宽一致。
SizeCode = Annotated[str, Field(min_length=1, max_length=32, examples=["XL"])]

# ⚠️ **三层嵌套的规模上限**（modules/02 §6）。写在这里而不是散在各处：
# 上限是**契约**的一部分，前端据此分页/虚拟滚动，后端据此拒绝超大请求。
MAX_LINES = 200
MAX_COLORS_PER_LINE = 10
MAX_SIZE_LINES_PER_COLOR = 50

# ------------------------------------------------------------------ 增量维护（b-2）

#: 三个 PUT 接口与 PATCH 共用的乐观锁版本号。
#:
#: ⚠️ 取的是**表头聚合行**的 ``version``，不是子表的 —— 全量替换会把子表旧行软删、
#: 新行从 ``version = 1`` 重来，拿子表的版本当锁等于没有锁（同
#: :class:`app.modules.base.schemas.RatioReplaceIn` 的同一理由）。
Version = Annotated[
    int, Field(ge=1, description="裁剪单表头 version（聚合行乐观锁，不匹配 → 10003）")
]
