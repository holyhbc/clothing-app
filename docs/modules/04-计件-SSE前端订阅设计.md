# 设计：计件事件流前端订阅端（`@garment/shared` 的 SSE 客户端）

| 项 | 内容 |
| --- | --- |
| 任务卡编号 | T-PIECE-011 |
| 模块 | piecework（前端订阅端）；落在 `@garment/shared`（框架无关） |
| 编写人 | AI |
| 日期 | 2026-10-16 |
| 状态 | 已确认（用户 2026-10-16 选择「只做 shared SSE 客户端 + 30s 轮询降级」，不建看板页面） |
| 关联需求 | — |
| 关联 ADR | 无（SSE 契约与降级规则见 [`05 §8`](../05-接口设计规范.md)；后端实现见 T-PIECE-010；**看板页面属 P6，本卡不碰**） |

---

## 1. 背景与目标

**为什么做**：T-PIECE-010 把后端的 `piecework_counted` 广播与 `GET /events/stream`
补齐了，但前端没有任何订阅代码 —— `05 §8` 明确要求前端「订阅事件流，失败时降级为 30s 轮询」。
本卡交付这一层**可复用、框架无关**的客户端。

**成功标准（可度量）**：

1. 给定 `piecework_counted`，连接 `GET /api/v1/events/stream`（token 走 `?token=`，携带
   `events` 白名单），收到事件后回调出 `{event, data, id}`。
2. 断线自动重连，且重连时**带 `last_event_id` 续传**（`05 §8`「断线重连带 Last-Event-ID」）。
3. 连续失败 / 浏览器不支持 `EventSource` / 无 token 时**降级为 30s 轮询**（`05 §8`）。
4. `@garment/shared` 覆盖率闸门 **≥ 90%**（`vitest.config.ts` thresholds），全仓 typecheck 通过。

**非目标（明确不做）**：

- **看板页面**：消费端「车间看板」属 P6，尚无设计稿 —— 本卡不发明任何业务页面/交互。
- 事件的业务解析：`data` 是 JSON 文本，本卡**只递原文**，不认识 `piecework_counted` 的字段。
- 轮询**哪个接口**：由宿主通过 `onFallbackPoll` 回调决定（shared 不认识业务资源）。
- 与 `ApiClient` 的耦合：SSE 客户端**不**复用请求层的解包/刷新逻辑（事件流不是
  `{code,message,data}` 包装），只复用 `buildQuery`。

---

## 2. 领域模型

### 2.1 实体与字段

不新建类型定义（`03 §2.1` 规则 3：类型只放 shared，接口类型来自生成物）。本卡只定义
**流客户端的运行时类型**（非接口 DTO）：

| 项 | 值 | 说明 |
| --- | --- | --- |
| `StreamEvent.event` | string | 事件名，如 `piecework_counted`（`<资源>_<动作>`） |
| `StreamEvent.data` | string | 原始 JSON 文本（**不**替你 `JSON.parse`） |
| `StreamEvent.id` | string \| null | 服务端 `id:`（Redis Stream ID），续传锚点 |
| `StreamStatus` | `idle`/`connecting`/`open`/`reconnecting`/`polling`/`closed` | 供 UI 显示「实时 / 已降级」 |

### 2.2 关系

宿主（未来的看板 / 工位机页）`new EventStreamClient({...})` → `open()`；客户端持有
一条 `EventSource`；`error` 时自管重连 / 降级；`close()` 释放。客户端**不认识**业务，
只把事件交给 `onEvent`。

### 2.3 不变量

| # | 不变量 | 由谁保证 |
| --- | --- | --- |
| FE-INV-1 | 事件是**实时提示**，不写真值状态；消费端确认仍查接口（`05 §8`） | 客户端只回调，不改任何 store |
| FE-INV-2 | 重连**必须**带 `last_event_id`，不得重复投递已收事件 | 客户端记录 `lastEventId` 并放进重连 URL |
| FE-INV-3 | 降级轮询周期固定 **30s**（不叠加退避，避免饿死） | `fallbackIntervalMs` 默认 30000 |
| FE-INV-4 | 客户端**不抛**未捕获异常；轮询回调的失败由宿主自己的请求层提示 | `onFallbackPoll` 用 `Promise.resolve().catch()` 兜住 |
| FE-INV-5 | `close()` 之后**不再**有任何定时器 / 连接 / 回调 | `_clearTimers()` + `closed` 标志 |

### 2.4 索引与查询场景

不适用（前端内存态，无持久化、无查询）。

---

## 3. 状态机

客户端自身的连接状态机（**不是**单据状态机，`08` 不适用）：

```
idle ──open()──▶ connecting ──onopen──▶ open
                    │                     │
                 error×max             error
                    │                     │
                    ▼                     ▼
                 polling ◀──error×max── reconnecting ──timer──▶ connecting
                    │(每 30s 回调 onFallbackPoll)
                 close() ──▶ closed（终态）
```

- `idle → connecting`：`open()`。
- 触发降级的三种情形：① 浏览器不支持 `EventSource`；② `getToken()` 为 `null`；
  ③ 连续失败达到 `maxReconnectAttempts`（默认 3）。
- `polling` 保留**自愈**：除非是「不支持 EventSource」这种不可逆情形，否则每个轮询
  周期顺带**重试一次 SSE**；一旦 `onopen` 即回到 `open` 并停掉轮询（INV-3 的周期不变）。

---

## 4. 接口清单

本卡**不新增后端接口**，消费 T-PIECE-010 的：

| 方法 | 路径 | 权限点 | 请求要点 | 响应要点 | 错误码 |
| --- | --- | --- | --- | --- | --- |
| GET | `/api/v1/events/stream` | 已登录 | `token`（query）、`events`（csv 白名单）、`last_event_id`（可选，续传） | `text/event-stream`：`id:`/`event:`/`data:` + 每 25s `:ping` | `11001`；Redis 不可用 `11012`（503） |

- 客户端行为：`11001`/`11012` 都表现为 `EventSource` 的 `error`（浏览器不暴露状态码）
  → 统一走重连→降级；**不做**错误码分支（`EventSource` 拿不到 `code`）。

---

## 5. 并发与一致性（前端侧）

| 风险 | 场景 | 方案 |
| --- | --- | --- |
| token 过期（15min）后重连失败 | access token 短期 | 重连时**重新**取 `getToken()` 构造 URL（原生 `EventSource` 自动重连会沿用旧 URL/旧 token） |
| 重连风暴 | 后端短时不可用 | 退避 `reconnectDelayMs × 尝试次数`，封顶 `fallbackIntervalMs` |
| 定时器叠加 | 重连定时器与轮询定时器同时存活 | 任一状态切换前 `_clearTimers()` 清干净 |
| 重复投递 | 断线期间的事件 | 记 `lastEventId` 续传（FE-INV-2） |
| 轮询回调抛错 | 宿主实现有 bug | `Promise.resolve().catch()` 兜住（FE-INV-4） |

**必须配套的并发/时序测试用例**：见任务卡验收（fake timers 编排）。

---

## 6. 前端设计

### 6.1 位置与形态

放 `@garment/shared`（**框架无关**，与 `ApiClient` 同层），**不**放 admin/mobile 的
`composables/` —— 因为它是纯逻辑，两端（PC 看板 / 员工端首页）都要用，且 shared 约定
「不依赖 Vue」（见 `client.ts` 注释）。

导出：`EventStreamClient` + `EventStreamOptions` / `StreamEvent` / `StreamStatus` /
`EventSourceLike` / `EventSourceFactory`，经 `src/index.ts` barrel（`03 §2` 规则：页面
从 `@garment/shared` import）。

### 6.2 可测试性

`EventSource` 在 node 环境不存在 → 构造器通过 `eventSourceFactory` 注入替身
（与 `ApiClient` 注入 `fetchImpl` 同思路）。测试用 fake timers 编排退避与 30s 轮询。

### 6.3 消费端（后续卡，不在本卡）

未来看板页只需：

```ts
const stream = new EventStreamClient({
  getToken: () => client.accessToken,
  events: ['piecework_counted'],
  onEvent: (e) => { /* 提示 + 失效对应查询 */ },
  onFallbackPoll: () => { void reloadBoard() },   // 30s 轮询兜底
  onStatus: (s) => (status.value = s),
})
stream.open()
onBeforeUnmount(() => stream.close())
```

---

## 7. 风险与回滚

| 风险 | 影响 | 缓解 | 回滚方式 |
| --- | --- | --- | --- |
| 无消费端（看板未建） | 该客户端暂无页面使用 | 仅交付**规范已定行为**（`05 §8`），不建业务 UI；P6 看板接入即生效 | 删除 `src/sse/` 与导出（无副作用） |
| `EventSource` 拿不到 HTTP 状态码 | 无法区分 `11001`/`11012` | 统一重连→降级；身份失效由轮询回调经 `ApiClient` 触发跳登录 | — |
| 轮询回调重复注册 | 宿主 `open()` 调多次 | 客户端幂等：`open()` 先 `close()` 再连 | — |
| 与 `ApiClient` 类型/工具分叉 | query 拼接两套 | 复用导出的 `buildQuery` | — |

- **迁移回滚**：不适用（无数据库改动）。
- **发布回滚**：`shared` 是源码包，回退提交即可；无运行时副作用。

---

## 8. 任务拆分

| 任务卡 | 描述 | 依赖 | 改动文件 | 验收标准 |
| --- | --- | --- | --- | --- |
| T-PIECE-011 | 前端：shared SSE 客户端 + 30s 轮询降级（本卡） | T-PIECE-010 | `frontend/packages/shared/src/sse/eventStream.ts`（新）、`src/sse/eventStream.test.ts`（新）、`src/index.ts` | 见任务卡验收 |
| T-PIECE-012（待开） | 看板页面接入（P6，需先出看板设计稿） | 本卡 + P6 看板设计 | 待定 | 待定 |

---

## 9. 测试计划

| 层级 | 用例 | 断言 |
| --- | --- | --- |
| 单元 | 连接 URL | 含 `token` 与 `events` 白名单；默认 `baseURL=/api/v1` |
| 单元 | 收事件 | `onEvent` 收到 `{event,data,id}`；`lastEventId` 更新 |
| 单元 | 不支持 EventSource | 立即 `polling`；每 30s 调 `onFallbackPoll` |
| 单元 | 无 token | 不建连接，直接 `polling` |
| 单元 | 连续失败 | 退避重连；达上限 → `polling` |
| 单元 | 断线续传 | 重连 URL 带 `last_event_id` |
| 单元 | `close()` | status `closed`；再 `error` 不重连；定时器清空 |
| 单元 | 轮询回调 reject | 不产生未处理拒绝（FE-INV-4） |
| 单元 | 轮询自愈 | 一个周期内 SSE 恢复 → 回 `open`、停轮询 |

覆盖闸门：`shared` 覆盖率 **≥ 90%**（statements/branches/functions/lines）。

---

## 10. 待决问题

| # | 问题 | 需要谁确认 | 阻塞什么 | 状态 |
| --- | --- | --- | --- | --- |
| Q-SSE-FE-1 | 看板页面显示哪些实时指标、是否要「实时/已降级」角标 | 业务方（车间/跟单）+ 前端 | **不阻塞本卡**（本卡只交客户端；页面属 P6） | 待确认 |
| Q-PW12 | 部分生产后的「剩余手」是否要自动提醒 | 业务方 + 生产 | 不阻塞本卡 | 待确认 |

> 本卡无阻塞性待决问题（两个待决都属于「页面/提醒策略」，与「订阅客户端」是两层）。
