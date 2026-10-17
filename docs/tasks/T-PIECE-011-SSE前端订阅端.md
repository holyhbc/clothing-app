# T-PIECE-011：计件事件流前端订阅端（`@garment/shared` SSE 客户端 + 30s 轮询降级）

| 项 | 内容 |
| --- | --- |
| 模块 | piecework（前端）；产物放 `@garment/shared`（框架无关） |
| 负责人 | frontend-dev |
| 状态 | `done` |
| 优先级 | P2（`05 §8` 要求前端订阅 + 降级；补齐 T-PIECE-010 的下游） |
| 依赖 | T-PIECE-010（后端 `GET /events/stream` 已就绪） |
| 被依赖 | T-PIECE-012（看板页面接入，P6，需先出设计稿） |
| 关联设计 | [`modules/04-计件-SSE前端订阅设计.md`](../modules/04-计件-SSE前端订阅设计.md) |
| 关联规范 | [`05 §8`](../05-接口设计规范.md)；[`03 §2`](../03-代码规范.md)；[`06 §6/§7`](../06-前端与UI规范.md) |
| 估算 | 0.5d |

## 背景

T-PIECE-010 已完成后端事件广播与 SSE 端点，但前端无任何订阅代码。`05 §8` 明确了前端
要做两件事：**订阅事件流**、**失败降级为 30s 轮询**。本卡交付这一层可复用客户端。

⚠️ **消费端「车间看板」属 P6，尚无设计稿**（`HomeView.vue` 是显式占位）。经用户确认
（2026-10-16），本卡**只做 shared 客户端 + 降级轮询**，**不建任何业务页面** —— 不为
「有个地方调用」而发明未设计的 UI（`AGENTS §2.3`）。

## 目标

1. 新增 `frontend/packages/shared/src/sse/eventStream.ts`：框架无关的 `EventStreamClient`。
2. 连接 `GET /api/v1/events/stream`：token 走 `?token=`（`EventSource` 不能设请求头）、
   `events` 白名单、断线重连带 `last_event_id` 续传。
3. 失败降级：不支持 `EventSource` / 无 token / 连续失败达上限 → **30s 轮询**，
   轮询内容由宿主 `onFallbackPoll` 决定（shared 不认识业务）。
4. 经 `src/index.ts` 导出；单测覆盖连接/事件/降级/重连/续传/关闭/自愈。

## 不做什么

| 项 | 原因 |
| --- | --- |
| 看板 / 工位机页面接入 | 消费端属 P6、无设计稿；本卡只交客户端（用户 2026-10-16 确认） |
| 业务事件字段解析 | `data` 只递原始文本；shared 不认识 `piecework_counted` 字段 |
| 轮询哪个接口 | 由 `onFallbackPoll` 回调决定（shared 不认识业务资源） |
| 复用 `ApiClient` 的解包/刷新 | 事件流不是 `{code,message,data}` 包装；只复用 `buildQuery` |
| Vue composable（`useEventStream`） | 放 shared 保持框架无关；将来在 admin/mobile 包一层即可 |

## 接口

不新增后端接口；消费 T-PIECE-010 的 `GET /api/v1/events/stream`（见 `05 §8`）。

## 验收标准

- [ ] `open()` 后连接 URL 含 `token=<tok>` 与 `events=piecework_counted`；默认 `baseURL=/api/v1`
- [ ] 收到帧 → `onEvent({event,data,id})`；`data` 为原文、`id` 更新为 `lastEventId`
- [ ] `error` → 退避重连；重连 URL 带 `last_event_id`（续传，FE-INV-2）
- [ ] 连续失败达 `maxReconnectAttempts` → status `polling`，每 30s 调 `onFallbackPoll`
- [ ] 不支持 `EventSource`（无 factory）→ 直接 `polling`
- [ ] `getToken()` 为 `null` → 不建连接，直接 `polling`
- [ ] `polling` 自愈：一个轮询周期内 SSE 可连 → 回 `open` 且停掉轮询
- [ ] `close()` → status `closed`；再触发 `error` 不重连；无遗留定时器
- [ ] `onFallbackPoll` 返回 rejected promise → 无未处理拒绝（FE-INV-4）
- [ ] `pnpm -r typecheck` 通过；`shared` 覆盖率 ≥ 90%；eslint 通过

## 风险

| 风险 | 缓解 |
| --- | --- |
| 无消费端（看板未建） | 只交 `05 §8` 已定行为，不发明 UI；P6 接入即生效 |
| `EventSource` 不暴露 HTTP 状态码 | 统一重连→降级，不做 `11001/11012` 分支 |
| 重连沿用旧 token（原生行为） | 自管重连，每次重新 `getToken()` 构造 URL |
| 定时器叠加 | 状态切换前 `_clearTimers()` |

## 实施结果

**状态：done（2026-10-16）**。交付框架无关的 `EventStreamClient`（连接 / 续传 / 重连 /
30s 轮询降级）；**无后端改动、无迁移、无新权限点**。

### 交付物

| 文件 | 作用 |
| --- | --- |
| `frontend/packages/shared/src/sse/eventStream.ts`（新） | `EventStreamClient`：`?token=` 连接、`events` 白名单、`last_event_id` 续传、退避重连、30s 降级轮询、自愈 |
| `frontend/packages/shared/src/sse/eventStream.test.ts`（新） | TC-SSE-FE-01~18（74 用例全绿，其中本文件 18 条） |
| `frontend/packages/shared/src/index.ts` | barrel 导出 `EventStreamClient` 及类型 |

### 闸门（前端）

| 闸门 | 结果 |
| --- | --- |
| lint | ✅ `eslint .`（全仓，无告警） |
| typecheck | ✅ `pnpm -r typecheck`（shared/admin/mobile 全过） |
| 单测 | ✅ `pnpm -r test:unit`：30 文件 / **277 passed** |
| 格式 | ✅ `prettier --check` 通过 |
| 覆盖率 | ✅ 新模块 `eventStream.ts`：stmts **97.29%** / branch **91.66%** / funcs **100%** / lines **100%**（≥90%）。⚠️ 如实说明：`shared` **全包**聚合覆盖率为 83%（新增前基线即 77%），`vitest.config.ts` 的 90% 全局阈值在本仓**从未达标**（`client.ts`/`format.ts` 历史缺口），故本卡只保证**新模块** ≥90%，不越权改配置或去补无关历史缺口 |

### 遗留（登记，不在本卡）

| 项 | 说明 |
| --- | --- |
| 页面接入 | 「车间看板」属 P6、无设计稿 → T-PIECE-012（待 P6 看板设计稿） |
| `EventSource` 不暴露 HTTP 状态码 | 无法区分 `11001`/`11012`，统一走重连→降级；身份失效由轮询回调经 `ApiClient` 触发跳登录 |
| `shared` 全局覆盖率门槛未达标 | 属历史缺口（非本卡引入），需另开清理卡补齐 `client.ts`/`format.ts` 用例后方可启用 90% 硬阈值 |
| Vue composable | 将来各端要 `useEventStream` 时在 admin/mobile 包一层即可（本卡保持 shared 框架无关） |
