# T-AUTH-002：认证接口、JWT 双通道、权限与数据范围

| 项 | 内容 |
| --- | --- |
| 模块 | auth |
| 负责人 | AI |
| 状态 | **`done`**（2026-10-03；闸门 1-4 宿主机与容器内全绿，284 测试 / 覆盖率 92.7%，`alembic check` 无漂移） |
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
| **login-1** | 成功登录 | 200 + access token + refresh cookie | ✅ |
| **login-2** | refresh cookie 属性 | `HttpOnly` + `SameSite=Lax`，非生产不加 `Secure` | ✅ |
| **login-3** | 库里只存 sha256 摘要 | 64 位、不含明文 | ✅ |
| **login-4** | 写 `auth_login_logs` | `(工号, 成功)` | ✅ |
| **login-5** | 记录 IP 与渠道 | `127.0.0.1` / `H5_SMS` | ✅ |
| **login-6** | **工号不存在 vs 口令错误** | 同码 11003、**同文案**（防枚举） | ✅ |
| **login-7** | 账号停用 | 403 / `11004` | ✅ |
| **login-8** | 连错 5 次后锁定 | 第 6 次 429 / `10004`，**正确口令也不放行** | ✅ |
| **login-9** | 成功后清零 | 成功后再错 4 次不触发锁定 | ✅ |
| **login-10** | 工号不存在也写失败日志 | 留痕，可事后审计爆破 | ✅ |
| **login-11** | 失败计数按工号分键 | A 锁住不影响 B | ✅ |
| **idem-1** | 同键同体重试 | 返回**首次结果**，只签发一次 refresh | ✅ |
| **idem-2** | 同键不同体 | 400 / `10002` | ✅ |
| **idem-3** | 无幂等键 | 正常登录 | ✅ |
| **me-1** | 无 token | 401 / `11001` | ✅ |
| **me-2** | super_admin 自描述 | 角色 + **122** 个权限点 + FACTORY | ✅ |
| **me-3** | 账号被停用后 `/me` | `11004`（停用即时生效） | ✅ |
| **refresh-1** | 轮换 | 新 cookie ≠ 旧 cookie | ✅ |
| **refresh-2** | **重放旧 token** | 401 / `11002`（防重放） | ✅ |
| **refresh-3** | 无 cookie / 垃圾 cookie | `11001` / `11002` | ✅ |
| **logout-1** | 吊销全部 refresh token | 返回 2，两台设备都失效 | ✅ |
| **logout-2** | 清 cookie | `Set-Cookie` 删除 | ✅ |
| **logout-3** | 无 token | 401 | ✅ |
| **pwd-1** | 改密成功 | `must_change_password=false`、`version=2`、新口令可登录 | ✅ |
| **pwd-2** | 原口令错 | 401 / `11003` | ✅ |
| **pwd-3** | 新口令太弱 / 与原口令相同 | `10001` | ✅ |
| **pwd-4** | **改密后旧 token 失效** | 否则改密等于没改 | ✅ |
| **pwd-5** | 无 token | 401 | ✅ |
| **scope-1** | FACTORY | 不加范围条件（软删过滤仍加） | ✅ |
| **scope-2** | SELF | 只看到自己 | ✅ |
| **scope-3** | **资源不支持该列** | 查不到任何数据（**不是放行全部**） | ✅ |
| **scope-4** | WORKSHOP 按可见车间过滤 | 精确到车间 | ✅ |
| **scope-5** | **可见车间为空** | 查不到任何数据（最易写成越权的一处） | ✅ |
| **scope-6** | 并入本人所属车间 | 角色没授权也能看到自己 | ✅ |
| **scope-7** | GROUP 需车间 + 组别同时匹配 | 缺任一 → 查不到 | ✅ |
| **scope-8** | 软删过滤自动附加 | `deleted_at IS NULL` | ✅ |
| **scope-9** | `in_scope` 按 ID 直查防越权 | 范围外 → `False` | ✅ |
| **scope-10** | `assert_in_scope` | 抛 `12002` | ✅ |
| **scope-11** | **未登记资源按最严处理** | 不允许默认放行 | ✅ |
| **perm-1** | 无 bearer | `11001` | ✅ |
| **perm-2** | 垃圾令牌 | `11002` | ✅ |
| **perm-3** | **access/refresh 类型不可互换** | 用错类型 → `11002` | ✅ |
| **perm-4** | 用户不存在 | `11001` | ✅ |
| **perm-5** | 账号停用 | `11004` | ✅ |
| **perm-6** | **权限每次请求查库** | 令牌生成后再授权 → 立即可见 | ✅ |
| **perm-7** | 可见车间来自角色授权 | 与 `role_workshops` 一致 | ✅ |
| **perm-8** | 上下文不泄露口令哈希 / 手机号 | 按字段名核对 | ✅ |
| **perm-9** | `require_permission` 命中 / 未命中 | 放行 / `12001`（含所需权限点） | ✅ |
| **perm-10** | 全局 `*` 通配 | 放行一切 | ✅ |
| **perm-11** | **前缀通配不放行** | `base:*` 不给 `base:update`（L-027） | ✅ |
| **lock-1** | 阈值与窗口锁死 | 5 次 / 15 分钟不可被"顺手调优" | ✅ |
| **lock-2** | 读 Redis 计数 | 达阈值即锁 | ✅ |
| **lock-3** | **`is_login_locked` 不消耗计数** | 纯查询，探测 5 次不会锁账号 | ✅ |
| **lock-4** | **Redis 挂掉降级查日志窗口** | 5 条失败即锁 | ✅ |
| **lock-5** | 窗口外失败不计入 | 否则 15 分钟锁变成永久锁 | ✅ |
| **lock-6** | 成功后清零 | Redis 键删除 | ✅ |
| **sec-1** | 签发接口**无** `extra_claims` 参数 | 签名断言，口子焊死 | ✅ |
| **sec-2** | 载荷字段 ⊆ 白名单 | 多出字段 → 拒绝签发 | ✅ |
| **sec-3** | `AuthProvider` 结构化子类型可检查 | 新增通道必须实现同一组方法 | ✅ |
| **sec-4** | 验证码明文不进日志 | 用哨兵串扫全部日志输出 | ✅ |
| **sec-5** | 短信通道未配置时明确拒绝 | `10008` + 给出下一步 | ✅ |
| **drift-1** | `alembic check` | **No new upgrade operations detected** | ✅ |

**闸门结果（宿主机）**：闸门 1 ✅ ｜ 闸门 2 ✅ ｜ 闸门 3 **284 passed / 覆盖率 92.7%** ✅ ｜ 闸门 4 全量往返（`base → head`）✅ ｜ `alembic check` 无漂移 ✅
**闸门结果（容器内）**：闸门 1-2 ✅ ｜ 闸门 3 ✅ 284 passed / 92.7% ｜ 闸门 4 ✅ `0003 (head)`

## 实际改动（完成后回填）

| 文件 | 说明 |
| --- | --- |
| `app/modules/auth/schemas.py` | 6 组 Schema。**白名单式**：`password_hash` / `phone` 在 Schema 里根本没有声明，新增字段时不会静默泄露 |
| `app/modules/auth/service.py` | 6 条安全约定全部落地；`unit_of_work` 是全项目唯一的事务入口 |
| `app/modules/auth/router.py` | 6 个端点 + Cookie 读写 + 幂等依赖 |
| `app/modules/auth/providers/{base,sms}.py` | `AuthProvider` 协议 + 短信占位通道（ADR-0003） |
| `app/core/permissions.py` | `get_auth_context` / `require_permission` / `bearer_scheme` |
| `app/core/scope.py` | `apply_data_scope` / `in_scope` / `assert_in_scope` / `SCOPE_SPECS` |
| `app/core/idempotency.py` | `Idempotency-Key` 解析、指纹、缓存 |
| `app/core/cache.py` | Redis 访问器（幂等 + 登录锁定） |
| `app/core/security.py` | **移除 `extra_claims` 参数**，改为载荷白名单 `validate_claims` |
| `app/modules/auth/models.py` | 去掉 2 处悬空 FK、补 9 处表注释、补 `channel` 默认值 |
| `app/common/models.py` | **新增 `DocumentLog` 模型**；`remark` 显式 `Text()` |
| `alembic/versions/0003_align_models.py` | 3 个软删索引 + 58 处列注释 |
| `app/main.py` | `_register_module_routers`（唯一挂载点） |
| `tests/factories/user.py` | 6 个工厂 + `grant_role` / `grant_permissions` |
| `tests/conftest.py` | `auth_headers` 真实实现；**Redis 命名空间清理夹具** |
| `tests/modules/test_auth_router.py` | 32 例端点测试 |
| `tests/modules/test_auth_service.py` | 24 例 service 与锁定路径测试 |
| `tests/modules/test_permissions.py` | 13 例鉴权链路测试 |
| `tests/modules/test_scope.py` | 15 例数据范围测试 |
| `tests/modules/test_auth_providers.py` | 6 例外部通道测试 |

## 关键设计决策

| # | 决策 | 理由 |
| --- | --- | --- |
| 1 | **5 个端点都不挂 `require_permission`**，改用 OpenAPI `x-permission: null` 显式声明 | `login`/`refresh` 是公开端点；`me`/`logout`/`password` 是登录态自服务 —— 加权限点会让**没角色的人查不到自己的账号而卡死**。而 docs/07 §2.2 的 122 个码里确实没有 auth 权限点。登记 L-026 |
| 2 | 事务用 `unit_of_work`（savepoint + `commit`）而不是 `session.begin()` | SQLAlchemy autobegin 会在任何读之后开启事务，此时 `begin()` 抛 "already begun"；savepoint 在既有事务里安全。测试侧 `commit()` 只释放 savepoint，外层回滚照常清干净 |
| 3 | 数据范围用**显式 `ScopeSpec` 映射**而不是按列名猜 | 主数据表没有 `workshop_id`；`SELF` 的含义随资源而变（计件=本人，款号=我负责的款号）。按列名推断必然出错 |
| 4 | 列缺失或可见集合为空时一律 `where(false())` | 这两处最容易写成"跳过过滤"，而那等于把全表返回给本该受限的请求 |
| 5 | `is_login_locked` **纯查询**，自增只发生在失败分支 | 否则"探测 5 次"就把账号锁了 |
| 6 | 停用账号**不计入**失败次数 | 员工离职后系统还要能查审计，不该让他反复尝试把自己锁死 |
| 7 | 幂等缓存**优先 Redis**，降级进程内并打 WARN | 24h TTL 的结果缓存放内存会随重启丢失；降级必须可见，否则生产忘配 Redis 无人知晓 |
| 8 | refresh cookie `path="/api/v1/auth"` | 限制 Cookie 作用域，缩小被 CSRF 借用的面 |
| 9 | `samesite="lax"` 而非 `strict` | 同站场景 lax 足够挡跨站表单，又不会像 strict 那样从外链跳转进来丢 Cookie |
| 10 | 权限用**白名单常量 + `raise`** 而非 `assert` | `assert` 在 `python -O` 下会被整条优化掉，安全检查不能这样写 |

## 实测抓到的 8 个真缺陷

| # | 缺陷 | 现象 | 修法 |
| --- | --- | --- | --- |
| 1 | **`document_logs` 没有 ORM 模型** | `alembic check` 报 `remove_table` —— 任何人执行 `--autogenerate` 都会拿到 `drop_table('document_logs')`，一次误操作抹掉全厂审计日志，命令本身不给任何提示 | 补 `DocumentLog` 模型 |
| 2 | **`BaseModel.remark` 类型漂移** | `Mapped[str\|None]` 无显式类型被推断成 VARCHAR，与 docs/04 §2「remark text」及迁移冲突，autogenerate 反复报"改列类型" | 显式 `Text()` |
| 3 | **模型有 FK、迁移没有** | `users.workshop_id` / `role_workshops.workshop_id` 声明了 `ForeignKey("workshops.id")`，而 `workshops` 表 T-BASE-001 才建。结果：**任何**涉及 `users` 的 ORM 查询都抛 `NoReferencedTableError`，登录功能被打死 | 模型去掉 FK，与迁移一致；T-BASE-001 补 `ALTER TABLE` |
| 4 | **`data_scope` 是 String 列** | 从库里读回来是 `str`，`user.data_scope.value` 抛 `AttributeError`。模型注解写着 `Mapped[DataScope]`，但没有 `TypeDecorator` 时运行期并不保证 | 统一 `DataScope(user.data_scope)` 包一层 |
| 5 | **Redis 状态跨用例存活** | 登录失败计数不回滚 → 某个用例锁了 A001，后面所有用 A001 的用例都拿到 10004，报错点离原因极远（"账号已停用"的用例报 429） | `db_session` 夹具里清 `auth:login_fail:*` / `idem:*` |
| 6 | **Redis 单例跨 event loop** | `get_redis()` 是模块级单例，pytest-asyncio 每用例一个新 loop → 下一个用例抛 "Future attached to a different loop" | 夹具 teardown 调 `close_redis()` |
| 7 | **`_MISSING` 哨兵被当参数绑定** | `column.in_(哨兵)` 把 Python `object()` 传给 asyncpg → `expected str, got object` | 改用空元组语义：`None → () → where(false())` |
| 8 | **缺 3 个 `deleted_at` 索引** | 模型 `index=True` 声明过，迁移漏建。列表查询恒带 `WHERE deleted_at IS NULL`，没索引就是全表扫 | 迁移 0003 补 |

另有两处**我自己发明的规则**被规范否掉，已改：
- 前缀通配 `base:*` → docs/07 **完全没有定义通配**，改为按最严处理（只有全局 `*` 才放行），登记 **L-027**
- `extra_claims` 参数 → 将来有人传 `permissions=["*"]` 就会静默绕开"权限查库"这条硬规则，**签名断言焊死**

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| **L-026** | auth 模块 5 个端点无权限点，与 docs/05 §6「每个接口必填权限声明」不自洽 | docs/12 §5 L-026 |
| **L-027** | 权限点通配语义完全未定义 | docs/12 §5 L-027 |
| — | `users.workshop_id` / `role_workshops.workshop_id` 的 FK 仍需补 | T-BASE-001（已在其验收项） |
| — | `must_change_password` 的**写操作强制拦截**未做 | T-BASE-001（第一个有写接口的模块，届时加依赖） |
| — | 短信通道是占位实现，`send_code` 无真实服务商 | P2（ADR-0004） |
| — | `auth_headers` 工厂支持 `role="custom"` + 自定义 `permissions`，但尚无业务接口消费 | T-BASE-001 起逐步使用 |
| W1 | `auth_login_logs` / `auth_refresh_tokens` 的 DDL 需回写 docs/04 §7 + docs/07 §2.1 | 归档阶段 |
| W2 | append-only 表豁免 04 §2 公共字段需回写 04 §2 | 归档阶段 |
| W3 | 迁移 0003 补的 58 处列注释建议回写 docs/04 §7（列注释是规范的一部分） | 归档阶段 |

**提交记录**：见 `git log`（本卡分 3 次提交，均 < 800 行业务代码）
