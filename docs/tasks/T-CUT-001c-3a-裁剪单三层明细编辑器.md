# T-CUT-001c-3a：裁剪单三层明细编辑器（行 → 颜色 → 尺码明细）

| 项 | 内容 |
| --- | --- |
| 模块 | web |
| 状态 | `done` |
| 优先级 | P1 |
| 依赖 | T-CUT-001c-2a（API 层）、T-BASE-007a/007b（物料 / 供应商 / 布批候选端点） |
| 被依赖 | T-CUT-001c-3b（新建页与编辑页） |
| 关联设计 | [modules/02-裁剪.md](../modules/02-裁剪.md) §5 §5.2、C4 / C5 / C13 / C21 / C25 / C26 / C27 / C28 / C32 / C34 / C35 / C38 |
| 估算 | 0.5d |

## 目标

三层明细编辑器可复用：新增裁剪单与编辑裁剪单都拿它当录入面，且**三个业务口径**
在手数 / 件数 / 行余量上都有断言守着。

## 为什么拆成三个文件（ADR-0030 的单文件 400 行硬线）

| 文件 | 职责 | 为什么独立 |
| --- | --- | --- |
| `slotTypes.ts` | 收紧类型 + 三层算术 + 插槽收窄 | 算术**必须只有一份**。第一版三个组件各算各的，页面上「行余量」那一格显示 10、旁边问题清单说「少 10」，用户无法判断哪个是真的 |
| `LineEditor.vue` | 第一层：布批行（选料 / 耗料 / 行可出件数 / 行余量） | 换的是**选料与库存**（布批、门幅、可用量、`40006`） |
| `ColorEditor.vue` | 第二、三层：行内颜色 + 尺码明细 | 换的是**录入口径**（三种模式、手数与件数、C27） |

两边字段、校验、错误码来源都不同，塞一个文件里「选料相关」与「录入相关」的注释会互相打断。

## 范围

**要做**：

- [x] `slotTypes.ts`：`LineTree` / `LineColorTree` 收紧类型、`sizeOutputOf` / `colorOutputOf` / `lineOutputOf` / `balanceOf` / `num` / `asLine`
- [x] `LineEditor.vue`：行的增删改与上移下移、布批 Combo、耗料 / 布损 / 行可出件数、行余量、问题清单
- [x] `ColorEditor.vue`：颜色增删、录入模式（C26/C27）、尺码明细增删、手数 / 每手件数（C25）
- [x] `LineEditor.test.ts` 6 例

**不做**：

- 新建页与路由 → **T-CUT-001c-3b**
- **MASTER（按比例带出）**：它要调 `GET /{id}/suggest-lines`，而那需要 `order_id`
  —— 新建页上单据还不存在；且 `SuggestLinesOut.ratio` 是**小数**（1.5 手）而 `hands`
  必须是**整数**（ADR-0020），取整策略业务未拍板（`docs/12` **L-082**）。
  **给一个做不到的选项比不给更糟**：用户填到一半才发现要保存重来。
- C33 理论需布试算（`required_fabric_qty`）：**后端没实现**（`bom_items` 未建），前端也拿不到
- C24 门幅校验：款号表**没有**「铺布要求有效门幅」列（`02 §12 Q-C08` 未决）

## 验收标准

- [x] `npx vitest run src/views/cutting/components` 全过
- [x] `pnpm lint` / `pnpm typecheck` 通过，**无单文件超 400 行**
- [x] 闸门 1-5 全绿

## 测试清单

| # | 用例 | 口径 | 结果 |
| --- | --- | --- | --- |
| TC-CUT-E1 | C21 精确乘、不取整（10×3=30 / 7×13=91） | ADR-0020 | ✅ |
| TC-CUT-E2 | C28 人工指定件数**优先于**乘积 | C28 | ✅ |
| TC-CUT-E3 | C34 行余量 = 行可出件数 − Σ(颜色 Σ尺码)；负数进问题清单 | C34 | ✅ |
| TC-CUT-E4 | C27 切模式**二次确认**并写明「1 行尺码明细…全部清空」；确认前数据不动 | C27 | ✅ |
| TC-CUT-E5 | C4 层数 > 1 标注「含 N 层」 | C4 | ✅ |
| TC-CUT-E6 | 删中间行**重排 `line_no`**（不留跳号） | `UNIQUE (doc_id, line_no)` | ✅ |

## 实际改动

| 文件 | 行数 |
| --- | --- |
| `frontend/packages/admin/src/views/cutting/components/LineEditor.vue` | +373 |
| `frontend/packages/admin/src/views/cutting/components/ColorEditor.vue` | +365 |
| `frontend/packages/admin/src/views/cutting/components/LineEditor.test.ts` | +180 |
| `frontend/packages/admin/src/views/cutting/components/slotTypes.ts` | +105 |

**手写行数 ≈ 1023**（在 ADR-0030 的 1200 软上限内；单文件最大 373 行 < 400）。

## 抓到的四个坑

| # | 坑 | 为什么难发现 |
| --- | --- | --- |
| 1 | **`balanceOf` 把行对象当尺码行做过乘法** | 行上没有 `hands` / `qty_per_hand` → `undefined * undefined` = `NaN` → `NaN < 0` 是 **false** → 阻断条件**静默失效**。是 TC-CUT-E3 抓出来的；修法是算术收敛到 `slotTypes.lineOutputOf` |
| 2 | 生成类型里 `colors` / `size_lines` 是**可选**的 | 模型带 `default_factory` → OpenAPI `required` 里没有它。直接拿来编辑就要到处 `?? []`，而空数组分不清「没录」与「录漏了」 |
| 3 | antd `InputNumber` 的 `@change` 是 `ValueType`（`string \| number \| null`） | 处理函数写成 `(value: number \| null)` 在 `vue-tsc` 下报错，而 **vitest 不跑类型检查** → 「测试全绿、闸门 2 红」会让人怀疑测试白写了 |
| 4 | 多个「移除」按钮，DOM 里颜色层的排在行级操作列**之前** | 取第一个点到的是「删颜色」，而断言看的是 `line_no` → 失败信息张冠李戴（看起来像「重排逻辑没生效」） |

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| — | MASTER 取整策略（`ratio` 小数 → `hands` 整数） | docs/12 **L-082** |
| — | C33 耗料下限试算、C24 门幅校验 | 后端（`bom_items` / 款号门幅字段） |
| — | 新建页组装 + 编辑页（三层 PUT + 版本链） | T-CUT-001c-3b |