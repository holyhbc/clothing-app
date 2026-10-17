# T-PAY-011：PC 前端（周期 / 结算单 / 工资单 / 导出）

| 项 | 内容 |
| --- | --- |
| 模块 | payroll |
| 负责人 | frontend-dev |
| 状态 | `todo` |
| 优先级 | P2 |
| 依赖 | T-PAY-009（端点 + 权限 + 契约） |
| 被依赖 | T-PAY-012（E2E） |
| 关联设计 | [`modules/05-工资.md`](../modules/05-工资.md) §6.1/§6.1.1/§6.4 |
| 关联规范 | `docs/06 §2/§5`（`ResourceList`/`ResourceForm`/`v-can`/原因必填）、`docs/05 §9.1` |
| 关联 ADR | — |
| 估算 | 0.5d/页 |

## 目标

在 PC 端交付工资模块可操作页面：周期管理、结算单、工资单、代发文件导出。

## 范围

**要做**：
- [ ] 周期页：列表（含 `unsettled_log_count`/`unsettled_amount`）、四种建法（自定义/整月/两月/按裁剪截取）、
      建前展示核对数、`close`/`reopen`（必填原因 Modal）
- [ ] 结算单页：列表/详情/生成/提交/审核/驳回/撤回/反审核（`v-can="'payroll:approve'"` 等）
- [ ] 工资单页：列表/详情（行）、审核/反审核、发放 `pay`、工资条、代发文件下载
- [ ] 菜单按权限过滤；反审核/驳回/作废 `Modal.confirm` **必填原因**（`06 §2.2/§5`）
- [ ] 列表导出（`v-can="'payroll:export'"`，与列表共用后端 service）

**不做**：
- 不做员工端 H5（T-PAY-012）
- 不做扣款项录入（Q-PL01）；不做凭证页面（M9）

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `frontend/packages/admin/src/views/payroll/PeriodList.vue` 等 | 新增 | 周期页 |
| `frontend/packages/admin/src/views/payroll/SettlementList.vue` + `Detail.vue` | 新增 | 结算单 |
| `frontend/packages/admin/src/views/payroll/SheetList.vue` + `Detail.vue` | 新增 | 工资单 |
| `frontend/packages/admin/src/api/payroll.ts` | 新增 | API 层 |
| `frontend/packages/admin/src/router/index.ts` + `layouts/menu.ts` | 修改 | 路由与菜单 |

## 实现要点

- [ ] 统一 `ResourceList`/`ResourceForm`，只配 `columns`/`fields`/`rules`（REQ-000 §5）
- [ ] 金额展示 `Decimal` 字符串，不用 JS float 再运算
- [ ] 颜色/间距用 `var(--color-*)`，禁字面量（`06 §1`，`tokens.test.ts` 守卫）
- [ ] 单文件 ≤400 行（ADR-0030），列表/详情可拆子组件

## 验收标准

- [ ] 页面单测：`pnpm -r test:unit` 通过（含权限按钮显隐、必填原因）
- [ ] `pnpm typecheck`/`lint`/`build` 全绿
- [ ] 周期建法调用 `generate` 后展示核对数
- [ ] 手工验收：四页主流程可用

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-PAY-FE01 | 无 `payroll:approve` 不显示审核按钮 | 通过 | |
| TC-PAY-FE02 | 反审核必填原因 | 通过 | |
| TC-PAY-FE03 | 周期核对数展示 | 通过 | |
| TC-PAY-FE04 | 代发文件下载 | 通过 | |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| | +0 / -0 | |

**提交记录**：
- `<hash>` feat(web): 工资 PC 页面（T-PAY-011）

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| | | docs/12 §遗留问题清单 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。

## 变更记录

| 日期 | 变更内容 | 操作人 |
| --- | --- | --- |
| 2026-10-17 | 初版 | AI |
