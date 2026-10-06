# 设计：cutting / system / auth / cli 超长文件拆分（L-081 剩余生产文件）

| 项 | 内容 |
| --- | --- |
| 任务卡编号 | T-CUT-002 / T-SYS-001 / T-AUTH-004 / T-INFRA-005 / T-REFACTOR-001 |
| 模块 | cutting / system / auth / cli（**单卡不跨模块**，见 §8） |
| 编写人 | AI（架构师） |
| 日期 | 2026-10-06 |
| 状态 | 待确认 |
| 关联需求 | docs/requirements/REQ-000-工期优化与复用策略.md（§2 复用资产：`cutting` 的 `recalc_*`） |
| 关联 ADR | docs/adr/0031-模块文件结构按职责拆分.md、docs/adr/0030-提交体量上限放宽到1200行.md |
| 关联设计 | docs/modules/base-重构拆分.设计.md（本系列照抄的配方） |

---

## 1. 背景与目标

**为什么做**：

`docs/12` L-081 的存量清单里，base 已按 ADR-0031 修复到全部 ≤400（models 7 / schemas 6 /
service 15 / router 8 / permissions 13，变更记录 0092~0098）。**剩余 7 个生产文件**仍未处理
（2026-10-06 以 `wc -l` 逐文件核对，本稿只列这 7 个，均为 ADR-0030 硬线「单文件 ≤400 行」的违反项）：

| # | 文件 | 真实行数 | 层级 |
| --- | --- | --- | --- |
| 1 | `app/modules/cutting/models.py` | 696 | models |
| 2 | `app/modules/cutting/schemas.py` | 457 | schemas |
| 3 | `app/modules/cutting/service.py` | 1047 | service |
| 4 | `app/modules/system/service.py` | 869 | service |
| 5 | `app/modules/system/router.py` | 511 | router |
| 6 | `app/modules/auth/service.py` | 424 | service |
| 7 | `app/cli/seed_dicts.py` | 447 | cli（不属 5 层，但同属「单文件」硬线） |

后果与 base 相同：Review 无法一次读完、改动极易引入回归、`__init__.py` 重导出这一已验证的
兼容机制尚未覆盖这些模块。

**成功标准**（本次**收窄**为上述 7 个文件）：

- [ ] 7 个文件按 ADR-0031 改为包（或最小拆分），**每个新增/拆分出的手写文件 ≤400 行**
- [ ] **对外导入面保持不变**：各 `__init__.py` 重导出原有公开名，现有导入零改动
      （唯一的例外见 §2.8）
- [ ] **OpenAPI 零 diff**：`openapi.json` / `schema.d.ts` / `permissions.ts` / `baseDictFields.ts`
      生成后 `git diff --exit-code` 无输出
- [ ] **`alembic check` 零漂移**：`cutting/models/` 拆包后模型注册不变、无表结构变更
- [ ] 全量测试通过（收集无 `ImportError`）、覆盖率不退步
- [ ] 闸门 1-5 全绿（由 T-REFACTOR-001 收口）

**非目标**：

| 非目标 | 理由 |
| --- | --- |
| **Alembic 迁移文件**（`0009` 731、`0005` 567、`0004` 551、`0002` 464、`0008` 407 行） | 历史已应用的迁移**不得重写/拆分**：revision 图、`downgrade` 历史与已落库的 schema 都依赖它们逐字不变。400 行线只对**新增**迁移生效 —— 这是本设计稿约定的**迁移豁免口径**（不改 ADR-0030 结论，只界定其适用范围；见 §10 Q-01） |
| **测试文件**（`test_docs_ddl_sync.py` 745、`test_base_style_service.py` 1787 等） | 另立测试拆分卡，本系列不做。本系列只允许因拆分导致的**测试 monkeypatch 目标路径**修改（见 §2.8 例外） |
| **前端**（`views/base/styles/Detail.vue` 914、`api/base.ts` 1500+） | 前端另议，按 ADR-0030「抽组件」而非包重导出 |
| 不改 API 契约 / 数据库结构 / 错误码 / 权限点 / 枚举值 / 单据号前缀 | 纯代码组织调整；状态迁移、审核/反审核副作用逻辑原样保留（本系列**不新增状态动作**） |
| 不新增业务功能、不「顺手重构」无关文件 | `AGENTS §2.1` |

---

## 2. 拆分策略

> 行数 = `wc -l`；「预估行数」= 迁入代码 + 该文件自身 import/常量/docstring 开销，全部 ≤400。

### 2.1 `cutting/models.py`（696 行）→ 4 领域文件 + `__init__.py`

**真实结构**：`CuttingEntryMode`(76-97) + 两个 PG enum 常量 `DOCUMENT_STATUS`/`CUTTING_ENTRY_MODE`
(109/115) + `CuttingOrder`(118-295) + `CuttingOrderLine`(298-436) +
`CuttingOrderLineColor`(439-544) + `CuttingOrderSizeLine`(547-649) + `CuttingDocNoSequence`(652-696)。

| 新文件 | 职责 | 来源行区间 | 预估行数 |
|--------|------|-----------|---------|
| `models/__init__.py` | 按 `enums → order → lines → sequence` 顺序导出全部公开名（5 模型 + 1 枚举 + 2 常量） | — | ~45 |
| `models/enums.py` | `CuttingEntryMode` + `DOCUMENT_STATUS` + `CUTTING_ENTRY_MODE` | 76-115 | ~55 |
| `models/order.py` | `CuttingOrder`（表头 + `__table_args__` + `lines` relationship） | 118-295 | ~205 |
| `models/lines.py` | `CuttingOrderLine` + `CuttingOrderLineColor` + `CuttingOrderSizeLine`（三层子表） | 298-649 | ~375 |
| `models/sequence.py` | `CuttingDocNoSequence` | 652-696 | ~60 |

**逐字不变**：枚举成员、`__tablename__`、每个 `mapped_column` / `__table_args__` / 约束名 / 索引名与
谓词 / `comment`。**只搬不改**（ADR-0031：逻辑层不变）。跨文件的 `relationship("CuttingOrderLine", ...)`
用字符串目标名，SQLAlchemy 在 mapper 配置期解析，包 `__init__` 导入全部子模块后即可解析。

**`__init__.py` 导入顺序固定为 enums → order → lines → sequence**：order/lines 依赖 enums 的
`CUTTING_ENTRY_MODE`/`DOCUMENT_STATUS`；sequence 只依赖 `Base`/`IdMixin`。

> **验证锚点**：`app/common/models.py::register_all_models` 用 `pkgutil.iter_modules` +
> `importlib.import_module("app.modules.<mod>.models")`；改为包后 import 触发 `__init__`，
> 模型照常注册（base 已验证，无需改 `register_all_models`）。

### 2.2 `cutting/schemas.py`（457 行）→ 3 内容文件 + `__init__.py`

**真实结构**：类型别名与上限常量(47-60)、三层入参(63-187)、出参(200-319)、增量维护入参(322-457)。

| 新文件 | 包含 | 来源行区间 | 预估行数 |
|--------|------|-----------|---------|
| `schemas/__init__.py` | 聚合重导出全部公开名 | — | ~40 |
| `schemas/common_schemas.py` | `StyleNo`/`ColorCode`/`SizeCode` 别名、`MAX_LINES`/`MAX_COLORS_PER_LINE`/`MAX_SIZE_LINES_PER_COLOR`、`Version` | 47-60、322-331 | ~70 |
| `schemas/order_schemas.py` | `SizeLineIn`/`LineColorIn`/`OrderLineIn`/`CuttingOrderCreateIn`；`SizeLineOut`/`LineColorOut`/`OrderLineOut`/`CuttingOrderOut`/`CuttingOrderListOut` | 63-319 | ~290 |
| `schemas/maintenance_schemas.py` | `CuttingOrderPatchIn`/`PutColorsIn`/`PutSizeLinesIn`/`EntryModeSwitchIn`/`SuggestSizeLineOut`/`SuggestLinesOut`/`RatioWarningOut`/`PutLinesIn` | 334-457 | ~160 |

依赖方向：`common_schemas ← order_schemas ← maintenance_schemas`（`maintenance` 引用
`LineColorIn`/`SizeLineIn`/`OrderLineIn`；`order` 引用 `common` 的别名与上限）。

> **更正**：本稿不设 `cutting/schemas.py` 里并不存在的独立「导出/导入模板类」——导出沿用通用 schema，
> 与 base 设计稿的更正口径一致。

### 2.3 `cutting/service.py`（1047 行）→ 5 Mixin + `recalc` + 组合 + `__init__.py`

**真实结构**：模块级 `ZERO`/`_current_version`/`_trim_zeros`(94-126，其中 `__all__` 在 98)；
`class CuttingOrderService`(129-975，`__init__` + 约 30 个方法)；
模块级纯函数 `recalc_color`/`recalc_line`/`recalc_order`(978-1047)。

`CuttingOrderService` 850 行**必须按方法边界拆到多个 Mixin**（照抄 base `StyleService` 6 Mixin 的做法）。
**Mixin 只用于「把一个类拆到多文件」，不承载跨模块通用逻辑。**

| 新文件 | 职责 / 方法（来源行区间） | 预估行数 |
|--------|--------------------------|---------|
| `service/__init__.py` | 重导出原 `__all__`：`CuttingOrderService`/`recalc_color`/`recalc_line`/`recalc_order` | ~60 |
| `service/common.py` | 模块级 `ZERO`/`_current_version`/`_trim_zeros`(94-126) + `CuttingOrderCommonMixin`：`_bump_header`(496-542)、`_editable_order`(544-573)、`_readable_order`(575-581)、`_reloaded`(482-492)、`_locked_graph`(583-585) | ~185 |
| `service/order_mixin.py` | `create`(137-204)、`patch`(208-239)、`delete`(241-262)、`get`(769-782)、`list_orders`(784-792)、`_assert_style_usable`(796-815) | ~230 |
| `service/line_mixin.py` | `put_lines`(266-302)、`put_colors`(304-343)、`put_size_lines`(345-382)、`_soft_delete_subtree`(587-616)、`_recalc_all`(618-670) | ~250 |
| `service/insert_mixin.py` | `_insert_size_line`(672-700)、`_next_size_line_no`(702-717)、`_insert_lines`(817-858)、`_assert_fabric_within_available`(860-888)、`_resolve_stock`(890-900)、`_insert_color`(902-975) | ~255 |
| `service/ratio_mixin.py` | `suggest_lines`(386-436)、`switch_entry_mode`(438-480)、`_load_ratios`(719-730)、`_style_size_codes`(732-741)、`_write_ratio_snapshot`(743-765) | ~195 |
| `service/recalc.py` | **REQ-000 复用资产**：`recalc_color`(981-994)、`recalc_line`(997-1019)、`recalc_order`(1022-1047) | ~95 |
| `service/cutting_order_service.py` | `class CuttingOrderService(OrderMixin, LineMixin, InsertMixin, RatioMixin, CommonMixin)`：仅组合 + `__init__` | ~35 |

**Mixin 边界理由**：按「表头写 / 三层批量替换 / 明细落库 / 比例与模式 / 公共锁与取单」五个主题切；
`_bump_header`（条件 UPDATE 乐观锁）与 `_editable_order`/`_readable_order`/`_reloaded`/`_locked_graph`
被多方调用，归 `common.py`（与 base 把共享助手归 `common` 同一口径）。交叉调用用 `TYPE_CHECKING`
前置声明（照抄 base `style_child_mixin.py`），不在运行期 import 兄弟 Mixin，避免循环。

**`recalc.py` 单独成模块的理由**：REQ-000 §2 明确 `cutting` 三层结构的
`recalc_*` 汇总重算要供 `bundling`（以及 `purchase`/`sales`/`stock` 单据）复用。
单独成文件后，`bundling` 既可 `from app.modules.cutting.service import recalc_order`
（包入口，稳定路径），也可 `from app.modules.cutting.service.recalc import recalc_order`
（深路径）。**保留包入口重导出**，深路径是额外便利。

**`__all__` 逐字保持** `["CuttingOrderService", "recalc_color", "recalc_line", "recalc_order"]`。
`tests/modules/test_cutting_service.py` 直接 `from app.modules.cutting.service import
CuttingOrderService, recalc_line` —— 由 `__init__.py` 重导出命中。

### 2.4 `system/service.py`（869 行）→ 4 内容文件 + 用户 Service 拆分 + `__init__.py`

**真实结构**：模块常量与 `permission_groups`(12-115)；`SystemUserService`(118-537，420 行)；
`SystemRoleService`(540-859，320 行)。`SystemUserService` 单类 420 行，**计入自身 import 即超 400**，
必须按方法边界拆 Mixin。

| 新文件 | 职责（来源行区间） | 预估行数 |
|--------|-------------------|---------|
| `service/__init__.py` | 重导出 `SystemUserService`/`SystemRoleService`/`permission_groups`/`PERMISSION_NAMES`/`MODULE_NAMES`/`logger` | ~35 |
| `service/common.py` | 模块 docstring、`logger`、`DOC_TYPE_USER`/`DOC_TYPE_ROLE`/`ACTION_CREATE`/`ACTION_UPDATE`、`PERMISSION_NAMES`、`MODULE_NAMES`、`MAX_OFFSET`、`_permission_module`、`permission_groups`(12-115) | ~135 |
| `service/user_query_mixin.py` | `list_users`(129-157)、`options`(159-176)、`get_user`(178-181)、`_base_stmt`(125-127)、`_role_codes`(408-422)、`_out`(523-537) | ~120 |
| `service/user_write_mixin.py` | `create_user`(183-226)、`patch_user`(228-270)、`disable_user`(272-318)、`reset_password`(320-347)、`assign_roles`(349-378)、`_revoke_refresh_tokens`(382-394)、`_required`(396-406)、`_require_roles_exist`(424-438)、`_role_ids_by_code`(440-447)、`_replace_user_roles`(449-455)、`_duplicate_user`(517-521) | ~240 |
| `service/user_guard_mixin.py` | `_guard_has_operator`(457-515) | ~80 |
| `service/user_service.py` | `class SystemUserService(UserQueryMixin, UserWriteMixin, UserGuardMixin)` + `__init__` | ~35 |
| `service/role_service.py` | `SystemRoleService` 整体(540-859) | ~355 |

`SystemUserService` 组合继承三个 Mixin，**对外类型名与构造签名保持不变**（`SystemUserService(session, ctx)`）。
交叉调用同样用 `TYPE_CHECKING` 前置声明。`SystemRoleService`（320 行）单文件即可放下。

**`__all__` 逐字保持**（`MODULE_NAMES`/`PERMISSION_NAMES`/`SystemRoleService`/`SystemUserService`/
`logger`/`permission_groups`），由 `__init__.py` 全量重导出。

### 2.5 `system/router.py`（511 行）→ 4 子 router + `deps` + `__init__.py`

**真实结构**：**18 个端点**（任务卡背景写的「20」与实际不符，以本稿核实为准）：
用户 9、角色 6、内置库恢复 2、权限点 1。

| 新文件 | 内容（来源行区间） | 预估行数 |
|--------|-------------------|---------|
| `router/__init__.py` | 组装**单个** `router = APIRouter(prefix="/system", tags=["系统管理"])`，按源码顺序 include 4 个子 router；重导出 `router` | ~45 |
| `router/deps.py` | `SessionDep`/`ContextDep`/`SYSTEM_TAGS`/`_require`/`_users`/`_roles`(53-78) | ~55 |
| `router/user_router.py` | 用户 9 端点(84-280)：列表 / 候选 / 新建 / 详情 / 局部更新 / 分配角色 / 停用 / 启用 / 重置口令 | ~215 |
| `router/role_router.py` | 角色 6 端点(286-417)：列表 / 候选 / 新建 / 局部更新 / 替换权限 / 停用 | ~150 |
| `router/restore_router.py` | 内置库 2 端点(423-486)：缺失清单 / 恢复 | ~85 |
| `router/permission_router.py` | 权限点 1 端点(492-508) | ~40 |

**硬约束**：`app/main.py:68` 为 `from app.modules.system.router import router as system_router`，
因此 `__init__.py` **必须导出名为 `router` 的单个 `APIRouter`**（不能导出列表）。
`include_router` 顺序 = 源码顺序：`user → role → restore → permission`；各子 router 内保持源码顺序
（`/users/options` 必须在 `/users/{user_id}` 之前，源码本就如此）。逐行对齐 `x-permission`，
**运行时权限码不得改变**。

### 2.6 `auth/service.py`（424 行）→ 4 内容文件 + 组合 + `__init__.py`

**真实结构**：常量与数据类(58-85)；`AuthService`(88-421) 含查询/锁定/登录/令牌/改口令五组方法；
模块级 `build_lock_key`(422-424)。

| 新文件 | 职责（来源行区间） | 预估行数 |
|--------|-------------------|---------|
| `service/__init__.py` | 重导出 `AuthService`/`IssuedTokens`/`LoginOutcome`/`MAX_LOGIN_FAILURES`/`LOCK_MINUTES`/`build_lock_key` | ~45 |
| `service/common.py` | 模块 docstring、`logger`、`MAX_LOGIN_FAILURES`/`LOCK_MINUTES`/`_LOCK_KEY_PREFIX`(58-66)、`IssuedTokens`(69-77)、`LoginOutcome`(79-85)、`build_lock_key`(422-424) | ~75 |
| `service/read_mixin.py` | 查询 + 锁定助手：`get_active_user`/`get_role_ids`/`get_roles`/`get_allowed_workshop_ids`(96-139)、`is_login_locked`/`_count_recent_failures`/`_register_failure`/`_clear_failures`(143-177) | ~150 |
| `service/login_mixin.py` | `authenticate`(181-258)、`_find_user_by_employee_no`(260-263)、`_record_login`(265-288)、`change_password`(382-419) | ~230 |
| `service/token_mixin.py` | `_issue_tokens`(292-321)、`refresh`(323-354)、`logout`(356-374)、`revoke_all_tokens`(376-378) | ~140 |
| `service/auth_service.py` | `class AuthService(ReadMixin, LoginMixin, TokenMixin)` + `__init__` | ~35 |

这是本系列**最小拆分**（424 仅越线 24 行）：按「查询/锁定」「登录/改口令」「令牌」三组方法边界切，
`common.py` 承载常量与两个 `dataclass`（`IssuedTokens`/`LoginOutcome` 被登录与令牌两侧共用）。

> **例外**：`tests/modules/test_auth_service.py` 有两处
> `monkeypatch.setattr("app.modules.auth.service.redis_get_int", ...)`（第 76/77/109 行）。
> 改包后 `redis_get_int`/`redis_incr_with_ttl` 绑定在**锁定助手所在子模块**（`read_mixin.py`），
> 包 `__init__` 上的重导出**无法**让 monkeypatch 作用到子模块的运行时名字。**这是本系列唯一
> 无法靠重导出保住的调用方**，必须把那三行 patch 目标改为
> `app.modules.auth.service.read_mixin.redis_get_int` / `..._incr_with_ttl`。详见 §2.8。

### 2.7 `cli/seed_dicts.py`（447 行）→ 数据 / 写入 / 校验 三文件 + `__init__.py`

**真实结构**：`BUILTIN_*` 数据常量(41-125)、`_SEED_OPERATOR`(128)、`DICT_DOC_TYPES`(136-145)、
墓碑与写入函数(148-382)、`check_dict_library`(385-447)。

| 新文件 | 职责（来源行区间） | 预估行数 |
|--------|-------------------|---------|
| `seed_dicts/__init__.py` | 重导出全部公开名（6 组 `BUILTIN_*` + `DICT_DOC_TYPES` + `tombstoned_codes` + 6 个 `seed_*` + `seed_dict_library` + `check_dict_library`） | ~60 |
| `seed_dicts/builtin_data.py` | `BUILTIN_COLORS`/`BUILTIN_SIZES`/`BUILTIN_SIZE_GROUPS`/`BUILTIN_PRODUCT_CATEGORIES`/`BUILTIN_UOM_UNITS`/`BUILTIN_MATERIAL_CATEGORIES`、`_SEED_OPERATOR`、`DICT_DOC_TYPES`(41-145) | ~120 |
| `seed_dicts/seeders.py` | `tombstoned_codes`/`_skip_tombstoned`/`seed_dict_library`/`seed_colors`/`seed_sizes`/`seed_size_groups`/`seed_product_categories`/`seed_uom_units`/`seed_material_categories`(148-382) | ~280 |
| `seed_dicts/checker.py` | `check_dict_library`(385-447) | ~95 |

**数据与逻辑分离**：`builtin_data.py` 只有数据常量（业务方评审对象），`seeders.py` 只放写入逻辑；
`seeders.py` 从 `builtin_data` 导入数据与 `DICT_DOC_TYPES`；`checker.py` 从 `builtin_data` 导入数据。
`system/restore.py` 依赖的 `DICT_DOC_TYPES`/`seed_dict_library` 由包 `__init__.py` 重导出，
**`app.modules.system.restore` 一行不用改**。

### 2.8 对外导入面保持不变（兼容策略，逐条核实）

各包 `__init__.py` 重导出原有公开名，使现有导入零改动。经代码核对，必须保住的外部导入：

| 导入方 | 从何处导入 | 必须保住的名字 |
|--------|-----------|---------------|
| `cutting/repository.py` | `cutting.models` | `CuttingOrder`/`CuttingOrderLine`/`CuttingOrderLineColor`/`CuttingOrderSizeLine` |
| `cutting/schemas.py` | `cutting.models` | `CuttingEntryMode` |
| `cutting/service.py` | `cutting.models` | `CuttingEntryMode` + 上述 4 模型 |
| `app/core/numbering.py` | `cutting.models` | `CuttingDocNoSequence` |
| `cutting/router.py` | `cutting.schemas` | `CuttingOrderCreateIn`/`CuttingOrderListOut`/`CuttingOrderOut`/`CuttingOrderPatchIn`/`EntryModeSwitchIn`/`PutColorsIn`/`PutLinesIn`/`PutSizeLinesIn`/`SuggestLinesOut` |
| `cutting/service.py` | `cutting.schemas` | `CuttingOrderCreateIn`/`CuttingOrderPatchIn`/`EntryModeSwitchIn`/`OrderLineIn`/`PutColorsIn`/`PutLinesIn`/`PutSizeLinesIn`/`SuggestLinesOut`/`SuggestSizeLineOut` |
| `tests/factories/cutting.py` | `cutting.schemas` | `CuttingOrderCreateIn`/`LineColorIn`/`OrderLineIn`/`SizeLineIn` |
| `tests/modules/test_cutting_router.py` | `cutting.schemas` | `OrderLineIn`/`CuttingOrderCreateIn`（函数内延迟导入） |
| `tests/modules/test_cutting_service.py` | `cutting.schemas` / `cutting.service` | 4 入参 schema；`CuttingOrderService`/`recalc_line` |
| `tests/modules/test_cutting_service2.py` | `cutting.schemas` / `cutting.service` / `cutting.models` | 7 schema；`CuttingOrderService`；4 模型 + `CuttingEntryMode`（函数内延迟） |
| `tests/modules/test_cutting_tables.py` | `cutting.models` | 4 模型 |
| `tests/e2e_seed.py` | `cutting.models` | 4 模型 |
| `app/main.py` | `cutting.router` | `router`（单个） |
| **REQ-000 未来 `bundling`** | `cutting.service`（或 `cutting.service.recalc`） | `recalc_color`/`recalc_line`/`recalc_order`（**核心复用资产，必须包入口可导入**） |
| `system/router.py` | `system.service` | `SystemRoleService`/`SystemUserService`/`permission_groups` |
| `system/service.py`（现）→ 拆分后 | `base.service` | `write_document_log`（base 已重导出；本系列不改） |
| `app/main.py` | `system.router` | `router`（单个） |
| `app/core/permissions.py` / `auth/router.py` / `tests/conftest.py` | `auth.service` | `AuthService` |
| `tests/modules/test_auth_service.py` | `auth.service` | `LOCK_MINUTES`/`MAX_LOGIN_FAILURES`/`AuthService`/`build_lock_key` |
| `app/cli/seed_baseline.py` | `cli.seed_dicts` | 6 组 `BUILTIN_*` + `check_dict_library` + `seed_dict_library` |
| `app/cli/restore_builtin.py` | `cli.seed_dicts` | `BUILTIN_COLORS`/`BUILTIN_SIZE_GROUPS`/`BUILTIN_SIZES`/`DICT_DOC_TYPES`/`seed_dict_library` |
| `app/modules/system/restore.py` | `cli.seed_dicts` | `DICT_DOC_TYPES`/`seed_dict_library` |
| `tests/modules/test_system_restore.py` | `cli.seed_dicts` | `BUILTIN_COLORS` |
| `tests/modules/test_material_tables.py` | `cli.seed_dicts` | `BUILTIN_MATERIAL_CATEGORIES`/`seed_material_categories` |
| `tests/modules/test_seed_cli.py` | `cli.seed_dicts` | `seed_dict_library`（函数内延迟） |

**⚠️ 无法用重导出保住、必须改调用方的点（全系列唯一一处）**：

| 位置 | 现状 | 拆分后必须改成 | 归属卡 |
|------|------|---------------|--------|
| `tests/modules/test_auth_service.py:76,77,109` | `monkeypatch.setattr("app.modules.auth.service.redis_get_int", _always_none)` 与 `...redis_incr_with_ttl` | `app.modules.auth.service.read_mixin.redis_get_int` / `app.modules.auth.service.read_mixin.redis_incr_with_ttl` | T-AUTH-004 |

> 原因：`monkeypatch.setattr("模块路径.属性", v)` 解析到的是**该模块对象**上的属性。改包后这两个名字绑定在
> 锁定助手子模块（`read_mixin`）的命名空间里；包 `__init__` 即使重导出同名对象，patch 包属性也
> **不会影响**子模块运行时查找的那个名字，测试会退化为「仍打真实 Redis」而失败。
> 这是「重导出兼容」的边界，如实登记，不粉饰。

---

## 3. 领域模型

**不适用（理由：数据库表结构不变，本次仅代码组织调整；`cutting/models/` 拆包后字段/约束/索引逐字不变）。**

---

## 4. 接口清单

**不适用（理由：OpenAPI 契约不变，仅内部导入路径与路由模块拆分；由各卡的「OpenAPI 零 diff」验收项校验）。**

---

## 5. 并发与一致性

**不适用（理由：纯代码重组，无新增并发风险。`_bump_header` 条件 UPDATE 乐观锁、唯一索引、
`FOR UPDATE` 锁序等原样迁移，逻辑不变更）。**

---

## 6. 前端设计

**不涉及前端变更**，仅在收口卡重跑 `pnpm generate:api` 以确认契约零 diff。

---

## 7. 风险与回滚

| 风险 | 影响 | 缓解 | 回滚方式 |
|------|------|------|---------|
| 导入路径破坏 | 引用这些模块的代码/测试报错 | 各 `__init__.py` 统一重导出（§2.8）；分批提交、每卡跑定向测试 | `git revert` 单次提交 |
| 循环依赖 | 启动失败 | 依赖方向单一（models→schemas→service；`common←各 Mixin←组合`）；`__init__.py` 只聚合不放逻辑 | 按 §8 顺序串行拆分 |
| 路由匹配/顺序被破坏 | 端点 404 或被遮蔽 | system 子 router 按源码顺序 include；子路由内保持 `/options` 先于 `/{id}` | `git revert` |
| 模型关系解析失败 | `alembic check` 报错/注册不到表 | `models/__init__.py` 固定导入顺序；`alembic check` 零漂移验收 | `git revert` |
| auth 测试 monkeypatch 失效 | `test_auth_service.py` 退化为打真 Redis | 按 §2.8 改 patch 目标（T-AUTH-004 卡内） | 同提交内修完 |
| 拆 Mixin 改变方法解析顺序 | 运行期行为变化 | 方法名互不重叠，组合类只 `__init__`；全量测试 | `git revert` |

**迁移回滚**：无数据库迁移，纯代码重构。
**发布回滚**：镜像回退上一 tag 即可。

---

## 8. 任务拆分（按依赖顺序串行；单卡不跨模块）

| 任务卡 | 描述 | 依赖 | 改动的生产文件 | 验收锚点 |
|--------|------|------|---------------|----------|
| T-CUT-002a | `cutting/models.py`(696) → `models/` 包（5 文件） | 无 | 5 | `alembic check` 零漂移 |
| T-CUT-002b | `cutting/schemas.py`(457) → `schemas/` 包（4 文件） | 002a | 4 | OpenAPI 零 diff |
| T-CUT-002c | `cutting/service.py`(1047) → `service/` 包（8 文件，含 `recalc.py`） | 002a、002b | 8 | 定向测试 + 复用资产可导入 |
| T-SYS-001a | `system/service.py`(869) → `service/` 包（7 文件） | 无 | 7 | `test_system_router.py` |
| T-SYS-001b | `system/router.py`(511) → `router/` 包（6 文件） | SYS-001a | 6 | OpenAPI 零 diff |
| T-AUTH-004 | `auth/service.py`(424) → `service/` 包（6 文件）+ 改 monkeypatch | 无 | 6 + 测试 3 行 | `test_auth_service.py` |
| T-INFRA-005 | `cli/seed_dicts.py`(447) → `seed_dicts/` 包（4 文件） | 无 | 4 | `test_seed_cli.py` |
| T-REFACTOR-001 | 收口：全量测试 + 闸门 1-5 + OpenAPI 零 diff | 以上全部 | `docs/12` | 5 闸门全绿 |

**串行执行序**：`CUT-002a → CUT-002b → CUT-002c → SYS-001a → SYS-001b → AUTH-004 → INFRA-005
→ REFACTOR-001`。
理由：`AGENTS §6` 要求单会话单模块；`cutting` 三卡有内部依赖（models→schemas→service），
`system` 两卡有内部依赖（service→router）；`auth`、`cli` 与其它模块无生产依赖，放在 cutting/system
之后以缩短依赖链。

---

## 9. 测试计划

| 层级 | 用例 | 断言 |
|------|------|------|
| 收集 | `pytest --co -q` 全仓 | 无 `ImportError`/`ModuleNotFoundError` |
| 单元 | cutting：`test_cutting_service.py`/`test_cutting_service2.py` | 行为与拆分前完全一致；`recalc_line` 仍可从包入口导入 |
| 单元 | cutting 表结构：`test_cutting_tables.py`/`test_docs_ddl_sync.py` | `alembic check` 零漂移、枚举值域不变 |
| 接口 | cutting：`test_cutting_router.py` | 状态码、响应结构、权限码、路由匹配顺序不变 |
| 接口 | system：`test_system_router.py` | 18 端点可达、`x-permission` 不变、数据范围拒绝 `12002` |
| 单元/接口 | auth：`test_auth_service.py`/`test_auth_router.py` | 锁定降级路径（改 patch 目标后）仍通过；改口令/刷新不变 |
| 单元/接口 | cli/system：`test_seed_cli.py`/`test_material_tables.py`/`test_system_restore.py`/`test_cli_entrypoint.py` | seed 幂等、墓碑跳过、`--check` 数量不变 |
| 契约 | 重跑 `generate:api` | `openapi.json`/`schema.d.ts`/`permissions.ts`/`baseDictFields.ts` 零 diff |
| 全量 | `pytest -q --cov=app --cov-fail-under=80` | 全绿、覆盖率不退步 |

> **本系列无新增业务用例**（ADR-0031：拆分类改动只验证「重导出命中 + 契约零 diff + 行为不变」）。
> 既有并发用例（`test_cutting_service2.py` 的乐观锁、`tests/integration/`）原样复跑即可。

---

## 10. 待决问题

| # | 问题 | 需要谁确认 | 阻塞什么 | 状态 |
|---|------|-----------|---------|------|
| Q-01 | 「历史已应用迁移不纳入 400 行拆分」是否需要独立 ADR，还是作为本设计稿的非目标约定即可 | 架构师 / 业务 | 是否新增 ADR | 本稿按**设计稿约定**处理（ADR-0031 已覆盖模块代码；迁移是历史不可变物，重写会破坏 revision 与 downgrade 历史）。**若业务要求独立决策记录，再新开 ADR**（不擅自新增） |
| Q-02 | `cutting/service.py` 的 5 Mixin 边界（表头写 / 批量替换 / 明细落库 / 比例模式 / 公共锁）是否为最终方案 | 架构师 | T-CUT-002c 文件清单 | 已确认：采纳 5 Mixin + `recalc.py` + 组合类（照 T-BASE-010c 配方），T-CUT-002c 已落地 |
| Q-03 | `recalc_*` 除包入口外是否同时承诺 `app.modules.cutting.service.recalc` 深路径稳定 | 架构师 | 未来 `bundling` 导入写法 | 已确认：**包入口为契约**（`from app.modules.cutting.service import recalc_order`），深路径不承诺；`bundling` 设计稿据此写 |
| Q-04 | `system/router.py` 实际 18 端点（非卡面所写 20）是否影响任何验收 | 架构师 | T-SYS-001b | 本稿以核实为准；端点集合逐字不变即可，数字仅背景描述 |
| Q-05 | 是否同步修订 L-081 的存量清单（把这 7 个文件标记为「已拆」） | 架构师 | docs/12 归档 | 建议在 T-REFACTOR-001 收口时更新 L-081（本系列只加 0101 变更行，不动 L-081 结论） |

> **有阻塞性待决问题时不得开始编码。** 本稿 Q-01 已给出不阻塞的默认口径；Q-02/Q-03 为 T-CUT-002c
> 编码前必须闭环项（但均为架构师在实现时按本稿决策即可，不依赖业务方）。

---

## 11. 复用资产（REQ-000 §2）

| 资产来源 | 复用方式 | 本系列动作 |
|---------|---------|-----------|
| `cutting` 三层结构：`recalc_color`/`recalc_line`/`recalc_order` | `bundling`/`purchase`/`sales`/`stock` 单据汇总重算 | **核心**：单独成 `service/recalc.py`，包入口重导出，路径稳定 |
| `cutting` 三层明细全量替换、乐观锁 UPDATE、行余量、软删 | 上游单据模块 | 原样迁入 `line_mixin.py`/`common.py`（Mixin），逻辑不变 |
| `base` 的 `write_document_log`/`DictService` 等 | 所有模块 | `system/service.py` 继续从 `base.service` 包入口导入，**不改** |
| `base` 的包重导出兼容配方（T-BASE-010a~g） | 本系列 7 个文件 | **照抄**：`__init__.py` 重导出、单文件 ≤400、OpenAPI 零 diff |

---

## 12. 变更记录

| 日期 | 变更内容 | 操作人 |
|------|---------|--------|
| 2026-10-06 | 初版：cutting/system/auth/cli 超长文件拆分设计（覆盖 L-081 剩余 7 个生产文件；迁移/测试/前端列非目标）。照抄 base 拆分配方（ADR-0030/0031） | AI |
