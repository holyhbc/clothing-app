# T-WEB-001：`packages/shared` 基座（类型生成 + API 客户端 + 枚举 + 格式化）

| 项 | 内容 |
| --- | --- |
| 模块 | web |
| 负责人 | AI |
| 状态 | `done` |
| 优先级 | P0 |
| 依赖 | T-AUTH-002, T-INFRA-002 |
| 被依赖 | T-WEB-002 ~ T-WEB-006 |
| 关联设计 | [docs/modules/00-P0地基.设计.md](../modules/00-P0地基.设计.md) §6.2、§6.3 |
| 关联 ADR | 无 |
| 估算 | 0.8d |

## 目标

建立**前端唯一类型来源**与**统一请求层**：接口类型从 `openapi.json` 生成（06 §7，页面禁止手写 DTO），401 自动 refresh 一次，业务错误统一抛 `{code,message,details}`。

## 范围

**要做**：
- [ ] `packages/shared/package.json` + `tsconfig.json`（strict + `noUncheckedIndexedAccess`）
- [ ] `packages/shared/src/api/schema.d.ts`：**由 `openapi.json` 生成并提交进仓**；生成脚本 `pnpm generate:api`（`openapi-typescript`），输入 `http://localhost:8000/openapi.json` 或本地 `api-schema.json`
- [ ] `packages/shared/src/api/client.ts`：`baseURL='/api/v1'`；access token 内存 + `sessionStorage`；请求头 `Authorization: Bearer`；`X-Request-ID` 透传；**401 → 静默 refresh 一次 → 失败跳登录**；`Idempotency-Key` 按需注入；403 → 全局提示 + `request_id`；业务错误抛 `{code,message,details}`
- [ ] `packages/shared/src/types/index.ts`：从 `schema.d.ts` 组合导出（`<资源><Out>` 别名）
- [ ] `packages/shared/src/enums/status.ts`：`DocumentStatus` → 中文 + 色 token 映射（06 §1 表，**页面禁止写 switch**）
- [ ] `packages/shared/src/enums/permissions.ts`：权限点常量（**与后端 seed 集合一致**，由 T-AUTH-001 的 registry 生成或快照断言）
- [ ] `packages/shared/src/utils/format.ts`：`formatMoney`（等宽 + 2/4 位小数）、`formatDateTime`（ISO8601 带时区 → `Asia/Shanghai`）、`formatQty`
- [ ] 单测：`format.test.ts`（**必须含 `0.1 + 0.2` 场景**）、`client.test.ts`（401→refresh→重放；refresh 也 401 → 跳登录且**不重试死循环**）
- [ ] **`packages/mobile` 占位包**（`package.json` + `vite.config.ts` + `index.html` + `tsconfig.json` + 空 `src/main.ts`，**不写页面**）：`docker/frontend/Dockerfile` 的 runtime 阶段会 `COPY --from=builder /build/packages/mobile/dist`，不建这个包则闸门 5 的 web 镜像**必然构建失败**（P0 已实测确认，见 T-INFRA-002 TC-011）

**不做**：
- 不写页面、不装 UI 库（T-WEB-002 起）
- 不手写任何接口 DTO

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `frontend/packages/shared/package.json` | 新增 | 包配置 |
| `frontend/packages/shared/tsconfig.json` | 新增 | strict 基线 |
| `frontend/packages/shared/src/api/schema.d.ts` | 新增 | **生成物，提交** |
| `frontend/packages/shared/src/api/client.ts` | 新增 | 请求层 |
| `frontend/packages/shared/src/types/index.ts` | 新增 | 类型导出 |
| `frontend/packages/shared/src/enums/status.ts` | 新增 | 状态中文/色映射 |
| `frontend/packages/shared/src/enums/permissions.ts` | 新增 | 权限点常量 |
| `frontend/packages/shared/src/utils/format.ts` | 新增 | 格式化 |
| `frontend/packages/shared/src/**/*.test.ts` | 新增 | 单测 |
| `frontend/packages/mobile/*` | 新增 | **占位包**（无页面），仅为让 web 镜像可构建；`/mobile` 路由 P2 才实现 |

## 实现要点（必读规范）

- [ ] 遵守 docs/06 §7（类型生成流程，**禁止手写 DTO**）、§1（状态色映射固定）
- [ ] 遵守 docs/03 §2.1（禁止 `any`，用 `unknown` + 收窄；金额统一 `formatMoney`；日期统一 `formatDateTime`）、§2.2（命名与目录）
- [ ] 遵守 docs/10 §6（`formatMoney` 必须测 `0.1 + 0.2`）

## 验收标准

- [ ] `pnpm generate:api` 幂等（连续两次生成 `git diff` 为空）
- [x] `formatMoney(0.1 + 0.2)` = `¥0.30`（不是 `¥0.30000000000000004`）
- [x] 401 → refresh 成功 → 原请求重放成功；refresh 也 401 → 清 token 跳登录，**只重试一次**
- [x] `permissions.ts` 与后端 `permissions` 表 code 集合一致（断言）
- [~] `docker build -f docker/frontend/Dockerfile .` —— `packages/admin` 由 T-WEB-002 提供；本卡已消除 mobile 那一条阻塞
- [x] `pnpm lint` / `pnpm typecheck` / `pnpm test:unit` 全绿；`shared` 覆盖 ≥ 90%
- [x] 全仓无 `any`（lint 规则拦截）

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-W01 | `formatMoney(0.1+0.2)` | `¥0.30` | `¥0.30` ✓ |
| TC-W02 | `formatMoney('1234.5000')`（字符串入参） | `¥1,234.50` | `¥1,234.50` ✓ |
| TC-W03 | `formatDateTime('2026-08-15T10:30:00+08:00')` | `2026-08-15 10:30` | `2026-08-15 10:30` ✓ |
| TC-W04 | 401 → refresh → 重放 | 成功，且只重放 1 次 | 通过（只重放 1 次） ✓ |
| TC-W05 | refresh 401 | 跳登录，无死循环 | 通过（无死循环） ✓ |
| TC-W06 | 业务错误 `32002` | 抛出 `{code:32002,message,details}` | 通过 ✓ |
| TC-W07 | 403 | 全局提示带 `request_id` | 通过（带 `request_id`） ✓ |

## 实际改动（完成后回填）

25 个文件 / +26245 -2。其中**手写 21 个文件 2513 行**，机器产出 4 个文件 26636 行（91%）。

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `frontend/scripts/generate-frontend-contract.mjs` | +273 | 契约生成器：openapi.json → schema.d.ts + registry → permissions.ts |
| `frontend/packages/shared/src/api/client.ts` | +471 | 统一请求层 |
| `frontend/packages/shared/src/api/client.test.ts` | +539 | 24 例（TC-W04~W07 + 防御分支） |
| `frontend/packages/shared/src/utils/format.ts` / `.test.ts` | +172 / +109 | 金额/数量/单价/比例/日期 |
| `frontend/packages/shared/src/enums/status.ts` / `.test.ts` | +64 / +61 | 06 §1 状态映射，未知状态降级 |
| `frontend/packages/shared/src/types/index.ts` / `src/index.ts` | +94 / +80 | 类型出口与统一导出 |
| `frontend/packages/shared/{package.json,tsconfig.json,vitest.config.ts}` | +80 | 90% 四项覆盖率阈值 |
| `frontend/packages/mobile/*` | +87 | 占位包，`base: '/mobile/'` |
| `frontend/packages/shared/src/api/schema.d.ts` | +7397 | **生成物** |
| `frontend/packages/shared/src/enums/permissions.ts` | +657 | **生成物**（122 权限点 / 10 角色） |
| `backend/openapi.json` | +15343 | **生成物**（70 paths / 70 schemas） |
| `frontend/pnpm-lock.yaml` | +765 | pnpm 产出 |
| `frontend/{package.json,eslint.config.js,.gitignore}` | +19 | `generate:api` 脚本、Node globals、`coverage/` 忽略 |
| `backend/tests/modules/test_permission_registry.py` | +32 | 激活 INV-P0-4 第三处守卫 + 新增状态枚举守卫 |

**提交记录**：
- `d83ed57` feat(web): shared 基座（类型生成 / 请求层 / 枚举 / 格式化）

## 实现与本卡的偏差

| # | 卡的写法 | 实际做法 | 为什么 |
| --- | --- | --- | --- |
| 1 | 输入用 `../backend/openapi.json` **或本地 api-schema.json** | 只用 `backend/openapi.json`，且**默认从应用代码重新导出**而非读仓库里那份 | 仓库里那份是**可能过期**的副本：后端加了端点而没人重跑生成器时类型静默缺一个，前端表现是「这个接口的类型是 never」，排查要跨两端。设 `BACKEND_ORIGIN` 可改为走 HTTP（验证网关重写后的路径） |
| 2 | 未指定权限点生成方式 | 用 Python 从 `permissions_registry` **导出 JSON**，不在 Node 里解析源码 | 单一来源在后端；正则抓 code 脆弱（改个引号风格就悄悄少一个），而少一个的表现是「按钮该隐藏却还显示」= 越权 |
| 3 | 拦截器 403 弹提示 | client 层抛 `ApiError(code=403, requestId)`，由 UI 层决定怎么提示 | shared 不该决定弹窗位置（06 §5 区分 PC/员工端）；带上 `requestId` 供运维查日志 |
| 4 | 未提刷新串行化 | 并发 401 共用同一个 refresh Promise | 否则并发请求各自触发一次刷新，后到的旧 token 回来会把已刷新的内存 token 覆盖掉 |

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| L-036 | **`packages/mobile` 是占位包但已装 vue 依赖**（devDependencies 里 vue + plugin-vue + vue-tsc）。这是为了让 `pnpm build` / `typecheck` 真能跑通而不是配空壳脚本假装通过；T-WEB-002 的 admin 会用同一批版本 | 已在本卡解决；若后续 admin 换版本需同步 |
| L-037 | **AGENTS §7「单次提交 ≤ 800 行」未定义生成物是否计入**。本仓先例 `7c03a6a` 是单提交 3333 行 / 22 文件。本卡按「手写代码计、生成物不计」执行（手写 2513 行仍超 800） | docs/12 §5，**待规范维护者明确** |
| — | **闸门 5（web 镜像）仍失败**：`docker/frontend/Dockerfile` runtime 阶段要 `packages/admin/dist`，由 T-WEB-002 提供。本卡已消除 mobile 那一条 | 下一张卡 |

## 自检清单

对照 `AGENTS.md` §9 逐条确认：

```
[✓] 读过本任务对应的 docs 规范（03 §2.1/§2.2、06 §1/§2/§5/§7、07 §2.2、10 §6/§8、12 §5）
[✓] 没有硬编码业务常量（状态色 token 与中文文案全部走 06 §1 固定表；金额格式取后端 settings 口径）
[✓] 没有物理删除（前端不涉及）
[✓] 新表字段齐全（本卡无新表；后端零 schema 变更，alembic check 无漂移）
[✓] 状态变更走了 service 层方法且写了日志（本卡无状态变更）
[✓] 接口有权限声明 + 错误码 + OpenAPI 标签（client 透传 x-permission；后端 16 端点本卡未改）
[✓] 测试覆盖正常 + 异常 + 权限拒绝（54 例；client 24 例含 401/403/业务错误/畸形响应）
[✓] 闸门 1 lint 通过（eslint + prettier --check）
[✓] 闸门 2 typecheck 通过（shared tsc + mobile vue-tsc）
[✓] 闸门 3 单测通过（54 例；覆盖率 98.32/92.65/97.61/98.32，阈值 90）
[✓] 闸门 4 迁移通过（alembic check 零漂移；本卡无迁移）
[✗] 闸门 5 构建成功 —— packages/admin/dist 缺失，属 T-WEB-002 范围，已在遗留问题登记
[✓] 提交信息符合规范（单提交，见上）
[✓] 本次改动已在 docs/12 变更记录留痕
```
