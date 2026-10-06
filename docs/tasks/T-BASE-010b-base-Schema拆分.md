# T-BASE-010b：拆分 base schemas.py → 6 文件

| 项 | 内容 |
| --- | --- |
| 模块 | base |
| 负责人 | backend-dev |
| 状态 | todo |
| 优先级 | P0 |
| 依赖 | T-BASE-010a |
| 被依赖 | T-BASE-010c, T-BASE-010d, T-BASE-010f |
| 关联设计 | docs/modules/base-重构拆分.设计.md §2.3, §2.6, §8 |
| 关联 ADR | docs/adr/0030-提交体量上限放宽到1200行.md（第 3 条） |
| 估算 | 0.5d |

## 目标

将 `backend/app/modules/base/schemas.py`（**真实 913 行**；⚠️ 初版误把字节数 38985 当行数）拆分为 5 个内容文件 + `__init__.py` 聚合，单文件 ≤400 行。

## 范围

**要做**：
- [ ] 创建 `backend/app/modules/base/schemas/common_schemas.py`：`Versioned`(29)、`DictOut`(41)、`DictRow`(75)、`OptionOut`(85)、`StockBatchOptionOut`(100)、`DisableIn`(131)、`PatchIn`(140)、`DeleteOut`(336)、`DocumentLogOut`(343)、`SuggestedStyleNoOut`(367)、`DisableOut`(875)、`payload_dict()`(907)
- [ ] 创建 `backend/app/modules/base/schemas/dict_schemas.py`：9 字典的 `*Create`/`*Patch`（149-335）+ `CustomerCreate`(386)/`CustomerPatch`(405)
- [ ] 创建 `backend/app/modules/base/schemas/style_schemas.py`：`StyleCreate`(418)/`StylePatch`(462)/`StyleDisableIn`(478)/`StyleOut`(490)/`StyleListOut`(507)/`StyleColorCreate`(530)/`StyleColorOut`(541)/`StyleSizeCreate`(549)/`StyleSizeOut`(577)
- [ ] 创建 `backend/app/modules/base/schemas/style_child_schemas.py`：`RatioItemIn`(584)/`RatioReplaceIn`(596)/`RatioOut`(616)/`RatioListOut`(623)/`StyleOperationItemIn`(639)/`StyleOperationsReplaceIn`(662)/`StyleOperationOut`(674)/`StyleOperationsListOut`(685)/`StyleDetailOut`(690)/`TemplateCopyIn`(702)/`CopiedPriceOut`(728)/`TemplateCopyOut`(737)
- [ ] 创建 `backend/app/modules/base/schemas/rate_schemas.py`：`OperationRateCreate`(767)/`OperationRateOut`(824)/`OperationRateSetOut`(844)/`OperationRateListOut`(854)/`RateResolveOut`(859)
- [ ] 更新 `backend/app/modules/base/schemas/__init__.py`：聚合重导出全部 schema + `WRITE_MODELS` + `ACTION_BY_OPERATION`

**不做**：
- 不改变对外 API 请求/响应结构，不新增/删减字段
- **不创建 `export_schemas.py` / `import_schemas.py`**：本文件**没有**独立导入/导出模板类（初版属虚构）；导入导出沿用通用 schema

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `backend/app/modules/base/schemas/common_schemas.py` | 新增 | 公共片段 + 通用响应，约 225 行 |
| `backend/app/modules/base/schemas/dict_schemas.py` | 新增 | 字典写入体 + 客户写入体，约 240 行 |
| `backend/app/modules/base/schemas/style_schemas.py` | 新增 | 款号主表/色码/尺码，约 185 行 |
| `backend/app/modules/base/schemas/style_child_schemas.py` | 新增 | 比例/款号工序/模板复制，约 205 行 |
| `backend/app/modules/base/schemas/rate_schemas.py` | 新增 | 工序单价，约 125 行 |
| `backend/app/modules/base/schemas/__init__.py` | 新增/修改 | 聚合导出 + `WRITE_MODELS` + `ACTION_BY_OPERATION`，约 90 行 |
| `backend/app/modules/base/schemas.py` | 删除 | 原 913 行文件 |

## 实现要点（必读规范）

- [ ] 遵守 docs/05-接口设计规范.md：请求/响应模型、分页结构、错误码、字段命名
- [ ] 保持 `from_attributes=True` 兼容 ORM 模型
- [ ] `WRITE_MODELS`（885-898）与 `ACTION_BY_OPERATION`（899-906）放 `schemas/__init__.py`，避免 `common_schemas` ↔ `dict_schemas` 循环导入
- [ ] 导入路径更新：`from app.modules.base.models.xxx import Xxx` 按新 models 结构
- [ ] `__init__.py` 重导出全部公开名，保证 `from app.modules.base.schemas import X`（router/repository/service/document_logs/测试）零改动

## 验收标准

- [ ] `uv run pytest tests/modules/test_base_dict_router.py -q` 全部通过
- [ ] `uv run pytest tests/modules/test_base_style_service.py -q` 全部通过
- [ ] `uv run pytest tests/modules/test_operation_rates.py -q` 全部通过
- [ ] `uv run pytest tests/modules/test_base_resource_registry.py -q` 全部通过（`WRITE_MODELS` 守卫）
- [ ] `pnpm generate:api` 生成的 `schema.d.ts` 与拆分前无 diff
- [ ] 本次新增 6 个文件单文件 ≤400 行
- [ ] 闸门 2 (typecheck) 通过

## 测试清单

| # | 用例 | 期望 | 结果 |
|---|------|------|------|
| TC-01 | 所有 Schema 可导入 | `from app.modules.base.schemas import StyleCreate, OperationRateSetOut, WRITE_MODELS` 无报错 | |
| TC-02 | 字典 CRUD 请求校验 | 必填字段缺失→422、编码重复→10001 | |
| TC-03 | 款号创建/编辑 | 三层嵌套结构校验通过、比例/工序可选 | |
| TC-04 | 单价设价/调价 | 三档取价参数校验、区间不重叠校验 | |
| TC-05 | OpenAPI 生成 | `openapi.json` 与拆分前完全一致 | |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| | +0 / -0 | |

**提交记录**：
- `<hash>` feat(base): split schemas.py (913 lines) into 5 modules + package init (≤400 each)

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| | | docs/12 §遗留问题清单 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。

## 变更记录

| 日期 | 变更内容 | 操作人 |
|------|---------|--------|
| 2026-10-06 | 修正行数口径（原误将字节 38985 当行数，真实 913），按真实类重排为 5 内容文件 + `__init__.py`；删除虚构的 import/export schema | AI |
