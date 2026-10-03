# T-AUTH-001：认证与权限表、迁移与基线 seed

| 项 | 内容 |
| --- | --- |
| 模块 | auth |
| 负责人 | AI |
| 状态 | `todo` |
| 优先级 | P0 |
| 依赖 | T-INFRA-004 |
| 被依赖 | T-AUTH-002, T-WEB-001 |
| 关联设计 | [docs/modules/00-P0地基.设计.md](../modules/00-P0地基.设计.md) §2.1 组 B、§2.3 INV-P0-4、§9（TC-A01） |
| 关联 ADR | ADR-0003（员工端认证预留）、ADR-0008（权限点命名与总表） |
| 估算 | 0.8d |

## 目标

把认证与权限的**数据底座**建起来：9 张表 + 07 §2.2 全量权限点 + 07 §2.3 的 10 个内置角色 seed 全部落库，且 seed **连跑 3 次结果完全一致**（TC-A01）。

## 范围

**要做**：
- [ ] `alembic/versions/0002_auth.py`（组 B 九张表，全部带 04 §2 公共字段，关联表 `user_roles`/`role_permissions`/`role_workshops` **无公共字段**）：
  - `users`（`employee_no` 唯一、`phone` 唯一、`password_hash`、`workshop_id` FK、`group_no`、`data_scope`、`is_active`、`must_change_password`）
  - `roles`（`code` 唯一、`name`、`data_scope`、`is_system`）
  - `permissions`（`code` 唯一、`name`、`module`、`action`、`sort`）
  - `user_roles` / `role_permissions` / `role_workshops`（复合 PK）
  - `employee_external_identities`（UNIQUE(`provider`,`external_id`)，ADR-0003 预留）
  - `auth_login_logs`（**新表，设计稿 §10.1 W1 要求回写 07 §2.1**：`channel`/`ip inet`/`user_agent`/`is_success`/`fail_reason`/`created_at` + 2 个索引；append-only，**豁免公共字段**）
  - `auth_refresh_tokens`（**新表，W1**：`token_hash` 唯一（sha256，不存明文）、`channel`、`device_id`、`expires_at`、`revoked_at`、`created_at` + 部分索引；append-only）
  - 索引：`uq_users_employee_no`、`uq_users_phone`、`pk_user_roles(user_id)`、`pk_role_permissions(role_id)`、`idx_auth_login_logs_employee_no`、`uq_auth_refresh_tokens_hash`、`idx_auth_refresh_tokens_user … WHERE revoked_at IS NULL`
  - **权限点 seed**：07 §2.2 全表（含 `self:*` 四个）用 `ON CONFLICT (code) DO NOTHING`
  - **内置角色 seed**：07 §2.3 的 10 个角色（`is_system=true`）+ 角色权限绑定
- [ ] `app/modules/auth/models.py`：SQLAlchemy 模型（**仅字段与约束，无逻辑**，03 §1.3）
- [ ] `app/core/security.py`：argon2id 哈希（`pwdlib[argon2]`）+ JWT 签发/校验（HS256，15min，`type`/`sub`/`role_ids`/`jti`；**载荷不含权限明细**，07 §1.1）+ `sha256` 工具（refresh token）
- [ ] `app/common/permissions_registry.py`：**权限点与内置角色的单一来源常量**（`list[PermissionSeed]` / `list[RoleSeed]`），迁移与测试都从这里取 —— 这是 INV-P0-4 的实现基础
- [ ] `app/cli/seed_baseline.py`：写权限点 + 内置角色（幂等，`ON CONFLICT DO NOTHING`）
- [ ] `app/cli/restore_builtin.py`：显式恢复入口（占位实现，字典部分在 T-BASE-001）
- [ ] 测试：`test_auth_models.py`（结构 + 公共字段）、`test_permission_registry.py`（**INV-P0-4：解析 `docs/07 §2.2` 表格，与 registry / seed 结果 / `packages/shared/enums/permissions.ts` 三方双向断言**）

**不做**：
- 不写登录接口与 JWT 中间件（T-AUTH-002）
- 不建 `workshops` 表（`users.workshop_id` FK 需等 T-BASE-001）→ ⚠️ **处理**：本卡 `users.workshop_id` **不加 FK**，在 T-BASE-001 迁移里补 `ADD CONSTRAINT fk_users_workshops`（04 §1 要求外键，跨卡补齐需在 T-BASE-001 验收里显式检查）
- 不写 `factories/user.py`（T-AUTH-002 补）

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `backend/alembic/versions/0002_auth.py` | 新增 | 9 表 + 索引 + 权限点/角色 seed |
| `backend/app/modules/auth/__init__.py` | 新增 | 空 |
| `backend/app/modules/auth/models.py` | 新增 | SQLAlchemy 模型 |
| `backend/app/core/security.py` | 新增 | argon2id + JWT + sha256 |
| `backend/app/common/permissions_registry.py` | 新增 | 权限点/角色单一来源 |
| `backend/app/cli/__init__.py` | 新增 | 空 |
| `backend/app/cli/seed_baseline.py` | 新增 | 幂等 seed |
| `backend/app/cli/restore_builtin.py` | 新增 | 恢复入口 |
| `backend/tests/modules/test_auth_models.py` | 新增 | 结构断言 |
| `backend/tests/modules/test_permission_registry.py` | 新增 | INV-P0-4 三处一致 |

## 实现要点（必读规范）

- [ ] 遵守 docs/07 §2.1（表结构）、§2.2（权限点总表，**逐条抄，一个不漏一个不多**）、§2.3（内置角色与数据范围）、§6（新增角色/权限的操作流程）
- [ ] 遵守 docs/04 §2（公共字段）、§1（外键命名 `fk_<子>_<父>`、索引命名 `idx_`/`uq_`）
- [ ] 遵守 docs/03 §1.1（类型标注全量、`print` 禁用）
- [ ] 密码哈希**禁止** MD5/SHA1/裸存；`JWT_SECRET` 无默认值，缺失时**启动即失败**（不静默用弱密钥）

## 验收标准

- [ ] **闸门 4 通过**（`upgrade head → downgrade -1 → upgrade head`）
- [ ] TC-A01：seed 连跑 3 次，`permissions` / `roles` / `role_permissions` 行数与内容**完全一致**
- [ ] INV-P0-4 断言通过：`permissions` 表 code 集合 **==** registry 集合 **==** 07 §2.2 表格解析结果；缺一即失败
- [ ] `pytest tests/modules/test_permission_registry.py -q` 全绿
- [ ] TC-A07：`hash_password("x")` 两次结果不同、前缀为 `$argon2id$`、`verify` 正确
- [ ] `app/modules/auth/models.py` 每个模型含 04 §2 全部公共字段（测试断言）
- [ ] `users` / `roles` / `permissions` 存在且为 04 §6.1 硬删禁令表（测试断言"无 DELETE 路径"仅在 service 层体现，本卡只断言表存在）
- [ ] 新增行覆盖 100%；`app/modules/auth/models.py` 覆盖 100%
- [ ] 闸门 1/2/3/5 全绿

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-A01 | `seed_baseline` 连跑 3 次 | 权限点/角色/绑定行数与内容零变化 | |
| TC-A02 | registry 权限点集合 vs 07 §2.2 表格 | 完全相等（双向差集为空） | |
| TC-A03 | 内置角色集合 vs 07 §2.3 表 | 10 个角色 code 完全相等，`is_system=true` | |
| TC-A04 | 每个角色是否有数据范围 | 与 07 §2.3 一致（`employee`=SELF、`merchandiser`=SELF…） | |
| TC-A05 | `auth_refresh_tokens.token_hash` 唯一约束 | 重复插入报 `IntegrityError` | |
| TC-A06 | `auth_login_logs` append-only | `erp_app` 的 `UPDATE`/`DELETE` 被拒 | |
| TC-A07 | argon2id | 前缀正确、同密码两次哈希不同、`verify` 通过 | |
| TC-A08 | JWT 载荷 | 只含 `sub`/`role_ids`/`jti`/`type`/`exp`，**不含权限明细** | |
| TC-A09 | 过期 token | `decode_token` 抛 `BusinessError(11001)` | |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| | +0 / -0 | |

**提交记录**：
- `<hash>` feat(auth): 认证权限表、迁移与基线 seed …

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| W1 | `auth_login_logs` / `auth_refresh_tokens` DDL 需回写 07 §2.1 与 04 §7 | 归档阶段（设计稿 §10.1 W1） |
| | `users.workshop_id` FK 待 T-BASE-001 补 | T-BASE-001 验收项 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。
