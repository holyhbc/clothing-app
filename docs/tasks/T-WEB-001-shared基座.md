# T-WEB-001：`packages/shared` 基座（类型生成 + API 客户端 + 枚举 + 格式化）

| 项 | 内容 |
| --- | --- |
| 模块 | web |
| 负责人 | AI |
| 状态 | `todo` |
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

## 实现要点（必读规范）

- [ ] 遵守 docs/06 §7（类型生成流程，**禁止手写 DTO**）、§1（状态色映射固定）
- [ ] 遵守 docs/03 §2.1（禁止 `any`，用 `unknown` + 收窄；金额统一 `formatMoney`；日期统一 `formatDateTime`）、§2.2（命名与目录）
- [ ] 遵守 docs/10 §6（`formatMoney` 必须测 `0.1 + 0.2`）

## 验收标准

- [ ] `pnpm generate:api` 幂等（连续两次生成 `git diff` 为空）
- [ ] `formatMoney(0.1 + 0.2)` = `¥0.30`（不是 `¥0.30000000000000004`）
- [ ] 401 → refresh 成功 → 原请求重放成功；refresh 也 401 → 清 token 跳登录，**只重试一次**
- [ ] `permissions.ts` 与后端 `permissions` 表 code 集合一致（断言）
- [ ] `pnpm lint` / `pnpm typecheck` / `pnpm test:unit` 全绿；`shared` 覆盖 ≥ 90%
- [ ] 全仓无 `any`（lint 规则拦截）

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-W01 | `formatMoney(0.1+0.2)` | `¥0.30` | |
| TC-W02 | `formatMoney('1234.5000')`（字符串入参） | `¥1,234.50` | |
| TC-W03 | `formatDateTime('2026-08-15T10:30:00+08:00')` | `2026-08-15 10:30` | |
| TC-W04 | 401 → refresh → 重放 | 成功，且只重放 1 次 | |
| TC-W05 | refresh 401 | 跳登录，无死循环 | |
| TC-W06 | 业务错误 `32002` | 抛出 `{code:32002,message,details}` | |
| TC-W07 | 403 | 全局提示带 `request_id` | |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| | +0 / -0 | |

**提交记录**：
- `<hash>` feat(web): shared 基座（类型生成/请求层/枚举/格式化） …

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| | | docs/12 §遗留问题清单 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。
