# T-AUTH-002：认证接口、JWT 双通道、权限与数据范围

| 项 | 内容 |
| --- | --- |
| 模块 | auth |
| 负责人 | AI |
| 状态 | `todo` |
| 优先级 | P0 |
| 依赖 | T-AUTH-001 |
| 被依赖 | T-BASE-001（数据范围依赖 `apply_data_scope`）、T-WEB-001 |
| 关联设计 | [docs/modules/00-P0地基.设计.md](../modules/00-P0地基.设计.md) §3.1、§4.2、§4.3、§5、§9（TC-A09~A13） |
| 关联 ADR | ADR-0003（`AuthProvider` 协议）、ADR-0008 |
| 估算 | 0.8d |

## 目标

交付 P0 出口标准之二：**能登录**。5 个认证接口可用，`require_permission` + `apply_data_scope` 两道闸门生效，越权一律 403。

## 范围

**要做**：
- [ ] `app/modules/auth/schemas.py`：5 组 Schema（`LoginIn`/`RefreshIn`/`PasswordChangeIn`/`MeOut`/`LoginOut`），全部 `extra="forbid"`，字段带 `description` + `examples`（05 §7）
- [ ] `app/modules/auth/service.py`：
  - `login`：`employee_no`+`password` → argon2id 校验 → 签发 access(15min) + refresh(7天，HttpOnly+Secure+SameSite=Lax Cookie) → 写 `auth_login_logs`；**连续 5 次失败锁 15 分钟**（Redis 计数，Redis 不可用时降级查 `auth_login_logs` 窗口）
  - `refresh`：**轮换**（旧 token 立即 `revoked_at=now()`），防重放
  - `logout`：吊销 + 写日志
  - `change_password`：校验旧密码；策略 ≥8 位含字母数字；成功后 `must_change_password=false`
  - `get_active_user`：**每次请求校验 `is_active`**（停用 → `11004`）
  - `to_auth_context()`：装配 `permissions`（**每次请求查库**，07 §1.1）+ `data_scope` + `allowed_workshop_ids`（本人车间 ∪ `role_workshops`）
- [ ] `app/modules/auth/router.py`：5 个端点（§4.2 表），`tags="认证"`，每个有 `summary`；`login`/`refresh` 走 `Idempotency-Key` 依赖
- [ ] `app/modules/auth/providers/base.py`：`AuthProvider` Protocol + `ExternalIdentity`（照抄 07 §1.3）；`sms.py` 放 `SmsAuthProvider` **占位**（阶段一不实现发送，P2）
- [ ] `app/core/permissions.py`：`AuthContext`（dataclass，照抄 07 §3.1）+ `get_auth_context` + `require_permission(permission, scope=...)` + `bearer_scheme`
- [ ] `app/core/scope.py`：`apply_data_scope(stmt, model, ctx, *, user_column=None, scope=ScopeSpec(...))` —— **必须支持"模型没有 `workshop_id`/`group_no`"的情况**（主数据表），用显式 `ScopeSpec` 声明列映射，禁止按列名猜
- [ ] `app/core/idempotency.py`：`Idempotency-Key` 依赖（Redis TTL 24h；同键同 body 返首次结果；同键不同 body → `10002`）
- [ ] 测试：`test_auth_router.py`（5 接口 + 锁定 + 轮换 + 停用）、`test_permissions.py`（**每个接口 5 条权限矩阵**，10 §3）、`test_scope.py`（4 种范围 × 3 类模型）

**不做**：
- 不实现短信验证码发送与员工端登录接口（P2）
- 不做 2FA（07 §1.1 标注后续里程碑）
- 不写 `users`/`roles` 的管理接口（T-BASE-001 之后再补，本卡只做认证 5 接口）

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `backend/app/modules/auth/schemas.py` | 新增 | 5 组 Schema |
| `backend/app/modules/auth/service.py` | 新增 | 业务 + 事务边界 |
| `backend/app/modules/auth/router.py` | 新增 | 5 端点 |
| `backend/app/modules/auth/providers/__init__.py` | 新增 | 空 |
| `backend/app/modules/auth/providers/base.py` | 新增 | `AuthProvider` Protocol |
| `backend/app/modules/auth/providers/sms.py` | 新增 | 占位实现 |
| `backend/app/core/permissions.py` | 新增 | `AuthContext` + `require_permission` |
| `backend/app/core/scope.py` | 新增 | `apply_data_scope` + `ScopeSpec` |
| `backend/app/core/idempotency.py` | 新增 | 幂等键 |
| `backend/app/core/security.py` | 修改 | 补 refresh token 签发/校验 |
| `backend/tests/modules/test_auth_router.py` | 新增 | 接口测试 |
| `backend/tests/modules/test_permissions.py` | 新增 | 权限矩阵 |
| `backend/tests/modules/test_scope.py` | 新增 | 数据范围 |
| `backend/tests/factories/user.py` | 新增 | 用户/角色工厂 |

## 实现要点（必读规范）

- [ ] 遵守 docs/07 §1.1/§1.2/§1.3（token 时效、Cookie 属性、AuthProvider）、§3.1/§3.2（`AuthContext`、`apply_data_scope` 三条铁律）、§3.3（写操作额外校验）、§5（审计与合规）
- [ ] 遵守 docs/03 §1.3（Router 不查库）、§1.4（Service 正例：事务边界只在首层、私有方法不开事务）
- [ ] 遵守 docs/05 §5（幂等）、§6（权限声明必填）
- [ ] 遵守 docs/10 §3（权限矩阵 5 条）、§2.1（测试用工厂）

## 验收标准

- [ ] `POST /api/v1/auth/login` 成功返回 `access_token` + `expires_in` + `permissions[]`；refresh token 在 **HttpOnly Cookie** 里（响应体不含）
- [ ] 错误密码 → `11003`/401；无 token → `11001`/401；非法 token → `11002`；停用用户 → `11004`
- [ ] 连错 5 次后第 6 次即使密码正确也拒（`11004`，文案说明"锁定"），15 分钟窗口内持续拒绝
- [ ] `refresh` 后旧 refresh token 立即失效（重放 → `11002`）
- [ ] `GET /api/v1/auth/me` 返回 `permissions` 与 `data_scope`、`allowed_workshop_ids`
- [ ] 角色权限变更后，**旧 access token 的后续请求立即按新权限**（权限查库，无缓存）
- [ ] **权限矩阵 5 条 × 5 接口全绿**（TC-A09~A13）
- [ ] `apply_data_scope` 单测：`FACTORY` 不过滤；`WORKSHOP` 无车间 → `where(False)`；`GROUP`/`SELF` 正确；**恒附加 `deleted_at IS NULL`**；主数据模型（无 `workshop_id`）不报错
- [ ] `app/modules/auth/service.py` 行覆盖 ≥ 90%；新增行 100%
- [ ] 闸门 1/2/3/4 全绿

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-A09 | `[无 token]` 调任一受保护接口 | 401 + `11001` | |
| TC-A10 | `[无权限]`（`employee` 角色）调管理接口 | 403 + `12001` | |
| TC-A11 | `[越数据范围]` 车间主管按 ID 查他车间对象 | 403 + `12002` | |
| TC-A12 | `[合法]` | 200 + `code=0` | |
| TC-A13 | `[参数非法]` 缺 `password` | 422 + `10001` | |
| TC-A14 | 同 `Idempotency-Key` 同 body 连发 2 次 login | 第 2 次返回首次结果，200（非错误） | |
| TC-A15 | 同 `Idempotency-Key` 不同 body | `10002` | |
| TC-A16 | 并发 20 次 login（正确密码） | 全部成功，`auth_login_logs` 20 行 | |
| TC-A17 | `SmsAuthProvider.verify_code` 未实现时调用 | 抛明确 `BusinessError`，**不是 `NotImplementedError` 泄漏到 HTTP** | |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| | +0 / -0 | |

**提交记录**：
- `<hash>` feat(auth): 认证接口、JWT 双通道、权限与数据范围 …

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| | 权限每次请求查库的延迟需实测；若超 00 §6 阈值再评估缓存（须写 ADR） | docs/12 §遗留问题清单 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。
