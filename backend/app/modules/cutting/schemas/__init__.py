"""裁剪单的请求 / 响应模型（docs/05 §2、§3 + [modules/02-裁剪](../docs/modules/02-裁剪.md) §6）。

## 三层嵌套的结构（ADR-0017）

``POST /cutting-orders`` 一次性收表头 + 布批行 + 行内颜色 + 尺码明细：

```
CuttingOrderCreateIn
└── lines[]                      布批行（≤200）—— ★ 耗料记在行
    └── colors[]                 行内颜色（≤10）—— 一床可多色
        └── size_lines[]         尺码明细（≤50）—— ★ 出数权威来源
```

## 五条约定（与 ``app/modules/base/schemas.py`` 同款）

1. **请求模型一律 ``extra="forbid"``**（docs/05 §2）—— 少一个字段是 typo，
   多一个字段是「以为服务端会用而其实没用」，后者更贵
2. 响应里**不出现** ``deleted_at`` / ``created_by`` / ``updated_by``
3. **数量与金额在响应里一律 ``str``**（docs/05 §3 铁律）—— JS 的 ``number``
   表示不了 ``0.378000``，而本模块的耗料是 ``numeric(14,3)``
4. 请求侧数量用 ``Decimal``（docs/03 §1.1 第 8 条：**禁止 float**）
5. ``version`` 用**表头聚合行**的版本，不用子表的 —— 全量替换子表会让子表的
   ``version`` 每次从 1 重来，拿它当乐观锁等于没有锁（同 ``RatioReplaceIn``）

## ⚠️ ``hands`` / ``qty_per_hand`` 是 ``int`` 而不是 ``Decimal``（ADR-0020）

``modules/02 §3.4`` 的字段表还写着 ``numeric(14,4)`` +「可小数 1.5 手」，
那是 **ADR-0020 之前**的残留，已被 ADR-0020 取代（「手数是权威输入、件数 = 乘法
**精确值**，不再 floor、不允许 1.5 手」），与同文档 C13/C20/C21、
``04 §7.7.2`` 及**已建的 ``integer`` 列**全部冲突 —— 以 ``04 §7.7.2`` 为准。

用 ``int`` 而不是 ``Decimal`` 的收益是**类型层面就挡住** ``1.5 手``：
写 ``hands: 1.5`` 会在 Pydantic 校验阶段报 ``10001``，而如果声明成
``Decimal`` 再在 service 里判，那条校验很容易被后来的人漏掉。

本包由 :mod:`common_schemas` / :mod:`order_schemas` / :mod:`maintenance_schemas`
三个子模块聚合重导出，**对外导入面保持不变**：现有代码继续使用
``from app.modules.cutting.schemas import X``，不要改深路径（设计稿 §2.2 / §2.8）。
"""

from .common_schemas import (
    MAX_COLORS_PER_LINE,
    MAX_LINES,
    MAX_SIZE_LINES_PER_COLOR,
    ColorCode,
    SizeCode,
    StyleNo,
    Version,
)
from .maintenance_schemas import (
    CuttingOrderPatchIn,
    EntryModeSwitchIn,
    PutColorsIn,
    PutLinesIn,
    PutSizeLinesIn,
    RatioWarningOut,
    SuggestLinesOut,
    SuggestSizeLineOut,
)
from .order_schemas import (
    CuttingOrderCreateIn,
    CuttingOrderListOut,
    CuttingOrderOut,
    LineColorIn,
    LineColorOut,
    OrderLineIn,
    OrderLineOut,
    SizeLineIn,
    SizeLineOut,
)

__all__ = [
    "MAX_COLORS_PER_LINE",
    "MAX_LINES",
    "MAX_SIZE_LINES_PER_COLOR",
    "ColorCode",
    "CuttingOrderCreateIn",
    "CuttingOrderListOut",
    "CuttingOrderOut",
    "CuttingOrderPatchIn",
    "EntryModeSwitchIn",
    "LineColorIn",
    "LineColorOut",
    "OrderLineIn",
    "OrderLineOut",
    "PutColorsIn",
    "PutLinesIn",
    "PutSizeLinesIn",
    "RatioWarningOut",
    "SizeCode",
    "SizeLineIn",
    "SizeLineOut",
    "StyleNo",
    "SuggestLinesOut",
    "SuggestSizeLineOut",
    "Version",
]
