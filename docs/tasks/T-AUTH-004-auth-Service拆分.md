# T-AUTH-004：拆分 auth service.py → 查询/登录/令牌 Mixin（6 文件）

| 项 | 内容 |
| --- | --- |
| 模块 | auth |
| 负责人 | backend-dev |
| 状态 | done |
| 优先级 | P0 |
| 依赖 | 无 |
| 被依赖 | T-REFACTOR-001 |
| 关联设计 | docs/modules/cutting-system-auth-cli-拆分.设计.md §2.6, §2.8, §8 |
| 关联 ADR | docs/adr/0031-模块文件结构按职责拆分.md、docs/adr/0030-提交体量上限放宽到1200行.md |
| 估算 | 0.5d |

## 目标

将 `backend/app/modules/auth/service.py`（**真实 424 行**）按方法边界最小拆分为 6 个文件，
单文件 ≤400 行。唯一**无法靠重导出保住**的调用方（`test_auth_service.py` 的 monkeypatch 目标）
在本卡内同步修正。

## 范围

**要做**（6 个文件，见设计稿 §2.6）：
- [ ] `service/common.py`：模块 docstring、`logger`、`MAX_LOGIN_FAILURES`/`LOCK_MINUTES`/`_LOCK_KEY_PREFIX`(58-66)、`IssuedTokens`(69-77)、`LoginOutcome`(79-85)、`build_lock_key`(422-424)
- [ ] `service/read_mixin.py`：查询 + 锁定助手 `get_active_user`/`get_role_ids`/`get_roles`/`get_allowed_workshop_ids`(96-139)、`is_login_locked`/`_count_recent_failures`/`_register_failure`/`_clear_failures`(143-177)
- [ ] `service/login_mixin.py`：`authenticate`(181-258)/`_find_user_by_employee_no`(260-263)/`_record_login`(265-288)/`change_password`(382-419)
- [ ] `service/token_mixin.py`：`_issue_tokens`(292-321)/`refresh`(323-354)/`logout`(356-374)/`revoke_all_tokens`(376-378)
- [ ] `service/auth_service.py`：`class AuthService(ReadMixin, LoginMixin, TokenMixin)` + `__init__`
- [ ] `service/__init__.py`：重导出 `AuthService`/`IssuedTokens`/`LoginOutcome`/`MAX_LOGIN_FAILURES`/`LOCK_MINUTES`/`build_lock_key`
- [ ] **同步修正** `tests/modules/test_auth_service.py:76,77,109` 的 monkeypatch 目标（见「实现要点」）
- [ ] 删除原 `service.py`

**不做**：
- 不改 6 条安全约定（不区分工号/口令错误、成功失败都写日志、5 次锁 15 分钟、refresh 轮换、
  只存 sha256、改密吊销全部 refresh）
- 不改 `router.py`(265)、`models.py`、`schemas.py`、`providers/`

## 将要改动的文件

| 文件 | 类型 | 说明 | 预估行数 |
| --- | --- | --- | --- |
| `backend/app/modules/auth/service/__init__.py` | 新增 | 聚合重导出 | ~45 |
| `backend/app/modules/auth/service/common.py` | 新增 | 常量 + 两个 dataclass + `build_lock_key` | ~75 |
| `backend/app/modules/auth/service/read_mixin.py` | 新增 | 查询 + 锁定助手（含 Redis 降级） | ~150 |
| `backend/app/modules/auth/service/login_mixin.py` | 新增 | 登录 + 改口令 | ~230 |
| `backend/app/modules/auth/service/token_mixin.py` | 新增 | 签发/刷新/登出 | ~140 |
| `backend/app/modules/auth/service/auth_service.py` | 新增 | 组合类 + `__init__` | ~35 |
| `backend/app/modules/auth/service.py` | 删除 | 原 424 行文件 | - |
| `backend/tests/modules/test_auth_service.py` | 修改 | monkeypatch 目标改指向 `read_mixin`（仅 3 行） | ~0 |

## 实现要点（必读规范）

- [ ] 遵守 docs/07-认证与权限规范.md §1：6 条安全约定逐字保留；锁定时长/次数走常量
- [ ] 遵守 docs/03-代码规范.md：显式 `unit_of_work`；`AuthService(session)` 构造签名不变
- [ ] **唯一例外（设计稿 §2.8）**：改包后 `redis_get_int`/`redis_incr_with_ttl` 绑定在锁定助手子模块。
      必须把 `monkeypatch.setattr("app.modules.auth.service.redis_get_int", ...)` 与
      `"...redis_incr_with_ttl"` 改为
      `app.modules.auth.service.read_mixin.redis_get_int` / `..._incr_with_ttl`。
      包 `__init__` 即使重导出同名对象也不能让 patch 作用到子模块运行时名字。
- [ ] `app/core/permissions.py` / `auth/router.py` / `tests/conftest.py` 的 `AuthService` 导入零改动

## 验收标准

- [ ] `uv run pytest tests/modules/test_auth_service.py -q` 全部通过（含 Redis 不可用降级路径）
- [ ] `uv run pytest tests/modules/test_auth_router.py -q` 全部通过
- [ ] `uv run pytest tests/modules/test_permissions.py -q` 全部通过（`core/permissions.py` 经 `AuthService`）
- [ ] `uv run pytest tests/ --co -q` 无 `ImportError`
- [ ] 本次新增 6 个文件单文件 ≤400 行
- [ ] 闸门 1-4 本地预跑通过

## 测试清单

| # | 用例 | 期望 | 结果 |
|---|------|------|------|
| TC-01 | 锁定阈值 | `MAX_LOGIN_FAILURES==5` / `LOCK_MINUTES==15` | |
| TC-02 | Redis 降级 | patch 目标改 `read_mixin` 后，降级查 `auth_login_logs` 窗口生效 | |
| TC-03 | 登录成功/失败 | 工号不存在与口令错误同码 `11003`；停用 `11004` | |
| TC-04 | 刷新轮换 | 旧 refresh 立即吊销、重放 `11002` | |
| TC-05 | 改口令 | 原口令错 `11003`；改后吊销全部 refresh | |
| TC-06 | 导入面 | `from app.modules.auth.service import AuthService, build_lock_key, MAX_LOGIN_FAILURES, LOCK_MINUTES` 无报错 | |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `backend/app/modules/auth/service/__init__.py` | 34 | 聚合重导出 6 个公开名 |
| `backend/app/modules/auth/service/common.py` | 62 | 原模块 docstring + `logger` + 锁常量 + 两 dataclass + `build_lock_key` |
| `backend/app/modules/auth/service/read_mixin.py` | 113 | 查询 + 登录锁定助手（含 Redis 降级） |
| `backend/app/modules/auth/service/login_mixin.py` | 200 | `authenticate`/`_find_user_by_employee_no`/`_record_login`/`change_password` |
| `backend/app/modules/auth/service/token_mixin.py` | 128 | `_issue_tokens`/`refresh`/`logout`/`revoke_all_tokens` |
| `backend/app/modules/auth/service/auth_service.py` | 20 | `AuthService(ReadMixin, LoginMixin, TokenMixin)` + `__init__` |
| `backend/app/modules/auth/service.py` | -424 | 删除原文件 |
| `backend/tests/modules/test_auth_service.py` | 3 处 | monkeypatch 目标改指 `read_mixin`（不改断言/逻辑） |

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| | | docs/12 §遗留问题清单 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。

## 变更记录

| 日期 | 变更内容 | 操作人 |
|------|---------|--------|
| 2026-10-06 | 初版：auth/service.py(424) → 查询/登录/令牌三 Mixin + common + 组合；登记并修正 monkeypatch 目标例外 | AI |
| 2026-10-06 | 完成：6 文件 557 行（净 +133），单文件最大 200 行；定向 64 passed、闸门 1-4 全绿；提交 `d67d88c` | AI |
