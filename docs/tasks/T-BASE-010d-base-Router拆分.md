# T-BASE-010d：拆分 base router.py → 7 内容文件 + 单个 `router`

| 项 | 内容 |
| --- | --- |
| 模块 | base |
| 负责人 | backend-dev |
| 状态 | done |
| 优先级 | P0 |
| 依赖 | T-BASE-010c |
| 被依赖 | T-BASE-010f |
| 关联设计 | docs/modules/base-重构拆分.设计.md §2.2, §2.6, §8 |
| 关联 ADR | docs/adr/0030-提交体量上限放宽到1200行.md（第 3 条） |
| 估算 | 0.5d |

## 目标

将 `backend/app/modules/base/router.py`（**真实 1362 行**；⚠️ 初版误把字节数 52274 当行数，并错误声称"3 个 Router"）拆分为 7 个内容文件 + `__init__.py`，单文件 ≤400 行。

## 范围

**要做**（8 个文件，见设计稿 §2.2）：
- [ ] `router/deps.py`：共享依赖、权限助手与导出常量（`SessionDep`/`ContextDep`/`EXPORT_GLOBAL_PERMISSION`/`_permission`/`_require`/`_require_base`/`_service`/`_style_service`/`_rate_service`/`_material_options_service`/`_list_query`/`_export_filename`/`STYLE_TAGS`/`STYLE_EXPORT_COLUMNS`/`RATE_EXPORT_COLUMNS`）
- [ ] `router/dict_handlers.py`：9 字典资源 7 个 handler + `_validate`/`_short`/`_EXPORT_COLUMN_MAP`/`_export_columns`（162-475）
- [ ] `router/dict_factory.py`：路径注册机制 `register_resource_routes`/`_register_one`/`_bind`/`_signature_without_resource`（476-620）
- [ ] `router/style_router.py`：`/styles` 列表/导出/候选/创建/建议号/详情/停用/改（763-999）
- [ ] `router/style_child_router.py`：款号色码/尺码/比例/款号工序/模板复制（1000-1170）
- [ ] `router/material_router.py`：`/materials`、`/suppliers`、`/material-stocks` 候选 + `/document-logs`（684-760、1172-1197）
- [ ] `router/rate_router.py`：`/operation-rates` 列表/取价/导出/设价（1200-1346）
- [ ] `router/__init__.py`：组装**单个** `router`（`APIRouter(tags=["基础资料"])`），按源码顺序 include 子 router，**末尾**调用 `register_resource_routes(router)`；重导出 `router`/`RESOURCES`/`DictResource`/`register_resource_routes`/`EXPORT_GLOBAL_PERMISSION`/`_EXPORT_COLUMN_MAP`
- [ ] 删除原 `router.py`

**不做**：
- 不改变 API 路径、权限点、数据范围、响应结构，不新增/删减端点

## 将要改动的文件

| 文件 | 类型 | 说明 | 预估行数 |
| --- | --- | --- | --- |
| `backend/app/modules/base/router/__init__.py` | 新增 | 组装单个 `router` + 重导出 | ~60 |
| `backend/app/modules/base/router/deps.py` | 新增 | 共享依赖/权限助手/导出常量 | ~160 |
| `backend/app/modules/base/router/dict_handlers.py` | 新增 | 字典 handler + 导出列 | ~345 |
| `backend/app/modules/base/router/dict_factory.py` | 新增 | 路径注册机制 | ~150 |
| `backend/app/modules/base/router/style_router.py` | 新增 | 款号主表路由 | ~240 |
| `backend/app/modules/base/router/style_child_router.py` | 新增 | 款号子表路由 | ~175 |
| `backend/app/modules/base/router/material_router.py` | 新增 | 物料/供应商/布批/日志 | ~135 |
| `backend/app/modules/base/router/rate_router.py` | 新增 | 工序单价路由 | ~175 |
| `backend/app/modules/base/router.py` | 删除 | 原 1362 行文件 | - |

## 实现要点（必读规范）

- [ ] 遵守 docs/05-接口设计规范.md：统一响应包装、错误码、OpenAPI 标签、权限声明 `openapi_extra={"x-permission": ...}`
- [ ] 遵守 docs/03-代码规范.md：Router 不写业务、只做依赖注入→Pydantic 校验→调 service→包装响应
- [ ] **`__init__.py` 必须导出名为 `router` 的单个 `APIRouter`**：`app/main.py:66` 是 `from app.modules.base.router import router as base_router`，导出列表会直接报错
- [ ] **`register_resource_routes(router)` 必须在所有手写端点之后调用**（它注册 9 资源的 `/{key}/{code}` 形态，早于手写端点会遮蔽）；各子 router 内保持源码顺序（尤其 `/styles/suggested-no` 必须先于 `/styles/{style_no}`）
- [ ] **运行时 `_permission(resource, action)` 生成的权限码不得改变**，逐行对齐 `x-permission`
- [ ] 数据范围过滤在 service 层 `apply_data_scope`，Router 不处理
- [ ] 从新 `service` 包导入：`from app.modules.base.service import DictService, StyleService, RateService, MaterialOptionsService`

## 验收标准

- [x] `uv run pytest tests/modules/test_base_dict_router.py -q` 全部通过
- [x] `uv run pytest tests/modules/test_base_style_service.py -q` 全部通过（含 router 集成）
- [x] `uv run pytest tests/modules/test_operation_rates.py -q` 全部通过
- [x] `uv run pytest tests/modules/test_style_disable_export.py -q` 全部通过（`EXPORT_GLOBAL_PERMISSION` 导入）
- [x] `uv run pytest tests/modules/test_material_stock_options.py -q` 全部通过
- [x] 权限测试：无权限→12001、越权数据范围→12002
- [x] OpenAPI 生成无 diff
- [x] 本次新增 8 个文件单文件 ≤400 行（最大 347）
- [x] 闸门 1-4 本地预跑通过

## 测试清单

| # | 用例 | 期望 | 结果 |
|---|------|------|------|
| TC-01 | 字典列表/详情/创建/编辑/停用/删除 | 状态码 200/201、响应结构标准、权限校验 | ✅ |
| TC-02 | 款号 CRUD + 比例/工序全量替换 | 三层嵌套响应、建议号端点正常 | ✅ |
| TC-03 | 单价设价/调价/取价/历史 | 三档参数、区间校验、取价 SQL 正确 | ✅ |
| TC-04 | 候选端点 | 物料/供应商/布批候选返回结构不变 | ✅ |
| TC-05 | 路由匹配顺序 | `/colors/exports`、`/styles/suggested-no` 不被 `/{code}`、`/{style_no}` 遮蔽 | ✅ |
| TC-06 | 导入面 | `from app.modules.base.router import router, EXPORT_GLOBAL_PERMISSION, _EXPORT_COLUMN_MAP` 无报错 | ✅ |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `backend/app/modules/base/router/__init__.py` | 56 | 组装**单个** `router = APIRouter(tags=["基础资料"])`；按源码顺序 include 4 个子 router；末尾调用 `register_resource_routes(router)`；重导出 `router`/`RESOURCES`/`DictResource`/`register_resource_routes`/`EXPORT_GLOBAL_PERMISSION`/`_EXPORT_COLUMN_MAP` |
| `backend/app/modules/base/router/deps.py` | 166 | `logger`/`EndpointResult`/`SessionDep`/`ContextDep`/`EXPORT_GLOBAL_PERMISSION`/`_export_filename`/`_permission`/`_require`/`_require_base`/`_service`/`_style_service`/`_rate_service`/`_material_options_service`/`_list_query`/`STYLE_TAGS`/`STOCK_TAGS`/`STYLE_EXPORT_COLUMNS`/`RATE_EXPORT_COLUMNS`（原 77-160、629-681、1349-1356） |
| `backend/app/modules/base/router/dict_handlers.py` | 347 | 9 字典资源 7 个 handler + `_validate`/`_short`/`_EXPORT_COLUMN_MAP`/`_export_columns`（原 162-475） |
| `backend/app/modules/base/router/dict_factory.py` | 179 | `register_resource_routes`/`_register_one`/`_bind`/`_signature_without_resource`（原 476-620） |
| `backend/app/modules/base/router/style_router.py` | 281 | `/styles` 列表/导出/候选/创建/建议号（先于 `/{style_no}`）/详情/停用/改（原 763-999） |
| `backend/app/modules/base/router/style_child_router.py` | 197 | 款号色码/尺码/比例/款号工序/模板复制（原 1000-1170） |
| `backend/app/modules/base/router/material_router.py` | 134 | `/materials`、`/suppliers`、`/material-stocks` 候选 + `/document-logs`（原 684-760、1172-1197） |
| `backend/app/modules/base/router/rate_router.py` | 187 | `/operation-rates` 列表/取价/导出/设价（原 1200-1346） |
| `backend/app/modules/base/router.py` | -1362 | **删除**（纯代码搬运，内容逐字迁入上述 8 个文件） |

新增/搬运合计 **+1547 / -1362**（净 +185）；单文件最大 347 行（≤400，ADR-0030）。逐字搬运原端点与辅助函数；新增各文件 import / 子 router 声明 / 包聚合约 185 行。OpenAPI（`backend/openapi.json` / `schema.d.ts` / `permissions.ts` / `baseDictFields.ts`）生成后 `git diff` 无输出。

**提交记录**：
- `见下` refactor(base): 拆分 router.py(1362 行) 为 7 模块 + 包 __init__（导出单个 router，≤400 行）

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| | | docs/12 §遗留问题清单 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。

## 变更记录

| 日期 | 变更内容 | 操作人 |
|------|---------|--------|
| 2026-10-06 | 修正行数口径（原误将字节 52274 当行数，真实 1362）；由"3 个 Router"更正为 7 内容文件 + `__init__.py`，明确 `__init__.py` 必须导出**单个** `router`（原稿"导出 router 列表"会使 `main.py` 报错）；补 `_EXPORT_COLUMN_MAP`/`EXPORT_GLOBAL_PERMISSION` 重导出 | AI |
| 2026-10-06 | 完成拆分：8 文件合计 1547 行（原 1362 行删除），单文件最大 347；定向测试 163 passed、闸门 1-4 全绿、OpenAPI 零 diff | AI |
