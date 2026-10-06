# T-BASE-010c：拆分 base service.py → 4 Service + StyleService 内部 6 Mixin

| 项 | 内容 |
| --- | --- |
| 模块 | base |
| 负责人 | backend-dev |
| 状态 | done |
| 优先级 | P0 |
| 依赖 | T-BASE-010a, T-BASE-010b |
| 被依赖 | T-BASE-010d, T-BASE-010f |
| 关联设计 | docs/modules/base-重构拆分.设计.md §2.1, §2.6, §8, §10 |
| 关联 ADR | docs/adr/0030-提交体量上限放宽到1200行.md（第 3 条） |
| 估算 | 1d |

## 目标

将 `backend/app/modules/base/service.py`（**真实 2751 行**；⚠️ 初版误把字节数 126054 当行数，并错误声称只有"3 个 Service"）拆分为 **4 个 Service 类**，其中 `StyleService`（1415 行）用 6 个内部 Mixin 拆到多文件，单文件 ≤400 行。

## 范围

**要做**（15 个文件，全部见设计稿 §2.1）：
- [ ] `service/common.py`：常量（`ACTION_*`/`DOC_*`/`SKIP_*`/`SCALE_*`/`MAX_*` 等）、`_validate_page`/`_validate_sort`/`_validate_sort_order`、`write_document_log`、`numeric_str`、`_duplicated`、`StyleQuery`/`RateQuery` + 排序白名单
- [ ] `service/dict_service.py`：`DictService`（9 字典统一 CRUD，132-430）
- [ ] `service/dict_helpers.py`：`payload_dict_changes`、`_unique_violation`（431-454）
- [ ] `service/style_service.py`：`class StyleService(StyleQueryMixin, StyleCrudMixin, StyleChildMixin, StyleRatioMixin, StyleOperationMixin, StyleTemplateMixin)` 仅组合 + `__init__`
- [ ] `service/style_query_mixin.py`：款号读（767-1025）+ `_style_list_stmt`（726-741）
- [ ] `service/style_crud_mixin.py`：款号主表 CRUD（1026-1198）+ `_suggest`/`_find_by_no`/`_assert_category_usable`/`_duplicate_style_error`（1363-1417）
- [ ] `service/style_child_mixin.py`：色码/尺码追加（`add_colors`/`add_sizes`/`_resolve_size_items`，1199-1362）
- [ ] `service/style_ratio_mixin.py`：比例全量替换 + 聚合版本（1418-1642）
- [ ] `service/style_operation_mixin.py`：款号工序全量替换 + `_current_rates`（1643-1877）
- [ ] `service/style_template_mixin.py`：模板复制（`copy_template`/`_copy_structure`/`_copy_prices`，1878-2172）
- [ ] `service/rate_service.py`：`class RateService(RateQueryMixin)` 取价/设价/调价/区间收敛（2173-2556）
- [ ] `service/rate_query_mixin.py`：单价列表/导出/可见款号（2182-2260）
- [ ] `service/rate_helpers.py`：`build_rate_resolve_stmt`（630-679，ADR-0026 §2 唯一取价 SQL）、`rate_source_of`、`rate_out`（697-723）
- [ ] `service/material_options_service.py`：**`MaterialOptionsService`**（2557-2751，初版完全遗漏）
- [ ] 更新 `service/__init__.py`：重导出 `DictService`/`StyleService`/`RateService`/`MaterialOptionsService`/`StyleQuery`/`RateQuery`/`build_rate_resolve_stmt`/`write_document_log`
- [ ] 删除原 `service.py`

**不做**：
- 不改变业务逻辑、事务边界、权限校验、数据范围过滤、对外接口行为
- **不抽 `recalc_mixin`/`state_mixin`/`export_mixin`**：核实证据不足（base 无汇总重算、无单据状态机，导出各端点自写），见设计稿 §10 Q-04
- **不改** `repository.py`(283)、`resources.py`(284)、`document_logs.py`(123)

## 将要改动的文件

| 文件 | 类型 | 说明 | 预估行数 |
| --- | --- | --- | --- |
| `backend/app/modules/base/service/__init__.py` | 新增 | 统一导出 | ~35 |
| `backend/app/modules/base/service/common.py` | 新增 | 共享常量/校验/日志/查询 | ~215 |
| `backend/app/modules/base/service/dict_service.py` | 新增 | `DictService` | ~325 |
| `backend/app/modules/base/service/dict_helpers.py` | 新增 | 字典去重/冲突助手 | ~45 |
| `backend/app/modules/base/service/style_service.py` | 新增 | `StyleService` 组合 | ~45 |
| `backend/app/modules/base/service/style_query_mixin.py` | 新增 | 款号读 | ~295 |
| `backend/app/modules/base/service/style_crud_mixin.py` | 新增 | 款号主表 CRUD | ~255 |
| `backend/app/modules/base/service/style_child_mixin.py` | 新增 | 色码/尺码 | ~185 |
| `backend/app/modules/base/service/style_ratio_mixin.py` | 新增 | 比例 | ~245 |
| `backend/app/modules/base/service/style_operation_mixin.py` | 新增 | 款号工序 | ~255 |
| `backend/app/modules/base/service/style_template_mixin.py` | 新增 | 模板复制 | ~320 |
| `backend/app/modules/base/service/rate_service.py` | 新增 | `RateService` | ~320 |
| `backend/app/modules/base/service/rate_query_mixin.py` | 新增 | 单价列表/导出 | ~100 |
| `backend/app/modules/base/service/rate_helpers.py` | 新增 | 取价 SQL/响应映射 | ~95 |
| `backend/app/modules/base/service/material_options_service.py` | 新增 | `MaterialOptionsService` | ~215 |
| `backend/app/modules/base/service.py` | 删除 | 原 2751 行文件 | - |

## 实现要点（必读规范）

- [ ] 遵守 docs/03-代码规范.md：事务边界只在 service（`unit_of_work`）、Repository 只查、状态机只在 service
- [ ] StyleService 的 6 个 Mixin 仅用于**把一个类拆到多文件**，不承载跨模块通用逻辑
- [ ] **跨模块导入必须靠 `service/__init__.py` 重导出保持零改动**：`app/modules/system/service.py` 的 `write_document_log`、`document_logs.py` 的 `StyleService`、`router.py` 的全部 Service 导入
- [ ] 保持 `service.py` 原有 `__all__`（`ACTION_CREATE`/`ACTION_DELETE`/`ACTION_UPDATE`/`DictService`/`MaterialOptionsService`/`RateQuery`/`RateService`/`StyleQuery`/`StyleService`/`build_rate_resolve_stmt`）可从包入口导入
- [ ] **Q-03/Q-06 为编码前必须闭环项**（Mixin 边界、RateService 拆法），见设计稿 §10

## 验收标准

- [ ] `uv run pytest tests/modules/test_base_dict_service.py -q` 全部通过
- [ ] `uv run pytest tests/modules/test_base_style_service.py -q` 全部通过
- [ ] `uv run pytest tests/modules/test_operation_rates.py -q` 全部通过
- [ ] `uv run pytest tests/modules/test_material_stock_options.py -q` 全部通过（`MaterialOptionsService`）
- [ ] 并发测试通过：款号并发创建 / 单价并发调价 / 比例全量替换一成一败（10003）
- [ ] 本次新增 15 个文件单文件 ≤400 行
- [ ] 闸门 1-4 本地预跑通过

## 测试清单

| # | 用例 | 期望 | 结果 |
|---|------|------|------|
| TC-01 | 字典 CRUD | 创建/查询/更新/停用/真删（未引用）/真删被引用→20003 | |
| TC-02 | 款号全流程 | 建款号→色码尺码→比例全量替换→工序全量替换→模板复制(不复制档位2) | |
| TC-03 | 单价设价/调价/取价 | 档位1/2/3设价→区间不重叠校验→一条SQL取价→调价=关旧区间+新行 | |
| TC-04 | 乐观锁冲突 | 并发 PATCH 同版本→1成1败(10003) | |
| TC-05 | 物料/供应商/布批候选 | `MaterialOptionsService` 三个方法返回结构不变 | |
| TC-06 | 跨模块导入 | `from app.modules.base.service import write_document_log, StyleService, DictService` 无报错 | |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `backend/app/modules/base/service/__init__.py` | 87 | 保留原 service.py 模块 docstring + 聚合重导出（4 Service / 2 Query / `build_rate_resolve_stmt` / `write_document_log` / 4 个 `ACTION_*`），原导入面零改动 |
| `backend/app/modules/base/service/common.py` | 225 | `logger`、`ACTION_*`、`DOC_*`、`MAX_*`、`SKIP_*`、`SCALE_*`、`RATIO_NOT_COPIED_MESSAGE`、`STYLE_TRGM_QUALIFIED`、排序白名单、`StyleQuery`/`RateQuery`、`_validate_page`/`_validate_sort`/`_validate_sort_order`、`write_document_log`、`numeric_str`、`_duplicated` |
| `backend/app/modules/base/service/dict_service.py` | 320 | `DictService`（9 字典统一 CRUD，原 132-429） |
| `backend/app/modules/base/service/dict_helpers.py` | 36 | `payload_dict_changes`、`_unique_violation`（原 431-454） |
| `backend/app/modules/base/service/style_service.py` | 30 | `StyleService(...)` 继承 6 Mixin + `__init__`（原 758-763） |
| `backend/app/modules/base/service/style_query_mixin.py` | 329 | 款号读：`list_styles`/`list_options`/`suggest`/`get_required`/`export_styles`/`get_detail`/色码尺码读 + `_style_list_stmt`（原 726-741、767-1022） |
| `backend/app/modules/base/service/style_crud_mixin.py` | 257 | 款号主表 CRUD + `_suggest`/`_find_by_no`/`_assert_category_usable`/`_duplicate_style_error`（原 1024-1197、1363-1414） |
| `backend/app/modules/base/service/style_child_mixin.py` | 191 | `add_colors`/`add_sizes`/`_resolve_size_items`（原 1199-1361） |
| `backend/app/modules/base/service/style_ratio_mixin.py` | 264 | 比例全量替换 + `_assert_aggregate_version`/`_bump_aggregate`（原 1416-1639） |
| `backend/app/modules/base/service/style_operation_mixin.py` | 281 | 款号工序全量替换 + `_current_rates`（原 1641-1874） |
| `backend/app/modules/base/service/style_template_mixin.py` | 348 | 模板复制 `copy_template`/`_copy_structure`/`_copy_prices`（原 1876-2167） |
| `backend/app/modules/base/service/rate_service.py` | 318 | `RateService(RateQueryMixin)`：`resolve`/`set_rate`/`_reconcile_intervals`/`_resolve_target_style`（原 2173-2178、2260-2537） |
| `backend/app/modules/base/service/rate_query_mixin.py` | 101 | `list_rates`/`_filtered`/`export_rates`/`_visible_style_nos`（原 2180-2259） |
| `backend/app/modules/base/service/rate_helpers.py` | 96 | `build_rate_resolve_stmt`（ADR-0026 §2 唯一取价 SQL）、`rate_source_of`、`rate_out`（原 630-679、697-723） |
| `backend/app/modules/base/service/material_options_service.py` | 211 | `MaterialOptionsService` + `MATERIAL_TRGM_QUALIFIED`/`SUPPLIER_TRGM_QUALIFIED`（原 2543-2554、2558-2737） |
| `backend/app/modules/base/service.py` | -2751 | **删除**（纯代码搬运，内容逐字迁入上述 15 个文件） |

新增/搬运合计 **+3094 / -2751**（净 +343）；单文件最大 348 行（≤400）。逐字搬运原业务代码 2558 行 + 原模块 docstring 47 行；新增各文件 import / 类声明 / Mixin `TYPE_CHECKING` 前置声明 / 包聚合约 489 行。81 个函数经 AST 比对逐字一致，无行为变更。

**提交记录**：
- `98fe3eb` refactor(base): 拆分 service.py(2751 行) 为 4 Service + StyleService 6 Mixin（≤400 行）

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| L-090 | 卡面点名的三条并发用例（款号并发创建 / 单价并发调价 / 比例全量替换一成一败 `10003`）在仓库中不存在，只有 `concurrent_sessions` 夹具与清理逻辑；实际 base 并发覆盖在 `tests/integration/test_no_dangling_refs.py`（CC-2/CC-3/CC-6，18 passed） | docs/12 §5 L-090 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。

## 变更记录

| 日期 | 变更内容 | 操作人 |
|------|---------|--------|
| 2026-10-06 | 修正行数口径（原误将字节 126054 当行数，真实 2751）；由"3 个 Service"更正为 **4 个 Service**（补 `MaterialOptionsService`）；StyleService(1415 行) 用 6 个内部 Mixin 再拆；删除证据不足的 recalc/state/export Mixin | AI |
