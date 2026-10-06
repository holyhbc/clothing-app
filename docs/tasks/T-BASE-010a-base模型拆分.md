# T-BASE-010a：拆分 base models.py → 6 领域文件

| 项 | 内容 |
| --- | --- |
| 模块 | base |
| 负责人 | backend-dev |
| 状态 | done |
| 优先级 | P0 |
| 依赖 | 无 |
| 被依赖 | T-BASE-010b, T-BASE-010c, T-BASE-010d, T-BASE-010f |
| 关联设计 | docs/modules/base-重构拆分.设计.md §2.4, §2.6, §8 |
| 关联 ADR | docs/adr/0030-提交体量上限放宽到1200行.md（第 3 条） |
| 估算 | 0.5d |

## 目标

将 `backend/app/modules/base/models.py`（**真实 1302 行**，24 个类；⚠️ 初版误把字节数 53599 当行数）拆分为 6 个领域文件 + `__init__.py` 统一导出，单文件 ≤400 行。

## 范围

**要做**：
- [x] 创建 `backend/app/modules/base/models/org.py`：`Workshop`(89)、`WorkshopGroup`(116)、`Warehouse`(151)、`UomUnit`(179)
- [x] 创建 `backend/app/modules/base/models/dict.py`：`Color`(209)、`Size`(252)、`SizeGroup`(303)、`SizeGroupItem`(343)、`Operation`(374)、`ProductCategory`(429)
- [x] 创建 `backend/app/modules/base/models/style.py`：`Customer`(469)、`Style`(503)、`StyleColor`(569)、`StyleSize`(599)、`StyleColorSizeRatio`(629)、`StyleOperation`(665)、`StyleNoSequence`(824)
- [x] 创建 `backend/app/modules/base/models/rate.py`：**`OperationRate`(722)**（REQ-000 §2 三档取价核心资产，单独归属）
- [x] 创建 `backend/app/modules/base/models/material.py`：`MaterialCategory`(876)、`Material`(914)、`Supplier`(976)
- [x] 创建 `backend/app/modules/base/models/stock.py`：`MaterialStock`(1055)、`WipStock`(1192)、`WipLedgerLine`(1263)
- [x] 更新 `backend/app/modules/base/models/__init__.py`：按原顺序导出全部 24 个模型，保证现有 `from app.modules.base.models import X` 零改动
- [x] 删除原 `models.py`（或重命名为 `.bak` 待验证后删）

**不做**：
- 不改变任何表结构、字段、约束、索引
- 不迁移数据
- **不列 `StockReservation`**：它在 `app/modules/stock/models.py:296`，不属于 base
- **不动 `app/modules/base/document_logs.py`**：它是查询工具 + 常量（123 行），不是模型文件；`DocumentLog` 模型在 `app/common/models.py`
- **不改 `register_all_models()`**：`app/common/models.py` 用 `pkgutil.iter_modules` import `app.modules.<mod>.models`，`models.py` 改为 `models/` 包后仍可导入
- 不动 `repository.py`(283)、`resources.py`(284)、`document_logs.py`(123)（均已 ≤400）

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `backend/app/modules/base/models/org.py` | 新增 | 组织类模型，约 150 行 |
| `backend/app/modules/base/models/dict.py` | 新增 | 字典类模型，约 285 行 |
| `backend/app/modules/base/models/style.py` | 新增 | 款号类模型，约 335 行 |
| `backend/app/modules/base/models/rate.py` | 新增 | `OperationRate`，约 130 行 |
| `backend/app/modules/base/models/material.py` | 新增 | 物料/供应商模型，约 200 行 |
| `backend/app/modules/base/models/stock.py` | 新增 | 库存基础表模型，约 270 行 |
| `backend/app/modules/base/models/__init__.py` | 修改 | 统一导出 24 个模型，约 60 行 |
| `backend/app/modules/base/models.py` | 删除 | 原 1302 行文件 |

## 实现要点（必读规范）

- [x] 遵守 docs/03-代码规范.md：模型定义风格、Mixin 组合顺序、CheckConstraint 命名
- [x] 遵守 docs/04-数据库规范.md：公共字段、枚举 PG enum、部分唯一索引、GIN 索引表达式
- [x] `models/__init__.py` 按原顺序导出，确保现有导入零改动（`resources.py`/`cutting/service.py`/`cli/seed_dicts.py`/`core/numbering.py` 及全部测试）
- [x] `register_all_models()` 自动发现新包，无需修改

## 验收标准

- [x] `uv run pytest tests/modules/test_base_dict_service.py -q` 全部通过
- [x] `uv run pytest tests/modules/test_base_style_service.py -q` 全部通过
- [x] `uv run pytest tests/modules/test_material_tables.py -q` 全部通过
- [x] `uv run pytest tests/modules/test_stock_basis.py -q` 全部通过
- [x] `uv run alembic check` 无漂移（模型与数据库完全一致）
- [x] 本次新增的 7 个文件单文件 ≤400 行
- [x] 闸门 1-4 本地预跑通过

## 测试清单

| # | 用例 | 期望 | 结果 |
|---|------|------|------|
| TC-01 | 所有模型可导入 | `from app.modules.base.models import Workshop, Style, OperationRate, MaterialStock` 无报错 | | 通过 |
| TC-02 | 字典 CRUD | 创建/查询/更新/停用/真删（未引用）正常 | | 通过 |
| TC-03 | 款号全流程 | 建款号→色码尺码→比例→工序→模板复制正常 | | 通过 |
| TC-04 | 物料/供应商/库存 | 建物料→建供应商→库存结存查询正常 | | 通过 |
| TC-05 | 迁移检查 | `alembic check` 无新操作 | | 通过 |
| TC-06 | 模型注册 | `register_all_models()` 后 `Base.metadata` 含全部 24 表 | | 通过 |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `backend/app/modules/base/models/__init__.py` | 84 | 统一导出 24 个模型 + `SIZE_CLASS`/`TRGM_*`/`_trgm_index`，保持原导入面零改动 |
| `backend/app/modules/base/models/_shared.py` | 31 | **新增**：`_trgm_index` + `TRGM_*` 常量（dict/style 两域共用、无天然归属，故单独放置） |
| `backend/app/modules/base/models/org.py` | 129 | `Workshop` / `WorkshopGroup` / `Warehouse` / `UomUnit` |
| `backend/app/modules/base/models/dict.py` | 293 | `Color` / `Size` / `SizeGroup` / `SizeGroupItem` / `Operation` / `ProductCategory` + `SIZE_CLASS` |
| `backend/app/modules/base/models/style.py` | 327 | `Customer` / `Style` / `StyleColor` / `StyleSize` / `StyleColorSizeRatio` / `StyleOperation` / `StyleNoSequence` |
| `backend/app/modules/base/models/rate.py` | 113 | `OperationRate`（三档取价核心复用资产，单独归属） |
| `backend/app/modules/base/models/material.py` | 197 | `MaterialCategory` / `Material` / `Supplier` |
| `backend/app/modules/base/models/stock.py` | 277 | `MaterialStock` / `WipStock` / `WipLedgerLine` |
| `backend/app/modules/base/models.py` | -1302 | **删除**（纯代码搬运，内容逐字迁入上述 7 个文件） |

新增/搬运合计 **+1451 / -1302**；单文件最大 327 行（≤400）。

**提交记录**：
- 3592e86 refactor(base): 拆分 models.py(1302 行) 为 6 领域文件 + _shared + __init__（≤400 行）

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| | | docs/12 §遗留问题清单 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。

## 变更记录

| 日期 | 变更内容 | 操作人 |
|------|---------|--------|
| 2026-10-06 | 修正行数口径（原误将字节 53599 当行数，真实 1302），按真实 24 类重排为 6 文件；补 `OperationRate`(rate.py)、删误列的 `StockReservation`、更正 `document_logs.py` 性质 | AI |
