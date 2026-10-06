# T-CUT-002c：拆分 cutting service.py → 5 Mixin + recalc + 组合（8 文件）

| 项 | 内容 |
| --- | --- |
| 模块 | cut |
| 负责人 | backend-dev |
| 状态 | todo |
| 优先级 | P0 |
| 依赖 | T-CUT-002a, T-CUT-002b |
| 被依赖 | T-REFACTOR-001 |
| 关联设计 | docs/modules/cutting-system-auth-cli-拆分.设计.md §2.3, §2.8, §10, §11 |
| 关联需求 | docs/requirements/REQ-000-工期优化与复用策略.md §2（`recalc_*` 复用资产） |
| 关联 ADR | docs/adr/0031-模块文件结构按职责拆分.md、docs/adr/0030-提交体量上限放宽到1200行.md |
| 估算 | 1d |

## 目标

将 `backend/app/modules/cutting/service.py`（**真实 1047 行**）拆分为 8 个文件，单文件 ≤400 行：
`CuttingOrderService`（850 行）按方法边界拆 5 个 Mixin；模块级纯函数
`recalc_color`/`recalc_line`/`recalc_order`（REQ-000 核心复用资产）单独成 `service/recalc.py`。

## 范围

**要做**（8 个文件，见设计稿 §2.3）：
- [ ] `service/common.py`：`ZERO`/`_current_version`/`_trim_zeros`(94-126) + `CuttingOrderCommonMixin`（`_bump_header` 496-542、`_editable_order` 544-573、`_readable_order` 575-581、`_reloaded` 482-492、`_locked_graph` 583-585）
- [ ] `service/order_mixin.py`：`create`(137-204)/`patch`(208-239)/`delete`(241-262)/`get`(769-782)/`list_orders`(784-792)/`_assert_style_usable`(796-815)
- [ ] `service/line_mixin.py`：`put_lines`(266-302)/`put_colors`(304-343)/`put_size_lines`(345-382)/`_soft_delete_subtree`(587-616)/`_recalc_all`(618-670)
- [ ] `service/insert_mixin.py`：`_insert_size_line`(672-700)/`_next_size_line_no`(702-717)/`_insert_lines`(817-858)/`_assert_fabric_within_available`(860-888)/`_resolve_stock`(890-900)/`_insert_color`(902-975)
- [ ] `service/ratio_mixin.py`：`suggest_lines`(386-436)/`switch_entry_mode`(438-480)/`_load_ratios`(719-730)/`_style_size_codes`(732-741)/`_write_ratio_snapshot`(743-765)
- [ ] `service/recalc.py`：**复用资产** `recalc_color`(981-994)/`recalc_line`(997-1019)/`recalc_order`(1022-1047)
- [ ] `service/cutting_order_service.py`：`class CuttingOrderService(OrderMixin, LineMixin, InsertMixin, RatioMixin, CommonMixin)` + `__init__`
- [ ] `service/__init__.py`：重导出原 `__all__`（`CuttingOrderService`/`recalc_color`/`recalc_line`/`recalc_order`）
- [ ] 删除原 `service.py`

**不做**：
- 不改业务逻辑、事务边界（`unit_of_work`）、权限校验、数据范围、乐观锁条件 UPDATE、SQL、错误码
- **不新增/删除状态迁移动作**（状态机仍属 T-CUT-001b-3）
- 不改 `repository.py`(316)、`models/`、`schemas/`、`router.py`

## 将要改动的文件

| 文件 | 类型 | 说明 | 预估行数 |
| --- | --- | --- | --- |
| `backend/app/modules/cutting/service/__init__.py` | 新增 | 聚合重导出（含复用资产） | ~60 |
| `backend/app/modules/cutting/service/common.py` | 新增 | 常量/纯助手 + 公共锁与取单 Mixin | ~185 |
| `backend/app/modules/cutting/service/order_mixin.py` | 新增 | 表头建/改/删/读 + 列表 + 款号校验 | ~230 |
| `backend/app/modules/cutting/service/line_mixin.py` | 新增 | 三层批量替换 + 全单重算 | ~250 |
| `backend/app/modules/cutting/service/insert_mixin.py` | 新增 | 明细/颜色/尺码落库与布批校验 | ~255 |
| `backend/app/modules/cutting/service/ratio_mixin.py` | 新增 | 比例带出 + 模式切换 | ~195 |
| `backend/app/modules/cutting/service/recalc.py` | 新增 | `recalc_color`/`recalc_line`/`recalc_order`（REQ-000 复用资产） | ~95 |
| `backend/app/modules/cutting/service/cutting_order_service.py` | 新增 | 组合类 + `__init__` | ~35 |
| `backend/app/modules/cutting/service.py` | 删除 | 原 1047 行文件 | - |

## 实现要点（必读规范）

- [ ] 遵守 docs/03-代码规范.md：事务边界唯一（`unit_of_work`）、`service` 层核心、Repository 只查
- [ ] Mixin 只用于「把一个类拆到多文件」，交叉调用用 `TYPE_CHECKING` 前置声明（照抄 base `style_child_mixin.py`）
- [ ] **`recalc_*` 必须可从包入口导入**（REQ-000：`bundling` 将复用 `recalc_order` 仅改字段映射）：
      `from app.modules.cutting.service import recalc_order` 必须可用；深路径
      `app.modules.cutting.service.recalc` 为额外便利（Q-03）
- [ ] `tests/modules/test_cutting_service.py` 的 `from app.modules.cutting.service import CuttingOrderService, recalc_line` 零改动
- [ ] 保持原 `__all__` 逐字不变；`ZERO`/`_trim_zeros`/`_current_version` 可被同包内引用
- [ ] **Q-02（5 Mixin 边界）为编码前闭环项**，见设计稿 §10

## 验收标准

- [ ] `uv run pytest tests/modules/test_cutting_service.py -q` 全部通过（含 `recalc_line` 负数校验）
- [ ] `uv run pytest tests/modules/test_cutting_service2.py -q` 全部通过（含乐观锁并发）
- [ ] `uv run pytest tests/modules/test_cutting_router.py -q` 全部通过
- [ ] `uv run pytest tests/integration/ -q` 全部通过
- [ ] `uv run pytest tests/ --co -q` 无 `ImportError`
- [ ] **复用资产导入验证**：`from app.modules.cutting.service import recalc_color, recalc_line, recalc_order` 无报错
- [ ] 本次新增 8 个文件单文件 ≤400 行
- [ ] 闸门 1-4 本地预跑通过

## 测试清单

| # | 用例 | 期望 | 结果 |
|---|------|------|------|
| TC-01 | 建单三层 | `create` 后表头五列与三层明细与拆分前一致 | |
| TC-02 | 三条 PUT 全量替换 | 软删旧行 + 插新行、表头汇总落库、返回重读（无 `MissingGreenlet`） | |
| TC-03 | 乐观锁 | 同版本并发写一成一败（`10003`） | |
| TC-04 | 比例带出/模式切换 | `suggest_lines` 同事务写快照、`switch_entry_mode` 清手数需 `confirm` | |
| TC-05 | 汇总纯函数 | `recalc_line` 负数 → `30002`；包入口可导入 | |
| TC-06 | 布批校验 | 超可用量 → `40006` | |

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
| 2026-10-06 | 初版：cutting/service.py(1047) → 5 Mixin + `recalc.py` + 组合，照抄 T-BASE-010c 配方；`recalc_*` 单独成模块保 REQ-000 复用 | AI |
