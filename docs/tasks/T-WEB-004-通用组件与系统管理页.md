# T-WEB-004：admin 通用组件补齐与系统管理页

| 项 | 内容 |
| --- | --- |
| 模块 | web |
| 负责人 | AI |
| 状态 | `done` |
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
- [x] `src/components/StatusTag.vue`：五状态映射（草稿灰 / 待审核蓝 / 已审核绿 / 已驳回红 / 已作废灰+删除线），色值**只来自 token**
- [x] `src/components/MoneyText.vue`：`formatMoney` + 右对齐 + `tabular-nums`
- [x] `src/components/PageLayout.vue`：标题 + 操作区插槽（主操作 1 个 `primary`，危险操作进 `...` 下拉并二次确认）
- [x] `src/components/EmptyState.vue`：`Empty` + **下一步动作文案**（如「还没有款号，点右上角新建」）
- [x] `src/components/TableToolbar.vue`：已选 N 项时的批量操作区 + 导出按钮
- [ ] `src/api/system.ts`：用户 / 角色 / 权限点封装（类型来自 `@garment/shared`，**不手写 DTO**）
- [ ] `src/views/system/users/{List,Form}.vue`：列表（筛选：关键字 / 车间 / 状态；**不渲染 `password_hash`**）+ 表单（角色分配、停用必填原因）
- [ ] `src/views/system/roles/{List,Form}.vue`：列表 + 表单（**权限点按模块分组树勾选**；`is_system=true` 的内置角色 code 只读、禁止删）
- [x] 组件测试：`StatusTag.test.ts`（五状态色值）、`MoneyText.test.ts`、`v-can` 联动用例

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

- [x] 用户列表响应含 `password_hash` 时**前端也不渲染** —— 后端 `UserOut` 模型层就没有这一列，连"记得别渲染"都不需要
- [x] 角色表单权限树**按模块分组**，顺序取自 registry 的声明顺序（用 `frozenset` 会让每次打开顺序都变）
- [x] 内置角色（`is_system=true`）的 `code` 输入框只读，`…` 菜单里**没有**停用项（不是 disabled —— 永远点不动的按钮只会让人反复点）
- [x] 停用用户弹 `Modal.confirm` + **必填原因**，确认按钮初始 disabled 且原因为空时请求根本不发不出去
- [x] 四态齐全：加载 Spin / 空态（带下一步动作）/ 错误（业务文案 + 重试）/ 无权限 403 由守卫处理
- [x] 表格列宽固定（`width` + `scroll.x`）、分页显示总数；操作列只直显 1 个，其余进 `...`
- [x] `pnpm lint` / `pnpm typecheck` / `pnpm test:unit` 全绿

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-W19 | `StatusTag` 五状态 | 色 token 与 06 §1 一致、文案一致 | 色 token 与文案与 06 §1 一致 ✓ |
| TC-W20 | `MoneyText` | 右对齐 + 等宽数字 | 右对齐 + 等宽数字 ✓ |
| TC-W21 | 停用未填原因 | 提交按钮禁用 | 确认按钮初始 disabled ✓（但因页面未做，改为 `utils/danger.ts` 的 10 例覆盖） |
| TC-W22 | 内置角色 | code 只读、无删除按钮 | **未做** —— 角色表单依赖后端端点 |
| TC-W23 | 用户列表含 `password_hash` 字段 | 页面不渲染 | **未做** —— 依赖用户列表端点 |

## ⚠️ 本卡只完成了一半，原因是后端没有端点

实现到「用户管理 / 角色权限」两页时发现：**后端没有任何用户 / 角色管理端点**。

`backend/openapi.json` 里与账号相关的只有 `auth` 模块六个端点
（`login` / `logout` / `me` / `password` / `refresh` / `sms/send-code`）；
`users` / `roles` / `permissions` 三张表（T-AUTH-001 建好了）**只有 seed、没有接口**。

原路线图里也没有对应卡片 —— T-AUTH-001 只做表与 seed，T-AUTH-002 只做「自己的登录态」。
于是两页无处可接：接口类型只能从 `openapi.json` 生成（docs/06 §7「页面禁止手写 DTO」），
没有端点就没有类型，硬写就是违规。

**已补路线图上的这个洞**：`docs/tasks/T-AUTH-003-用户与角色管理接口.md`
（含 4 个必须由业务方回答的问题 —— 停用用户在途单据怎么办、多角色 `data_scope` 怎么合并、
最后一个管理员能否停用、重置口令怎么给）。

**同时把菜单里的「系统管理」两项删了**：菜单里留一个点进去是 404 的入口比没有更糟 ——
用户会以为权限有问题，反复重登、找管理员。端点与页面都到位后再加回来。

所以本卡的 `api/system.ts` 与 `users/{List,Form}.vue`、`roles/{List,Form}.vue` **未做**，
依赖它们的 TC-W22 / TC-W23 也未做。组件那部分是后续所有页面都要用的地基，已完整交付。

## 实际改动（完成后回填）

**两阶段合计 30 个文件**。第一阶段（组件）16 文件 / +1180；第二阶段（两页）14 文件 / +2060。
生成物：`schema.d.ts` 因新增 13 个端点而重新生成（8792 行）+ `permissions.ts` 增加 `ROLE_NAMES`。

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `src/components/Combo.vue` … | — | （上卡） |
| `src/components/StatusTag.vue` + `.test.ts` | +68 / +84 | 色只来自 `shared/status.ts`，未知状态原样显示 |
| `src/components/MoneyText.vue` + `.test.ts` | +57 / +84 | `formatMoney` + `tabular-nums`，`places` 可传 |
| `src/components/PageLayout.vue` | +142 | 标题 / `extra`（结构上只允许一个 primary）/ filter / toolbar |
| `src/components/EmptyState.vue` | +95 | `Empty` + 下一步动作（主/辅两个按钮） |
| `src/components/TableToolbar.vue` | +155 | 已选 N 项 + 清空选择 + 导出（`v-can`）+ 危险操作进 `…` |
| `src/components/layout.test.ts` | +214 | 三组件 15 例 |
| `src/utils/danger.ts` + `.test.ts` | +145 / +190 | 危险操作二次确认 + **必填原因**（TC-W21） |
| `src/test-support/paths.ts` | +30 | 测试里定位仓库根（`import.meta.url` 在 jsdom 下不可用） |
| `src/layouts/menu.ts` | +21 | **移除**系统管理两项 + 写下「只有真有页面才登记」的规矩 |
| `docs/tasks/T-AUTH-003-*.md` | +100 | 新卡 |

**提交记录**：
- `948000b` 第一阶段：通用组件（StatusTag/MoneyText/PageLayout/EmptyState/TableToolbar）
- `6c02c0b` 第二阶段：用户管理 / 角色权限两页

## 过程中被测试抓出来的 8 个真缺陷

| # | 缺陷 | 症状 |
| --- | --- | --- |
| 1 | **`<a-button>` 这类 kebab 全局标签在本项目里根本不生效** | `main.ts` 没有 `app.use(Antd)`（全局注册会把整个 antd 打进首屏），所以 `<a-button>` 被 Vue 当成未知自定义元素渲染：**标签出现在页面上但没有任何行为**，点击不触发、样式全无。编译不报错、lint 不报错，只有真点一下才发现 |
| 2 | **`<Empty :description="null">` 会把 title 与 hint 整段吞掉** | antd 的 `Empty` 内部是 `const { description = slots.description?.() \|\| undefined } = props`；prop 与插槽同时存在且 prop 为 `null` 时，描述渲染成空注释节点 —— 页面上只剩一个空 Empty 图标 |
| 3 | **`v-can` 在单测里假绿** | 指令是在 `main.ts` 里 `app.directive('can', …)` 注册的，单测直接 `mount` 没注册 → 指令压根没执行，「无权时按钮被移除」这条断言因为"按钮从没被处理过"而通过 |
| 4 | **`MoneyText` 差点写死 2 位小数** | 单价是 `numeric(18,4)`（docs/04 §7），写死 2 位会把 `0.378000` 显示成 `0.38`，单价表直接失去意义。加了 `places` prop 与对应用例 |
| 5 | **表格「姓名」列渲染成空白** | 列定义只给了 `key` 没给 `dataIndex`，antd 不知道取哪个字段。**页面上没有任何报错**，就是一列没内容 —— review 看不出来，是测试断言"显示姓名"时炸的 |
| 6 | **下拉菜单的断言一直是假绿** | antd 的 `Dropdown` / `Modal` 被 **teleport 到 `document.body`**，`wrapper.findAll()` 永远返回空数组 —— 于是"内置角色的菜单里没有停用项"这条断言因为"根本没找到菜单项"而通过。改成 `attachTo` + 从 `document` 查 |
| 7 | **权限树 / 角色菜单项显示英文 code** | `UserOut.role_codes` 只有 code，用 `PERMISSION_NAMES` 映射是错的（那是**权限点**的名字）。车间里没人认得 `workshop_supervisor`。已让生成器从 registry 导出 `ROLE_NAMES` |
| 8 | **`/system/users/new` 被 `:id` 吃掉** | 路由顺序反了的话 `id="new"`，页面以为自己在编辑一个叫 new 的用户，显示"编辑用户"并去查一个不存在的账号。这条在写测试时才暴露 —— 因为测试的桩路由也犯了同样的错 |

另外**测试数据踩了自己的规则**：`confirmDanger` 的 `MIN_REASON_LENGTH=5`，
而我第一版用例填的「员工离职」正好 4 个字 → "解禁按钮"与"确认后返回理由"两条一起挂，
症状看起来像组件坏了。已补一条显式断言边界（并登记为 L-050）。

## 与本卡的偏差

| # | 卡的写法 | 实际做法 | 为什么 |
| --- | --- | --- | --- |
| 1 | 交付 5 组件 + `api/system.ts` + 4 个页面 | 只交付 5 组件 + 1 个 `danger.ts` | 后端无端点。详见上面「本卡只完成了一半」 |
| 2 | 组件测试含 `v-can` 联动用例 | 放在 `TableToolbar` 的导出按钮上（断言「不可见」而非「DOM 里没有」） | `v-can` 刻意用 `display:none` 而不是 `remove()`（权限异步到达，`remove()` 不可逆），所以"无权"的表现是仍在 DOM 里但不可见 |
| 3 | 未提危险确认的抽取 | 抽成 `utils/danger.ts` 返回 `Promise<{reason} \| null>` | 「必填原因」很容易被绕过（按钮没绑校验 / 没提示 / 后端返回 `10006` 后用户输入全丢）。做成 Promise 后调用方拿到 `null` 就不发请求，"忘了校验"在结构上不可能 |
| 4 | 未提测试路径 helper | 新增 `src/test-support/paths.ts` | `import.meta.url` 在 vitest jsdom 下是 http URL，`fileURLToPath` 直接抛错。上一张卡已经踩过一次，抽出来复用 |

## 遗留问题

登记进 `docs/12` §5，编号与那里一致：

| # | 问题 | docs/12 状态 |
| --- | --- | --- |
| L-050 | `confirmDanger` 的 `MIN_REASON_LENGTH=5` 会把「员工离职」这类常用短理由挡掉，需业务方确认字数下限 | 待业务方 |
| L-051 | ~~用户管理 / 角色权限两页与 `api/system.ts` 未做~~ | **已闭环**：T-AUTH-003 落地后第二阶段补完 |
| L-052 | ~~菜单「系统管理」已移除~~ | **已闭环**：页面到位后已加回 |

## 自检清单

对照 `AGENTS.md` §9 逐条确认：

```
[✓] 读过本任务对应的 docs 规范（03 §2.1/§2.3、06 §1/§2.1/§2.2/§2.3/§5、07 §2.3/§4.1/§5、05 §3/§9.1）
[✓] 没有硬编码业务常量（状态色只来自 shared；颜色/间距全 var(--token)，有扫描守卫）
[✓] 没有物理删除（前端不涉及；T-AUTH-003 已把「禁 DELETE」写成硬约束）
[✓] 新表字段齐全（本卡无新表；alembic check 零漂移）
[✓] 状态变更走了 service 层方法且写了日志（本卡无状态变更；`danger.ts` 强制收集原因备用）
[✓] 接口有权限声明 + 错误码 + OpenAPI 标签（本卡未新增后端接口；页面全部走 `api/system.ts`）
[✓] 测试覆盖正常 + 异常 + 权限拒绝 —— 109 例 admin（组件 40 + 两页 15 + 前两卡 54）+ 54 shared
[✓] 闸门 1 lint 通过（eslint 0 error 0 warning + prettier --check）
[✓] 闸门 2 typecheck 通过
[✓] 闸门 3 单测通过
[✓] 闸门 4 迁移通过（alembic check 零漂移）
[✓] 闸门 5 构建成功（web 镜像 + 容器烟测首页 200）
[✓] 提交信息符合规范
[✓] 本次改动已在 docs/12 变更记录留痕
```
