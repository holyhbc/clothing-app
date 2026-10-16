# 设计：计件事件流（SSE `piecework_counted`）

| 项 | 内容 |
| --- | --- |
| 任务卡编号 | T-PIECE-010 |
| 模块 | piecework（生产端）+ system/core（事件总线与 SSE 端点） |
| 编写人 | AI |
| 日期 | 2026-10-16 |
| 状态 | 已确认 |
| 关联需求 | — |
| 关联 ADR | 无（Redis Stream 作为领域事件总线见 [`01 §5`](../01-技术选型与架构.md) / `01 §121`；SSE 契约见 [`05 §8`](../05-接口设计规范.md)；事件触发点见 [`04 §5.1 ⑪`](./04-计件.md)） |

---

## 1. 背景与目标

**为什么做**：`04 §6.1` 登记了 `GET /events/stream`（SSE），`04 §5.1 ⑪` 要求扫码计件
成功后**广播 `piecework_counted`**，供看板 / 主管端实时看到「谁在这手码上计了几件」。
现状是 `main.py` 里一个 **501 占位端点**（「计划在 P2 阶段交付」），生产端一次事件都没发。
本卡把这半条链路补齐。

**成功标准（可度量）**：

1. 扫码计件（非幂等）成功提交后，`erp:events` 流里出现一条 `piecework_counted`，字段与 `05 §8` 示例一致。
2. `GET /api/v1/events/stream` 建立后能收到该事件；带 `Last-Event-ID` 可从断点续传；无事件时每 25s 收到 `:ping`。
3. 计件响应**不依赖**事件发布是否成功（Redis 故障时计件仍 200）。

**非目标**：

- 前端订阅端（EventSource 封装 + 30s 轮询降级）——另开前端卡 T-PIECE-011。
- 其它事件名（`bundling_*` / `stock_*`）——总线可扩展，但本卡只生产 `piecework_counted`。
- 事件持久化 / 审计（SSE 是**实时提示**，不是真值来源；真值查接口，`05 §8`）。

---

## 2. 领域模型

### 2.1 实体与字段

不新建表。事件用 **Redis Stream** 承载（`01 §5`：Redis 不做唯一数据源，做队列）。

| 项 | 值 | 说明 |
| --- | --- | --- |
| Stream key | `erp:events` | 全局单流，事件名放 payload，避免按事件名建流导致订阅端要列举流 |
| 字段 `event` | `piecework_counted` | 事件名 `<资源>_<动作>` 蛇形（`05 §8`） |
| 字段 `data` | JSON 字符串 | `{bundle_no, style_no, operation_no, employee_name, qty}`（`05 §8` 示例逐字） |
| Stream ID | Redis 自动 | 26 位 ms 序号，SSE 的 `id:` 用它做 `Last-Event-ID` 续传锚点 |

### 2.2 关系

事件生产方 = `count_piecework` 提交之后的路由器；消费方 = SSE 端点（可多连接）。
两者只通过 `erp:events` 解耦，无直接调用。

### 2.3 不变量

| # | 不变量 | 由谁保证 |
| --- | --- | --- |
| INV-1 | 计件落库成功**与**事件发布成功是两件事；发布失败绝不回滚计件 | service 先 commit，router 后发布（best-effort） |
| INV-2 | 幂等重试（`idempotent=true`）**不重复发布** | router 判 `out.idempotent` 后才发布 |
| INV-3 | 事件里绝不出现金额以外的敏感信息（无 token / 无口令） | 事件字段白名单（仅 5 个业务字段） |
| INV-4 | 流长度有上限，不会无限增长 | `XADD ... MAXLEN ~ 1000`（近似修剪） |

### 2.4 索引与查询场景

| 场景 | Redis 命令 | 说明 |
| --- | --- | --- |
| 发布事件 | `XADD erp:events MAXLEN ~ 1000 * event <t> data <json>` | O(1) |
| 从头/续传读 | `XREAD BLOCK <ms> COUNT 100 STREAMS erp:events <id>` | WebSocket 式阻塞读 |

> Stream 是 Redis 内建结构，无额外索引。

---

## 3. 状态机（如适用）

不适用（理由：事件是**已发生事实的广播**，无状态迁移；计件单本身的状态机见 `04 §5`，
本卡不改动它）。

---

## 4. 接口清单

| 方法 | 路径 | 权限点 | 数据范围 | 请求要点 | 响应要点 | 错误码 |
| --- | --- | --- | --- | --- | --- | --- |
| GET | `/api/v1/events/stream` | 已登录（无单独权限点，`04 §6.1`） | — | `token`（query，或 `Authorization` 头）、`events`（可选 csv 过滤）、`last_event_id`（可选）或 `Last-Event-ID` 头 | `text/event-stream`：`id:` / `event:` / `data:` 帧 + 每 25s `:ping` | `11001`；Redis 不可用 `11012` |

- 幂等要求：无（GET 只读，幂等天然成立）。
- 分页 / 排序 / 过滤参数：`events` 只做**事件名白名单过滤**；不做分页（流式）。
- SSE 事件：`piecework_counted`。

**鉴权方式（`05 §8`：token 走 query 或 cookie）**：本项目 access token 存
`localStorage`、走 `Authorization` 头，而浏览器的 `EventSource` 无法自定义请求头，
故本端点**额外接受 `?token=`**。服务端优先取 `Authorization` 头，缺失再取 `token` query。
两者都走同一个 `decode_token`（仍只带身份、权限照查库，`07 §1.1`）。

---

## 5. 并发与一致性

| 风险 | 场景 | 方案 |
| --- | --- | --- |
| 发布阻塞计件 | Redis 网络慢 / 挂 | 发布包 `try/except (RedisError, OSError, TimeoutError)`，失败仅 WARN，计件照常 200 |
| 同一连接读到重复事件 | `XREAD` 语义 | 用返回的 stream ID 作 `last_id` 游标推进，天然不重放 |
| 断线重连丢失 | 断连期间的事件 | 客户端带 `Last-Event-ID` 从断点读（`XREAD` 支持任意 ID 起读） |
| 订阅端上线前的事件 | 新连接默认 `$` | 默认只收**新**事件（`$`）；要历史由客户端显式给 `last_event_id` |

**必须配套的并发测试用例**：

- 计件成功 → 流里恰好一条事件；幂等重试 → **不新增**事件。
- Redis 不可用 → 计件仍 200、事件发布静默降级（WARN）。

---

## 6. 前端设计

不适用（理由：本卡是后端卡，前端订阅端另开 T-PIECE-011）。

---

## 7. 风险与回滚

| 风险 | 影响 | 缓解 | 回滚方式 |
| --- | --- | --- | --- |
| redis 未配置（本地 / 单进程）时 SSE 不可用 | 该端点 503（`11012`） | 文档明示「SSE 需 Redis」；前端按其降级为 30s 轮询（`05 §8`） | 回退镜像；无数据副作用 |
| `?token=` 进入 nginx / 反代日志 | token 泄漏 | access token 仅 15 分钟（`07 §1.1`）；建议反代对 `events/stream` 的 query 日志脱敏（记入遗留） | 停用该端点 |
| SSE 长连接期间**占用一个 DB 连接**（鉴权用的 `get_db` session 随响应存续到断开） | 观众端很多时可能耗尽连接池 | 观众端是看板 / 主管（数量有限）；若超阈值，改为「鉴权后立即释放 session 再进入流」（另开卡） | 停用端点 |
| 发布被计入计件事务 | 计件因 Redis 抖动失败 | 发布在 commit **之后**、且 best-effort（INV-1） | — |
| 流无限增长 | Redis 内存 | `MAXLEN ~ 1000` 近似修剪 | — |

- **迁移回滚**：本卡**无迁移**。
- **发布回滚**：镜像回退到上一 tag；无数据回滚脚本（事件是易失的实时提示）。

---

## 8. 任务拆分

| 任务卡 | 描述 | 依赖 | 改动文件 | 验收标准 |
| --- | --- | --- | --- | --- |
| T-PIECE-010 | 后端：事件发布 + SSE 端点（本卡） | T-PIECE-001~009 | `core/events.py`、`core/permissions.py`、`core/errors.py`、`modules/piecework/{router,events}.py`、`modules/system/events.py`、`main.py` | 见任务卡验收 |
| T-PIECE-011 | 前端：EventSource 封装 + 降级轮询 + 看板接入 | T-PIECE-010 | `frontend/packages/shared/src/api/*` | 断线自动重连、30s 轮询降级 |

---

## 9. 测试计划

| 层级 | 用例 | 断言 |
| --- | --- | --- |
| 单元 | `format_sse`（id/event/data 帧） | 帧格式与 `05 §8` 一致，data 为 JSON |
| 单元 | `_event_stream`（注入真 Redis，读到一条后取消） | 收到已发布事件；`events` 过滤生效 |
| 接口 | `GET /events/stream` 无 token | `11001` / 401 |
| 接口 | 计件成功发布事件 | 流里出现 `piecework_counted`，字段齐全 |
| 接口 | 幂等重试不重复发布 | 流里仍只一条 |
| 权限 | 已登录即可订阅（无额外权限点） | 200 且 `content-type: text/event-stream` |
| 并发 | Redis 不可用 | 计件仍 200；端点 503 `11012` |

---

## 10. 待决问题

| # | 问题 | 需要谁确认 | 阻塞什么 | 状态 |
| --- | --- | --- | --- | --- |
| Q-PW12 | 部分生产后的「剩余手」是否要自动提醒 | 业务方（车间/跟单）+ 生产 | **不阻塞本卡**：本卡只做「已发生事件的实时广播」，不做提醒策略 | 待确认 |

> 本卡无阻塞性待决问题（Q-PW12 属「提醒策略」，与「事件广播」是两层）。
