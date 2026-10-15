# T-PIECE-005：PC 计件流水台账页

| 项 | 内容 |
| --- | --- |
| 模块 | piecework（前端 admin） |
| 负责人 | frontend-dev |
| 状态 | `done` |
| 优先级 | P2（把 T-PIECE-003 的只读端点接到主管屏幕上，纯读、低风险） |
| 依赖 | T-PIECE-003（`GET /piecework-logs` / `/{id}` / `/export`，均已闭环）；T-WEB-001~006（admin 基座） |
| 被依赖 | 主管对账、工资结算前的流水核对 |
| 关联设计 | [`modules/04-计件.md`](../modules/04-计件.md) §6.1（列表 / 详情 / 导出）；[`06-前端与UI规范.md`](../06-前端与UI规范.md) §2.2（列表页标准结构）、§2.3（详情）、§2.6（导出） |
| 关联规范 | `docs/05 §9.1`（导出文件名带时间戳）、`docs/07 §2.2`（`piecework:read` / `piecework:export`） |
| 估算 | 0.5d |

## 目标

主管打开 PC 端「计件流水」页，按**日期区间 + 款号 + 工序 + 车间 + 类型**筛出流水，
看到**手数与件数并列**（BR-PW-31）的台账；点「详情」看单条的码信息与红冲关联；
点「导出」拿到与列表**同一套筛选**的 CSV。

## 不做什么

| 项 | 原因 |
| --- | --- |
| 工位机扫码页（06 §2.5） | 独立卡，涉及全局 keydown / 离线队列 / 工位绑定，改天单开 |
| 汇总页（`GET /piecework/summary`） | 独立卡；本页只做台账（列表 + 详情 + 导出） |
| 红冲 / 补录的操作入口 | 写操作要权限 + 二次确认 + 原因必填，独立卡（T-PIECE-006） |
| 员工筛选（按 `employee_id`） | 无「员工下拉候选」端点，且 UUID 对人不可读；先不做，登记 Q-PW17 |

## 验收标准

- [x] 路由 `piecework/logs`（`piecework:read`）+ 菜单「生产管理 → 计件流水」；`PERM.PIECEWORK_READ`
- [x] 筛选：日期区间（默认空=全部）/ 款号 / 工序 / 车间 / 类型（SCAN/REVERSAL/SUPPLEMENT/MANUAL）/ 打菲号 / 结算状态；「查询」「重置」
- [x] 表格列：工作日期 / 员工（工号+姓名）/ 款号 / 工序 / 打菲号（不截断可复制）/ 色·码 / 第几手（第 X/Y 手）/ **件数** / 单价 / **金额** / 类型 / 结算 / 操作（详情）
- [x] 金额右对齐 + `tabular-nums`；打菲号 `nowrap` + 省略 + `title`（06 §2.2）
- [x] 详情用 Drawer：本体 + 码信息（第几手 / 手件数 / 当前计件人）+ 原流水 / 被红冲流水链
- [x] 导出按钮：`v-can="PERM.PIECEWORK_EXPORT"`；带当前筛选；文件名 `计件流水_{摘要}_{时间}.csv`（05 §9.1）
- [x] 四态（加载 / 空 / 错误+重试）走 `usePageList` + `EmptyState`
- [x] 组件测试：列表渲染 / 筛选传递 / 导出调用 / 空态 / 错误态

## 风险

| 风险 | 缓解 |
| --- | --- |
| 导出复用 `useExport` 会硬拼 `.xlsx` | 不复用 `useExport`（它绑死 xlsx），只复用其 `triggerBlobDownload`；本地拼 `.csv` 文件名 |
| `ReverseIn` 类型同名坑 | 本页只读，不涉及；详情里 `log_type` 用本地类型标签（不是文档状态，不进 `StatusTag`） |
| 单文件超 400 行 | 列表页拆 `columns.ts` + 详情 `components/LogDetailDrawer.vue` |

## 实施结果

**状态：done（2026-10-15）**。纯前端卡，消费 T-PIECE-003 已闭环的三个只读端点，**无后端改动、无迁移、无契约重生成**。

### 交付物

| 文件 | 说明 |
| --- | --- |
| `frontend/packages/shared/src/types/index.ts` | 追加计件别名：`PieceworkLogType` / `PieceworkLogRowOut` / `PieceworkLogDetailOut` / `PieceworkSummaryRowOut` / `PieceworkStationTodayOut`（全部 `ApiModel<...>`，不手写 DTO） |
| `frontend/packages/shared/src/index.ts` | 把上述别名加进具名导出（`src/index.ts` 是**显式导出清单**，不加外面 import 不到 —— 第一次 typecheck 就栽在这） |
| `frontend/packages/admin/src/api/piecework.ts` | `PieceworkLogQuery` + `listPieceworkLogs` / `getPieceworkLog` / `exportPieceworkLogs` |
| `frontend/packages/admin/src/views/piecework/columns.ts` | 13 列定义（`scroll.x = 1560`） |
| `frontend/packages/admin/src/views/piecework/List.vue` | 台账列表（`usePageList` + 四态） |
| `frontend/packages/admin/src/views/piecework/components/LogDetailDrawer.vue` | 详情抽屉（本体 + 码信息 + 红冲关联链） |
| `frontend/packages/admin/src/views/piecework/List.test.ts` | 组件测试 TC-PW-L01~L05 |
| `frontend/packages/admin/src/router/index.ts` | 路由 `piecework/logs`（`PERM.PIECEWORK_READ`） |
| `frontend/packages/admin/src/layouts/menu.ts` | 生产管理 → 计件流水 |

### 实现期暴露 / 偏离

1. **`@garment/shared` 的 `src/index.ts` 是显式导出清单**（`export { X, Y } from './types/index.ts'`），不是 `export *`。往 `types/index.ts` 加了类型但忘了加进清单，admin 侧 `TS2305: has no exported member` —— 只读共享包源码看不出来。
2. **`collapsed` 是只读入参、没有内置折叠开关**：`PageLayout` 把 `filterCollapsed`（默认 `true`）透传给 `#filter` 插槽，页面**没有**「更多条件」按钮。原稿把「车间 / 类型 / 结算 / 打菲号」包在 `v-if="collapsed === false"` 里 → 这几个筛选**永远不渲染**。改为全部常显（与打菲 `List.vue` 一致）。
3. **antd `Select` 的 `SelectValue` 不接受 `boolean`**：结算状态原用 `value: true/false`，`vue-tsc` 报 `Type 'boolean' is not assignable to type 'SelectValue'`。改为 `'true'/'false'` 字符串 + 提交前转回 `boolean`。
4. **导出不复用 `useExport`**（其扩展名写死 `.xlsx` 且要求 `exportXlsx` 签名）：只复用其纯函数 `buildExportFilename` / `triggerBlobDownload`，本地把 `.xlsx` 换成 `.csv`。
5. 详情抽屉的 `bundle` / `reversal_of` / `reversed_by` 都是**可选**字段，模板里 `!== null` 挡不住 `undefined`（`TS18048 possibly undefined`），改用真值判断 + 可选链。

### 闸门（前端；后端本卡无改动，闸门 4/5 不适用）

- 闸门 1 lint：`pnpm run lint`（eslint）**通过**。
- 闸门 2 typecheck：`pnpm -C packages/shared typecheck` + `pnpm -C packages/admin typecheck`（vue-tsc）**通过**。
- 闸门 3 单测：`pnpm run test:unit` **248 passed**（原 243 + 本卡 5）。
- 闸门 5 构建：`pnpm run build` **通过**（含 `LabelPrint` 路由 chunk）。
- 契约：`openapi.json` / `schema.d.ts` **未变**（无后端改动，无需 `generate:api`）。

### 遗留

- 员工筛选（`employee_id`）未暴露：无「员工下拉候选」端点，登记 **Q-PW17**。
- 汇总页、工位机扫码页、红冲/补录写入口仍为独立卡。
