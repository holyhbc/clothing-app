# T-SYS-001a：拆分 system service.py → 用户 Mixin + 角色 + common（7 文件）

| 项 | 内容 |
| --- | --- |
| 模块 | auth（`system` 归 auth 提交域） |
| 负责人 | backend-dev |
| 状态 | todo |
| 优先级 | P0 |
| 依赖 | 无 |
| 被依赖 | T-SYS-001b, T-REFACTOR-001 |
| 关联设计 | docs/modules/cutting-system-auth-cli-拆分.设计.md §2.4, §2.8, §8 |
| 关联 ADR | docs/adr/0031-模块文件结构按职责拆分.md、docs/adr/0030-提交体量上限放宽到1200行.md |
| 估算 | 0.5d |

## 目标

将 `backend/app/modules/system/service.py`（**真实 869 行**）拆分为 7 个文件，单文件 ≤400 行：
`SystemUserService`（420 行，计 import 后超 400）按方法边界拆 3 个 Mixin；`SystemRoleService`（320 行）
保持单文件；模块常量与 `permission_groups` 归 `common.py`。

## 范围

**要做**（7 个文件，见设计稿 §2.4）：
- [ ] `service/common.py`：模块 docstring、`logger`、`DOC_TYPE_USER`/`DOC_TYPE_ROLE`/`ACTION_CREATE`/`ACTION_UPDATE`、`PERMISSION_NAMES`、`MODULE_NAMES`、`MAX_OFFSET`、`_permission_module`、`permission_groups`(12-115)
- [ ] `service/user_query_mixin.py`：`list_users`(129-157)/`options`(159-176)/`get_user`(178-181)/`_base_stmt`(125-127)/`_role_codes`(408-422)/`_out`(523-537)
- [ ] `service/user_write_mixin.py`：`create_user`(183-226)/`patch_user`(228-270)/`disable_user`(272-318)/`reset_password`(320-347)/`assign_roles`(349-378)/`_revoke_refresh_tokens`(382-394)/`_required`(396-406)/`_require_roles_exist`(424-438)/`_role_ids_by_code`(440-447)/`_replace_user_roles`(449-455)/`_duplicate_user`(517-521)
- [ ] `service/user_guard_mixin.py`：`_guard_has_operator`(457-515)
- [ ] `service/user_service.py`：`class SystemUserService(UserQueryMixin, UserWriteMixin, UserGuardMixin)` + `__init__`
- [ ] `service/role_service.py`：`SystemRoleService`(540-859)
- [ ] `service/__init__.py`：重导出 `SystemUserService`/`SystemRoleService`/`permission_groups`/`PERMISSION_NAMES`/`MODULE_NAMES`/`logger`
- [ ] 删除原 `service.py`

**不做**：
- 不改业务逻辑、事务边界、`document_logs` 留痕、`populate_existing` 读法、最后管理员守卫
- 不改 `schemas.py`(269)、`restore.py`(165)、`scopes.py`(18)、`router.py`（由 T-SYS-001b 处理）

## 将要改动的文件

| 文件 | 类型 | 说明 | 预估行数 |
| --- | --- | --- | --- |
| `backend/app/modules/system/service/__init__.py` | 新增 | 聚合重导出 | ~35 |
| `backend/app/modules/system/service/common.py` | 新增 | 常量 + `permission_groups` | ~135 |
| `backend/app/modules/system/service/user_query_mixin.py` | 新增 | 用户读 + 角色码/出参映射 | ~120 |
| `backend/app/modules/system/service/user_write_mixin.py` | 新增 | 用户建/改/停用/重置口令/分配角色 | ~240 |
| `backend/app/modules/system/service/user_guard_mixin.py` | 新增 | 最后管理员守卫 | ~80 |
| `backend/app/modules/system/service/user_service.py` | 新增 | 组合类 + `__init__` | ~35 |
| `backend/app/modules/system/service/role_service.py` | 新增 | `SystemRoleService` | ~355 |
| `backend/app/modules/system/service.py` | 删除 | 原 869 行文件 | - |

## 实现要点（必读规范）

- [ ] 遵守 docs/07-认证与权限规范.md §2.3/§5：内置角色、权限变更留痕、角色不进数据范围
- [ ] 遵守 docs/03-代码规范.md：`unit_of_work`、写后 `populate_existing` 读
- [ ] Mixin 交叉调用用 `TYPE_CHECKING` 前置声明；组合类只 `__init__`
- [ ] `SystemUserService(session, ctx)` / `SystemRoleService(session, ctx)` 构造签名与类型名不变
- [ ] `__all__` 逐字保持（`MODULE_NAMES`/`PERMISSION_NAMES`/`SystemRoleService`/`SystemUserService`/`logger`/`permission_groups`），由 `__init__.py` 全量重导出

## 验收标准

- [ ] `uv run pytest tests/modules/test_system_router.py -q` 全部通过
- [ ] `uv run pytest tests/modules/test_system_restore.py -q` 全部通过
- [ ] `uv run pytest tests/modules/test_permission_registry.py -q` 全部通过
- [ ] `uv run pytest tests/ --co -q` 无 `ImportError`
- [ ] 本次新增 7 个文件单文件 ≤400 行
- [ ] 闸门 1-4 本地预跑通过

## 测试清单

| # | 用例 | 期望 | 结果 |
|---|------|------|------|
| TC-01 | 用户列表/详情/新建/改/停用/启用/重置口令 | 状态码与响应结构不变；工号重复 `10001` | |
| TC-02 | 分配角色 | 整体替换、写 `document_logs` | |
| TC-03 | 停用最后管理员 | `10008`（`_guard_has_operator`） | |
| TC-04 | 角色 CRUD / 替换权限 / 停用 | 内置角色不可停用；软删真收权 | |
| TC-05 | `permission_groups()` | 按 registry 顺序、模块顺序不变 | |
| TC-06 | 导入面 | `from app.modules.system.service import SystemUserService, SystemRoleService, permission_groups` 无报错 | |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| | | |

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| | | docs/12 §遗留问题清单 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。

## 变更记录

| 日期 | 变更内容 | 操作人 |
|------|---------|--------|
| 2026-10-06 | 初版：system/service.py(869) → SystemUserService 3 Mixin + SystemRoleService + common，照抄 base 配方 | AI |
