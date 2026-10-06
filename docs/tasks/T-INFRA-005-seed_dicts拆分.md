# T-INFRA-005：拆分 cli seed_dicts.py → 数据 / 写入 / 校验（4 文件）

| 项 | 内容 |
| --- | --- |
| 模块 | infra（`app/cli`） |
| 负责人 | backend-dev |
| 状态 | todo |
| 优先级 | P1 |
| 依赖 | 无 |
| 被依赖 | T-REFACTOR-001 |
| 关联设计 | docs/modules/cutting-system-auth-cli-拆分.设计.md §2.7, §2.8, §8 |
| 关联 ADR | docs/adr/0031-模块文件结构按职责拆分.md、docs/adr/0030-提交体量上限放宽到1200行.md |
| 估算 | 0.5d |

## 目标

将 `backend/app/cli/seed_dicts.py`（**真实 447 行**）拆分为 4 个文件，单文件 ≤400 行：
`BUILTIN_*` 业务数据与 `seed_*` 逻辑分离。

## 范围

**要做**（4 个文件，见设计稿 §2.7）：
- [ ] `seed_dicts/builtin_data.py`：`BUILTIN_COLORS`/`BUILTIN_SIZES`/`BUILTIN_SIZE_GROUPS`/`BUILTIN_PRODUCT_CATEGORIES`/`BUILTIN_UOM_UNITS`/`BUILTIN_MATERIAL_CATEGORIES`(41-125)、`_SEED_OPERATOR`(128)、`DICT_DOC_TYPES`(136-145)
- [ ] `seed_dicts/seeders.py`：`tombstoned_codes`(148-177)/`_skip_tombstoned`(202-204)/`seed_dict_library`(180-199)/`seed_colors`(207-227)/`seed_sizes`(230-261)/`seed_size_groups`(264-307)/`seed_product_categories`(310-332)/`seed_uom_units`(335-357)/`seed_material_categories`(360-382)
- [ ] `seed_dicts/checker.py`：`check_dict_library`(385-447)
- [ ] `seed_dicts/__init__.py`：重导出全部公开名（6 组 `BUILTIN_*` + `DICT_DOC_TYPES` + `tombstoned_codes` + 6 个 `seed_*` + `seed_dict_library` + `check_dict_library`）
- [ ] 删除原 `seed_dicts.py`

**不做**：
- 不改 seed 数据内容、数量、墓碑口径、`ON CONFLICT DO NOTHING`、`--check` 预期值
- 不改 `seed_baseline.py`(266)、`restore_builtin.py`(260)、`system/restore.py`(165)

## 将要改动的文件

| 文件 | 类型 | 说明 | 预估行数 |
| --- | --- | --- | --- |
| `backend/app/cli/seed_dicts/__init__.py` | 新增 | 聚合重导出 | ~60 |
| `backend/app/cli/seed_dicts/builtin_data.py` | 新增 | 内置库数据常量 + `DICT_DOC_TYPES` | ~120 |
| `backend/app/cli/seed_dicts/seeders.py` | 新增 | 墓碑 + 6 个 `seed_*` + `seed_dict_library` | ~280 |
| `backend/app/cli/seed_dicts/checker.py` | 新增 | `check_dict_library` | ~95 |
| `backend/app/cli/seed_dicts.py` | 删除 | 原 447 行文件 | - |

## 实现要点（必读规范）

- [ ] 遵守 docs/04 §7.4 / ADR-0025 §决策 3：幂等、不覆盖用户改动、墓碑跳过
- [ ] 依赖方向：`builtin_data ← seeders`、`builtin_data ← checker`；`__init__.py` 只聚合
- [ ] `app.modules.system.restore` 的 `from app.cli.seed_dicts import DICT_DOC_TYPES, seed_dict_library` **零改动**
- [ ] `seed_baseline.py` / `restore_builtin.py` / 测试的 `BUILTIN_*` 导入零改动（§2.8）
- [ ] 迁移豁免：本卡不涉及任何迁移文件

## 验收标准

- [ ] `uv run pytest tests/modules/test_seed_cli.py -q` 全部通过
- [ ] `uv run pytest tests/modules/test_material_tables.py -q` 全部通过
- [ ] `uv run pytest tests/modules/test_system_restore.py -q` 全部通过
- [ ] `uv run pytest tests/modules/test_cli_entrypoint.py -q` 全部通过
- [ ] `uv run pytest tests/ --co -q` 无 `ImportError`
- [ ] 本次新增 4 个文件单文件 ≤400 行
- [ ] 闸门 1-4 本地预跑通过

## 测试清单

| # | 用例 | 期望 | 结果 |
|---|------|------|------|
| TC-01 | seed 幂等 | 连跑两次第二次新增 0 | |
| TC-02 | 墓碑跳过 | 被真删的内置项不复活 | |
| TC-03 | `--check` | 各表数量与定稿清单一致（色 16 / 尺码 8 / 码表 2+8 / 分类 6 / 计量 3 / 物料类目 10） | |
| TC-04 | 数据导入 | `BUILTIN_MATERIAL_CATEGORIES`/`BUILTIN_COLORS` 等旧路径可导入 | |
| TC-05 | 恢复内置库 | `system/restore.py` 经包入口正常调用 | |

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
| 2026-10-06 | 初版：cli/seed_dicts.py(447) → 数据/写入/校验三文件 + 包 | AI |
