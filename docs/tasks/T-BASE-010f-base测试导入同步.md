# T-BASE-010f：同步/核对所有测试导入路径

| 项 | 内容 |
| --- | --- |
| 模块 | base |
| 负责人 | qa |
| 状态 | todo |
| 优先级 | P0 |
| 依赖 | T-BASE-010a, T-BASE-010b, T-BASE-010c, T-BASE-010d, T-BASE-010e |
| 被依赖 | T-BASE-010g |
| 关联设计 | docs/modules/base-重构拆分.设计.md §2.6, §8 |
| 关联 ADR | 无 |
| 估算 | 0.5d |

## 目标

在 base 各模块改为包（`__init__.py` 重导出）后，核对并修复所有测试/夹具/工厂的导入路径，保证全量测试收集无 `ImportError`、行为与拆分前一致。

> **设计策略**：§2.6 采用"包入口重导出"兼容策略，预期**绝大多数测试导入零改动**。本卡职责是**逐文件核对**并在必要时（某名字未被重导出）修正；**不做无意义的重构**。

## 范围

**要做**：
- [ ] 核对并（必要时）修正以下引用 base 的测试/夹具文件：
  - `tests/conftest.py`（`permissions_registry` 的 `ROLES`/`resolve_role_permissions`）
  - `tests/e2e_seed.py`（`base.models`/`base.schemas`/`base.service`/`permissions_registry`）
  - `tests/factories/user.py`、`tests/factories/stock.py`、`tests/factories/cutting.py`
  - `tests/integration/test_no_dangling_refs.py`（`base.models`/`base.service`）
  - `tests/modules/test_base_dict_service.py`、`test_base_style_service.py`、`test_base_dict_router.py`、`test_base_resource_registry.py`
  - `tests/modules/test_operation_rates.py`、`test_material_tables.py`、`test_material_stock_options.py`、`test_stock_basis.py`、`test_style_disable_export.py`、`test_document_logs.py`
  - `tests/modules/test_cutting_router.py`、`test_cutting_service.py`、`test_cutting_service2.py`、`test_cutting_tables.py`
  - `tests/modules/test_permission_registry.py`、`test_seed_cli.py`、`test_system_restore.py`、`test_system_router.py`
  - `tests/modules/test_docs_ddl_sync.py`、`test_docs_ddl_sync_fields.py`（`register_all_models()` 自动适配，验证无需改）
- [ ] 确认生产侧跨模块导入未受影响：`app/modules/system/service.py` 的 `write_document_log`、`app/modules/base/document_logs.py` 的 `StyleService`（靠包入口重导出）
- [ ] `pytest --co` 收集全量通过

**不做**：
- 不修改测试逻辑、断言、数据
- 不新增/删减测试用例
- **不为了"用上新路径"而把兼容导入改成深路径**（除非某名字确实未重导出）

## 将要改动的文件

> 预期零改动或少量改动；下表为**核对范围**，最终只在确有未重导出名字时修改对应文件。

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `backend/tests/conftest.py` | 核对 | 权限注册表导入 |
| `backend/tests/e2e_seed.py` | 核对 | base 模型/schema/service |
| `backend/tests/factories/user.py` | 核对 | `base.models` |
| `backend/tests/factories/stock.py` | 核对 | `base.models` |
| `backend/tests/factories/cutting.py` | 核对 | `base.models` |
| `backend/tests/integration/test_no_dangling_refs.py` | 核对 | `base.models`/`base.service` |
| `backend/tests/modules/test_base_dict_service.py` | 核对 | `base.models`/`base.service` |
| `backend/tests/modules/test_base_style_service.py` | 核对 | `base.models`/`base.schemas`/`base.service` |
| `backend/tests/modules/test_base_dict_router.py` | 核对 | `base.models`/`base.schemas`/`base.router` |
| `backend/tests/modules/test_base_resource_registry.py` | 核对 | `base.schemas.WRITE_MODELS` |
| `backend/tests/modules/test_operation_rates.py` | 核对 | `base.models`/`base.schemas`/`base.service` |
| `backend/tests/modules/test_material_tables.py` | 核对 | `base.models` |
| `backend/tests/modules/test_material_stock_options.py` | 核对 | `base.models`/`base.schemas`/`permissions_registry` |
| `backend/tests/modules/test_stock_basis.py` | 核对 | `base.models` |
| `backend/tests/modules/test_style_disable_export.py` | 核对 | `base.models`/`base.router.EXPORT_GLOBAL_PERMISSION` |
| `backend/tests/modules/test_document_logs.py` | 核对 | `base.models` |
| `backend/tests/modules/test_cutting_router.py` | 核对 | `base.models`/`permissions_registry` |
| `backend/tests/modules/test_cutting_service.py` | 核对 | `base.models` |
| `backend/tests/modules/test_cutting_service2.py` | 核对 | `base.models` |
| `backend/tests/modules/test_cutting_tables.py` | 核对 | `base.models` |
| `backend/tests/modules/test_permission_registry.py` | 核对 | `permissions_registry` 全部公开名 |
| `backend/tests/modules/test_seed_cli.py` | 核对 | `permissions_registry.role_codes` |
| `backend/tests/modules/test_system_restore.py` | 核对 | `base.models`/`permissions_registry` |
| `backend/tests/modules/test_system_router.py` | 核对 | `permissions_registry.PERMISSIONS` |
| `backend/tests/modules/test_docs_ddl_sync.py` | 核对 | `register_all_models()` 自动适配 |
| `backend/tests/modules/test_docs_ddl_sync_fields.py` | 核对 | 同上 |

## 实现要点（必读规范）

- [ ] 遵守 docs/10-测试规范.md：测试隔离、夹具复用、覆盖率要求
- [ ] 模型导入：`from app.modules.base.models import X`（包入口）
- [ ] Service 导入：`from app.modules.base.service import DictService, StyleService, RateService, MaterialOptionsService, write_document_log`
- [ ] Router 导入：`from app.modules.base.router import router`（单个对象）
- [ ] 权限注册表：`from app.common.permissions_registry import ROLES, PERMISSIONS, SELF_PERMISSION_CODES, permission_codes, role_codes, resolve_role_permissions`
- [ ] `register_all_models()` 用 `pkgutil` 自动发现 `base/models/` 包，无需改动

## 验收标准

- [ ] `uv run pytest tests/ -q --co` 收集无 `ImportError` / `ModuleNotFoundError`
- [ ] `uv run pytest tests/modules/test_base_dict_service.py tests/modules/test_base_style_service.py tests/modules/test_base_dict_router.py tests/modules/test_operation_rates.py -q` 全部通过
- [ ] `uv run pytest tests/modules/test_cutting_*.py -q` 全部通过
- [ ] `uv run pytest tests/modules/test_stock_basis.py tests/modules/test_material_tables.py tests/modules/test_material_stock_options.py -q` 全部通过
- [ ] `uv run pytest tests/modules/test_permission_registry.py tests/modules/test_seed_cli.py -q` 全部通过
- [ ] 覆盖率 ≥80%（核心 ≥90%）
- [ ] 闸门 3 通过

## 测试清单

| # | 用例 | 期望 | 结果 |
|---|------|------|------|
| TC-01 | 全量测试收集 | `pytest --co` 无 `ImportError` / `ModuleNotFoundError` | |
| TC-02 | base 字典/款号/单价测试 | 全部通过、覆盖率达标 | |
| TC-03 | cutting 模块测试 | 全部通过（依赖 base 模型/服务） | |
| TC-04 | stock/物料测试 | 全部通过（依赖 base 模型） | |
| TC-05 | 权限/认证测试 | 全部通过（依赖 permissions_registry 聚合入口） | |
| TC-06 | 跨模块生产导入 | `system/service.py`、`base/document_logs.py` 导入无报错 | |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| | +0 / -0 | |

**提交记录**：
- `<hash>` test(base): verify/repair import paths after module split

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| | | docs/12 §遗留问题清单 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。

## 变更记录

| 日期 | 变更内容 | 操作人 |
|------|---------|--------|
| 2026-10-06 | 修正行数口径导致的范围变化：明确 §2.6 兼容重导出下本卡为"核对为主、必要时修正"；按真实测试文件补齐清单；补 `test_material_tables`/`test_base_resource_registry`/`test_system_*`/`test_docs_ddl_sync*` | AI |
