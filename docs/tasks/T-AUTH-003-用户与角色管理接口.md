# T-AUTH-003：用户与角色管理接口（后台）

| 项 | 内容 |
| --- | --- |
| 模块 | auth |
| 负责人 | AI |
| 状态 | `done` |
| 优先级 | P0 |
| 依赖 | T-AUTH-002 |
| 被依赖 | T-WEB-004（用户管理页 / 角色权限页） |
| 关联设计 | [docs/modules/00-P0地基.设计.md](../modules/00-P0地基.设计.md) §6.1（admin 的「用户管理」「角色权限」两页） |
| 关联 ADR | ADR-0008（权限点） |
| 估算 | 1.2d |

## 为什么有这张卡

T-WEB-004（admin 通用组件与系统管理页）在实现时发现：**后端没有任何用户 / 角色管理端点**。

`backend/openapi.json` 里与账号相关的只有 `auth` 模块的六个端点
（`login` / `logout` / `me` / `password` / `refresh` / `sms/send-code`），
`users` / `roles` / `permissions` 三张表（T-AUTH-001 建好了）**只有 seed、没有任何接口**。

原路线图里也没有对应卡片 —— T-AUTH-001 只做了表与 seed，T-AUTH-002 只做了「自己的登录态」。
于是 T-WEB-004 的两页无处可接：接口类型只能从 `openapi.json` 生成
（docs/06 §7「页面禁止手写 DTO」），没有端点就没有类型，硬写就是违规。

**所以这张卡是补路线图上的一个洞，不是加需求。**

## 目标

让管理员能在界面上建账号、分配角色、授予与回收权限点 —— 也就是
docs/07 §2.1~§2.3 已经定义好的数据模型，终于有一个可操作的入口。

## 范围

**要做**（`/api/v1/system/*`，统一挂在 `modules/auth/system_router.py` 或新建 `modules/system/`）：

- [x] `GET /api/v1/system/users`（筛选 `q` / `workshop_id` / `is_active`；分页；**响应绝不含 `password_hash` 与 `phone`**）
- [x] `POST /api/v1/system/users`
- [x] `GET /api/v1/system/users/{id}`
- [x] `PATCH /api/v1/system/users/{id}`（`version` 乐观锁，docs/05 §2）
- [x] `POST /api/v1/system/users/{id}/disables`（**必填原因**，docs/06 §5）
- [x] `POST /api/v1/system/users/{id}/password-resets`（管理员重置口令，返回**一次性初始口令**；写入 `must_change_password=true`）
- [x] `GET /api/v1/system/roles` / `POST` / `PATCH /{id}` / `POST /{id}/disables`
- [x] `GET /api/v1/system/permissions`（按模块分组，供权限树勾选；数据来自 `permissions_registry`）
- [x] `PUT /api/v1/system/roles/{id}/permissions`（**整体替换** + 差异写 `document_logs`，docs/07 §5「权限变更留痕」）
- [x] `GET /api/v1/system/users/options`、`GET /api/v1/system/roles/options`（Combo 用，docs/05 §9.5.2）

**硬约束**：

| 约束 | 依据 |
| --- | --- |
| 权限点 `require_permission('system:user:manage')` / `('system:role:manage')` | docs/05 §6；docs/07 §2.2 |
| 内置角色（`is_system=true`）**禁止改 code、禁止删** | docs/07 §2.3 |
| `data_scope` 由 service 强制注入，前端传参**不能放大范围** | docs/07 §3.2 |
| 停用 / 重置口令 / 改权限必须写 `document_logs`（含原因） | docs/07 §5 |
| **禁止 `DELETE` 物理删除**用户与角色 | AGENTS §2.1；`erp_app` 被 REVOKE DELETE（docs/04 §6.2.1 / ADR-0025） |
| 响应**不含** `password_hash` / `phone` | docs/05 §3 |
| 改权限后**立刻生效**（不靠 token 携带权限） | docs/07 §1.1「权限查库」 |

**不做**：

- 「复制内置角色」（T-WEB-004 已明确不做）
- 单点登录 / 短信登录（P2）
- 组织架构（车间 / 组别）的管理接口 —— 由 T-BASE 的字典接口覆盖

## 四个待确认问题：实现前的调查结论

原计划"停下来问业务方"。实际动手前先按 AGENTS §2.3 第 4 步**搜了代码里的同类实现**，
结果四个问题里三个**已有答案**（既有实现早就决定了，只是没人写下来）：

| # | 问题 | 结论 | 依据 |
| --- | --- | --- | --- |
| Q-A1 | 停用用户在途单据怎么办？ | **停用只影响登录，不动任何业务数据** | P0 **还没有任何单据表**（`openapi.json` 里无 cutting/bundling/piecework 端点）。有 `is_active=false` 的用户仍被所有单据的 `created_by` 引用，历史单据照常可查。等单据落地后这条要重答 —— 已登记 L-055 |
| Q-A2 | 多角色的 `data_scope` 怎么合并？ | **不存在合并** —— `data_scope` 是 `users` 表上的用户属性 | `auth/service.py::_issue_tokens` 直接读 `users.data_scope` 塞进 token；`roles.data_scope` 只是"建号时的默认值"。本模块沿用同一口径，不引入第二套规则 |
| Q-A3 | 最后一个管理员能否停用？ | **不能，抛 `10008`** | 先例是 `cli/seed_baseline.py::ensure_initial_admin` 要求"至少存在一个超管账号"。本模块把同一条不变量延伸到停用动作：剩下的人里只要还有一个带 `super_admin` **或** 带 `system:role:manage` 就放行 |
| Q-A4 | 重置口令的口令从哪来？ | **管理员自己填**，应用不生成 | `cli/seed_baseline.py` 的 `ERP_INITIAL_ADMIN_PASSWORD` 已经定了口径："初始口令只从部署侧提供，应用**绝不生成**弱口令"。而本项目没有可用送达通道（`auth/sms/send-code` 是 P2 未启用），"应用生成随机口令再告诉用户"这条路根本走不通 |

**只有 Q-A1 需要业务方在单据落地后重新回答**（已登记 L-055）。

## 验收标准

- [x] `openapi.json` 出现上述端点（实测 70 → **83 paths** / 70 → **94 schemas**），`pnpm generate:api` 幂等
- [x] 用户列表响应**没有** `password_hash` / `phone` 字段 —— 模型层就没有这两列（断言在 schema，不靠页面自觉）
- [x] 无 `system:user:manage` 的账号访问用户接口 → 403；`roles/options` 挂的是 `system:user:manage` 而非 `system:role:manage`（否则车间主管连账号都建不了）
- [x] 停用 / 重置口令 / 停用角色都必须填原因，不足 5 字被 schema 拦下（422）
- [x] 内置角色 `code` 不可改（`extra=forbid`，422）、内置角色不可停用（`10008`）、`is_system` 不可通过接口置 true
- [x] 改权限 / 软删角色后原用户**立刻**失去该权限（**用同一个旧 token**，有断言）
- [x] `document_logs` 里有权限变更记录（`added` / `removed` / 操作人 / 原因）
- [x] `alembic check` 零漂移、downgrade→upgrade 往返通过；闸门 1-5 全绿（**514 passed**）

## 实际改动

| 文件 | 说明 |
| --- | --- |
| `app/modules/system/{__init__,schemas,service,router}.py` + `scopes.py` | 13 个端点 |
| `app/core/permissions.py` | **补软删过滤**（见下） |
| `app/core/scope.py` | `roles` 加入 `SCOPE_EXEMPT_TABLES` |
| `alembic/versions/0006_system_join_table_grants.py` | 关联表 `GRANT DELETE` |
| `tests/modules/test_system_router.py` | 27 例 |
| `tests/test_migrations.py` | 白名单边界断言（+8 例） |
| `docs/adr/0029-关联表授予DELETE.md`、`docs/04 §6.2.1` | 权限口径 |

**提交记录**：
- 见 `git log --grep T-AUTH-003`

## 实现中发现的 4 个真缺陷

| # | 缺陷 | 症状 |
| --- | --- | --- |
| 1 | **软删角色不会收权**（既有代码的漏洞） | `core/permissions.py::load_permissions` 只 join 了 `role_permissions`，**没碰 `roles`** —— 软删一个角色之后它的权限点**照样授予**用户。界面显示"已停用"、实际一点没少。`load_allowed_workshops` 同样漏。**在任何停用接口出现之前这是个潜伏漏洞，加了接口就变成真的** |
| 2 | **`roles` 表没有 `is_active` 列** | 原以为"停用角色"是置标志位，实现时才发现 T-AUTH-001 选的是**软删**（表有 `deleted_at`、无 `is_active`）。于是"停用"= 软删 —— 而这恰恰让缺陷 1 变成真漏洞 |
| 3 | **停用判断写反了** | `if user.is_active is not enable` —— 停用一个启用中的账号时 `True is not False` 成立，直接抛「已经是停用状态」。**停用功能从来没成功过**，而"启用一个已停用的"反而报错。两个方向都反了 |
| 4 | **权限树顺序不确定** | `permission_codes()` 返回 `frozenset`，迭代顺序由哈希决定 —— 用它遍历会让**角色表单里的权限树每次打开顺序都在变**，用户找不到上次勾的那一项。模块顺序也踩了：registry 里 `bundling` 排在 `cutting` 前，而按业务分组的字典反了。改为一律按 registry 声明顺序 |

另外两个是"写完测试立刻炸"的自伤：`_guard_has_operator` 把 `UserRole` 同时 join 进候选集
和 `IN` 子查询 → SQLAlchemy 报 cartesian product，而 `filterwarnings=error` 把它变成
失败（症状是"停用接口 500，报错却说 cartesian product"）；以及 `update()` 之后读
expired 属性触发 `MissingGreenlet`。

## 遗留问题

| # | 问题 |
| --- | --- |
| L-054 | **ADR-0029 的白名单扩容待业务负责人确认**：给 `user_roles` / `role_permissions` 授 `DELETE`。若不认可，退路是给关联表加 `deleted_at` 并在 `load_permissions` 里过滤 |
| L-055 | **Q-A1 需要在单据落地后重答**：停用用户在途单据怎么办。P0 无单据表所以当前口径是"停用只挡登录" |
| L-056 | **管理员重置口令后没有送达通道**：口令由管理员线下告知。员工端自助改密（`auth/password`）已有，但新员工入职场景需要一条正式流程 |

## 自检清单

对照 `AGENTS.md` §9 逐条确认：

```
[✓] 读过本任务对应的 docs 规范（04 §6.2.1/§7.9、05 §2/§3/§6/§9.5、07 §1.1/§2.2/§2.3/§3.2/§5/§6、09、11 §2、12 §5、ADR-0025、ADR-0029）
[✓] 没有硬编码业务常量（权限点/角色 code 全部来自 registry）
[✓] 没有物理删除业务数据（users/roles 只软删；唯一授 DELETE 的是两张**关联表**，ADR-0029 已论证）
[✓] 新表字段齐全（**本卡零新表**，只复用 T-AUTH-001 建好的五张；alembic check 零漂移）
[✓] 状态变更走了 service 层方法且写了日志（`unit_of_work` 内 `write_document_log`，与数据同事务）
[✓] 接口有权限声明 + 错误码 + OpenAPI 标签（13 端点全带 `x-permission` / `summary` / `tags`）
[✓] 测试覆盖正常 + 异常 + 权限拒绝（27 例接口 + 8 例权限边界）
[✓] 闸门 1 lint 通过（ruff + mypy 45 文件零问题）
[✓] 闸门 2 typecheck 通过（前端 shared/admin/mobile 全绿）
[✓] 闸门 3 单测通过（后端 514 passed / 前端 148 例）
[✓] 闸门 4 迁移通过（0006 upgrade → downgrade → upgrade 往返，零漂移）
[✓] 闸门 5 构建成功（web 镜像 + 容器烟测首页 200）
[✓] 提交信息符合规范
[✓] 本次改动已在 docs/12 变更记录留痕
```