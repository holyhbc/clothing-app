# T-SYS-001b：拆分 system router.py → 4 子 router + deps + 单个 `router`

| 项 | 内容 |
| --- | --- |
| 模块 | auth（`system` 归 auth 提交域） |
| 负责人 | backend-dev |
| 状态 | todo |
| 优先级 | P0 |
| 依赖 | T-SYS-001a |
| 被依赖 | T-REFACTOR-001 |
| 关联设计 | docs/modules/cutting-system-auth-cli-拆分.设计.md §2.5, §2.8, §8 |
| 关联 ADR | docs/adr/0031-模块文件结构按职责拆分.md、docs/adr/0030-提交体量上限放宽到1200行.md |
| 估算 | 0.5d |

## 目标

将 `backend/app/modules/system/router.py`（**真实 511 行，18 个端点**）拆分为 4 个子 router +
`deps.py` + `__init__.py`，单文件 ≤400 行。`__init__.py` 必须导出**单个** `router`（`app/main.py:68` 依赖）。

## 范围

**要做**（6 个文件，见设计稿 §2.5）：
- [ ] `router/deps.py`：`SessionDep`/`ContextDep`/`SYSTEM_TAGS`/`_require`/`_users`/`_roles`(53-78)
- [ ] `router/user_router.py`：用户 9 端点(84-280)
- [ ] `router/role_router.py`：角色 6 端点(286-417)
- [ ] `router/restore_router.py`：内置库 2 端点(423-486)
- [ ] `router/permission_router.py`：权限点 1 端点(492-508)
- [ ] `router/__init__.py`：组装**单个** `router = APIRouter(prefix="/system", tags=["系统管理"])`，按 `user → role → restore → permission` 顺序 include；重导出 `router`
- [ ] 删除原 `router.py`

**不做**：
- 不改路径、方法、权限点、响应模型、数据范围、函数名（OpenAPI `operationId` 依赖函数名）
- 不新增/删减端点；本稿核实为 **18** 个（卡面背景写的「20」与实际不符）

## 将要改动的文件

| 文件 | 类型 | 说明 | 预估行数 |
| --- | --- | --- | --- |
| `backend/app/modules/system/router/__init__.py` | 新增 | 组装单个 `router` + 重导出 | ~45 |
| `backend/app/modules/system/router/deps.py` | 新增 | 共享依赖/权限助手/Service 构造 | ~55 |
| `backend/app/modules/system/router/user_router.py` | 新增 | 用户 9 端点 | ~215 |
| `backend/app/modules/system/router/role_router.py` | 新增 | 角色 6 端点 | ~150 |
| `backend/app/modules/system/router/restore_router.py` | 新增 | 内置库 2 端点 | ~85 |
| `backend/app/modules/system/router/permission_router.py` | 新增 | 权限点 1 端点 | ~40 |
| `backend/app/modules/system/router.py` | 删除 | 原 511 行文件 | - |

## 实现要点（必读规范）

- [ ] 遵守 docs/05-接口设计规范.md §6：每个端点 `openapi_extra={"x-permission": ...}` 且函数体显式 `_require`
- [ ] 遵守 docs/03-代码规范.md：Router 不写业务、不查库
- [ ] **`__init__.py` 必须导出名为 `router` 的单个 `APIRouter`**（`main.py:68` 是 `from app.modules.system.router import router as system_router`）
- [ ] `include_router` 顺序 = 源码顺序；子 router 内保持源码顺序（`/users/options` 先于 `/users/{user_id}`）
- [ ] 逐行对齐 `x-permission`，运行时权限码不得改变

## 验收标准

- [ ] `uv run pytest tests/modules/test_system_router.py -q` 全部通过
- [ ] 权限测试：无权限 → `12001`、越权数据范围 → `12002`
- [ ] **OpenAPI 零 diff**：`openapi.json` / `schema.d.ts` / `permissions.ts` 生成后 `git diff` 无输出
- [ ] 18 个端点全部可达、`/dicts/builtin-missing` 与 `/permissions` 不被遮蔽
- [ ] 本次新增 6 个文件单文件 ≤400 行
- [ ] 闸门 1-4 本地预跑通过

## 测试清单

| # | 用例 | 期望 | 结果 |
|---|------|------|------|
| TC-01 | 用户/角色全端点 | 状态码、响应结构、权限校验与拆分前一致 | |
| TC-02 | 内置库恢复端点 | `system:config:manage` 校验、返回结构不变 | |
| TC-03 | 权限点端点 | `permission_groups()` 输出顺序不变 | |
| TC-04 | 路由顺序 | `/users/options`、`/roles/options` 不被 `/{id}` 遮蔽 | |
| TC-05 | 导入面 | `from app.modules.system.router import router` 单个对象 | |

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
| 2026-10-06 | 初版：system/router.py(511) → 4 子 router + deps + 包，导出单个 `router`，照抄 T-BASE-010d 配方 | AI |
