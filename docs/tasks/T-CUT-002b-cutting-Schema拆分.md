# T-CUT-002b：拆分 cutting schemas.py → 3 内容文件 + `__init__.py`

| 项 | 内容 |
| --- | --- |
| 模块 | cut |
| 负责人 | backend-dev |
| 状态 | todo |
| 优先级 | P0 |
| 依赖 | T-CUT-002a |
| 被依赖 | T-CUT-002c, T-REFACTOR-001 |
| 关联设计 | docs/modules/cutting-system-auth-cli-拆分.设计.md §2.2, §2.8, §8 |
| 关联 ADR | docs/adr/0031-模块文件结构按职责拆分.md、docs/adr/0030-提交体量上限放宽到1200行.md |
| 估算 | 0.5d |

## 目标

将 `backend/app/modules/cutting/schemas.py`（**真实 457 行**）拆分为 3 个内容文件 + `__init__.py`，单文件 ≤400 行。纯代码搬运，类 docstring、字段、类型、`Field` 校验与 `ConfigDict` 逐字迁移。

## 范围

**要做**（4 个文件，见设计稿 §2.2）：
- [ ] 创建 `schemas/common_schemas.py`：`StyleNo`/`ColorCode`/`SizeCode`(47-60)、`MAX_LINES`/`MAX_COLORS_PER_LINE`/`MAX_SIZE_LINES_PER_COLOR`、`Version`(322-331)
- [ ] 创建 `schemas/order_schemas.py`：`SizeLineIn`(63-94)/`LineColorIn`(97-124)/`OrderLineIn`(127-162)/`CuttingOrderCreateIn`(165-187)；`SizeLineOut`(200-214)/`LineColorOut`(217-236)/`OrderLineOut`(239-266)/`CuttingOrderOut`(269-299)/`CuttingOrderListOut`(302-319)
- [ ] 创建 `schemas/maintenance_schemas.py`：`CuttingOrderPatchIn`(334)/`PutColorsIn`(351)/`PutSizeLinesIn`(367)/`EntryModeSwitchIn`(388)/`SuggestSizeLineOut`(404)/`SuggestLinesOut`(413)/`RatioWarningOut`(434)/`PutLinesIn`(446)
- [ ] 创建 `schemas/__init__.py`：聚合重导出全部公开名
- [ ] 删除原 `schemas.py`

**不做**：
- 不改字段名、类型、默认值、`extra="forbid"`、`from_attributes=True`、`Field` 约束、`examples`
- 不改 `models/`、`service.py`、`router.py`、`repository.py`

## 将要改动的文件

| 文件 | 类型 | 说明 | 预估行数 |
| --- | --- | --- | --- |
| `backend/app/modules/cutting/schemas/__init__.py` | 新增 | 聚合重导出 | ~40 |
| `backend/app/modules/cutting/schemas/common_schemas.py` | 新增 | 别名/上限/版本号 | ~70 |
| `backend/app/modules/cutting/schemas/order_schemas.py` | 新增 | 三层入参 + 出参 | ~290 |
| `backend/app/modules/cutting/schemas/maintenance_schemas.py` | 新增 | 增量维护入参 | ~160 |
| `backend/app/modules/cutting/schemas.py` | 删除 | 原 457 行文件 | - |

## 实现要点（必读规范）

- [ ] 遵守 docs/05-接口设计规范.md §2/§3：请求 `extra="forbid"`、响应数量金额用 `Str`、`version` 聚合行
- [ ] 依赖方向：`common_schemas ← order_schemas ← maintenance_schemas`，禁止反向
- [ ] `__init__.py` 重导出后 `from app.modules.cutting.schemas import X` 零改动（router/service/factories/tests 见设计稿 §2.8）
- [ ] 不新增独立「导出/导入模板类」（本文件没有）；导出沿用通用 schema

## 验收标准

- [ ] `uv run pytest tests/modules/test_cutting_service.py -q` 全部通过
- [ ] `uv run pytest tests/modules/test_cutting_service2.py -q` 全部通过
- [ ] `uv run pytest tests/modules/test_cutting_router.py -q` 全部通过
- [ ] `uv run pytest tests/ --co -q` 无 `ImportError`
- [ ] **OpenAPI 零 diff**：重跑 `generate:api` 后 `backend/openapi.json` 无输出
- [ ] 本次新增 4 个文件单文件 ≤400 行
- [ ] 闸门 1-4 本地预跑通过

## 测试清单

| # | 用例 | 期望 | 结果 |
|---|------|------|------|
| TC-01 | schema 可导入 | `from app.modules.cutting.schemas import CuttingOrderCreateIn, PutLinesIn, SizeLineIn` 无报错 | |
| TC-02 | 入参 `extra="forbid"` | 多传字段报 `10001` | |
| TC-03 | 工厂与测试导入 | `tests/factories/cutting.py` / 两个 service 测试的 schema 导入零改动 | |
| TC-04 | 契约 | OpenAPI 与拆分前零 diff | |

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
| 2026-10-06 | 初版：cutting/schemas.py(457) → schemas 包，照抄 T-BASE-010b 配方 | AI |
