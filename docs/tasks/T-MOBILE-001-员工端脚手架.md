# T-MOBILE-001：员工端 H5 脚手架（登录 / 布局 / 路由 / SSE 消费基座）

| 项 | 内容 |
| --- | --- |
| 模块 | web（`frontend/packages/mobile`） |
| 负责人 | frontend-dev |
| 状态 | `todo` |
| 优先级 | P2（`REQ-000 §7` 第 1 周「mobile 登录骨架」的补齐；`T-PAY-012` 的前置） |
| 依赖 | 无（后端 `POST /api/v1/auth/*` 与 `GET /events/stream` 已就绪） |
| 被依赖 | T-PAY-012（员工端工资条页）、后续任何员工端页面 |
| 关联设计 | [`modules/04-计件.md`](../modules/04-计件.md) §5.2（链路 B）、[`REQ-000`](../requirements/REQ-000-工期优化与复用策略.md) §5/§7 |
| 关联规范 | `docs/07 §1.2/§2.2`、`docs/06 §3`（员工端 Vant 4）、`docs/05 §8`（SSE） |
| 关联 ADR | ADR-0003（员工端认证方式）、ADR-0008（权限点） |

## 目标

`frontend/packages/mobile` 目前**只有占位 `main.ts`**（注释写明「页面在 P2 实现」），无登录、布局、路由、
无 `useAuthContext`/`useSSE`。本卡把员工端 H5 的**可运行基座**建出来，让 `T-PAY-012` 及其后页面能直接挂页。

## 范围

**要做**：
- [ ] Vant 4 应用骨架：`createApp` + `Vant` 全量/按需注册 + `vue-router` + `pinia`（`main.ts`）
- [ ] 路由：`/m/login`、`/m/`（首页/重定向）、404；路由守卫按 token 跳登录
- [ ] `stores/auth.ts`：员工端 token 存取（短期 token 2h，`07 §1.2`），`login/logout/getToken`
- [ ] 登录页 `views/Login.vue`：手机号 + 验证码（短信通道见后端 `SmsAuthProvider`，未配置时给出明确提示）
- [ ] 基础布局 `layouts/DefaultLayout.vue`（Vant `NavBar` + `Tabbar` 占位）+ 触控目标 ≥44px（`00 §6`）
- [ ] SSE 消费基座：薄封装 `@garment/shared` 的 `EventStreamClient`（T-PIECE-011），**不新造 composable 名**，供页面订阅
- [ ] 单测：登录 store/守卫/路由可挂载（`vitest`）

**不做**：
- 不做工资条页（`T-PAY-012`）、不做计件扫码页（链路 B 另卡）
- 不做微信内嵌适配（AuthProvider 抽象已预留，见 README 员工端章节）
- 不改后端

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `frontend/packages/mobile/src/main.ts` | 修改 | 挂 Vant/router/pinia |
| `frontend/packages/mobile/src/router/index.ts` | 新增 | 路由 + 守卫 |
| `frontend/packages/mobile/src/layouts/DefaultLayout.vue` | 新增 | 基础布局 |
| `frontend/packages/mobile/src/views/Login.vue` | 新增 | 登录页 |
| `frontend/packages/mobile/src/stores/auth.ts` | 新增 | token 存取 |
| `frontend/packages/mobile/src/api/` | 新增 | 复用 `@garment/shared` ApiClient 的 mobile 实例 |
| `frontend/packages/mobile/vite.config.ts` / `index.html` | 可能修改 | 与 admin 对齐 |

## 实现要点

- [ ] 复用 `@garment/shared` 的 `ApiClient` / `EventStreamClient`，**不复制**一份
- [ ] 员工端权限点只读（`self:*`，`07 §2.2`）；本卡不做写操作
- [ ] 06 §1「禁止 color/spacing 字面量」——统一 design token（与 admin 同源）
- [ ] 单文件 ≤400 行（ADR-0030）

## 验收标准

- [ ] `pnpm -r lint` / `typecheck` / `test:unit` 全绿
- [ ] `pnpm build`（mobile 包）成功
- [ ] 路由守卫：无 token 访问受保护页 → 跳 `/m/login`
- [ ] SSE 基座可开可关（单测断言连接/降级回调被调用）
- [ ] 闸门 1-5 全绿（含 mobile 镜像/构建）

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-MOB-01 | 无 token 访问受保护路由 | 重定向 `/m/login` | |
| TC-MOB-02 | 登录成功写 token 并跳首页 | store/token 有值 | |
| TC-MOB-03 | 登出清 token | store 清空 + 回登录页 | |
| TC-MOB-04 | SSE 基座订阅/降级回调 | 调 `onFallbackPoll` | |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| | +0 / -0 | |

**提交记录**：
- `<hash>` feat(web): 员工端 H5 脚手架（T-MOBILE-001）

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| 短信通道是否已配置（登录依赖） | 07 §1.2 / Q-PW09 |
| 微信内嵌适配 | ADR-0003（阶段二） |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。

## 变更记录

| 日期 | 变更内容 | 操作人 |
| --- | --- | --- |
| 2026-10-17 | 初版：由 Q-PL10 决策（选项 ②）单开，`T-PAY-012` 的前置 | AI |
