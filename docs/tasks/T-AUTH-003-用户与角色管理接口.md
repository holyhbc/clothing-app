# T-AUTH-003：用户与角色管理接口（后台）

| 项 | 内容 |
| --- | --- |
| 模块 | auth |
| 负责人 | AI |
| 状态 | `todo` |
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

- [ ] `GET /api/v1/system/users`（筛选 `q` / `workshop_id` / `is_active`；分页；**响应绝不含 `password_hash` 与 `phone`**）
- [ ] `POST /api/v1/system/users`
- [ ] `GET /api/v1/system/users/{id}`
- [ ] `PATCH /api/v1/system/users/{id}`（`version` 乐观锁，docs/05 §2）
- [ ] `POST /api/v1/system/users/{id}/disables`（**必填原因**，docs/06 §5）
- [ ] `POST /api/v1/system/users/{id}/password-resets`（管理员重置口令，返回**一次性初始口令**；写入 `must_change_password=true`）
- [ ] `GET /api/v1/system/roles` / `POST` / `PATCH /{id}` / `POST /{id}/disables`
- [ ] `GET /api/v1/system/permissions`（按模块分组，供权限树勾选；数据来自 `permissions_registry`）
- [ ] `PUT /api/v1/system/roles/{id}/permissions`（**整体替换** + 差异写 `document_logs`，docs/07 §5「权限变更留痕」）
- [ ] `GET /api/v1/system/users/options`、`GET /api/v1/system/roles/options`（Combo 用，docs/05 §9.5.2）

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

## 需要先确认的业务问题

| # | 问题 | 为什么不能自行发明 |
| --- | --- | --- |
| Q-A1 | **停用一个用户时，他当前持有的未完成单据怎么办？**（阻止登录 / 允许继续操作 / 转移） | docs/08 未定义"离职/停用"与在途单据的关系；这会直接影响工厂生产 |
| Q-A2 | **一个用户能否同时属于多个角色，且 `data_scope` 如何合并？**（取最大 / 取最窄 / 必须唯一） | `AuthContext` 用的是单个 `data_scope`，多角色合并规则未定义 |
| Q-A3 | **最后一个系统管理员被停用时是否阻止？** | 现场只有一两个能改权限的人，误停用会把系统锁死 |
| Q-A4 | 管理员重置口令时，初始口令怎么给？打印小票 / 后台展示一次 / 走短信 | docs/07 §1 未涉及 |

以上四点必须在实现前由业务方确认；未确认前**不许猜**（AGENTS §2.3）。

## 验收标准

- [ ] `openapi.json` 出现上述端点，前端能生成类型（`pnpm generate:api` 无 diff 即幂等）
- [ ] 用户列表响应**没有** `password_hash` / `phone` 字段（有断言）
- [ ] 无 `system:user:manage` 的账号访问用户接口 → 403（有断言）
- [ ] 停用未填原因 → `10006 REASON_REQUIRED`（有断言）
- [ ] 改内置角色 code / 删内置角色 → 被拒（有断言）
- [ ] 改权限后原用户**立刻**失去该权限（不重新登录即可，有断言）
- [ ] `document_logs` 里有权限变更记录（角色 / 操作人 / 从→到 / 原因）
- [ ] `alembic check` 零漂移；闸门 1-5 全绿

## 提交记录

- `<hash>` feat(auth): 用户与角色管理接口 …

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。