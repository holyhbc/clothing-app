# T-WEB-006：款号与工序单价页 + E2E-00

| 项 | 内容 |
| --- | --- |
| 模块 | web |
| 负责人 | AI |
| 状态 | `todo` |
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
- [ ] `views/base/styles/List.vue`：列表（款号 / 款名 / 归属客户 / 分类 / 跟单 / 状态 / 最近使用）；`Combo` 选客户与分类
- [ ] `views/base/styles/Form.vue`：**货号用户自定义**（必填、格式 + 唯一校验）+ 「生成建议号」按钮（调建议号接口，**用户可改**）+ 分类必选 + 归属客户（可空）+ 客户货号备注 + 跟单归属人
- [ ] `views/base/styles/Detail.vue`：四个 Tab —— 色组 / 尺码（支持选码表一键带出）/ 款号工序 / 现行价；右上 sticky 操作区（编辑、模板复制、停用）
- [ ] `views/base/styles/ratios.vue`：**按颜色切换的手数矩阵**；显示 `hands_total`；缺配尺码黄色提示「去补比例」；偏离建议 >20% 黄字提示（不拦截，ADR-0020）
- [ ] `views/base/operation-rates/List.vue` + `Form.vue`：款号 × 工序 × 档位的区间列表；**必须显示「当前生效价来源」`rate_source`**（ADR-0020 强制）；被款号价遮蔽的档位打「被覆盖」标记；调价弹**必填原因**
- [ ] 模板复制弹窗：`copy_mode` 与 `conflict_policy` **均无默认值、必须显式选**；响应展示写入/覆盖/跳过条数 + `skipped[]` + 逐项新价；成功提示追加「尺码比例未复制，请按目标款的销售构成为各颜色录入尺码比例」
- [ ] `e2e/`：**E2E-00** 登录 → 建款号 → 配 2 工序 → 设价 → `resolve` → 刷新页面数据仍在；数据经 API seed 准备（10 §4）
- [ ] 更新 `prototype/README.md` 与实际页面的映射说明（可选，归档时一起）

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

- [ ] 遵守 docs/06 §2.2/§2.3（列表/详情标准结构 + sticky 操作区 + 变更历史抽屉 `document_logs`）、§2.4（表单规范）、§5（危险确认必填原因）
- [ ] 遵守 docs/05 §9.5（款号候选默认 `last_used_at DESC`）、§4（错误码文案）
- [ ] 遵守 docs/04 §4（单价 6 位小数 → `InputNumber` 精度 6）
- [ ] 遵守 docs/10 §4（E2E：数据经 API seed、`--trace on`、独立库可并行）
- [ ] 业务决策：Q-P0-04（货号自定义 + 建议号）、Q-P0-05（跟单按货号）、Q-P0-10（`customer_id` 保留可空）、ADR-0026（三档必须可见）

## 验收标准

- [ ] 款号详情 4 Tab 数据完整；「变更历史」抽屉展示 `document_logs`
- [ ] 比例矩阵：按 `(style_no, color_code)` 全量保存；`hands_total` 实时显示；缺配提示可见
- [ ] 单价列表显示 `rate_source`（`款号价` / `分类价` / `工序通用价`）与生效区间；被遮蔽档位有「被款号价覆盖」标记
- [ ] 调价未填原因 → 提交禁用；成功后历史区间在列表可见
- [ ] 模板复制：`copy_mode`/`conflict_policy` 未选时提交禁用；成功提示含「尺码比例未复制」
- [ ] **E2E-00 通过**（`pnpm test:e2e`）
- [ ] **P0 出口三条全部可演示**：能登录 / 能建款号 / 能拿到并使用 API token
- [ ] `pnpm lint` / `pnpm typecheck` / `pnpm test:unit` / `pnpm test:e2e` 全绿；闸门 1~5 全绿

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-W30 | 建款号不填货号 | 提交禁用（自定义必填） | |
| TC-W31 | 点「生成建议号」 | 回填建议号，**仍可手动修改** | |
| TC-W32 | 比例矩阵保存 | 刷新后 `hands_total` 不变 | |
| TC-W33 | 单价列表 | 每行显示 `rate_source` 文案 | |
| TC-W34 | 款号有专用价时分类价行 | 显示「被款号价覆盖」标记 | |
| TC-W35 | 模板复制未选模式 | 提交禁用 | |
| E2E-00 | 登录→建款号→配工序→设价→resolve→刷新 | 数据仍在 | |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| | +0 / -0 | |

**提交记录**：
- `<hash>` feat(web): 款号与工序单价页，收口 P0 …

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| | | docs/12 §遗留问题清单 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。**本卡完成后执行 P0 收尾**：docs/12 变更记录追加行、docs/01 §6 ADR 索引补 0025/0026、设计稿 §10.1 的 W1~W9 全部回写、`docs/requirements/` 与 `docs/tasks/` 回填、首次 `git commit`。
