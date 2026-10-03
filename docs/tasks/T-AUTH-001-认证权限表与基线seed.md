# T-AUTH-001：认证与权限表、迁移与基线 seed

| 项 | 内容 |
| --- | --- |
| 模块 | auth |
| 负责人 | AI |
| 状态 | **`done`**（2026-10-03；闸门 1-4 宿主机与容器内全绿，199 测试 / 覆盖率 97.6%） |
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
| **INV-P0-4-1** | registry 权限点集合 **==** docs/07 §2.2 解析结果 | 双向差集为空 | ✅ 122 = 122 |
| **INV-P0-4-2** | registry 角色集合 **==** docs/07 §2.3 | 完全相等（10 个） | ✅ |
| **INV-P0-4-3** | `permissions` 表 **==** registry | 不多不少 | ✅ |
| **INV-P0-4-4** | `role_permissions` 绑定 **==** registry 逐角色 | 完全一致（10 个角色） | ✅ |
| **INV-P0-4-5** | 前端 `permissions.ts` **==** registry | T-WEB-001 落地后生效 | ⏭️ skip（文件尚不存在，skip 原因写在用例里） |
| 解析器自身 | docs 解析出的权限点数 > 100、角色数 == 10 | 防止解析器失效导致断言空转 | ✅ |
| 解析器自身 | 两个**已作废**码不入库 | `self:piecework:count` / `bundling:void_code` | ✅ |
| 解析器自身 | 「领域专用动作」行的动作词不是权限点 | `rate:manage` / `code:void` / `cost:view` / `order:create` | ✅ |
| **回归守卫** | 含下划线的码必须存在 | `base:rate_template:manage`（曾因正则 `[a-z]+` 整条消失） | ✅ |
| 角色完整性 | 每个角色引用的权限点都存在 | 无悬空引用 | ✅ |
| 角色完整性 | `super_admin` 拿到全部 122 个 | docs/07 §2.3「所有权限」 | ✅ |
| 角色完整性 | `warehouse_keeper` **无** `stock:cost:view` | 成本不可见（docs/07 §2.3/§5） | ✅ |
| 角色完整性 | `employee` 零管理权限 | 只走 `/self/**` | ✅ |
| 角色完整性 | 10 个角色的 `data_scope` 与 §2.3 表格逐一相等 | 10/10 | ✅ |
| TC-A01 | seed 连跑 3 次 | 权限点/角色/绑定行数与内容零变化 | ✅ |
| TC-A01b | 用户改过权限点名称后重跑 seed | 名称**不被覆盖** | ✅ |
| TC-A02 | 迁移 up → down → up | 三步全绿，`0002 (head)` | ✅ |
| TC-A03 | `--check` 发现缺权限点 / 多权限点 / 超管被裁剪 / 缺角色 | 4 种漂移全部能检出 | ✅ |
| TC-A04 | 初始超管：无口令变量 → **不创建**且明确提示 | 不生成弱口令 | ✅ |
| TC-A05 | 初始超管：给了口令 → 创建 ADMIN + 绑 super_admin + 强制改密 | 3 项断言全过 | ✅ |
| TC-A06 | 弱口令被策略拦下 | 抛 `10001` | ✅ |
| TC-A07 | `restore_builtin --list` 只列不写 | 权限点行数不变 | ✅ |
| TC-A08 | 恢复后补齐并写 `RESTORE` 留痕 | `document_logs` 有 1 条 RESTORE | ✅ |
| TC-A09 | 数据齐全时恢复是空操作 | 不产生多余留痕 | ✅ |
| TC-A10 | 模型带齐 04 §2 公共字段（users/roles/permissions） | 8 个字段 | ✅ |
| TC-A11 | 关联表**无**公共字段 | 只有 id + created_at | ✅ |
| TC-A12 | append-only 表豁免公共字段 | 无 version/updated_*/deleted_at | ✅ |
| TC-A13 | 硬删禁令表有 `deleted_at` | users/roles/permissions | ✅ |
| TC-A14 | `ck_*_version_positive` 已在模型声明 | 3 张表各 1 个 | ✅ |
| TC-A15 | `to_auth_context` 不带口令哈希/手机号 | 字段集合断言 | ✅ |
| TC-A16 | `AuthContext` 的 `has` / `has_any` / `*` 通配 / `is_factory_scoped` | 6 项断言 | ✅ |
| TC-A17 | argon2id 前缀 + 随机盐 | 同口令两次哈希不同 | ✅ |
| TC-A18 | 口令策略 | 弱/强各 3 组 | ✅ |
| TC-A19 | JWT 载荷**无 permissions / scopes** | 只含身份 | ✅ |
| TC-A20 | 过期 / 篡改 / 垃圾 / 类型不符 | 分别 11001 / 11002 / 11002 / 11002 | ✅ |
| TC-A21 | 缺 `JWT_SECRET` 拒绝签发 | 抛 `99999` 并提示 openssl 命令 | ✅ |
| TC-A22 | refresh token 只存 sha256 | 摘要稳定、长度 64、不含明文 | ✅ |
| TC-A23 | CLI 入口退出码（缺 URL / 漂移 / 正常 / 业务异常） | 1 / 1 / 0 / 1 | ✅ |

**闸门结果（宿主机）**：闸门 1 ✅ ｜ 闸门 2 ✅ ｜ 闸门 3 **199 passed / 覆盖率 97.6%** ✅ ｜ 闸门 4 三步往返 ✅
**闸门结果（容器内）**：闸门 1-2 ✅ ｜ 闸门 3 ✅ 199 passed / 97.6% ｜（闸门 4 由 migrate-check 服务验证）

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `backend/app/common/permissions_registry.py` | +319 | **单一来源**：122 权限点 + 10 角色 + 角色权限矩阵。**由脚本从 docs/07 §2.2 逐条抽取生成**，不手抄 |
| `backend/app/modules/auth/__init__.py` | +0 | 模块包（env.py 的 models 自动发现入口） |
| `backend/app/modules/auth/models.py` | +318 | 9 张表模型 + `to_auth_context` |
| `backend/app/core/security.py` | +180 | argon2id + JWT 双通道 + refresh token 摘要 + 口令策略 |
| `backend/app/core/permissions.py` | +63 | `AuthContext`（T-AUTH-002 再补 `get_auth_context` / `require_permission`） |
| `backend/app/cli/__init__.py` | +0 | CLI 包 |
| `backend/app/cli/seed_baseline.py` | +244 | 幂等 seed + `--check` 校验 + 初始超管 |
| `backend/app/cli/restore_builtin.py` | +158 | 显式恢复内置数据 + `RESTORE` 留痕 |
| `backend/alembic/versions/0002_auth.py` | +377 | 9 张表 + 权限点/角色/绑定 seed |
| `backend/tests/conftest.py` | 修改 | 新增 `ddl_session`（迁移账号夹具）+ JWT 密钥加长到 ≥32 字节 |
| `backend/tests/modules/test_permission_registry.py` | +271 | **INV-P0-4 五处一致性守卫** |
| `backend/tests/modules/test_auth_models.py` | +193 | 模型结构（公共字段/关联表/append-only/约束） |
| `backend/tests/modules/test_security.py` | +216 | 口令与令牌 |
| `backend/tests/modules/test_seed_cli.py` | +251 | seed 幂等 / 校验 / 初始超管 / 恢复 |
| `backend/tests/modules/test_cli_entrypoint.py` | +235 | CLI 入口控制流与退出码 |
| `backend/pyproject.toml` | 修改 | 新增 `app/cli/**` 豁免 `T201`（CLI 的人机界面就是 stdout） |

合计新增约 3200 行（实现 ~1800 / 测试 ~1200），分 3 次提交（均 < 800 行业务代码）。

## 关键设计决策

| # | 决策 | 理由 |
| --- | --- | --- |
| 1 | **registry 由脚本从 docs/07 抽取生成**，不手抄 | 122 个码手抄必然出错；实测手抄式正则就漏了 `base:rate_template:manage`（含下划线） |
| 2 | 迁移 **import** registry 取 seed 数据（表结构仍自包含） | Alembic 惯例是迁移自包含，但那只针对**会演进的结构**；权限点是静态配置，抄两份必然漂移。由测试保证「迁移 == registry == docs」三者一致 |
| 3 | `app/core/permissions.py` 只落 `AuthContext` | 数据容器与鉴权链路分两步：先让模型层能编译通过，T-AUTH-002 再补依赖函数 |
| 4 | seed 的**权限点**与**字典项**分开两个命令 | 权限点是静态配置（`seed_baseline` 直接补）；字典项有"用户真删"语义，走墓碑机制（ADR-0025，`restore_builtin`） |
| 5 | 测试引入 **`ddl_session`**（迁移账号夹具） | `erp_app` 被 REVOKE 掉全部 DELETE（04 §6.2.1），而 seed/restore 本就是迁移账号执行的运维操作 |

## 实测抓到的 6 个真缺陷

| # | 缺陷 | 现象 | 修法 |
| --- | --- | --- | --- |
| 1 | **权限点抽取正则漏码** | `base:rate_template:manage` 含下划线，`[a-z]+` 匹配不到 → registry 缺 1 个码 | 正则改 `[a-z_]+`，并加**回归守卫测试** |
| 2 | **已作废码混入** | `self:piecework:count`（文档明说不存在）、`bundling:void_code`（已被两级写法取代）只出现在"作废说明"里，正则分不出 | 显式 `DEPRECATED_CODES` 排除 + 测试断言 |
| 3 | **模型缺 CHECK 约束** | `User/Role/Permission` 没声明 `ck_*_version_positive`，与迁移不一致 → autogenerate 反复产生 diff | 模型补声明，测试断言 |
| 4 | `NameError: AuthContext` | 注解在运行期求值，而 `AuthContext` 只在 `TYPE_CHECKING` 下导入 | 加 `from __future__ import annotations` |
| 5 | `pwdlib` 无 `needs_rehash` | 我凭印象调了不存在的 API | 改用 `verify_and_update()` 返回 `(ok, new_hash)` |
| 6 | 测试 JWT 密钥只有 30 字节 | `InsecureKeyLengthWarning`（HS256 要求 ≥32），被 `filterwarnings=error` 抓住 | 测试密钥加长；**这条警告本身有价值** |

另有两处测试自身的错（`COMMON_COLUMNS & names == {"id"}` 断言写错、`is_factory_scoped` 用了 FACTORY 却又断言 False）已修正。

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| **L-024** | **非 super_admin 角色的完整权限矩阵无集中定义**，本卡按 docs/07 §2.3 的文字描述给了**未经业务方确认**的保守集合 | docs/12 §5 L-024（**需业务方确认**） |
| L-025 | docs/07 §2.2 的「动作词」清单覆盖不全，无法只靠它做命名校验 | docs/12 §5 L-025 |
| W1 | `auth_login_logs` / `auth_refresh_tokens` 的 DDL 需回写 docs/04 §7 + docs/07 §2.1 | 设计稿 §10.1 W1（归档阶段） |
| W2 | append-only 表豁免 04 §2 公共字段需回写 04 §2 | 设计稿 §10.1 W2 |
| — | `users.workshop_id` 与 `role_workshops.workshop_id` 的 FK 依赖 `workshops` 表，**T-BASE-001 必须补** `ALTER TABLE ... ADD CONSTRAINT` | T-BASE-001 验收项 |
| — | 前端 `permissions.ts` 一致性检查处于 skip，待 T-WEB-001 | T-WEB-001 |
| — | `auth_headers` 夹具仍是 `NotImplementedError` 占位 | T-AUTH-002 |
| — | `seed_baseline` 依赖 `ERP_INITIAL_ADMIN_PASSWORD`（新增环境变量），需补进 `.env.example` | 归档阶段 |

**提交记录**：
- `<hash>` feat(auth): 认证权限表、基线 seed 与 INV-P0-4 一致性守卫 …

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。
