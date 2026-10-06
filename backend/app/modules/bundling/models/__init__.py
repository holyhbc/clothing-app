"""打菲模块模型包（T-BUND-001 / 迁移 0014）。

## 四张表的关系

```
bundling_orders                 打菲单表头（来源裁剪单必须 APPROVED）
└── bundling_order_lines        按尺码的明细（件数/手数权威来源 = cutting_size_line_id）
    └── bundles                 一码一手（ADR-0016：手序号 + 该手件数）
         ╲
          bundle_label_prints   标签打印留痕（append-only，逻辑引用 bundle_no）
```

## 三条贯穿四张表的建模口径

1. **``bundles`` 照抄 ``04 §7.0``，不改写**：它是打菲 / 计件 / 标签共用的物理契约
   （``modules/03 §3.3`` 明令）。本包与 §7.0 逐列一致，只把约束 / 索引写成 ORM。
2. **手数是整数、件数不取整**（ADR-0020，变更 0111）：``bundling_order_lines.hands``
   与 ``cutting_order_size_lines.hands`` 都是 ``integer``；件数 = 整数乘法精确值。
3. **一张软删表对一张 append-only 表**：前三张用 :class:`BaseModel`（软删 + 乐观锁），
   ``bundle_label_prints`` 用 :class:`IdMixin`（append-only，只有 id/created_at/created_by）。

本包**只做字段与约束**，无 relationship 写入逻辑、无 service / repository。
"""

from app.modules.bundling.models.bundle import (
    BUNDLE_STATUS,
    Bundle,
    BundleLabelPrint,
    BundleStatus,
)
from app.modules.bundling.models.order import BundlingOrder, BundlingOrderLine

__all__ = [
    "BUNDLE_STATUS",
    "Bundle",
    "BundleLabelPrint",
    "BundleStatus",
    "BundlingOrder",
    "BundlingOrderLine",
]
