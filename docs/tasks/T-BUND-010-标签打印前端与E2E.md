# T-BUND-010：QR/标签打印前端 + P1 打菲 E2E（P1 出口）

| 项 | 内容 |
| --- | --- |
| 模块 | bundling |
| 负责人 | frontend-dev + qa |
| 状态 | `done` |
| 优先级 | P1（P1 里程碑出口，`REQ-000 §7` 第 2 周） |
| 依赖 | T-BUND-009、T-BUND-006、T-BUND-007 |
| 被依赖 | — |
| 关联设计 | [`modules/03-打菲.md`](../modules/03-打菲.md) §5.4 标签规格、§11.5、B14/B15/B25；[`P1-打菲-实施说明.md`](../modules/P1-打菲-实施说明.md) §6 |
| 关联规范 | `docs/06 §4`（标签 40mm×40mm）、`docs/10`（E2E） |
| 估算 | 0.5d |

## 目标

标签打印前端（含二维码/Code128 与 40mm×40mm 打印样式、批量/按手区间重打），
并跑通打菲全链路 E2E：建单 → 提交 → 审核生成码 → 打印 → 计件可扫。

## 范围

**要做**：
- [x] `views/bundling/LabelPrint.vue`：打印预览网格、按手区间选择、重打、`@page { size: 40mm 40mm; margin: 0 }`
- [x] 二维码（纯文本 `bundle_no`，纠错 M，≥20mm）+ Code 128（同内容）
- [x] 打印登记：调 `POST /label-prints`，**必传 `hands_seq`**；重打写日志
- [x] `frontend/e2e/p1-bundling-flow.spec.ts`：建单 → 提交 → 审核 → 码数=手数可见 →
      标签含「第 N 手 / 共 M 手」与件数 → 重打留痕
- [x] `backend/tests/e2e_seed.py` 补打菲所需数据（款号/工序/裁剪单已审核）

**不做**：
- 不接条码打印机驱动/标签软件（`REQ-000 §8` 非目标：浏览器打印 + CSV 导出）
- 不改后端接口（发现缺口另开修复卡）

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `frontend/packages/admin/src/views/bundling/LabelPrint.vue` | 新增 | 打印预览与登记 |
| `frontend/packages/admin/src/views/bundling/LabelPrint.test.ts` | 新增 | 单测 |
| `frontend/e2e/p1-bundling-flow.spec.ts` | 新增 | E2E |
| `backend/tests/e2e_seed.py` | 修改 | 补打菲 seed |

## 实现要点（必读规范）

- [x] `docs/06 §4` 标签尺寸、`@media print` 隐藏交互元素、条码/二维码内容 = `bundle_no`
- [x] **重打必须带 `hands_seq`**，缺 → `10002`（后端 T-BUND-006）
- [x] E2E 用独立库 + 与生产一致的授权（照 T-WEB-006/CUT-001c-4 的 `create-e2e-db.sh`）
- [x] 二维码库选型写 ADR 或复用既有前端依赖，不引入重量级包（`AGENTS §5`）
- [x] E2E 断言页面正文时对空白归一化（antd 汉字间空格）

## 验收标准

- [x] `pnpm test:unit` / `pnpm lint` / `pnpm typecheck` 全过
- [x] `pnpm e2e p1-bundling-flow` 通过
- [x] 标签打印预览含手号、件数、二维码、条码
- [x] 重打产生 `bundle_label_prints` 新行且 `is_reprint=true`（变更 0143 补齐）
- [x] 单文件 ≤400 行；闸门 1-5 全绿（含镜像构建）

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-LE-01 | 打印预览含手号 | 「第 3 手 / 共 5 手」 | ✅ `LabelPrint.test.ts` |
| TC-LE-02 | 打印样式 | 40mm×40mm，无交互元素 | ✅ `LabelPrint.css` + `@media print`（构建产物内作用域标记完好） |
| TC-LE-03 | 重打 | `is_reprint=true` + 新行 + 批次号重复拒绝 | ✅ E2E 三条：缺 `print_seq` → `10002`；重打产出**与首次不重叠**的 `print_ids` 且累计张数累加；**同批次号再打 → `10008`** |
| TC-LE-04 | E2E 审核 | 码数=手数 | ✅ `p1-bundling-flow.spec.ts`（数的是 `GET /bundles` 的**真实码行**，不是表头汇总） |
| TC-LE-05 | E2E 刷新后仍在 | 数据持久化 | ✅ 末尾回列表核对单号 |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `frontend/e2e/p1-bundling-flow.spec.ts` | +274 | 新增。建单 → 提交 → **换账号**审核 → 码数=手数 → 打印登记 → 回列表 |
| `frontend/packages/admin/src/views/bundling/components/HandsTable.vue` | +170 | 新增。详情页「按手列表」走 `GET /bundles`（原为占位，拿明细行当码渲染） |
| `frontend/packages/admin/src/views/bundling/components/HandsTable.test.ts` | +154 | 新增。6 条（含「一行 = 一手」「reloadKey 变化后重拉」「作废码也列出」） |
| `frontend/packages/admin/src/views/bundling/Form.vue` | +41 −30 | `style_no` 传款号而非 UUID；明细行色码/工序号由表头下发；工序候选取 `/operations/options` |
| `frontend/packages/admin/src/views/bundling/Form.test.ts` | +117 −92 | 抽 `fillWholeForm`；新增 **TC-BW-03c** 断提交体形状（款号/工序号/行级色码） |
| `frontend/packages/admin/src/views/bundling/Detail.vue` | +3 −35 | 占位按手列表换成 `HandsTable` |
| `frontend/packages/admin/src/views/bundling/components/LineEditor.vue` | +23 −3 | 选完来源退出编辑态；编辑键带 `line_no` |
| `frontend/packages/admin/src/composables/useLabelCodes.ts` | +23 −6 | 条码改 `bwip-js/browser` 的 `toCanvas`（node 侧声明里才有 `toDataURL`） |
| `frontend/packages/admin/src/api/bundling.ts` | +15 −3 | `exportLabels` 返回类型改用契约的 `LabelExportOut` |
| `frontend/packages/admin/src/views/bundling/LabelPrint.vue` | +7 −1 | 取 `res.items` |
| `frontend/packages/shared/src/types/index.ts` | +7 | 补 `LabelExportOut` 别名 |
| `backend/tests/e2e_seed_accounts.py` | +329 | 新增（从 `e2e_seed.py` 拆出）：账号 + 库复位 + 非 E2E 库守卫 |
| `backend/tests/e2e_seed_cutting_source.py` | +222 | 新增（从 `e2e_seed.py` 拆出）：已审核来源裁剪单 |
| `backend/tests/e2e_seed.py` | −314 | 净减：拆成三个文件后回到 379 行（ADR-0030 的 400 行线内） |
| 其余（4 个 `.test.ts` / `labelPrintHarness.ts` / `HeaderFields.vue` / `shared/index.ts`） | +86 −20 | mock 与真实契约对齐、字段改名、夹具补 `mockLabelExport()` |

**行数构成**：手写代码新增约 1060 行、删除约 240 行，**无生成物**，未超 1200 行上限；
单个文件最大 379 行（`e2e_seed.py`），未超 400 行硬线。

**提交记录**：
- `<hash>` fix(bundling): P1 打菲 E2E 跑通 + 闭环 8 个只有真浏览器才暴露的缺陷

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| 「点打印真的出纸」永远验不了：`window.print()` 在 headless Chromium 里是无副作用的空操作。已能断的那一半（登记）现已全覆盖 | 不可解决，不必再登记 |
| Playwright 验收视口被 `devices['Desktop Chrome']` 静默覆盖成 1280×720 | `docs/12` L-116 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`：

```
[✅] 读过本任务对应的 docs 规范（03/05/06/09/10/12）
[✅] 没有硬编码业务常量
[✅] 没有物理删除（seed 的复位是测试库、且带非 E2E 库守卫）
[✅] 新表字段齐全 —— 本次无新表
[✅] 状态变更走了 service 层迁移方法且写了日志 —— 本次无状态机改动
[✅] 接口有权限声明 + 错误码 + OpenAPI 标签 —— 本次无新接口
[✅] 测试覆盖正常路径 + 异常路径 + 权限拒绝（10005 换账号）+ 并发（沿用 T-BUND-008）
[✅] 闸门 1 lint 通过（ruff + eslint）
[✅] 闸门 2 typecheck 通过（mypy + vue-tsc）
[✅] 闸门 3 单测通过（后端 992 passed / 91.27%；前端 297 passed）
[✅] 闸门 4 迁移可正向且可回滚（0018，零漂移）
[✅] 闸门 5 构建镜像成功（api-image / web-image 均 build + run --rm）
[✅] E2E：p0 + p1-bundling + p1-cutting = 6/6，连跑 3 轮
[✅] 提交信息符合规范
[✅] 手写行数 ≤ 1200，单文件 ≤ 400
[✅] 本次改动已在 docs/12 变更记录留痕（0138）
```

## 变更记录

| 日期 | 变更内容 | 操作人 |
| --- | --- | --- |
| 2026-10-06 | 初版：标签打印前端 + P1 打菲 E2E 出口 | AI |
| 2026-10-12 | 置 `done`：E2E 跑通，回填验收清单与实际改动；TC-LE-03 的 E2E 部分转入遗留问题 | AI |
