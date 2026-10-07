"""裁剪单四层表模型（T-CUT-001a / 迁移 0009，DDL 权威来源 ``docs/04 §7.7.2``）。

## 三层结构（ADR-0017）

```
cutting_orders                       裁剪单（日期 / 车间 / 款号）
└── cutting_order_lines               ★ 行 = 布批（缸号 + 匹号）—— 耗料记在行
    └── cutting_order_line_colors     ★ 颜色（一床可多个）—— 录入模式 + 比例快照
        └── cutting_order_size_lines  尺码 + hands + qty_per_hand + output_qty
```

「为什么是三层而不是把颜色平铺」写在 ``modules/02 §3.0``（照抄 04 §7.7.3）：
平铺会丢失「每个颜色一套录入模式与一套比例」的语义，而模式**恰恰**是
「这匹布上这个颜色」的属性 —— 换一匹布就可能换模式（同一缸布被两个颜色用时
各自的模式与手数独立）。

## 三条贯穿四张表的建模口径

1. **耗料记在行、出数记在尺码明细**。耗料是**布的属性**，与裁几个颜色无关
   （同一缸布被多色共用时只录一次）；出数是**颜色的属性**。
   余量归行：``line.balance_qty = line.output_qty - Σ(颜色尺码 output_qty)``。
2. **业务键冗余、外键指主键**。``style_no`` / ``dye_lot_no`` / ``bolt_no`` /
   ``color_code`` 一律**不建外键**：``styles.uq_styles_no`` 与
   ``material_stocks.uq_material_stocks_lot`` 都是 ``WHERE deleted_at IS NULL``
   的**部分索引**（为了让款号 / 缸号软删后可复用），而 PG 的外键只能引用普通
   唯一约束或主键 —— 照抄会报 ``there is no unique constraint matching given keys``
   （T-DOCS-003 实测）。与 0005 / 0008 同一口径。
3. **四张表全部软删 + 乐观锁**（都用 :class:`BaseModel`），**没有一张 append-only**。
   裁剪单要反审核、要留痕、要软删历史单据；append-only 那套
   （只有 ``id`` + ``created_at`` + ``created_by``）表达不了「可撤销」。
   唯一的 append-only 表是 ``wip_ledger_lines``（迁移 0008）。

## 本文件**不包含**的东西（T-CUT-001a 范围之外）

数量口径（头汇总重算 / ``floor`` 取整 / 行耗料下限 C33）、乐观锁 UPDATE、
锁批（``material_stocks.locked_qty`` 累加）、``docs/08 §2.1`` 的审核七步 ——
全在 **T-CUT-001b**；接口与页面在 **T-CUT-001c**。所以这里**没有 relationship**：
父子导航是 service 的事，而 service 还没写；先摆 relationship 只会让人误以为
ORM 的级联已经处理了 ``ON DELETE RESTRICT``（它没有 —— RESTRICT 是数据库行为，
ORM 一旦配了 ``cascade="all, delete-orphan"`` 就会与应用账号无 DELETE 权限打架）。
"""

from app.modules.cutting.models.enums import (
    CUTTING_ENTRY_MODE,
    DOCUMENT_STATUS,
    CuttingEntryMode,
)
from app.modules.cutting.models.lines import (
    CuttingOrderLine,
    CuttingOrderLineColor,
    CuttingOrderSizeLine,
)
from app.modules.cutting.models.order import CuttingOrder
from app.modules.cutting.models.outputs import (
    CuttingOutput,
    CuttingScrapRecord,
    scrap_type_enum,
)
from app.modules.cutting.models.sequence import CuttingDocNoSequence

__all__ = [
    "CUTTING_ENTRY_MODE",
    "DOCUMENT_STATUS",
    "CuttingDocNoSequence",
    "CuttingEntryMode",
    "CuttingOrder",
    "CuttingOrderLine",
    "CuttingOrderLineColor",
    "CuttingOrderSizeLine",
    "CuttingOutput",
    "CuttingScrapRecord",
    "scrap_type_enum",
]
