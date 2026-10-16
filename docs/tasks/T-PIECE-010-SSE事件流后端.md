# T-PIECE-010：计件事件流后端（发布 `piecework_counted` + SSE `GET /events/stream`）

| 项 | 内容 |
| --- | --- |
| 模块 | piecework（生产端）+ system/core（事件总线与 SSE 端点） |
| 负责人 | backend-dev |
| 状态 | `done` |
| 优先级 | P2（把 `04 §5.1 ⑪` / `04 §6.1` / `05 §8` 已设计但只有 501 占位的半条链路补齐） |
| 依赖 | T-PIECE-001~009（扫码链路 A 已闭环）；`01 §5`/`§121`（Redis Stream 领域事件总线）；`05 §8`（SSE 契约） |
| 被依赖 | T-PIECE-011（前端订阅端，下一步） |
| 关联设计 | [`modules/04-计件-SSE事件流设计.md`](../modules/04-计件-SSE事件流设计.md) |
| 关联规范 | [`modules/04-计件.md`](../modules/04-计件.md) §5.1 ⑪ / §6.1；[`05 §8`](../05-接口设计规范.md)；[`01 §5`](../01-技术选型与架构.md)（Redis 非唯一数据源） |
| 估算 | 1d |

## 背景

`04 §5.1 ⑪` 要求计件成功后「发 Redis Stream 事件 → SSE 广播 `piecework_counted`」，
`04 §6.1` 登记了 `GET /events/stream`，`05 §8` 给了事件帧格式与降级约定；
但代码里只有一个返回 **501** 的占位端点，**生产端一次事件都没发**。本卡补齐后端。

本卡**不做**任何提醒策略（Q-PW12 另说）：只把「已经发生的计件事实」实时广播出去。
事件**不是真值来源**（`05 §8` 禁止用 SSE 下发写操作结果）——员工确认计件结果仍以
`POST /piecework/count` 的响应为准。

## 目标

1. 新增 `core/events.py`：`erp:events` 流的 best-effort `publish_event` + 阻塞 `read_events`。
2. `count_piecework` 提交后（**非幂等**时）发布 `piecework_counted`；发布失败**不影响**计件。
3. 用真实 SSE 端点替换 501 占位：`GET /api/v1/events/stream`，支持 `?token=` /
   `Authorization` 头、`Last-Event-ID` 续传、`events` 过滤、25s `:ping` 心跳。
4. Redis 不可用 → 该端点 `503` / `11012`（新增错误码，登记 `05 §4`）。

## 不做什么

| 项 | 原因 |
| --- | --- |
| 前端 EventSource 封装 / 轮询降级 | 另开 T-PIECE-011（本卡只保证后端契约） |
| 其它事件名（打菲 / 库存） | 总线可扩展；本卡只落地 `piecework_counted` |
| 事件持久化 / 审计表 | SSE 是实时提示，不落库（`05 §8`） |
| 改 `count_piecework` 的校验链 / 事务边界 | 本卡只在 commit 之后旁挂发布（INV-1） |

## 接口

| 方法 | 路径 | 权限点 | 请求 | 响应 | 错误码 |
| --- | --- | --- | --- | --- | --- |
| GET | `/api/v1/events/stream` | 已登录 | `token`（query，或 `Authorization` 头）、`events`（可选 csv）、`last_event_id`（可选）或 `Last-Event-ID` 头 | `text/event-stream`：`id:`/`event:`/`data:` + `:ping` | `11001`、`11012` |

## 验收标准

- [ ] `publish_event`：`XADD erp:events MAXLEN ~ 1000`；Redis 未配 / 异常 → 返回 `None` 且仅 WARN，不抛
- [ ] 计件成功 → 流里出现 1 条 `piecework_counted`，`data` 含 `bundle_no/style_no/operation_no/employee_name/qty`
- [ ] 幂等重试（`idempotent=true`）→ **不**新增事件
- [ ] 计件响应不受发布结果影响（发布抛错也 200）
- [ ] `GET /events/stream`：无 token → `11001`；Redis 不可用 → `11012`（503）
- [ ] 已登录 + `last_event_id=0` → 收到 `id:` / `event: piecework_counted` / `data:` 帧，`Content-Type: text/event-stream`
- [ ] `events` 过滤：不含匹配名的事件被跳过
- [ ] `?token=` 与 `Authorization` 头两种鉴权等价
- [ ] 移除 `main.py` 的 501 占位；OpenAPI 契约重生成且重跑无 diff

## 风险

| 风险 | 缓解 |
| --- | --- |
| 发布拖慢 / 拖垮计件 | best-effort + 短超时（沿用 `Redis.from_url` 的 2s）+ 失败仅 WARN（INV-1） |
| 发布与计件事务耦合 | 发布放在 service 返回（已 commit）之后的路由器里 |
| SSE 端点无消费方 | 本卡交付后端契约；`05 §8` 已约定，前端 T-PIECE-011 接入 |
| `?token=` 进反代日志 | access token 仅 15 分钟；日志脱敏记入遗留 |

## 实施结果

**状态：done（2026-10-16）**。后端契约卡，生产 `piecework_counted` + 真实 SSE 端点；
**无迁移、无新权限点**（新增错误码 `11012`）。

### 交付物

| 文件 | 作用 |
| --- | --- |
| `backend/app/core/events.py`（新） | `erp:events` 流：`publish_event`（best-effort，`MAXLEN ~ 1000`）+ `read_events`（`XREAD BLOCK`） |
| `backend/app/modules/piecework/events.py`（新） | `publish_counted_event`：查 `users.name` + 组装 `05 §8` 契约字段 |
| `backend/app/modules/system/events.py`（新） | `GET /events/stream`：SSE 帧 / 心跳 / 续传 / 过滤 / `redis_available` |
| `backend/app/core/permissions.py` | `_build_context`（唯一鉴权实现）+ `get_sse_auth_context`（头或 `?token=`） |
| `backend/app/core/errors.py`、`docs/05 §4` | 新增 `11012`（503，Redis 不可用） |
| `backend/app/main.py` | 移除 501 占位、注册 `events_router` |
| `backend/app/modules/piecework/router.py` | 计件提交后、非幂等时发布事件 |
| `backend/tests/modules/test_events_stream.py`（新） | TC-EVT-01~10 |
| `backend/tests/{conftest,test_app_base,test_errors_registry}.py` | 清 `erp:events`；501 断言改「未登录 401」；`11012` HTTP 映射 |
| `backend/openapi.json`、`frontend/packages/shared/src/api/schema.d.ts` | 契约重生成（`+94` / `+33`；`permissions.ts` 零 diff） |

### 闸门

| 闸门 | 结果 |
| --- | --- |
| 1 lint | ✅ `ruff check` + `ruff format --check` 全仓 |
| 2 typecheck | ✅ 后端 `mypy app`（192 文件） |
| 3 单测 | ✅ **1114 passed / 90.80%**（新增 10 条 TC-EVT） |
| 4 迁移 | ✅ 本卡**无迁移**；`alembic upgrade/downgrade/upgrade` 往返 + `alembic check` 无漂移 |
| 5 镜像 | ✅ `api-image` + `web-image` 均构建成功、可导入 |

### 遗留（登记，不在本卡）

| 项 | 说明 |
| --- | --- |
| 前端订阅端 | EventSource 封装 + 30s 轮询降级 + 看板接入 → T-PIECE-011 |
| `?token=` 与反代日志 | access token（15min）可能进 nginx 访问日志的 query；建议对 `events/stream` 的 query 脱敏 |
| Redis 中途掉线 | 连接建立后 Redis 掉线只会持续 `:ping`（不主动断开）；靠客户端重连 |
| SSE 长连接占用一个 DB 连接 | 鉴权用的 `get_db` session 随响应存续到断开；观众端数量有限时可接受，超阈值时改为鉴权后立即释放 |
| 其它事件名 | 总线可扩展（`bundling_*` / `stock_*`），本卡只落地 `piecework_counted` |
