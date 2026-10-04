# T-WEB-006：款号与工序单价页 + E2E-00

| 项 | 内容 |
| --- | --- |
| 模块 | web |
| 负责人 | AI |
| 状态 | `done` |
| 优先级 | P0 |
| 依赖 | T-WEB-005, T-BASE-002 |
| 被依赖 | 无（P0 收口） |
| 关联设计 | [docs/modules/00-P0地基.设计.md](../modules/00-P0地基.设计.md) §4.5、§6.1、§9（E2E-00） |
| 关联 ADR | ADR-0026（三档取价必须可见）、ADR-0009（模板复制）、ADR-0013/0014（比例仅建议） |
| 估算 | 1d |

## 目标

交付 P0 出口标准之三的界面部分并**收口 P0**：款号建档 → 尺码比例 → 款号工序 → 工序单价全链路页面 + 一条 E2E 走通，出口标准「能登录、能建款号、能发 API token」全部可演示。

## 范围

**要做**：
- [x] `views/base/styles/List.vue`：列表（款号 / 款名 / 归属客户 / 分类 / 跟单 / 状态 / 最近使用）；`Combo` 选客户与分类；**导出**（T-WEB-006 期间补的 `/styles/exports`）
- [x] `views/base/styles/Form.vue`：**货号用户自定义**（必填、格式 + 唯一校验）+ 「生成建议号」按钮（调建议号接口，**用户可改**）+ 分类必选 + 归属客户（可空）+ 客户货号备注 + 跟单归属人
- [x] `views/base/styles/Detail.vue`：四个 Tab —— 色组 / 尺码（支持选码表一键带出）/ 款号工序 / 现行价；右上 sticky 操作区（编辑、模板复制、变更历史、**停用**）
- [x] `views/base/styles/Ratios.vue`：**按颜色切换的手数矩阵**；显示 `hands_total`；缺配尺码黄色提示「去补比例」。
  ⚠️ **不做「偏离建议 >20%」提示** —— 那属于裁剪单页（拿得到当单的裁剪棵数），不在款号档案上。已与用户确认
- [x] `views/base/operation-rates/List.vue`：款号 × 工序 × 档位的区间列表；**必须显示「当前生效价来源」`rate_source`**（ADR-0020 强制）；被款号价遮蔽的档位打「被覆盖」标记；调价弹**必填原因**。
  ⚠️ **设价/调价表单内嵌在列表页的 `Modal` 里，没有单独的 `Form.vue`** —— 它只在列表上下文打开（要带着当前筛选的档位与工序），单独拆文件反而要在父子间传一堆筛选状态。已在 docs/12 登记
- [x] 模板复制弹窗（`CopyDialog.vue` + `CopyResult.vue`）：`copy_mode` 与 `conflict_policy` **均无默认值、必须显式选**；响应展示写入/覆盖/跳过条数 + `skipped[]` + 逐项新价；成功提示追加「尺码比例未复制，请按目标款的销售构成为各颜色录入尺码比例」
- [x] `e2e/`：**E2E-00** 登录 → 款号详情（含 3 道工序与 3 段单价区间）→ **刷新页面数据仍在** → 单价列表 → `resolve` 命中对应区间 → **从菜单能点到款号页**。数据经 `backend/tests/e2e_seed.py` 准备（10 §4）。
  ⚠️ 款号/工序/单价由 seed 建好后页面**只读**：用 UI 造数据会让一条用例同时覆盖「表单校验 + 路由 + 接口」，一失败三处都可疑
- [ ] 更新 `prototype/README.md` 与实际页面的映射说明（可选）—— **本次未做**，留到 P0 收尾时一起

**不做**：
- 不做导入
- 不做物料 / BOM / 客户页面（客户只读展示，P1 补）
- 不做移动端任何页面（P2）

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `frontend/packages/admin/src/views/base/styles/List.vue` | 新增 | 款号列表 |
| `frontend/packages/admin/src/views/base/styles/Form.vue` | 新增 | 款号表单 |
| `frontend/packages/admin/src/views/base/styles/Detail.vue` | 新增 | 款号详情 4 Tab |
| `frontend/packages/admin/src/views/base/styles/ratios.vue` | 新增 | 手数比例矩阵 |
| `frontend/packages/admin/src/views/base/styles/copy-dialog.vue` | 新增 | 模板复制 |
| `frontend/packages/admin/src/views/base/operation-rates/List.vue` | 新增 | 单价列表 |
| `frontend/packages/admin/src/views/base/operation-rates/Form.vue` | 新增 | 设价/调价 |
| `frontend/packages/admin/src/router/index.ts` | 修改 | 注册路由 |
| `frontend/e2e/p0-main-flow.spec.ts` | 新增 | E2E-00 |
| `frontend/e2e/seed.ts` | 新增 | API seed |

## 实现要点（必读规范）

- [x] 遵守 docs/06 §2.2/§2.3（列表/详情标准结构 + sticky 操作区 + 变更历史抽屉 `document_logs`）、§2.4（表单规范）、§5（危险确认必填原因）
- [x] 遵守 docs/05 §9.5（款号候选默认 `last_used_at DESC`）、§4（错误码文案）
- [x] 遵守 docs/04 §4（单价 6 位小数 → `InputNumber` 精度 6）
- [x] 遵守 docs/10 §4（E2E：数据经 API seed、trace、独立库）
  ⚠️ 「可并行」暂不成立（`fullyParallel: false`，共用一个库）→ docs/12 L-066
- [x] 业务决策：Q-P0-04（货号自定义 + 建议号）、Q-P0-05（跟单按货号）、Q-P0-10（`customer_id` 保留可空）、ADR-0026（三档必须可见）

## 验收标准

- [x] 款号详情 4 Tab 数据完整；「变更历史」抽屉展示 `document_logs`
- [x] 比例矩阵：按 `(style_no, color_code)` 全量保存；`hands_total` 实时显示；缺配提示可见
- [x] 单价列表显示 `rate_source`（`款号价` / `分类价` / `工序通用价`）与生效区间；被遮蔽档位有「被款号价覆盖」标记
  ⚠️ 遮蔽判定**只按当前页**推导，跨页会漏标 → docs/12 L-065，建议后端在列表行返回 `is_shadowed`
- [x] 调价未填原因 → 提交禁用；成功后历史区间在列表可见
- [x] 模板复制：`copy_mode`/`conflict_policy` 未选时提交禁用；成功提示含「尺码比例未复制」
- [x] **E2E-00 通过**（`pnpm test:e2e`，4 例）
- [x] **P0 出口三条全部可演示**：能登录 / 能建款号 / 能拿到并使用 API token
- [x] `pnpm lint` / `pnpm typecheck` / `pnpm test:unit` / `pnpm test:e2e` 全绿；闸门 1~5 全绿
  ⚠️ E2E 在本机跑需要 `E2E_CHROMIUM_PATH`（见 docs/12 L-067）；CI 上不需要

## 测试清单

| TC-W30 | 建款号不填货号 | 提交禁用（自定义必填） | ✅ |
| TC-W31 | 点「生成建议号」 | 回填建议号，**仍可手动修改** | ✅ |
| TC-W32 | 比例矩阵保存 | 刷新后 `hands_total` 不变 | ✅ |
| TC-W33 | 单价列表 | 每行显示 `rate_source` 文案 | ✅ |
| TC-W34 | 款号有专用价时分类价行 | 显示「被款号价覆盖」标记 | ✅ |
| TC-W35 | 模板复制未选模式 | 提交禁用 | ✅ |
| TC-W36 | 停用：没填原因 | 确认按钮禁用且不发请求 | ✅ |
| TC-W37 | 停用 | 请求带 `version`（乐观锁） | ✅ |
| TC-W38 | 已停用的款号 | 不再给「停用」按钮 | ✅ |
| TC-W39 | 导出 | 带当前筛选、**不带分页** | ✅ |
| TC-W40 | 变更历史 | 按时间倒序；换单据不串上一个的日志 | ✅ |
| TC-W41 | 复制结果 | `messages` 逐条显示（含「尺码比例未复制」） | ✅ |
| E2E-00a | 款号详情 3 道工序 + 单价区间，**刷新后不变** | 数据在后端不在内存 | ✅ |
| E2E-00b | 单价列表显示「档位」列 + 3 段历史区间 | ADR-0026 可见性 | ✅ |
| E2E-00c | **从菜单能点到款号页** | 只有路由没菜单 = 打不开 | ✅ |
| E2E-00d | `resolve` 给定日期命中对应区间 | 档位回显 `STYLE` | ✅ |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `backend/app/modules/base/service.py` | +190 | `disable` / `export_styles` / `_filtered_styles`（列表与导出共用 WHERE）；`_style_list_stmt` 加 `LEFT JOIN users` |
| `backend/app/modules/base/router.py` | +80 | `POST /styles/{no}/disables`、`GET /styles/exports`（+ `STYLE_EXPORT_COLUMNS`） |
| `backend/app/modules/base/schemas.py` | +26 | `StyleDisableIn`、`StyleListOut` 补三字段 |
| `backend/tests/modules/test_style_disable_export.py` | +443 | 14 例 |
| `backend/tests/e2e_seed.py` | +232 | E2E 数据准备（走 service 层） |
| `frontend/packages/admin/src/views/base/styles/*` | +2210 | 七个页面 |
| `frontend/packages/admin/src/views/base/operation-rates/List.vue` | +549 | 单价页（内嵌设价/调价表单） |
| `frontend/packages/admin/src/views/base/styles/StylePages.test.ts` | +640 | 22 例组件测试 |
| `frontend/e2e/*` + `frontend/playwright.config.ts` | +430 | Playwright 基建 + 4 条 E2E |
| `scripts/create-e2e-db.sh` | +130 | 建 E2E 库（独立库 + 与生产一致的授权） |
| **生成物**（不计 800 行） | +168 | `openapi.json` + `schema.d.ts`，与生成它的源码同提交 |
| **手写合计** | **+4756** | 超 800 行，已按 AGENTS §7.1 分四个提交 |

**提交记录**：
- `36ef3d2` / `453fb08` — 前置缺口（建议号 / 变更历史端点）与 API 封装
- `505c790` feat(base): 补款号停用与导出端点
- `145c7f6` feat(web): 款号列表/表单/详情/比例/模板复制/变更历史页
- `47c4e77` feat(web): 工序单价页

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| L-064 | `AdminLayout.vue` 用 `<slot />` 而不是 `<RouterView />`，**所有嵌套路由的内容区永远空白**（组件测试测不出来） | **已修**，E2E 已覆盖 |
| L-065 | 单价「被款号价覆盖」标记只按当前页推导，跨页会漏标 | 待排期（建议后端返回 `is_shadowed`） |
| L-066 | E2E 只 4 条用例共用一个库，`docs/10 §4` 的「可并行」暂不成立 | 待排期 |
| L-067 | 本机（Ubuntu 20.04 / arm64）跑不了 Playwright 自带浏览器，留了 `E2E_CHROMIUM_PATH` 逃生口 | 待处理 |
| — | `operation-rates/Form.vue` 没单独拆文件（内嵌在列表页 Modal 里） | 已确认为有意为之 |
| — | `prototype/README.md` 的页面映射未更新（卡里标注为可选） | P0 收尾时一起 |

## 自检清单

- [x] 读过本任务对应的 docs 规范
- [x] 没有硬编码业务常量
- [x] 没有物理删除
- [x] 新表字段齐全（本卡无新表）
- [x] 状态变更走了 service 层迁移方法且写了日志（停用款号写 `document_logs`）
- [x] 接口有权限声明 + 错误码 + OpenAPI 标签
- [x] 测试覆盖正常路径 + 异常路径 + 权限拒绝 + 并发（如适用）
- [x] 闸门 1 lint 通过
- [x] 闸门 2 typecheck 通过
- [x] 闸门 3 单测通过（后端 569 例 / 前端 227 例）
- [x] 闸门 4 迁移可正向且可回滚（E2E 库从零 `upgrade head` 通过）
- [x] 闸门 5 构建镜像成功（`api-image` + `web-image` 都构建并 `run --rm` 通过）
- [x] 提交信息符合规范
- [x] 本次改动已在 docs/12 变更记录留痕（0066 / 0067 + L-064 ~ L-067）

**P0 收尾提示**：本卡完成后仍需做 P0 收尾 —— `prototype/README.md` 页面映射、设计稿 §10.1 的 W1~W9 回写、`docs/requirements/` 回填。
