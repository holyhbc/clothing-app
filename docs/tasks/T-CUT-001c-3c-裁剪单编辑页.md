# T-CUT-001c-3c：裁剪单编辑页（版本链 + 全量替换 + 删除草稿）

| 项 | 内容 |
| --- | --- |
| 模块 | web |
| 状态 | `done` |
| 优先级 | P1 |
| 依赖 | T-CUT-001c-3a（三层编辑器）、T-CUT-001c-3b（新建页）、T-CUT-001c-2c（只读详情页） |
| 被依赖 | T-CUT-001c-4（E2E） |
| 关联设计 | [modules/02-裁剪.md](../modules/02-裁剪.md) §5 §7、§11.5.2、[08-单据状态机规范.md](../08-单据状态机规范.md)（只读态口径） |
| 估算 | 0.5d |

## 目标

裁剪草稿能改：`PATCH` 表头 + `PUT /lines` 明细一次全量替换，**版本链正确**；
只有草稿能删；非草稿整块只读。

## 保存为什么是「`PATCH` + `PUT /lines`」两次，而不是逐层 PUT

后端 `PUT /lines` 的 `items` 是**完整的 `OrderLineIn`**（含 `colors[].size_lines[]`），
service 会 `_soft_delete_subtree` 掉整棵子树再按 `items` 重建 —— 一次调用落完整棵树，
**只需要一次版本链**。而 `PUT /lines/{line_id}/colors` 与 `PUT /size-lines` 是为「只改
其中一层」准备的：它们会保留别的行的数据与 `ratio_snapshot`（C29 禁止前端传入快照，
所以全量替换会把快照清掉）。

⚠️ **将来接「按比例带出」时必须改成增量保存** —— 带出写进去的 `ratio_snapshot`
会被 `PUT /lines` 清掉。已在 `docs/12` 记为 **L-083**。

## 三条最容易做错的

| # | 陷阱 | 正确做法 |
| --- | --- | --- |
| 1 | 用页面上的旧 `version` 发第二个请求 | `PATCH` 与 `PUT /lines` **各自 bump version**，必须把第一个的返回值传给第二个 —— 否则**每点一次保存失败一次** |
| 2 | 保存成功后继续用旧的行 `id` | `PUT /lines` 是**软删旧行 + 插新行** → **行 id 全变**，必须用响应重建本地树，否则下一次保存收 `10001` |
| 3 | 已提交 / 已审核的单还能编辑 | 后端 `_editable_order` 只允许 `DRAFT` / `REJECTED`；页面上明确置灰并提示「先撤回 / 先反审核」，**不假装能存** |

## 验收标准

- [x] `pnpm lint` / `pnpm typecheck` / `pnpm test:unit` 通过（202 例）
- [x] 无单文件超 400 行
- [x] 闸门 1-5 全绿

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-CUT-X1 | 没录完的行进问题清单并**挡住保存** | `06 §2.4` | ✅ |
| TC-CUT-X1b | 录完的行**不**出现在问题清单（不误判正常数据） | — | ✅ |
| TC-CUT-X2 | ★ 版本链：`PATCH` 带旧 version、`PUT` 带 `PATCH` 的新 version | `04 §2` | ✅ |
| TC-CUT-X3 | ★ 连续两次保存都成功，版本逐级递增（3→4→5→6→7） | — | ✅ |
| TC-CUT-X3b | ★ 连点两次保存**只发一次请求**（防重入） | `06 §2.4` | ✅ |
| TC-CUT-X4 | 非草稿状态整块只读且保存禁用 | `08 §2.1` | ✅ |
| TC-CUT-X5 | 加载失败显示错误原文与重试，**不渲染空表头** | `05 §5` | ✅ |
| TC-W42/43 | 新建路由要 `cutting:create`、编辑要 `cutting:update` | `07 §4.1` | ✅ |

## 实际改动

| 文件 | 行数 |
| --- | --- |
| `frontend/packages/admin/src/views/cutting/Edit.vue` | +387 |
| `frontend/packages/admin/src/views/cutting/Edit.test.ts` | +331 |
| `frontend/packages/admin/src/views/cutting/components/HeaderFields.vue` | +194（从 Form.vue 抽出，两页共用） |
| `frontend/packages/admin/src/views/cutting/Form.vue` | +40/-166（表头搬进 `HeaderFields`） |
| `frontend/packages/admin/src/router/index.ts` | +10 |
| `frontend/packages/admin/src/router/index.test.ts` | +28 |
| `frontend/packages/admin/src/views/cutting/Detail.vue` | +7（「编辑」按钮接上编辑页） |

**手写行数 ≈ 1000**（ADR-0030 软上限 1200 内；单文件最大 387 行 < 400）。

## 抓到的五个坑

| # | 坑 | 为什么难发现 |
| --- | --- | --- |
| 1 | 桩固定返回 version 4/5 | 页面行为**正确**而用例失败（第二次保存拿到比第一次小的版本）—— 失败信息「expected 4 to be 5」完全看不出是桩太笨。已改成按传入 version 递增 |
| 2 | antd `loading` 挡不住第二次点击 | 两次并发保存 → 两个请求带同一个 version → 后到的必然 `10003`。已在 `save()` / `remove()` 里加防重入守卫（`docs/06 §2.4` 的「loading 禁用防重复」靠按钮自己不够） |
| 3 | 测试桩路由少注册 `/edit` 段 | 推 `/cutting/orders/{id}/edit` 匹配不到 → `params.orderId` 是 undefined → 页面报「缺少裁剪单 ID（页面地址不对）」，失败信息完全看不出是桩路由不全 |
| 4 | 表头抽成组件后 `vm.form` 不存在 | 变量改名 `header`，但 vitest 不跑类型检查 → 断言「payload 里有 workshop_id」以 `undefined` 失败，看不出是变量名改了 |
| 5 | 模板里 `disabled \|\| lockIdentity` 传给 antd 组件 | props 在模板中未经默认值收窄 → `boolean \| undefined` → `exactOptionalPropertyTypes` 下**编译错误**，而报错说的是「target's properties」，与「我只想禁用一个字段」毫无关系。已挪到脚本里用 computed 算 |

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| — | ★ **明细全量替换会清掉 `ratio_snapshot`**（C29 禁止前端传入快照）→ 接「按比例带出」时必须改成**增量保存**（`PUT /size-lines` / `PUT /colors`） | docs/12 **L-083** |
| — | MASTER 取整策略 | docs/12 L-082 |
| — | 状态机端点（提交 / 审核 / 驳回 / 撤回 / 反审核 / 作废）与导出 | T-CUT-001b-3（等 `stock_ledgers` / `bundles` 建表）、T-CUT-001c-4 |
| — | 车间默认值可自动带出当前用户所在车间（`/auth/me` 里有） | 与状态机卡一并做 |