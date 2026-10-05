# T-CUT-001c-2b：裁剪单列表页与菜单入口

| 项 | 内容 |
| --- | --- |
| 模块 | web |
| 状态 | `done` |
| 优先级 | P1 |
| 依赖 | T-CUT-001c-2a（类型与 API 层） |
| 被依赖 | T-CUT-001c-2c（详情页）、T-CUT-001c-3（新建 / 编辑 / 审核 / 导出） |
| 关联设计 | [modules/02-裁剪.md](../modules/02-裁剪.md) §6、[06-前端与UI规范.md](../06-前端与UI规范.md) §2.2 |
| 估算 | 0.5d |

## 目标

裁剪单能被**找到**：状态 / 款号 / 车间 / 日期区间四个筛选 + 分页 + 点进详情。
这一页**只做定位**，明细编辑与状态迁移都在详情页与后续卡。

## 范围

**要做**：

- [x] `router/index.ts`：列表路由 `cutting-orders`（`permission: PERM.CUTTING_READ`）
- [x] `layouts/menu.ts`：新增「生产管理 / 裁剪单」分组
- [x] `views/cutting/List.vue`：四态（加载 / 空 / 错误 / 无权限交给守卫）+ 筛选 + 分页
- [x] `List.test.ts` 5 例 + 路由守卫 2 例（`router/index.test.ts`）

**不做**：

- 新建 / 编辑 / 审核 / 作废 / 删除按钮 → **T-CUT-001c-3**（前端页面尚不存在，
  先登记就是「点菜单白屏」，与 `menu.ts` 文件头的规矩冲突）
- 导出（T-CUT-001c-1 留的 `/exports` 端点尚未实现）

## 三条最容易做错的

| # | 陷阱 | 正确做法 |
| --- | --- | --- |
| 1 | 加一个模糊搜索框 | 后端 `list_cutting_orders` **没有 `q`**。多传一个被忽略的参数 = 界面假装筛了、结果没筛，用户会以为单丢了 |
| 2 | 车间候选用 `/options` | `/options` 的 value 是**车间编码**，而筛选参数是 `workshop_id`（**UUID**）→ 必须 `optionsById`，否则永远筛不出结果 |
| 3 | 表里显示 `workshop_id` | 那是 UUID，用户看不出是哪个车间。后端 DTO 没给车间名 → 这一版**不显示该列**（改 DTO 属 001c-2c/3 的范围） |

## 验收标准

- [x] `pnpm -C packages/admin test:unit` 全过
- [x] `pnpm lint` / `pnpm typecheck` 通过
- [x] 闸门 1-5 全绿

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-CUT-W1 | 单号 / 款号 / 状态按后端口径渲染，尾数 0 显示「—」 | `06 §1` | ✅ |
| TC-CUT-W2 | 筛选是等值传参，**没有 `q`** | `modules/02 §6` | ✅ |
| TC-CUT-W3 | 改筛选回到第 1 页 | `usePageList` 存在理由 | ✅ |
| TC-CUT-W4 | 点「详情」跳详情路由并带 id | `modules/02 §6` | ✅ |
| TC-CUT-W5 | 候选接口 500 不阻断列表 | `06 §2.2` | ✅ |
| TC-W40 | 只有 `base:read` 访问裁剪单 → **403** | `07 §4.1` | ✅ |
| TC-W41 | 有 `cutting:read` 时列表与详情都能进 | `07 §4.1` | ✅ |

## 实际改动

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `frontend/packages/admin/src/views/cutting/List.vue` | +392 | 列表页 |
| `frontend/packages/admin/src/views/cutting/List.test.ts` | +231 | 5 例 |
| `frontend/packages/admin/src/router/index.ts` | +18 | 列表 + 详情两条路由 |
| `frontend/packages/admin/src/router/index.test.ts` | +22 | 守卫 2 例 |
| `frontend/packages/admin/src/layouts/menu.ts` | +8 | 「生产管理」分组 |

**手写行数 ≈ 671**。

## 抓到的三个坑

| # | 坑 | 为什么难发现 |
| --- | --- | --- |
| 1 | `exactOptionalPropertyTypes` 下 antd `RangePicker` 的 `value` **既不能传 `undefined` 也不能传 `null`** | 类型写成 `[string,string] \| [Dayjs,Dayjs]`；报错是「Consider adding 'undefined'」，看不出解法是「**要么不传这个键**」→ 用 `v-bind` 一个没有 `value` 键的对象 |
| 2 | `import * as baseApi` 拿到的是模块命名空间，上面**没有** `workshops` 导出 | `vi.spyOn` 报「could not find an object to spy upon」，完全看不出是导入方式错 |
| 3 | antd 会在**两个汉字之间插空格**（`重 置`） | `includes('重置')` 永远匹配不上，断言失败信息是「expected undefined to be defined」 |

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| — | 列表没有「车间名」列（后端 DTO 只给 UUID） | T-CUT-001c-2c / 001c-3 一并改 DTO |
| — | 新建页、编辑、审核、导出 | T-CUT-001c-3 |
