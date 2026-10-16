# T-PIECE-009：计件单价查询端点 `GET /piecework/rates`

| 项 | 内容 |
| --- | --- |
| 模块 | piecework（后端）+ base 取价 SQL 复用 |
| 负责人 | backend-dev |
| 状态 | `done` |
| 优先级 | P2（只读、低风险；把 `04 §6.1` 已登记但未实现的端点补上） |
| 依赖 | T-PIECE-001~008（计件表 / 扫码 / 只读 / 纠错 / 台账 / 工位机，均闭环）；T-BASE-002（`operation_rates` 三档建模 + `build_rate_resolve_stmt`） |
| 被依赖 | 无已知消费方（见「背景」） |
| 关联设计 | [`modules/04-计件.md`](../modules/04-计件.md) §6.1（`/piecework/rates` 行）、§5.6（取价）；[`modules/01-基础资料.md`](../modules/01-基础资料.md) §3.4（生效区间）、§9（取价 SQL） |
| 关联规范 | [`adr/0026`](../adr/0026-工序单价分类维度与款号可空建模.md)（三档取价）、`docs/05 §3`（金额字符串）、`docs/07 §3.2`（数据范围铁律） |
| 估算 | 0.5d |

## 背景：为什么单独一张卡，且拖到现在

`GET /piecework/rates` 只出现在两处：`04 §6.1` 的接口清单（**规范列了、代码没有**）
与 `T-PIECE-004` 的「不做什么」表——原备注是**「语义冲突待架构 / 无订阅端，另开卡」**。

「语义冲突」核对后的准确含义（**不是**文档自相矛盾，而是**复用错端点会得到错的语义**）：

- 现存可复用的列表服务 `RateService.list_rates`（`base/service/rate_query_mixin.py`）
  把 `effective_from` / `effective_to` 当**「生效起始日落在该闭区间内」**来筛选
  （`01 §9` 的 `GET /operation-rates` 维护页口径，逐字一致）。
- 而 `/piecework/rates` 要的是**「在某 `work_date` 当天生效」**：
  `effective_from <= work_date AND (effective_to IS NULL OR effective_to > work_date)`
  （`01 §3.4` / `build_rate_resolve_stmt`）。
- 两者**不是同一个条件**：一个判「起点落在区间」，一个判「日期落在区间」。
  照搬 `list_rates` 会把「今天生效的价」错报成「今天新建的价」。故**需新 SQL**。

另：`OperationRateOut.is_current` 在 base 的口径是 `effective_to IS NULL`
（「长期有效」），与本端点「按 `work_date` 生效」**不是一回事**（一条未来的价
`effective_to IS NULL` 但今天并不生效），所以**不复用 `OperationRateOut`**。

**无消费方**：全仓（前端 / 下游卡片）没有调用 `/piecework/rates` 的地方。本卡按
`04 §6.1` 把它补成可用端点，不改动任何既有页面（YAGNI 边界见「风险」）。

## 决策（2026-10-16，用户确认**方案 A**）

> 「按 `work_date` 生效区间返回该 `style_no`（+ 可选 `operation_no`）**命中的适用行**
> （最多三行：`STYLE` / `CATEGORY` / `OPERATION`），每行带 `rate_source`；
> `is_current` 改为「该 `work_date` 生效」；无命中 → `20004`。不用 `list_rates`。」

## 目标

1. 新增 `GET /api/v1/piecework/rates`：按 `style_no`（必填）+ `operation_no`（可选）
   + `work_date`（可选，缺省今天，工厂当地日历）返回**当天生效**的单价行列表。
2. 新增共享 SQL `build_effective_rates_stmt`（放 base `rate_helpers.py`，与
   `build_rate_resolve_stmt` 并列），保证「展示用」与「取价用」两条 SQL 的
   **生效区间 + 三档适用**谓词同源。

## 不做什么

| 项 | 原因 |
| --- | --- |
| 前端页面 / API 封装 | 无消费方；有页面需求时另开前端卡（本卡只保证后端端点可用） |
| 改 `build_rate_resolve_stmt` | 它是 ADR-0026 §2 的权威实现，标注「勿改」；本卡并列新增、不动它 |
| 分页 / 行数上限 | `04 §6.1` 未登记；`operation_rates` 是基础资料量级。**记为风险**，出现大数据量再补 |
| SSE `piecework_counted` 订阅端 | 另一个缺口（T-PIECE-004 同表登记），独立卡 |
| `styles` 数据范围口径（L-121） | 本端点数据范围是 `FACTORY`，不触发 L-121；口径问题仍留架构 |

## 接口

| 方法 | 路径 | 权限点 | 数据范围 | 请求 | 响应 | 错误码 |
| --- | --- | --- | --- | --- | --- | --- |
| GET | `/api/v1/piecework/rates` | `piecework:rate:manage` | `FACTORY` | `style_no`（必填，≤32）、`operation_no`（可选，≤16）、`work_date`（可选，缺省今天） | `list[PieceworkRateOut]`：`operation_no` / `style_no` / `product_category_id` / `unit_price`(str) / `effective_from` / `effective_to` / `rate_source` / `is_current`(按 `work_date` 生效) | `12001` `20001` `20004` |

## 验收标准

- [x] `build_effective_rates_stmt`：`effective_from <= work_date AND (effective_to IS NULL OR effective_to > work_date)` + 三档适用谓词 + `deleted_at IS NULL`；`operation_no` 传了才过滤；按 `operation_no, tier, effective_from DESC` 排序
- [x] `GET /piecework/rates`：无 `piecework:rate:manage` → `12001`；未知款号 → `20001`；无生效行 → `20004`
- [x] 同工序三档全生效 → 返回 3 行，`rate_source` 分别为 `STYLE` / `CATEGORY` / `OPERATION`
- [x] 区间边界：`effective_from == work_date` **含**；`effective_to == work_date` **不含**；未来 `effective_from` **不含**
- [x] 软删行（`deleted_at` 非空）不返回；`operation_no` 缺省时跨工序返回
- [x] 金额 `unit_price` 是**字符串**（`docs/05 §3`）
- [x] 契约重生成：`openapi.json` / `schema.d.ts` 与源码同提交、重跑无 diff
- [x] 契约测试：新增用例覆盖上述全部分支（TC-PW-RT01~11）

## 风险

| 风险 | 缓解 |
| --- | --- |
| 「展示 SQL」与「取价 SQL」漂移（展示的价不是计件真用的那条） | `build_effective_rates_stmt` 与 `build_rate_resolve_stmt` 的谓词逐字对齐并互相在 docstring 里指认；新增「同工序三档」用例锁定形状 |
| 无 `operation_no` 时可能返回较大结果集 | 基础资料量级；本卡不加分页（spec 未登记），记入遗留清单 |
| `is_current` 恒为 `True`（已按 `work_date` 过滤）显得冗余 | 保留并显式注释：它是**显式语义**，防止调用方误用 base 的 `is_current`（`effective_to IS NULL`）口径 |
| 未知款号误用 `StyleService.get_required` 触发 L-121 的款号范围口径 | 走 `pricing.py::resolve_price` 同款做法：直接 `SELECT styles.category_id`，查不到 → `20001`（不碰款号数据范围） |

## 实施结果

**状态：done（2026-10-16）**。后续端卡，消费 T-BASE-002 的三档取价实体；**无迁移、无新权限点**。

### 交付物

| 文件 | 作用 |
| --- | --- |
| `backend/app/modules/base/service/rate_helpers.py` | `+build_effective_rates_stmt`（当天生效的三档适用行；与 `build_rate_resolve_stmt` 同源，不改后者） |
| `backend/app/modules/piecework/rates.py` | `list_effective_rates`：查 `styles.category_id` → 取生效行 → 映射（未知款号 `20001` / 无价 `20004`） |
| `backend/app/modules/piecework/schemas.py` | `+PieceworkRateOut`（`is_current` 按 `work_date`，**不复用** `OperationRateOut`） |
| `backend/app/modules/piecework/query_router.py` | `GET /piecework/rates`（`piecework:rate:manage`） |
| `backend/tests/modules/test_piecework_rates.py` | TC-PW-RT01~11（权限 / `20001` / `20004` / 三档 / 三条区间边界 / 软删 / 跨工序 / 默认日期） |
| `backend/openapi.json`、`frontend/packages/shared/src/api/schema.d.ts` | 契约重生成（`+221` / `+143`，重跑无 diff；`permissions.ts` 零 diff） |

### 闸门

| 闸门 | 结果 |
| --- | --- |
| 1 lint | ✅ `ruff check` + `ruff format --check`（顺带修 `test_piecework_operators.py` 一处 101 列漂移，非本卡范围） |
| 2 typecheck | ✅ 后端 `mypy app` 189 文件；前端 `pnpm typecheck` 全包 |
| 3 单测 | ✅ 后端 **1102 passed / 90.94%**（新增 11 条） |
| 4 迁移 | ✅ 本卡**无迁移**；`alembic check` 无漂移（未跑 upgrade/downgrade 往返） |
| 5 镜像 | ✅ `api-image` 构建 + `import ok`；本卡未改前端源码，`web-image` 未重建 |

### 遗留（登记，不在本卡）

| 项 | 说明 |
| --- | --- |
| 无消费方 | 端点暂无前端调用；出现页面需求时另开前端卡 |
| 无 `operation_no` 时结果集可能偏大 | spec 未登记分页；`operation_rates` 是基础资料量级，出现大数据量再补 |
| SSE `piecework_counted` 订阅端 | T-PIECE-004 同表登记的另一个缺口，独立卡 |
