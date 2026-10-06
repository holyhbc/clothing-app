# 设计：base 模块拆分重构（解决单文件超 400 行违规）

| 项 | 内容 |
| --- | --- |
| 任务卡编号 | T-BASE-010 |
| 模块 | base |
| 编写人 | AI |
| 日期 | 2026-10-06 |
| 状态 | 已确认（2026-10-06 修订：修正行数口径，按真实结构重排拆分边界） |
| 关联需求 | docs/requirements/REQ-000-工期优化与复用策略.md |
| 关联 ADR | docs/adr/0030-提交体量上限放宽到1200行.md（第 3 条：单文件 ≤400 行硬上限） |

---

## 1. 背景与目标

**为什么做**：

- 真实行数（2026-10-06 以 `wc -l` 逐文件核对）：`base/service.py` **2751**、`base/router.py` **1362**、`base/models.py` **1302**、`base/schemas.py` **913**、`app/common/permissions_registry.py` **850** —— 五处**违反 ADR-0030「单文件 ≤400 行」硬上限**。
- ⚠️ **本稿初版把文件字节数当成了行数**（原写"service.py 126,054 行"；126054 实际是**字节数**，真实行数 2751）。据此推导的拆分数多处不可行（例如"3 个 Service 文件装下 2751 行"）。本次修订以行数为唯一口径重排全部拆分边界。
- 同一轮核对中确认**已 ≤400 行、本次不动**的 base 文件：`repository.py` 283、`resources.py` 284、`document_logs.py` 123。
- 后果：Review 无法一次读完、改动极易引入回归、测试隔离困难、并行开发不可行。

**成功标准**（本次**收窄**为 base 模块 + 权限注册表范围）：

- [ ] `base/models/`、`base/schemas/`、`base/service/`、`base/router/`、`app/common/permissions/` 下本次新增/拆分出的**所有手写文件 ≤400 行**（含测试文件）
- [ ] 闸门 1-5 全绿
- [ ] base 现有功能零回归（全量测试通过）
- [ ] 对外导入面**保持不变**（各包 `__init__.py` 重导出，见 §2.6）
- [ ] 复用清单更新：后续模块按新结构导入

**非目标**：

- 不改变对外 API 契约（OpenAPI 保持兼容）
- 不改变数据库表结构
- 不新增业务功能
- **不处理本卡范围外的其它 >400 行文件**（留待后续卡，逐项登记 §10 Q-08）：`cutting/service.py` 1047、`system/service.py` 869、`cutting/models.py` 696、`system/router.py` 511、`cutting/schemas.py` 457、`cli/seed_dicts.py` 447、`auth/service.py` 424

---

## 2. 拆分策略

> 行数 = `wc -l` 行数；"预估行数" = 迁入代码 + 该文件自身 import/常量开销，全部 ≤400。

### 2.1 service.py（2751 行）→ 4 个 Service 类 + StyleService 内部 Mixin（15 个文件）

**真实结构**：`DictService` 132-430、`StyleService` 758-2172（**1415 行，单类必须再拆**）、`RateService` 2173-2556、`MaterialOptionsService` 2557-2751（初版**完全遗漏**）。

| 新文件 | 职责 | 来源行区间 | 预估行数 |
|--------|------|-----------|---------|
| `service/__init__.py` | 统一导出（见 §2.6） | — | ~35 |
| `service/common.py` | 常量（`ACTION_*`/`DOC_*`/`SKIP_*`/`SCALE_*`/`MAX_*` 等）、分页排序校验 `_validate_*`、`write_document_log`、`numeric_str`、`_duplicated`、`StyleQuery`/`RateQuery` + 排序白名单 | 125-131、461-514、517-571、573-593、596-627、682-694、744-752 | ~215 |
| `service/dict_service.py` | `DictService`：9 字典资源统一 CRUD | 132-430 | ~325 |
| `service/dict_helpers.py` | `payload_dict_changes`、`_unique_violation` | 431-441、444-454 | ~45 |
| `service/style_service.py` | `class StyleService(...)`：仅组合 6 个 Mixin + `__init__` | 758-763 | ~45 |
| `service/style_query_mixin.py` | 款号读：list/options/suggest/get_required/export/detail/色码尺码读 + `_style_list_stmt` | 726-741、767-1025 | ~295 |
| `service/style_crud_mixin.py` | 款号主表 CRUD + `_suggest`/`_find_by_no`/`_assert_category_usable`/`_duplicate_style_error` | 1026-1198、1363-1417 | ~255 |
| `service/style_child_mixin.py` | 色码/尺码追加：`add_colors`/`add_sizes`/`_resolve_size_items` | 1199-1362 | ~185 |
| `service/style_ratio_mixin.py` | 比例全量替换 + 聚合版本 `_assert_aggregate_version`/`_bump_aggregate` | 1418-1642 | ~245 |
| `service/style_operation_mixin.py` | 款号工序全量替换 + `_current_rates` | 1643-1877 | ~255 |
| `service/style_template_mixin.py` | 模板复制：`copy_template`/`_copy_structure`/`_copy_prices`（档位 2 不复制） | 1878-2172 | ~320 |
| `service/rate_service.py` | `class RateService(RateQueryMixin)`：取价/设价/调价/区间收敛 | 2173-2556（扣除 query mixin 段） | ~320 |
| `service/rate_query_mixin.py` | 单价列表/导出/可见款号：`list_rates`/`_filtered`/`export_rates`/`_visible_style_nos` | 2182-2260 | ~100 |
| `service/rate_helpers.py` | `build_rate_resolve_stmt`（ADR-0026 §2 唯一取价 SQL）、`rate_source_of`、`rate_out` | 630-679、697-723 | ~95 |
| `service/material_options_service.py` | `MaterialOptionsService`：物料/供应商/布批候选 | 2557-2751 | ~215 |

**StyleService 的 6 个 Mixin 是"把一个 1415 行类拆到多文件"的手段，不是通用业务 Mixin**。初版设计的 `recalc_mixin`/`state_mixin`/`export_mixin` 缺少证据（见 §10 Q-04），本轮**不抽**。

### 2.2 router.py（1362 行）→ 7 个内容文件 + `__init__.py`（单个 `router`）

**硬约束**：`app/main.py:66` 为 `from app.modules.base.router import router as base_router`，因此包内 `__init__.py` **必须导出名为 `router` 的单个 `APIRouter`**（不能导出列表）。

| 新文件 | 职责 | 来源行区间 | 预估行数 |
|--------|------|-----------|---------|
| `router/__init__.py` | 组装**单个** `router`、按源码顺序 include 子 router、末尾 `register_resource_routes(router)`；重导出 `router`/`RESOURCES`/`DictResource`/`register_resource_routes`/`EXPORT_GLOBAL_PERMISSION`/`_EXPORT_COLUMN_MAP` | 85、660-681、1360-1362 | ~60 |
| `router/deps.py` | 共享依赖、权限助手与导出常量：`SessionDep`/`ContextDep`/`EXPORT_GLOBAL_PERMISSION`/`_permission`/`_require`/`_require_base`/`_service`/`_style_service`/`_rate_service`/`_material_options_service`/`_list_query`/`_export_filename`/`STYLE_TAGS`/`STYLE_EXPORT_COLUMNS`/`RATE_EXPORT_COLUMNS` | 87-160、629-669、1349-1356 | ~160 |
| `router/dict_handlers.py` | 9 字典资源的 7 个 handler + `_validate`/`_short`/`_EXPORT_COLUMN_MAP`/`_export_columns` | 162-475 | ~345 |
| `router/dict_factory.py` | 路径注册机制：`register_resource_routes`/`_register_one`/`_bind`/`_signature_without_resource` | 476-620 | ~150 |
| `router/style_router.py` | `/styles` 列表/导出/候选/创建/**建议号（必须先于 `/{style_no}`）**/详情/停用/改 | 763-999 | ~240 |
| `router/style_child_router.py` | 款号色码/尺码/比例/款号工序/模板复制 | 1000-1170 | ~175 |
| `router/material_router.py` | `/materials`、`/suppliers`、`/material-stocks` 候选 + `/document-logs` | 684-760、1172-1197 | ~135 |
| `router/rate_router.py` | `/operation-rates` 列表/取价/导出/设价 | 1200-1346 | ~175 |

**路由顺序**：`register_resource_routes(router)` 必须**最后**调用（它注册 9 资源的 `/{key}/{code}` 形态，早于手写端点会遮蔽）；手写端点之间的相对顺序对匹配无影响（无前缀冲突），各子 router 内部保持源码顺序。**运行时 `_permission(resource, action)` 生成的权限码不得改变**（逐行对齐 `x-permission`）。

### 2.3 schemas.py（913 行）→ 5 个内容文件 + `__init__.py`

**真实结构**：本文件**没有**独立的 import/export 模板类（初版 `export_schemas.py`/`import_schemas.py` 属虚构），导入导出沿用通用 schema。

| 新文件 | 包含 | 来源行区间 | 预估行数 |
|--------|------|-----------|---------|
| `schemas/__init__.py` | 聚合重导出 + `WRITE_MODELS` + `ACTION_BY_OPERATION` | 885-906 | ~90 |
| `schemas/common_schemas.py` | `Versioned`/`DictOut`/`DictRow`/`OptionOut`/`StockBatchOptionOut`/`DisableIn`/`PatchIn`/`DeleteOut`/`DocumentLogOut`/`SuggestedStyleNoOut`/`DisableOut`/`payload_dict` | 29-148、336-385、875-884、907-913 | ~225 |
| `schemas/dict_schemas.py` | 9 字典的 `*Create`/`*Patch` + `SizeGroupItemIn` + `CustomerCreate`/`CustomerPatch` | 149-335、386-417 | ~240 |
| `schemas/style_schemas.py` | `StyleCreate`/`StylePatch`/`StyleDisableIn`/`StyleOut`/`StyleListOut`/`StyleColor*`/`StyleSize*` | 418-583 | ~185 |
| `schemas/style_child_schemas.py` | `Ratio*`/`StyleOperation*`/`StyleDetailOut`/`TemplateCopyIn`/`CopiedPriceOut`/`TemplateCopyOut` | 584-766 | ~205 |
| `schemas/rate_schemas.py` | `OperationRateCreate`/`OperationRateOut`/`OperationRateSetOut`/`OperationRateListOut`/`RateResolveOut` | 767-874 | ~125 |

`WRITE_MODELS` 放在 `schemas/__init__.py`（它同时引用 `dict_schemas` 的写入模型，放在子模块会与 `common_schemas` 形成循环导入）。

### 2.4 models.py（1302 行，24 个类）→ 6 个领域文件 + `__init__.py`

| 新文件 | 包含的模型（行号） | 来源行区间 | 预估行数 |
|--------|------------------|-----------|---------|
| `models/__init__.py` | 统一导出全部 24 个模型 | — | ~60 |
| `models/org.py` | `Workshop` 89 / `WorkshopGroup` 116 / `Warehouse` 151 / `UomUnit` 179 | 89-208 | ~150 |
| `models/dict.py` | `Color` 209 / `Size` 252 / `SizeGroup` 303 / `SizeGroupItem` 343 / `Operation` 374 / `ProductCategory` 429 | 209-468 | ~285 |
| `models/style.py` | `Customer` 469 / `Style` 503 / `StyleColor` 569 / `StyleSize` 599 / `StyleColorSizeRatio` 629 / `StyleOperation` 665 / `StyleNoSequence` 824 | 469-721、824-875 | ~335 |
| `models/rate.py` | **`OperationRate` 722（三档取价核心复用资产，必须单独归属）** | 722-823 | ~130 |
| `models/material.py` | `MaterialCategory` 876 / `Material` 914 / `Supplier` 976 | 876-1054 | ~200 |
| `models/stock.py` | `MaterialStock` 1055 / `WipStock` 1192 / `WipLedgerLine` 1263 | 1055-1302 | ~270 |

**更正两处初版错误**：

1. **删除 `StockReservation`**：它不在 base，真实位置 `app/modules/stock/models.py:296`。
2. **`document_logs.py` 不是模型文件**：`app/modules/base/document_logs.py` 是查询工具 + 常量（`MAX_PAGE_SIZE=100` 等，123 行），`DocumentLog` 模型定义在 `app/common/models.py`；该文件本次保持不动，**不要**把它列进 models 包。

**`register_all_models()` 无需改动**：`app/common/models.py` 用 `pkgutil.iter_modules` import `app.modules.<mod>.models`；`base/models.py` 改为 `base/models/` 包后，import `app.modules.base.models` 触发 `__init__.py` 导入全部子模块，模型照常注册。

### 2.5 permissions_registry.py（850 行）→ 11 个 `perm_*.py` + 数据类 + 聚合入口

**真实分布（122 个 `PermissionSeed`，按 `module` 前缀）**：base 11、bundling 12、cutting 12、finance 17、payroll 13、piecework 6、purchase 13、sales 12、self 4、stock 15、system 7。初版括号里的 `auth:*`、`arap:*` **不存在**，且**漏了 `purchase`、`self`**。

| 新文件 | 权限点前缀 | 数量 | 预估行数 |
|--------|-----------|------|---------|
| `permissions/__init__.py` | `PermissionSeed`/`RoleSeed` 数据类定义 | — | ~35 |
| `permissions/perm_base.py` | `base:*` | 11 | ~65 |
| `permissions/perm_bundling.py` | `bundling:*` | 12 | ~70 |
| `permissions/perm_cutting.py` | `cutting:*` | 12 | ~70 |
| `permissions/perm_finance.py` | `finance:*` | 17 | ~95 |
| `permissions/perm_payroll.py` | `payroll:*` | 13 | ~75 |
| `permissions/perm_piecework.py` | `piecework:*` | 6 | ~40 |
| `permissions/perm_purchase.py` | `purchase:*` | 13 | ~75 |
| `permissions/perm_sales.py` | `sales:*` | 12 | ~70 |
| `permissions/perm_self.py` | `self:*` | 4 | ~30 |
| `permissions/perm_stock.py` | `stock:*` | 15 | ~85 |
| `permissions/perm_system.py` | `system:*` | 7 | ~50 |
| `permissions_registry.py` | **聚合入口**：按原顺序拼接 `PERMISSIONS` + `ROLES` + `permission_codes`/`permissions_by_module`/`SELF_PERMISSION_CODES`/`role_codes`/`resolve_role_permissions` | 649-850 | ~330 |

**顺序必须逐字保持**：`PERMISSIONS` 原顺序 = base、bundling、cutting、finance、payroll、piecework、purchase、sales、self、stock、system；聚合入口按该顺序相加，`ROLES` 原样保留。`PermissionSeed`/`RoleSeed` 移到 `permissions/__init__.py` 以切断"聚合入口 ↔ perm_*"的循环导入，并从聚合入口重导出。

### 2.6 对外导入面保持不变（兼容策略）

所有被拆分模块改为**包**后，各 `__init__.py` **重导出原有公开名**，使现有导入零改动。经代码核对，本卡必须保住的外部导入：

| 导入方 | 从何处导入 | 必须保住的名字 |
|--------|-----------|---------------|
| `app/main.py` | `base.router` | `router`（单个对象） |
| `app/modules/system/service.py` | `base.service` | `write_document_log` |
| `app/modules/base/document_logs.py` | `base.service` | `StyleService` |
| `app/modules/base/resources.py`、`cutting/service.py`、`cli/seed_dicts.py`、`core/numbering.py` | `base.models` | 全部被引用的模型 |
| `base/router.py`、`base/repository.py`、`base/document_logs.py`、多份测试 | `base.schemas` | 全部被引用的 schema + `WRITE_MODELS` |
| `base/router.py`、多份测试 | `base.service` | `DictService`/`StyleService`/`RateService`/`MaterialOptionsService`/`StyleQuery`/`RateQuery`/`build_rate_resolve_stmt` |
| `tests/modules/test_style_disable_export.py` | `base.router` | `EXPORT_GLOBAL_PERMISSION` |
| `tests/modules/test_base_dict_router.py` | `base.router` | `_EXPORT_COLUMN_MAP` |
| `app/cli/seed_baseline.py`、`tests/conftest.py` 等 | `app.common.permissions_registry` | `PERMISSIONS`/`ROLES`/`SELF_PERMISSION_CODES`/`permission_codes`/`role_codes`/`resolve_role_permissions` |

---

## 3. 领域模型

**不适用（理由：数据库表结构不变，本次仅代码组织调整）。**

---

## 4. 接口清单

**不适用（理由：OpenAPI 契约不变，仅内部导入路径变更；由 T-BASE-010g 校验 `schema.d.ts` 无 diff）。**

---

## 5. 并发与一致性

**不适用（理由：纯代码重组，无新增并发风险；现有乐观锁/唯一索引/区间约束逻辑原样迁移）。**

---

## 6. 前端设计

**不涉及前端变更**，仅 `shared/api` 重新生成（`pnpm generate:api`）以确认契约无 diff。

---

## 7. 风险与回滚

| 风险 | 影响 | 缓解 | 回滚方式 |
|------|------|------|---------|
| 导入路径破坏 | 引用 base 的代码报错 | 各 `__init__.py` 统一重导出（§2.6）；分批提交验证 | `git revert` 单次提交 |
| 循环依赖 | 启动失败 | `PermissionSeed`/`RoleSeed` 下沉到 `permissions/__init__.py`；分层 models → schemas → repository → service → router，禁止反向 import | 拆分顺序：models → schemas → service → router |
| 路由匹配顺序被破坏 | 端点 404 或被字典 `/{code}` 遮蔽 | `register_resource_routes(router)` 必须最后调用；子 router 内保持源码顺序 | `git revert` |
| 测试失效 | 夹具/工厂导入路径变 | 兼容重导出 + T-BASE-010f 全量核对 | 同一提交内修完测试 |

**迁移回滚**：无数据库迁移，纯代码重构。

**发布回滚**：镜像回退上一 tag 即可。

---

## 8. 任务拆分（按依赖顺序）

| 任务卡 | 描述 | 依赖 | 改动文件 | 验收标准 |
|--------|------|------|----------|----------|
| T-BASE-010a | 拆分 `models.py` 1302 行 → 6 领域文件 + `__init__.py` | 无 | `models/{org,dict,style,rate,material,stock}.py`、`models/__init__.py` | 24 模型可导入、`alembic check` 无漂移、测试全过 |
| T-BASE-010b | 拆分 `schemas.py` 913 行 → 5 内容文件 + `__init__.py` | 010a | `schemas/{common,dict,style,style_child,rate}_schemas.py`、`schemas/__init__.py` | schema 可导入、`WRITE_MODELS` 不变、OpenAPI 无 diff |
| T-BASE-010c | 拆分 `service.py` 2751 行 → 4 Service + StyleService 内部 6 Mixin（15 文件） | 010a,010b | `service/` 下 15 个文件（见 §2.1） | 4 Service 单测全过、功能零回归、每文件 ≤400 |
| T-BASE-010d | 拆分 `router.py` 1362 行 → 7 内容文件 + `__init__.py`（单个 `router`） | 010c | `router/` 下 8 个文件（见 §2.2） | 端点可达、权限码不变、OpenAPI 无 diff |
| T-BASE-010e | 拆分 `permissions_registry.py` 850 行 → 11 perm + 数据类 + 聚合入口（13 文件） | 无 | `permissions/` 下 12 文件 + `permissions_registry.py` | 权限点 122 不变、`ROLES` 不变、顺序不变 |
| T-BASE-010f | 同步/核对所有测试导入路径 | 010a-010e | `tests/` 下引用 base 的文件（见卡片） | 全量测试收集无 ImportError、通过、覆盖率达标 |
| T-BASE-010g | 运行闸门 1-5 验证 | 010f | `docs/12` | 5 道闸门全绿 |

---

## 9. 测试计划

| 层级 | 用例 | 断言 |
|------|------|------|
| 单元 | 每个新 Service/Mixin 的现有单测 | 行为与拆分前完全一致 |
| 接口 | 字典/款号/单价 全部 CRUD + 导出 | 状态码、响应结构、权限码、数据范围 |
| 权限 | 每个权限点的允许/拒绝 | `12001`/`12002` 正确 |
| 并发 | 款号并发创建/单价并发调价 | 乐观锁冲突、唯一索引兜底 |
| E2E | P0 基础资料全流程 | 登录→建字典→建款号→设单价→导出 |

---

## 10. 待决问题

| # | 问题 | 需要谁确认 | 阻塞什么 | 状态 |
|---|------|-----------|---------|------|
| Q-01 | `StyleService.copy_template` 归属 | 架构师 | 010c 拆分边界 | 已确认：归 `style_template_mixin.py`（款号强相关） |
| Q-02 | `RateService` 三档取价 SQL 是否下推 repository | 架构师 | 010c 实现细节 | 已确认：`build_rate_resolve_stmt` 放 `rate_helpers.py`（供计件复用），repository 只做简单查询 |
| Q-03 | `StyleService` 内部 6 Mixin 的切分边界（query/crud/child/ratio/operation/template）是否最终方案 | 架构师 | 010c 文件清单 | 待确认（本稿为建议方案，编码前定稿） |
| Q-04 | 初版的 `recalc_mixin`/`state_mixin`/`export_mixin` 是否成立 | 架构师 | 010c | 已核实**证据不足**：base 的字典/款号/单价无汇总重算、无单据状态机，导出为各端点自写；本轮不抽，待后续模块出现重复再抽 |
| Q-05 | `service/common.py` 是否再拆为 `common` + `query` | 架构师 | 010c | 待确认（本轮合并，约 215 行，仍 ≤400） |
| Q-06 | `RateService`（384 行）计入自身 import 后是否超 400 | 架构师 | 010c | 待确认：本轮用 `RateQueryMixin` 拆为两文件以保证 ≤400；若团队认定 import 不计入，可合并回单文件 |
| Q-07 | 跨模块生产导入（`system/service.py::write_document_log`、`document_logs.py::StyleService`）是否改深路径 | 架构师 | 010f | 待确认：本轮靠 `service/__init__.py` 重导出保持零改动 |
| Q-08 | 非目标清单里其它 >400 行文件（cutting/system/auth/cli）的拆分排期 | 业务/架构师 | 后续卡 | 待排期 |

> **有阻塞性待决问题时，不得开始编码。** Q-03/Q-06 为 010c 编码前必须闭环项。

---

## 11. 复用资产（REQ-000 §2）

| 资产来源 | 复用方式 |
|---------|---------|
| `base` 原有 CRUD/权限/范围/审计/单据号 | **保持不变**，按职责归属到 4 个 Service |
| `cutting` 汇总重算/三层结构 | **不复用**（base 无三层结构） |
| `stock_ledgers` 台账结构 | **不复用**（base 无台账） |
| `OperationRate` 三档取价 | **核心复用**：模型归 `models/rate.py`，取价 SQL 归 `service/rate_helpers.py::build_rate_resolve_stmt`，供 `piecework`/`purchase`/`sales` 直接 import |

---

## 12. 变更记录

| 日期 | 变更内容 | 操作人 |
|------|---------|--------|
| 2026-10-06 | 初版：base 模块拆分设计，解决单文件超 400 行违规 | AI |
| 2026-10-06 | **修正行数口径（原误将字节当行数）并按真实结构重排拆分边界**：models 1302→6 文件（补 `OperationRate`、删误列 `StockReservation`）、schemas 913→6 文件（删虚构 import/export schema）、service 2751→4 Service + StyleService 内部 6 Mixin（补 `MaterialOptionsService`）、router 1362→8 文件（单个 `router`）、permissions 850→11 perm + 数据类 + 聚合；成功标准收窄为 base + permissions 范围 | AI |
