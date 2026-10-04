# T-WEB-005：基础资料页（组织 / 字典 / 工序）

| 项 | 内容 |
| --- | --- |
| 模块 | web |
| 负责人 | AI |
| 状态 | `done`（2026-10-04） |
| 优先级 | P0 |
| 依赖 | T-WEB-004, T-BASE-001 |
| 被依赖 | T-WEB-006 |
| 关联设计 | [docs/modules/00-P0地基.设计.md](../modules/00-P0地基.设计.md) §4.4、§6.1 |
| 关联 ADR | ADR-0025（删除两分支）、ADR-0020（分类） |
| 估算 | 1d |

## 目标

把 9 个主数据资源的页面一次做齐，每个列表页都是 06 §2.2 的标准三段式，并且**字典删除的两分支提示**（引用计数）与「恢复内置库」按钮真实可用。

## 实现前的调查结论（2026-10-03，AI 动手前先搜了代码）

后端侧**已经齐了**：9 个资源 × 8 个端点（`list` / `create` / `get` / `patch` /
`disable` / `delete` / `options` / `exports`）全部在 `openapi.json` 里，
`DictRow.ref_count` 与 `delete` 的 422（引用被占用）都有。所以这张卡是纯前端。

但有三件事必须**先定口径**，否则做出来的东西会立刻返工：

### ① `restore_builtin` 的 HTTP 端点 —— **已补**（2026-10-03，`96a6a9a`）

原本只有 CLI（`app/cli/restore_builtin.py`），而那个文件自己的注释写着
"用户点『恢复内置库』" —— 设计时就预期有界面，那一层一直没做。
现在有了两个端点（权限点 `system:config:manage`）：

| 端点 | 用途 |
| --- | --- |
| `GET /api/v1/system/dicts/builtin-missing` | 缺失清单 —— 按钮的**前置提示**（先告诉用户"少了 3 个颜色"再让他确认） |
| `POST /api/v1/system/dicts/builtin-restores` | 恢复。body `{permissions?, roles?, dicts?}`，`dicts` 默认 `true`（这是那个按钮的语义）；三类都不勾返回 `10002` 而不是静默"恢复 0 条" |

⚠️ 三个做前端时必须知道的语义：

1. **只恢复内置项**。用户自建的字典项删掉就是删掉了 —— `seed_colors` 只重建
   `BUILTIN_COLORS` 清单里的编码，而墓碑机制（ADR-0025 §决策 3）正是为此。
   按钮的文案要写"恢复**内置**库"，别写成"找回删除的数据"。
2. **墓碑顺序敏感**：先解除墓碑、再 seed。反了会出现"日志说恢复了、数据没回来"。
   已经接的是 CLI 的同一组函数，顺序由那边保证，别在调用侧重排。
3. **`operator_name` 是真实用户**。CLI 调用时那里没有用户上下文，落的是随机 UUID +
   `restore_builtin`；接口层传真实 ctx。所以界面可以放心显示"张三 恢复了内置库"。

### ② 前端不要手写 72 个函数 —— **已定稿**（`97694e2` / `88638a7`）

后端已经用**声明式注册表**解决了同构问题（`app/modules/base/resources.py::RESOURCES`，
注释写得很清楚：「九个资源的 CRUD 形状完全同构… 写成九份重复的 handler 意味着同一条
业务规则要改九遍」）。前端若照抄成 9 × 8 = 72 个函数，就是**把同一个错误换个语言
再犯一遍** —— 改一个路径要改 9 处，漏一处就是那个资源静默 404。

**定稿结论**：采纳建议，且比建议更严一档。

| 层 | 谁说了算 | 内容 |
| --- | --- | --- |
| 契约 | **后端生成物** `BASE_DICT_CONTRACT` | 路径参数列、写权限、**必填字段**、字段类型、后端默认值、真删还是软删、引用检查器 |
| 界面 | **前端注册表** `api/base.ts::RESOURCE_REGISTRY` | 中文名、表格列、筛选项、占位提示、枚举中文映射 |

`api/base.ts` 由 `makeResourceApi(key)` **一个工厂**生成 7 个端点封装（不是 63 个函数），
九个资源各一行 `baseApi[资源]`。

**刻意不写进注册表的开关**（写进去就是第二份真相）：
- 内置角标 / 「恢复内置库」按钮 ← 契约的 `hasBuiltinFlag`
- 删除确认框说真删还是软删 ← `allowPhysicalDelete`
- 「引用」列 ← 每行的 `ref_count`（`null` 显示 `—`）

**一致性守卫（三层，缺一层就又出现「9 份真相」）**：
1. `pydantic WRITE_MODELS` →（生成器）→ `BASE_DICT_CONTRACT`
2. `backend/tests/modules/test_base_resource_registry.py`：生成物**逐字** == 后端模型；
   注册表 key 集合 == `RESOURCES` − **显式登记**的待做资源（`customers`：端点齐全但
   不在本卡九页范围内）。三条都用「故意写坏」反验过确实报红。
3. `packages/admin/src/api/base.test.ts`：`BaseColumn.name` 的类型是 `keyof BaseDictRow`
   （列名打错**编译就红**）；`validateDecl` 抓漏字段 / 拼错的筛选项 / 没候选来源的 combo。

⚠️ 注册表**不能从 `openapi.json` 推导**（原判断成立）：那里面不包含"这个资源前端要显示
哪几个字段"。但**必填清单也不能在前端推导** —— 它既不是界面口径，也不是 `DictOut` 的
属性，只能来自写入模型，于是交给生成器。

### ③ `DictOut` 是"一个大模型 + 大量可空字段" —— **已定稿**（`97694e2` / `8d61dc6`）

后端注释里明说了这个取舍：「用一个大模型换来的是每个响应都多出七八个 null 字段…
换来的是新增资源不用改这个类」。所以前端拿到的行类型**大部分字段是 `T | null`**。

**定稿结论**：必填字段清单**不放前端**，由后端出、经生成器落成
`packages/shared/src/enums/baseDictFields.ts` 的 `create.required` / `patch.required`。

理由（比"少写 9 份"更要紧）：前端另抄一份就是 9 份真相，而后端给某个模型加一个必填
字段时**前端不会有任何提示** —— 用户填完第三页表单提交才被 `10001` 拒，那时他已经
填了两页。表单的红星与校验规则直接由契约驱动；前端注册表只补"这个字段叫什么中文名、
用什么控件"（后端不可能知道的部分）。

连带的两个决定：
- **后端默认值也进契约**（`field.default`）。表单的 Switch / InputNumber 总得有个初值，
  而"初值 = 后端默认"是条业务口径（`is_piecework` 默认 `true`）。前端自己写一份的话，
  后端改成 `false`、前端还停在 `true`，用户不动开关直接提交，存进去的值与"什么都不填"
  **不同**，而界面看不出异常。
- **编辑态可改字段 = `patch.fields` 减去 `version`**。不是"create 字段减去编码列"——
  两者差集里有真东西：`WorkshopGroupPatch` 里**没有** `workshop_id`，组别的所属车间
  建后不可改。前端按前者渲染的话，用户能改车间，提交被 `extra=forbid` 拒（10001），
  界面上看不出任何异常。（这条是写守卫时被断言抓出来的。）

⚠️ **生成物为什么长成「一个 JSON 对象字面量」**：第 2 层守卫要断言"生成物 == 后端模型"。
若发的是普通 TS 字面量（key 不带引号、带 `as const`），那个断言只能靠正则解析 TS ——
而正则解析生成物正是本仓已踩过的坑（`permissions.ts` 守卫改个引号风格就悄悄少一项，
少一项的表现是「按钮该隐藏却还显示」= 越权）。所以给该文件加了 prettier 覆盖
（`singleQuote:false` / `quoteProps:preserve` / `trailingComma:none`），对象体成为合法
JSON，`json.loads` 直接可用，守卫不依赖任何正则。

### 其余已就绪、不需要再决策的

| 项 | 现状 |
| --- | --- |
| `usePageList` / `TableToolbar` / `PageLayout` / `EmptyState` / `Combo` / `confirmDanger` | T-WEB-003 / T-WEB-004 已交付并测过，直接复用 |
| 路由与菜单 | `layouts/menu.ts` 的「基础资料」组已有「款号」一项（占位页），加 9 项即可 |
| 导出行数与列表一致 | 后端 `exports` 与 `list` 共用同一筛选与 service 方法（`DictService.export_rows` → `list_rows`），前端只需把当前 `query` 原样传过去 |
| 字典删除两分支 | 后端 `DictService.delete` 已实现两分支（ADR-0025），422 携带 `references[]` |

### 动手时才发现的三个缺口（已登记 docs/12 §5）

| # | 缺口 | 后果 | 本卡的处理 |
| --- | --- | --- | --- |
| A | **基础资料没有「启用」端点**（`DictOut` 无 `is_active` 可写，`*Patch` 里也没有） | 停用之后**再也启不回来** | 界面不给「启用」按钮（不给一个点了就 404 的入口）。登记 L-060，建议后端补 `POST /{key}/{code}/enables` |
| B | **`DictOut` 里没有 `items`** | 码表成员**读不回来**，而 `replace_size_group_items` 是 delete + 批量 insert | 编辑态**不渲染**成员编辑器（渲染空编辑器 = 给用户一个"一保存就清空全表"的陷阱），并在页面上写明原因。登记 L-061 |
| C | **`warehouse_type` 是自由文本**（DB 无 enum，后端只在 description 里列了三个值） | 做不了真下拉 | 给文本框 + 占位提示，**不编一个下拉去限定取值**（AGENTS §2.3 不许自行发明业务规则）。登记 L-062 |

## 范围

**要做**（每资源固定 `List.vue` / `Form.vue` 两件套，`warehouses` 等简单资源可省 `Detail`）：
- [x] `src/api/base.ts`：9 资源 × (list / options / create / patch / disable / delete / exports) 封装
      —— **实现方式与原计划不同**：由 `makeResourceApi(key)` 一个工厂生成，不写 63 个函数（缺口②）
- [x] 组织类：`workshops`、`workshop-groups`、`warehouses`、`uom-units`、`product-categories`
- [x] 字典类：`colors`、`sizes`、`size-groups`
- [x] 工序类：`operations`
- [x] 统一交互：筛选（关键字 + 状态，"更多条件"展开）、表格、分页、**导出按钮（与「新建」相邻，06 §9.1）**
- [x] 字典页额外能力：
  - 列表显示 `ref_count` 引用计数（不参与引用检查的资源显示 `—`）
  - 删除：引用 0 → 二次确认「**不可撤销**」；引用 >0 → 菜单项**禁用且写明原因** + 引导改用停用
  - 「恢复内置库」按钮（先查缺失清单 → 二次确认 → 只恢复字典）
  - `is_builtin` 显示「内置」角标
- [x] `operations` 页额外能力：显示引用计数；`operation_no` 编辑时 `disabled`；删除被引用 → `20003` 引导停用
- [x] `size-groups` 页：`items[]` 有序尺码编辑（**上移 / 下移**而非拖拽，理由见 `OrderedSizeItems.vue` 注释），
      保存全量替换；**编辑态不渲染**（缺口 B，`DictOut` 无 `items` 读不回来，渲染空编辑器会清空成员）

**不做**：
- 不做 Excel **导入**（三步式，P1 起）
- 不写款号与单价页（T-WEB-006）

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `frontend/packages/shared/src/enums/baseDictFields.ts` | **生成** | 九资源的写入契约（缺口③的根部） |
| `frontend/scripts/generate-frontend-contract.mjs` | 修改 | 第三个产物：后端 `WRITE_MODELS` → 上面那个文件 |
| `frontend/packages/shared/src/api/client.ts` | 修改 | `download()`：二进制导出（`send()` 无条件解包 JSON，xlsx 必然失败） |
| `frontend/packages/admin/src/api/base.ts` | 新增 | 注册表 + `makeResourceApi` 工厂 + `validateDecl` |
| `frontend/packages/admin/src/composables/useExport.ts` | 新增 | 导出（筛选摘要文件名 + revoke object URL） |
| `frontend/packages/admin/src/views/base/ResourceList.vue` | 新增 | **九页共用的**列表页（三段式 / 四态 / 删除两分支 / 恢复内置库） |
| `frontend/packages/admin/src/views/base/ResourceForm.vue` | 新增 | **九页共用的**表单（必填由契约驱动 / 10003 提示刷新） |
| `frontend/packages/admin/src/views/base/OrderedSizeItems.vue` | 新增 | 码表有序尺码成员编辑器 |
| `frontend/packages/admin/src/views/base/*/{List,Form}.vue` | 新增 | 九资源各两个 15~20 行薄壳（docs/03 §2.5 的目录约定） |
| `frontend/packages/admin/src/router/index.ts` | 修改 | 由注册表生成 27 条路由（`import.meta.glob`，**不是**带变量的 `import()`） |
| `frontend/packages/admin/src/layouts/menu.ts` | 修改 | 基础资料组 +9 项（**刻意手写**，与路由不共用数据） |
| `docker-compose.ci.yml` | 修改 | 闸门 3 挂前端源码，否则跨语言守卫在容器里形同虚设 |

> ⚠️ 原计划里的 `usePageList.ts` **不需要新建** —— T-WEB-004 已交付并测过，直接复用。

## 实现要点（必读规范）

- [x] 遵守 docs/06 §2.2（列表页标准结构）、§2.4（表单：`labelCol 6`、`blur` 校验、`InputNumber` 精度、提交 loading 防重复）、§5（四态与危险确认）、§8（UI 走查清单）、§10（Combo 强制）
- [x] 遵守 docs/05 §9.1（导出：与列表同一筛选、`.xlsx`）、§9.5（候选搜索）
- [x] 遵守 docs/modules/01 §4（`disable` 必填原因、`delete` 两分支、`operations.operation_no` 不可改）
- [x] 遵守 docs/03 §2.1（禁 `any`、禁 `console`）

## 验收标准

- [x] 9 个资源页面四态齐全 + 导出可用 + Combo 搜索可用
- [x] 字典删除：引用 0 → 真删并提示不可撤销；引用 >0 → **不给可点的删除入口**（菜单项禁用 + 写明原因，只留停用）
- [x] 「恢复内置库」按钮二次确认后调通（且只传 `dicts`）
- [x] 停用弹必填原因；后端 `10003` 时给「刷新为最新内容」按钮（不静默失败、不覆盖对方结果）
- [x] 导出行数与当前列表一致（同一筛选；`exports` 与 `list` 共用后端同一 service 方法）
- [x] 需要选主数据的字段（`workshop_id`、码表尺码成员）全部用 `Combo`，**无裸 `<select>`**
- [x] `pnpm lint` / `pnpm format:check` / `pnpm typecheck` / `pnpm test:unit` 全绿；组件测试 ≥1 个/新组件
- [x] 5 道闸门（`scripts/gate.sh`）在**容器与宿主机两种模式**下全绿

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-W24 | 字典被引用时点删除 | 不发 DELETE 请求，引导停用 | ✅ 通过（菜单项 `disabled` + 文案含「不能删除…请改为停用」） |
| TC-W25 | 字典无引用时点删除 | 二次确认「不可撤销」→ 成功后行消失 | ✅ 通过（确认框含「不可撤销」；`remove('NVY')` 被调；列表重拉） |
| TC-W26 | 导出 | 下载 `.xlsx`，行数 == 列表 `total` | ✅ 通过（文件名 `颜色_藏青_<时间戳>.xlsx`；导出请求带当前 `q`；`revokeObjectURL` 被调） |
| TC-W27 | 工序被引用 | 删除按钮禁用 + Tooltip 说明原因 | ✅ 通过（菜单项禁用 + 文案含「已被5处引用，请改为停用」） |
| TC-W28 | 码表保存 | 尺码顺序按 `sort_order` 落库 | ✅ 通过（加入顺序 1,2；「下移」后落库为 2,1 → 顺序是业务数据） |
| TC-W29 | 空态 | 「还没有颜色，点右上角新建」 | ✅ 通过（空态文案「还没有颜色」+「新建颜色」） |
| 附加 | 必填由契约驱动 | 缺 `size_class` 时点保存不发请求并标红 | ✅ 通过 |
| 附加 | 编辑态不动码表成员 | 不渲染成员编辑器 + 页面写明原因 | ✅ 通过（缺口 B 的处理） |
| 附加 | 四态之错误 | 业务文案 + 重试，不出现 `Error:` | ✅ 通过 |

## 实际改动（完成后回填）

46 文件 / +5059 -61（手写 4769 行 / 生成物 290 行）。

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `frontend/packages/shared/src/enums/baseDictFields.ts` | +800 / -30（**生成**） | 九资源写入契约：路径参数列 / 写权限 / 必填 / 类型 / 默认值 |
| `frontend/scripts/generate-frontend-contract.mjs` | +223 | 生成器第三个产物（Python 出数据，`Decimal` 默认值转字符串） |
| `frontend/packages/shared/src/api/client.ts` | +106 | `download()` + `parseContentDispositionFilename` + `parseRowCount` |
| `frontend/packages/shared/src/{index,types/index}.ts` | +28 | 出口与 `BaseDictRow` / `DisableOut` / `DeleteOut` / `Builtin*` 类型 |
| `frontend/packages/shared/src/utils/format.ts` | +17 | `formatCompactStamp()`（业务时区，导出文件名用） |
| `frontend/packages/admin/src/api/base.ts` | +587 | 注册表 9 条 + `makeResourceApi` 工厂 + `validateDecl` + `editableFieldNames` |
| `frontend/packages/admin/src/api/base.test.ts` | +208 | 注册表与契约一致 + 路径 / 候选 / 导出文件名 |
| `frontend/packages/admin/src/composables/useExport.ts` | +107 | 导出（含 revoke、`X-Row-Count` 回显） |
| `frontend/packages/admin/src/composables/useExport.test.ts` | +123 | 文件名摘要 / 业务时区 / 点击时求值 |
| `frontend/packages/admin/src/views/base/ResourceList.vue` | +625 | **九页共用的**列表页 |
| `frontend/packages/admin/src/views/base/ResourceForm.vue` | +491 | **九页共用的**表单 |
| `frontend/packages/admin/src/views/base/OrderedSizeItems.vue` | +186 | 码表有序尺码成员编辑器 |
| `frontend/packages/admin/src/views/base/BasePages.test.ts` | +524 | TC-W24~W29 + 四态 + 契约驱动必填 |
| `frontend/packages/admin/src/views/base/*/{List,Form}.vue` | +630 | 九资源各两个薄壳 |
| `frontend/packages/admin/src/router/index.ts` | +90 | 27 条路由由注册表生成 |
| `frontend/packages/admin/src/router/index.test.ts` | +81 | 路由与菜单守卫 |
| `frontend/packages/admin/src/layouts/menu.ts` | +13 | 基础资料组 +9 项 |
| `frontend/packages/admin/src/api/system.ts` | +26 | 「恢复内置库」两个端点封装 |
| `backend/tests/modules/test_base_resource_registry.py` | +295 | 三层守卫的第 2 层 |
| `docker-compose.ci.yml` | +12 | 闸门 3 挂前端源码（否则守卫在容器里 skip） |
| `frontend/.prettierrc.json` | +13 | 生成物的三条覆盖（让它成为合法 JSON） |

**提交记录**（6 个，按依赖顺序）：
- `2fd4335` fix(infra): 闸门 1 在 `ba08065` 之后是红的（ruff format 未跑）—— 上一张卡遗留，先补
- `97694e2` feat(web): 基础资料的写入契约改由后端生成（缺口③的根部）
- `88638a7` feat(web): 基础资料前端注册表与请求封装（缺口②）+ 三层一致性守卫
- `8d61dc6` feat(web): 契约补上后端默认值（表单初值不再在前端写死）
- `081f4c4` feat(web): 基础资料九页（通用列表 / 表单 / 有序尺码编辑器 + 路由菜单）
- `47a0cd5` fix(infra): 闸门 3 的容器里挂上前端源码（跨语言守卫此前形同虚设）

**测试数**：admin 154 例（+19）/ shared 54 例 / 后端 542 例（+19，0 skipped）。
**闸门**：`scripts/gate.sh` 容器模式 5 道全绿；`scripts/gate.sh --host` 4 道全绿；
前端 `pnpm lint` / `format:check` / `typecheck` / `test:unit` 全绿。

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| 缺口 A | **基础资料没有「启用」端点** —— 停用之后再也启不回来（`*Patch` 里没有 `is_active`）。界面因此不给「启用」按钮 | docs/12 L-060（建议后端补 `POST /{key}/{code}/enables`） |
| 缺口 B | **`DictOut` 没有 `items`** —— 码表成员读不回来，而替换是 delete + 批量 insert。本卡的处理是编辑态**不渲染**成员编辑器 | docs/12 L-061（T-BASE-001 遗留，需后端在详情里回显 `items`） |
| 缺口 C | **`warehouse_type` 是自由文本**（DB 无 enum），做不了真下拉。本卡给文本框 + 占位提示，**不编下拉限定取值** | docs/12 L-062（待业务方定枚举） |
| D | **`customers` 有端点但没有页面**（T-BASE-002 组 D 交付了接口，本卡九页不含它）。守卫里显式登记为待做，不许悄悄放宽断言 | docs/12 L-063 |
| E | 跨语言一致性守卫依赖闸门 3 挂载前端源码；`test_permission_registry` 的两条前端守卫此前长期 skip（INV-P0-4「三处一致」在闸门 3 里从未被验证） | 已修（`47a0cd5`）；登记以免将来有人把挂载去掉 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选：

- [x] 读过本任务对应的 docs 规范（06 §1 §2.2 §2.4 §5 §9.1 §10、03 §2.1 §2.2 §2.5、05 §9.1 §9.5、modules/01 §4）
- [x] 没有硬编码业务常量（枚举中文映射在前端，**取值**来自后端 PG enum / 契约）
- [x] 没有物理删除（前端只调端点；真删规则在后端 service，ADR-0025）
- [x] 新表字段齐全 —— 本卡无新表
- [x] 状态变更走了 service 层迁移方法且写了日志 —— 本卡无状态机
- [x] 接口有权限声明 + 错误码 + OpenAPI 标签 —— 本卡无新端点（只封装既有端点，权限点读契约）
- [x] 测试覆盖正常路径 + 异常路径 + 权限拒绝 + 并发（TC-W24~W29 + 四态 + 10003 / 20003 分支）
- [x] 闸门 1 lint 通过（含 `pnpm format:check`）
- [x] 闸门 2 typecheck 通过
- [x] 闸门 3 单测通过（前端 154+54 / 后端 542，0 skipped）
- [x] 闸门 4 迁移可正向且可回滚（无迁移；`alembic check` 零漂移）
- [x] 闸门 5 构建镜像成功（api-image + web-image，web 容器首页 200）
- [x] 提交信息符合规范（含超 800 行的行数构成）
- [x] 本次改动已在 docs/12 变更记录留痕
