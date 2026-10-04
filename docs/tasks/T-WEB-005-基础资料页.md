# T-WEB-005：基础资料页（组织 / 字典 / 工序）

| 项 | 内容 |
| --- | --- |
| 模块 | web |
| 负责人 | AI |
| 状态 | `todo` |
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

### ① `restore_builtin` **没有 HTTP 端点** —— 它是 CLI

任务卡要求页面上有「恢复内置库」按钮，但真实情况是
`backend/app/cli/restore_builtin.py`，**`openapi.json` 里没有任何 restore 路径**。
（该文件自己的注释里就写着「用户点『恢复内置库』」—— 说明设计时预期有按钮，
但那一层一直没做。）

三个选择，**必须先定**：

| 方案 | 说明 |
| --- | --- |
| A. 补一个 `POST /api/v1/system/dicts/{kind}/restore-builtin` | 需要一张后端卡；权限点用 `system:config:manage` |
| B. 按钮只显示**提示**，让运维执行 `python -m app.cli.restore_builtin` | 零后端改动；但用户会看到"这个按钮没用" |
| C. 按钮调 A 的端点，A 复用 `app/cli/restore_builtin.py` 的逻辑 | 与 A 等价，只是确保只有一份实现 |

**倾向 A/C**，因为该 CLI 自己的注释就是这么写的。但这是**缺口**，不是本卡能顺手补的
（AGENTS §6：一个会话只动一个模块）。

### ② 前端不要手写 72 个函数

后端已经用**声明式注册表**解决了同构问题（`app/modules/base/resources.py::RESOURCES`，
注释写得很清楚：「九个资源的 CRUD 形状完全同构… 写成九份重复的 handler 意味着同一条
业务规则要改九遍」）。前端若照抄成 9 × 8 = 72 个函数，就是**把同一个错误换个语言
再犯一遍** —— 改一个路径要改 9 处，漏一处就是那个资源静默 404。

**建议**：前端也写一张 `RESOURCE_REGISTRY`（9 条声明：key / path / 中文名 /
特有字段 / 是否可引用检查 / 是否可停用），`api/base.ts` 按它生成函数，
并加一条测试断言**与后端 `RESOURCES` 的 key 集合一致** ——
和 `test_permission_registry.py` / `test_frontend_dockerfile.py` 同一个套路。

⚠️ 注册表**不能从 `openapi.json` 推导**：它描述的是服务端 HTTP 契约，
不包含"这个资源前端要显示哪几个字段""这个资源能不能删"。那部分只能显式声明。

### ③ `DictOut` 是"一个大模型 + 大量可空字段"

后端注释里明说了这个取舍：「用一个大模型换来的是每个响应都多出七八个 null 字段…
换来的是新增资源不用改这个类」。所以前端拿到的行类型**大部分字段是 `T | null`**。

后果：9 个 `Form.vue` 若各写各的空值判断，就是 9 份「哪些字段必填 / 哪些要显示」
的真相。**建议**在注册表里加一张 `fields` 声明（每资源：表格列 / 表单字段 / 校验），
9 个页面由它生成；至少也要有一张共享的"必填字段"清单，否则后端加一个必填字段时
前端不会有任何提示。

### 其余已就绪、不需要再决策的

| 项 | 现状 |
| --- | --- |
| `usePageList` / `TableToolbar` / `PageLayout` / `EmptyState` / `Combo` / `confirmDanger` | T-WEB-003 / T-WEB-004 已交付并测过，直接复用 |
| 路由与菜单 | `layouts/menu.ts` 的「基础资料」组已有「款号」一项（占位页），加 9 项即可 |
| 导出行数与列表一致 | 后端 `exports` 与 `list` 共用同一筛选与 service 方法（`DictService.export_rows` → `list_rows`），前端只需把当前 `query` 原样传过去 |
| 字典删除两分支 | 后端 `DictService.delete` 已实现两分支（ADR-0025），422 携带 `references[]` |

## 范围

**要做**（每资源固定 `List.vue` / `Form.vue` 两件套，`warehouses` 等简单资源可省 `Detail`）：
- [ ] `src/api/base.ts`：9 资源 × (list / options / create / patch / disable / delete / exports) 封装
- [ ] 组织类：`workshops`、`workshop-groups`、`warehouses`、`uom-units`、`product-categories`
- [ ] 字典类：`colors`、`sizes`、`size-groups`
- [ ] 工序类：`operations`
- [ ] 统一交互：筛选（关键字 + 状态，默认 3 个字段，"更多条件"展开）、表格、分页、**导出按钮（与「新建」相邻，06 §9.1）**
- [ ] 字典页额外能力：
  - 列表显示 `ref_count` 引用计数
  - 删除：引用 0 → 二次确认「**不可撤销**」；引用 >0 → 展示 `references[]` 清单 + 引导改用停用
  - 「恢复内置库」按钮（调 `restore_builtin`，二次确认）
  - `is_builtin` 显示「内置」角标
- [ ] `operations` 页额外能力：显示引用计数；`operation_no` 编辑时 `disabled`；删除被引用 → `20003` 引导停用
- [ ] `size-groups` 页：`items[]` 有序尺码编辑（可拖拽排序 / 上下移动），保存全量替换

**不做**：
- 不做 Excel **导入**（三步式，P1 起）
- 不写款号与单价页（T-WEB-006）

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `frontend/packages/admin/src/api/base.ts` | 新增 | 9 资源封装 |
| `frontend/packages/admin/src/composables/usePageList.ts` | 新增 | 列表分页/排序/筛选复用 |
| `frontend/packages/admin/src/composables/useExport.ts` | 新增 | 导出（带筛选摘要文件名） |
| `frontend/packages/admin/src/views/base/{workshops,workshop-groups,warehouses,uom-units,product-categories}/{List,Form}.vue` | 新增 | 组织类 5 资源 |
| `frontend/packages/admin/src/views/base/{colors,sizes,size-groups}/{List,Form}.vue` | 新增 | 字典类 3 资源 |
| `frontend/packages/admin/src/views/base/operations/{List,Form}.vue` | 新增 | 工序 |
| `frontend/packages/admin/src/router/index.ts` | 修改 | 注册菜单与路由 |

## 实现要点（必读规范）

- [ ] 遵守 docs/06 §2.2（列表页标准结构）、§2.4（表单：3 列 `labelCol 6`、`blur` 校验、`InputNumber` 精度、提交 loading 防重复）、§5（四态与危险确认）、§8（UI 走查清单）、§10（Combo 强制）
- [ ] 遵守 docs/05 §9.1（导出：与列表同一筛选、`.xlsx`）、§9.5（候选搜索）
- [ ] 遵守 docs/modules/01 §4（`disable` 必填原因、`delete` 两分支、`operations.operation_no` 不可改）
- [ ] 遵守 docs/03 §2.1（禁 `any`、禁 `console`）

## 验收标准

- [ ] 9 个资源页面四态齐全 + 导出可用 + Combo 搜索可用
- [ ] 字典删除：引用 0 → 真删并提示不可撤销；引用 >0 → 展示引用来源清单并**不给删除按钮**（只给停用）
- [ ] 「恢复内置库」按钮二次确认后调通
- [ ] 停用弹必填原因；`PATCH` 未传 `version` 时前端先提示刷新（后端 `10003` 时给「刷新重试」）
- [ ] 导出行数与当前列表一致（同一筛选）
- [ ] 候选 >30 项的字段全部用 `Combo`，**无裸 `<select>`**
- [ ] `pnpm lint` / `pnpm typecheck` / `pnpm test:unit` 全绿；组件测试 ≥1 个/新组件

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-W24 | 字典被引用时点删除 | 不发 DELETE 请求，引导停用 | |
| TC-W25 | 字典无引用时点删除 | 二次确认「不可撤销」→ 成功后行消失 | |
| TC-W26 | 导出 | 下载 `.xlsx`，行数 == 列表 `total` | |
| TC-W27 | 工序被引用 | 删除按钮禁用 + Tooltip 说明原因 | |
| TC-W28 | 码表保存 | 尺码顺序按 `sort_order` 落库 | |
| TC-W29 | 空态 | 「还没有颜色，点右上角新建」 | |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| | +0 / -0 | |

**提交记录**：
- `<hash>` feat(web): 基础资料页（组织/字典/工序） …

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| | | docs/12 §遗留问题清单 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。
