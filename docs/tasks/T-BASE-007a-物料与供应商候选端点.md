# T-BASE-007a：物料 / 供应商候选端点

| 项 | 内容 |
| --- | --- |
| 模块 | base |
| 状态 | `done` |
| 优先级 | P1 |
| 依赖 | T-BASE-006（`materials` / `suppliers` 表已建） |
| 被依赖 | T-CUT-001c-3（裁剪单新建页的「级联选料：供应商 → 面料」前两级）、T-BASE-007b（布批候选） |
| 关联设计 | [05-接口设计规范.md](../05-接口设计规范.md) §9.5.2 |
| 估算 | 0.3d |

> ⚠️ **本卡是被裁剪单新建页卡住的**：`OrderLineIn.stock_id` 是必填 UUID
> （ADR-0022「不允许自由输入缸号」），而建表之后**一个布批候选端点都没有** ——
> 也就是说新建页一行布批行都建不出来。`05 §9.5.2` 早就登记了这两个端点，
> 属于「文档已定、实现缺失」，不是新需求。按「供应商 / 面料 / 布批」三张拆，
> 本卡是第一级，布批在 T-BASE-007b。

## 目标

`GET /materials/options` 与 `GET /suppliers/options` 可用，前端能拿到
「编码 + 名称 + 副标识」的候选（05 §9.5.2）。

## 范围

**要做**：

- [x] `MaterialOptionsService.list_material_options` / `list_supplier_options`
- [x] 两个端点 + `x-permission: base:read` + 函数体 `_require`
- [x] 3 例测试

**不做**：

- 布批候选 → **T-BASE-007b**（权限点不同，是 `stock:read`）
- 物料 / 供应商的**档案 CRUD 与停用** → modules/01 的后续卡。
  刻意不凑一套 CRUD：那会让「谁能改批次结存 / 物料档案」变成一个没经过业务拍板的默认值
- 成衣（`FINISHED_GOODS`）候选 → 成衣库存另有入口

## 三条最容易做错的

| # | 陷阱 | 正确做法 |
| --- | --- | --- |
| 1 | 把物料 / 供应商塞进 `RESOURCES` 注册表 | 那样会连带生成列表 / 新建 / 停用 / 导出七个端点 —— 而 `05 §9.5.2` 只登记了候选。**端点比规范多，比端点少更危险**：多出来的接口没有业务拍板 |
| 2 | 候选里返回成衣 | 「裁剪要用的布料」选择器里出现成衣，用户要在一堆成衣里挑布料 |
| 3 | 停用时 `sub` 仍显示类型 | `sub` 只有一个位置。停用时让位给「已停用」（与款号候选同一口径）—— 否则用户选完才收 `20003` |

## 验收标准

- [x] `pytest tests/modules/test_material_stock_options.py -q` 全过
- [x] `ruff check` / `ruff format --check` / `mypy app` 通过
- [x] 闸门 1-5 全绿（748 passed）

## 实际改动

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `backend/app/modules/base/service.py` | +121 | `MaterialOptionsService` 两个方法 + `_check_window` |
| `backend/app/modules/base/router.py` | +53 | 两个端点 |
| `backend/tests/modules/test_material_stock_options.py` | +188 | 3 例 |

**手写行数 ≈ 362**（未超 `AGENTS §7.1` 的 800 行）。

## 抓到的坑

| # | 坑 | 为什么难发现 |
| --- | --- | --- |
| 1 | 物料候选的模糊匹配用三个 `ILIKE` OR | 只要有一项没索引就全表扫。改用 `||` 拼表达式（与 `STYLE_TRGM_QUALIFIED` 同一手法），能走上表达式 GIN |
| 2 | `materials` 没有 `spec` 列 | `05 §9.5.2` 写「返回 `code` / `name` / `spec`」，但表里只有 `material_type`。已按实际列实现；**文档漂移记在 `docs/12` L-079** |

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| — | 布批候选 `available_qty > 0` + FIFO + 不过滤 `purpose` | T-BASE-007b |
| — | `05 §9.5.2` 说物料候选返回 `spec`，实际表里是 `material_type` | docs/12 L-079 |
| — | 物料 / 供应商的档案 CRUD | modules/01 后续卡 |
