# T-CUT-001c-2a：裁剪单前端契约与 API 层

| 项 | 内容 |
| --- | --- |
| 模块 | web |
| 状态 | `done` |
| 优先级 | P1 |
| 依赖 | T-CUT-001c-1（10 个端点已挂表，`openapi.json` 里有裁剪的 schema） |
| 被依赖 | T-CUT-001c-2b（列表页）、T-CUT-001c-2c（详情页） |
| 关联设计 | [modules/02-裁剪.md](../modules/02-裁剪.md) §6、[06-前端与UI规范.md](../06-前端与UI规范.md) §7 |
| 估算 | 0.3d |

> ⚠️ 原 T-CUT-001c-2 的范围（API 层 + 列表页 + 详情页）实测约 **1150 手写行**，
> 超过 `AGENTS §2.1` 的「单次提交不超过 800 行」，因此按「**契约 / 页面**」拆成
> 001c-2a（本卡）/ 001c-2b（列表页）/ 001c-2c（只读详情页）三张，
> 各自可独立验收、每个提交都过闸门。

## 目标

裁剪单的**接口类型从生成物来**，页面侧有一层薄 API 封装 —— 之后两个页面卡直接
import 类型与函数，不再各自拼 URL。

## 范围

**要做**：

- [x] 重跑 `pnpm generate:api`：`openapi.json` → `schema.d.ts`（契约必须与代码一致）
- [x] `packages/shared/src/types/index.ts` 加裁剪单的类型别名（只做**别名**，不重述字段）
- [x] `packages/shared/src/index.ts` 出口补齐（`exports.test.ts` 会守这条）
- [x] `packages/admin/src/api/cutting.ts`：8 个函数的薄封装

**不做**：

- 任何页面（001c-2b / 001c-2c）
- 状态机与导入导出端点（后端还没有，见 T-CUT-001b-3）

## 三条最容易做错的

| # | 陷阱 | 正确做法 |
| --- | --- | --- |
| 1 | 手写裁剪单的 DTO | 类型只能是 `ApiModel<'X'>` 别名（`06 §7`）；手写的字段表与后端分叉时**没有任何东西会报错** |
| 2 | 复用 `api/base.ts` 的注册表工厂 | 裁剪单是**三层结构单据**，四个写接口全是**全量替换**，塞进注册表就是给 `ResourceList` 攒一串 `if` |
| 3 | `suggest-lines` 当成纯读接口 | 它**有副作用**（把比例快照写进行内颜色），不要在「用户只是看看」的场合调用 |

## 验收标准

- [x] `pnpm generate:api` 连跑两次 `git diff` 为空
- [x] `pnpm -C packages/shared typecheck` 通过
- [x] 闸门 1-5 全绿

## 实际改动

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `frontend/packages/admin/src/api/cutting.ts` | +195 | 8 个函数 + `CuttingOrderListQuery` |
| `frontend/packages/shared/src/types/index.ts` | +56 | 14 个类型别名 |
| `frontend/packages/shared/src/index.ts` | +16 | 出口 |
| `frontend/packages/shared/src/api/schema.d.ts` | 生成物 | `openapi-typescript` |
| `backend/openapi.json` | 生成物 | 从 `create_app().openapi()` 直接导出 |

**手写行数 ≈ 267**（生成物 2 个文件不计入 `AGENTS §7.1` 的 800 行）。
生成物与生成它的源码同在一个提交，重跑生成命令无 diff。

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| — | `listCuttingOrders` 的筛选全是**等值**（无 `q`），所以列表页不能放模糊搜索框 | 001c-2b 已在页面与测试里注明 |
| — | 三个 PUT 是**全量替换**：不带 `version` → 10001，带旧 `version` → 10003 | 001c-2c / 001c-3 |
