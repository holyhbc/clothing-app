"""组织、字典与工序主数据模型（docs/04 §7.4 / §7.8.1 / §7.11 + modules/01 §3.1）。

本模块十张表，分三类，**软删与删除策略完全不同**：

============  ==========================================================
表            删除策略
============  ==========================================================
字典四表      **未被引用可真删**，被引用只能停用（ADR-0025）
              ``colors`` / ``sizes`` / ``size_groups`` / ``size_group_items``
其余六表      只能软删（``deleted_at``），应用账号连 DELETE 权限都没有
============  ==========================================================

⚠️ **模型必须与迁移 0004 逐字一致**，否则 autogenerate 反复产生 diff。
T-AUTH-001 已经吃过一次亏（CHECK 约束、悬空 FK），这里的每个约束名都对着迁移核过。

⚠️ ``size_class`` 用 PG enum（04 §7.4 的 ``CREATE TYPE``），而
``users.data_scope`` 等是 ``String`` —— 两者不一致是规范本身的现状
（04 §3 规定 PG enum 只能追加值、不得改值，所以新设计一律走 enum）。
"""

from app.modules.base.models._shared import (
    TRGM_COLORS,
    TRGM_CUSTOMERS,
    TRGM_OPERATIONS,
    TRGM_SIZES,
    TRGM_STYLES,
    _trgm_index,
)
from app.modules.base.models.dict import (
    SIZE_CLASS,
    Color,
    Operation,
    ProductCategory,
    Size,
    SizeGroup,
    SizeGroupItem,
)
from app.modules.base.models.material import Material, MaterialCategory, Supplier
from app.modules.base.models.org import UomUnit, Warehouse, Workshop, WorkshopGroup
from app.modules.base.models.rate import OperationRate
from app.modules.base.models.stock import MaterialStock, WipLedgerLine, WipStock
from app.modules.base.models.style import (
    Customer,
    Style,
    StyleColor,
    StyleColorSizeRatio,
    StyleNoSequence,
    StyleOperation,
    StyleSize,
)

__all__ = [
    "SIZE_CLASS",
    "TRGM_COLORS",
    "TRGM_CUSTOMERS",
    "TRGM_OPERATIONS",
    "TRGM_SIZES",
    "TRGM_STYLES",
    "Color",
    "Customer",
    "Material",
    "MaterialCategory",
    "MaterialStock",
    "Operation",
    "OperationRate",
    "ProductCategory",
    "Size",
    "SizeGroup",
    "SizeGroupItem",
    "Style",
    "StyleColor",
    "StyleColorSizeRatio",
    "StyleNoSequence",
    "StyleOperation",
    "StyleSize",
    "Supplier",
    "UomUnit",
    "Warehouse",
    "WipLedgerLine",
    "WipStock",
    "Workshop",
    "WorkshopGroup",
    "_trgm_index",
]
