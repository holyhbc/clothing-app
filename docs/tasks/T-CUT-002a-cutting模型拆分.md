# T-CUT-002a：拆分 cutting models.py → 4 领域文件 + `__init__.py`

| 项 | 内容 |
| --- | --- |
| 模块 | cut |
| 负责人 | backend-dev |
| 状态 | todo |
| 优先级 | P0 |
| 依赖 | 无 |
| 被依赖 | T-CUT-002b, T-CUT-002c, T-REFACTOR-001 |
| 关联设计 | docs/modules/cutting-system-auth-cli-拆分.设计.md §2.1, §2.8, §8 |
| 关联 ADR | docs/adr/0031-模块文件结构按职责拆分.md、docs/adr/0030-提交体量上限放宽到1200行.md |
| 估算 | 0.5d |

## 目标

将 `backend/app/modules/cutting/models.py`（**真实 696 行**）拆分为 4 个领域文件 + `__init__.py`，单文件 ≤400 行。纯代码搬运，不改表结构、字段、约束、索引、枚举。

## 范围

**要做**（5 个文件，见设计稿 §2.1）：
- [ ] 创建 `models/enums.py`：`CuttingEntryMode`(76-97) + `DOCUMENT_STATUS`(109) + `CUTTING_ENTRY_MODE`(115)
- [ ] 创建 `models/order.py`：`CuttingOrder`(118-295)
- [ ] 创建 `models/lines.py`：`CuttingOrderLine`(298-436) + `CuttingOrderLineColor`(439-544) + `CuttingOrderSizeLine`(547-649)
- [ ] 创建 `models/sequence.py`：`CuttingDocNoSequence`(652-696)
- [ ] 创建 `models/__init__.py`：按 `enums → order → lines → sequence` 顺序重导出全部公开名（5 模型 + `CuttingEntryMode` + 2 个 PG enum 常量）
- [ ] 删除原 `models.py`

**不做**：
- 不改表结构、字段、约束名、索引名与谓词、`comment`、`__tablename__`、枚举成员
- 不改 `alembic/` 任何迁移
- 不改 `repository.py`(316)、`schemas.py`、`service.py`、`router.py`

## 将要改动的文件

| 文件 | 类型 | 说明 | 预估行数 |
| --- | --- | --- | --- |
| `backend/app/modules/cutting/models/__init__.py` | 新增 | 聚合重导出 5 模型 + 1 枚举 + 2 常量 | ~45 |
| `backend/app/modules/cutting/models/enums.py` | 新增 | `CuttingEntryMode` / `DOCUMENT_STATUS` / `CUTTING_ENTRY_MODE` | ~55 |
| `backend/app/modules/cutting/models/order.py` | 新增 | `CuttingOrder` | ~205 |
| `backend/app/modules/cutting/models/lines.py` | 新增 | 三层子表 3 个模型 | ~375 |
| `backend/app/modules/cutting/models/sequence.py` | 新增 | `CuttingDocNoSequence` | ~60 |
| `backend/app/modules/cutting/models.py` | 删除 | 原 696 行文件 | - |

## 实现要点（必读规范）

- [ ] 遵守 docs/04-数据库规范.md：公共字段、PG enum、部分唯一索引、索引谓词逐字不变
- [ ] 遵守 docs/03-代码规范.md §1.3 / ADR-0031：`__init__.py` 只聚合重导出，不放业务逻辑
- [ ] 跨文件 `relationship("...")` 用字符串目标名，SQLAlchemy 在 mapper 配置期解析；`__init__.py` 必须先 import enums，再 order/lines/sequence
- [ ] `from __future__ import annotations` 保留，模型注解不回退为运行期求值
- [ ] `register_all_models()` 用 `pkgutil` import `app.modules.cutting.models`，改包后无需修改（base 已验证）

## 验收标准

- [ ] `uv run pytest tests/modules/test_cutting_tables.py -q` 全部通过
- [ ] `uv run pytest tests/modules/test_docs_ddl_sync.py -q` 全部通过
- [ ] `uv run alembic check` 输出 `No new upgrade operations detected.`（**零漂移**）
- [ ] `uv run pytest tests/ --co -q` 无 `ImportError`（尤其 `app/core/numbering.py` 的 `CuttingDocNoSequence`、`repository.py` 的 4 模型）
- [ ] 本次新增 5 个文件单文件 ≤400 行
- [ ] 闸门 1-4 本地预跑通过

## 测试清单

| # | 用例 | 期望 | 结果 |
|---|------|------|------|
| TC-01 | 全部模型可导入 | `from app.modules.cutting.models import CuttingOrder, CuttingOrderLine, CuttingOrderLineColor, CuttingOrderSizeLine, CuttingDocNoSequence, CuttingEntryMode` 无报错 | |
| TC-02 | 枚举值域 | `cutting_entry_mode` / `document_status` 值集合与拆分前一致（`test_cutting_tables.py`） | |
| TC-03 | 模型注册 | `register_all_models()` 后 `Base.metadata` 含四张表 | |
| TC-04 | 迁移检查 | `alembic check` 无新操作 | |

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
| 2026-10-06 | 初版：cutting/models.py(696) → models 包，照抄 T-BASE-010a 配方 | AI |
