"""裁剪单的**业务逻辑**与**事务边界**（docs/03 §1.3：service 是唯一的核心）。

## 本文件只做「草稿态」（T-CUT-001b-1）

**不做**状态机的任何动作（``submit`` / ``approve`` / …）。原因不是省事，而是
那些动作**依赖四张还不存在的表**（详见任务卡）：审核第 ① 步要写
``stock_ledger_lines``（**连字段表都没有**）、第 ④ 步要 ``bom_items``、
第 ⑦ 步要 ``cutting_outputs``、反审核要 ``bundles.counted_at``。

## 三条贯穿全文件的口径

### ① 三级汇总一律重算，**从不信任前端**（C6）

```
cutting_order_size_lines.output_qty     ← 唯一的出数权威来源
    ↓ Σ
cutting_order_line_colors.output_qty_total / balance_qty_total / hands_total
    ↓ Σ
cutting_order_lines.output_qty(用户录入的估算值) / balance_qty / fabric_qty / waste_qty
    ↓ Σ
cutting_orders.fabric_qty / output_qty / cut_waste_qty / balance_qty / hands_total
```

⚠️ **表头的五列在入参 Schema 里连字段都没有**（``CuttingOrderCreateIn``）——
传了会被 ``extra="forbid"`` 报 ``10001``，而不是「悄悄被忽略」。
**能被忽略的入参是最坏的一种**：前端以为设的值生效了。

### ② 取整口径：整数精确乘法，**不 floor**（C13 / C21 / ADR-0020）

``output_qty = hands × qty_per_hand``，两个都是 ``int``，所以**乘法本身就没有
小数**，``floor`` / ``round`` 在这里是**多余的代码**。⚠️ 一旦引入 ``floor``，
就等于允许 ``1.5 手`` 这种非法值悄悄通过（P0 的错误码 ``30006`` 就是为它准备的）。

### ③ 行余量：正向录入值 - 明细合计（C34 口径 A，业务确认 2026-10-04）

```
行 balance_qty = 行 output_qty（用户按铺布实耗正向录入）
                 - Σ(颜色 Σ尺码 output_qty)
```

⚠️ **不要**把行 ``output_qty`` 覆盖成明细合计 —— 那会让 ``balance_qty`` 恒为 0，
「行余量」这个概念整个消失。这正是本次修掉的原缺陷（``modules/02 §6`` ①
原写「服务端算」）。为负 → ``30002``。
"""

# ---------------------------------------------------------------------------
# ⚠️ 以上是原 ``cutting/service.py`` 的模块 docstring，**逐字保留**（拆分不改业务说明）。
# 拆分后本包实际为：组合类 ``CuttingOrderService``（5 个 Mixin）+ ``recalc.py``
# 模块级纯函数，见设计稿 docs/modules/cutting-system-auth-cli-拆分.设计.md §2.3。
# 分层：``common``（常量 / 取单 / 乐观锁）不依赖任何 mixin；各 Mixin 只依赖
# common / recalc / models / schemas / repository / core；``cutting_order_service``
# 组合 mixin；本 ``__init__`` 最后 import，保证
# ``from app.modules.cutting.service import X`` 零改动。
# ⚠️ ``recalc_*`` 是 REQ-000 §2 的核心复用资产，**必须包入口可导入**：
#    ``from app.modules.cutting.service import recalc_order``（供 bundling 复用）。
# ---------------------------------------------------------------------------

from .cutting_order_service import CuttingOrderService
from .recalc import recalc_color, recalc_line, recalc_order

__all__ = ["CuttingOrderService", "recalc_color", "recalc_line", "recalc_order"]
