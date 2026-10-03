# T-WEB-004：admin 通用组件补齐与系统管理页

| 项 | 内容 |
| --- | --- |
| 模块 | web |
| 负责人 | AI |
| 状态 | `todo` |
| 优先级 | P0 |
| 依赖 | T-WEB-003, T-AUTH-002 |
| 被依赖 | T-WEB-005, T-WEB-006 |
| 关联设计 | [docs/modules/00-P0地基.设计.md](../modules/00-P0地基.设计.md) §4.3、§6.1 |
| 关联 ADR | ADR-0008 |
| 估算 | 0.7d |

## 目标

补齐列表页与详情页要用的通用组件（状态 Tag / 金额 / 空态 / 工具栏 / 页面容器），并交付**用户管理**与**角色权限**两页，让管理员能建账号、授权限。

## 范围

**要做**：
- [ ] `src/components/StatusTag.vue`：五状态映射（草稿灰 / 待审核蓝 / 已审核绿 / 已驳回红 / 已作废灰+删除线），色值**只来自 token**
- [ ] `src/components/MoneyText.vue`：`formatMoney` + 右对齐 + `tabular-nums`
- [ ] `src/components/PageLayout.vue`：标题 + 操作区插槽（主操作 1 个 `primary`，危险操作进 `...` 下拉并二次确认）
- [ ] `src/components/EmptyState.vue`：`Empty` + **下一步动作文案**（如「还没有款号，点右上角新建」）
- [ ] `src/components/TableToolbar.vue`：已选 N 项时的批量操作区 + 导出按钮
- [ ] `src/api/system.ts`：用户 / 角色 / 权限点封装（类型来自 `@garment/shared`，**不手写 DTO**）
- [ ] `src/views/system/users/{List,Form}.vue`：列表（筛选：关键字 / 车间 / 状态；**不渲染 `password_hash`**）+ 表单（角色分配、停用必填原因）
- [ ] `src/views/system/roles/{List,Form}.vue`：列表 + 表单（**权限点按模块分组树勾选**；`is_system=true` 的内置角色 code 只读、禁止删）
- [ ] 组件测试：`StatusTag.test.ts`（五状态色值）、`MoneyText.test.ts`、`v-can` 联动用例

**不做**：
- 不写基础资料页（T-WEB-005/006）
- 不做角色权限的「复制内置角色」功能（未在设计中）

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `frontend/packages/admin/src/components/StatusTag.vue` | 新增 | 状态 Tag |
| `frontend/packages/admin/src/components/MoneyText.vue` | 新增 | 金额 |
| `frontend/packages/admin/src/components/PageLayout.vue` | 新增 | 页面容器 |
| `frontend/packages/admin/src/components/EmptyState.vue` | 新增 | 空态 |
| `frontend/packages/admin/src/components/TableToolbar.vue` | 新增 | 工具栏 |
| `frontend/packages/admin/src/api/system.ts` | 新增 | API 封装 |
| `frontend/packages/admin/src/views/system/users/List.vue` | 新增 | 用户列表 |
| `frontend/packages/admin/src/views/system/users/Form.vue` | 新增 | 用户表单 |
| `frontend/packages/admin/src/views/system/roles/List.vue` | 新增 | 角色列表 |
| `frontend/packages/admin/src/views/system/roles/Form.vue` | 新增 | 角色表单 |
| `frontend/packages/admin/src/**/*.test.ts` | 新增 | 组件测试 |

## 实现要点（必读规范）

- [ ] 遵守 docs/06 §1（状态色映射固定、组件内禁字面量）、§2.2（列表页标准结构：PageHeader / FilterCard / TableCard）、§2.3（详情页结构 + sticky 操作区）、§5（危险确认必填原因、禁 `alert`）
- [ ] 遵守 docs/07 §2.3（内置角色不可删）、§4.1（显隐 + 禁用双层）
- [ ] 遵守 docs/03 §2.3（页面结构模板）、§2.1（禁 `any`）

## 验收标准

- [ ] 用户列表响应含 `password_hash` 时**前端也不渲染**（断言）
- [ ] 角色表单权限树**按模块分组**，与 `permissions.ts` 常量一致
- [ ] 内置角色（`is_system=true`）的 `code` 输入框 `disabled`，删除按钮不出现
- [ ] 停用用户弹 `Modal.confirm` + **必填原因**，未填不可提交
- [ ] 四态齐全：加载骨架 / 空态（带下一步动作）/ 错误（带重试）/ 无权限 403
- [ ] 表格列宽稳定、分页显示总数；操作列 ≤3 个直显，其余进 `...`
- [ ] `pnpm lint` / `pnpm typecheck` / `pnpm test:unit` 全绿

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-W19 | `StatusTag` 五状态 | 色 token 与 06 §1 一致、文案一致 | |
| TC-W20 | `MoneyText` | 右对齐 + 等宽数字 | |
| TC-W21 | 停用未填原因 | 提交按钮禁用 | |
| TC-W22 | 内置角色 | code 只读、无删除按钮 | |
| TC-W23 | 用户列表含 `password_hash` 字段 | 页面不渲染 | |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| | +0 / -0 | |

**提交记录**：
- `<hash>` feat(web): 通用组件与系统管理页 …

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| | | docs/12 §遗留问题清单 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。
