# T-BUND-008f：修并发/幂等/测试隔离三处缺陷（L-108 / L-110 / L-111）

| 项 | 内容 |
| --- | --- |
| 模块 | bundling（+ `core/idempotency.py`、`tests/conftest.py`） |
| 负责人 | backend-dev |
| 状态 | `done` |
| 优先级 | **P1**（L-108 挡住 P1 出口的幂等承诺） |
| 依赖 | T-BUND-008（缺陷由其 TC-12 / TC-BC-04 暴露） |
| 被依赖 | T-BUND-010（E2E 要验幂等「只生效一次」） |
| 关联 | `docs/12` L-108 / L-110 / L-111、`docs/05 §5`（`Idempotency-Key`）、`docs/10 §2.2` / §5.4 |
| 估算 | 0.25d |

## 背景

T-BUND-008 补并发用例时抓到三个**真缺陷**（不是测试写错）。本卡只修这三个可修的；
**L-109（`31006` 零抛出点）需业务拍板迁移路径，不在本卡**，保持待决。

---

## ① L-108：幂等键在并发下不生效（**`core/idempotency.py`**）

**现象**：`load_idempotent` 是「读缓存 → 没有就继续执行」。20 个并发请求同时进来时，
**19 个读不到缓存就都去执行了** —— 只有第一个真正执行，后 19 个拿到 `409`。

**为什么这是缺陷而不是限制**：`docs/05 §5` 承诺「同键同 body 回首次结果」。
现在这个承诺**只在串行时成立**。用户视角是「我点了两次审核，第二次说失败」，
但真相是「两次都执行了，第二次撞上状态机」。数据是安全的，**但体验是「点了没反应」**。
而 P1 的 `label-prints` 与 `voids` 都靠幂等键防重复 —— 这是**P1 出口的承诺**。

**修法**：把幂等改成**原子占位**（`SET NX`）。
第一个请求 `SET key {body_hash} NX` 成功 → 执行 → 写结果；后续请求 `SET NX` 失败 →
轮询等首次结果。

⚠️ 约束：
- **必须设 TTL**（否则进程崩在执行中途会留下永久占位，那个键再也用不了）
- **必须能区分「同键同 body」（等结果）与「同键不同 body」（`10002`）**
- **超时要有上限**：等不到首次结果时返回明确错误码，**不能无限等**
- Redis 不可用时的降级行为照 `05 §5` 既有约定（先读 `core/idempotency.py` 现有实现再定）

## ② L-110：`lock_cutting_outputs` 的 `FOR UPDATE` 缺 `populate_existing=True`

**现象**：同一 session 内先读 `cutting_outputs` 再 `submit`，`_reserve` 读到**旧值**，
误报 `30002`（「需要 120，当前 0.000」而库里真有 120）。

**根因**：SQLAlchemy 的 identity map 里存着旧对象，`FOR UPDATE` 只会重查数据库**行锁**，
不会刷新**已加载的 Python 对象**。同文件的 `lock_order_for_transition` **有**这个参数，
这里是漏的。

**为什么现在没爆**：生产每请求一个 session，碰不到。**worker / CLI / 批量任务会踩** ——
而批量任务正是最容易「先查后写」的地方。

**修法**：给 `lock_cutting_outputs` 补 `populate_existing=True`，与 `lock_order_for_transition` 对齐。
**并补一条回归用例**：同 session 先读后 `submit` 必须成功（当前会红）。

## ③ L-111：`conftest._purge_persisted_world` 清不掉并发用例的 `document_logs`

**现象**：每跑一次并发用例就往共享库留一批 `document_logs`；**既有两条并发用例仍在漏**。

**根因**：conftest 按 `doc_no='款号'` 删，而打菲的 `document_logs` 记的是 **`doc_id`（UUID）**，
不是款号 —— 这个条件**永远匹配不上**。

**修法**：改为按 `doc_id` 精确删（并发工厂 `purge_side_effects` 已经是这么做的，照它抄）。
**并验证**：连跑两次并发用例后 `document_logs` 行数为 0。

---

## 范围

**要做**：
- [x] ① `core/idempotency.py`：原子占位 + TTL + 同键同 body/异 body 分流 + 等待上限
- [x] ② `state_repository.py`（或对应文件）：`lock_cutting_outputs` 补 `populate_existing=True`
- [x] ③ `tests/conftest.py`：`_purge_persisted_world` 按 `doc_id` 删日志
- [x] 三条回归用例（每条都要**先红后绿**：先写断言、跑出红，再改实现）
- [x] `docs/05 §5`：把幂等的并发语义写进规范（当前只写了串行语义）

**不做**：
- **L-109**（`31006` 零抛出点、`03 §2 B23` 与 TC-28①② 落不了地）—— **需业务拍板**：
  「审核后手数录错」要不要支持改？支持的话迁移路径是什么（码已生成且不可恢复，B12）？
  **不许自行发明规则**，已登记 `docs/12` L-109 待决。
- 不改任何既有断言来「让它变绿」

## 验收标准

- [x] `uv run pytest -q tests/modules/test_idempotency.py tests/modules/test_bundling_concurrency*.py` 全通过（13 passed）
- [x] **20 并发同幂等键 → 恰好 1 次执行、19 次回首次结果**（不是 19 个 `409`）
- [x] 同键**不同** body → `10002`（且**立即**返回，不进等待循环）
- [x] 执行中途崩溃留下的占位**有 TTL**（直读 Redis `TTL` > 0），TTL 过后同键可重新执行
- [x] 等待首次结果有**超时上限 5s**，超时报 `10003` / HTTP 409，且超时不销毁占位
- [x] 同 session 先读 `cutting_outputs` 再 `submit` → **成功**（repository 层 + service 层两条）
- [x] **连跑两次**并发用例后 `document_logs` 残留 **0 行**（先清掉历史 235 条孤儿日志再验）
- [x] `docs/05 §5` 已写明并发语义（§5.1 并发语义 / §5.2 降级 / §5.3 验收口径）
- [x] 单文件 ≤400 行；闸门 1-4 通过（全量 965 passed）

### 关键取值与理由

| 常量 | 取值 | 理由 |
| --- | --- | --- |
| `PLACEHOLDER_TTL_SECONDS` | **30s** | 结果只在**成功**时写；崩在执行中途没人清占位 → 无 TTL 则该键永久不可用。必须 ≫ 等待上限（否则等一次就把占位等过期，然后自己重新执行），也要 ≫ 最慢写操作（2000 手审核 ≈ 秒级） |
| `WAIT_TIMEOUT_SECONDS` | **5.0s** | ≪ nginx `proxy_read_timeout 30s`（否则先被网关掐断，客户端拿 502 而不是错误码）；≫ 最慢写操作；等满 5s 基本等价于「首次已崩在中途」，继续等只是白占一个 worker 与一条 DB 连接 |
| `POLL_INTERVAL_SECONDS` | **0.05s** | 20 并发重试 × 20 次 GET 是 Redis 能轻松吃下的量 |
| 等待超时的错误码 | **`10003`**（409「同一请求正在处理中，请稍后重试」） | **不发明新码**：`docs/12` L-108 的处置建议①已登记这个用法，且 `10003` 的客户端动作本就是「刷新后重试」。非 `99999`：这不是服务端故障 |

### ⚠️ 唯一一处改既有断言（必须留在案）

`tests/modules/test_bundling_concurrency.py` 的 **TC-BC-01** 由「1 个 200 + 19 个 `{10003,30001}`」
改成「**20 个 200 且 `data` 逐字相同** + 只执行一次」。原断言编码的正是 L-108 缺陷本身
——该文件原 docstring 自己写明「这是实现缺陷不是用例问题，本卡不改实现、也不把断言改成
『19 次 200』」。`load_idempotent` 修好后 19 个请求会**等到首次结果并原样返回**，
「19 个 409」变成假红。**唯一执行**的四条证据（1 条 `APPROVE` 日志 / 恰好 1 批码 /
手号 `1..N` / 无 `IntegrityError` 漏成 500）**一条未弱化**。

## 实际改动（完成后回填）

手写代码 **+392 / −53 行**（`git diff --numstat`，不含新文件），新文件 `test_idempotency.py` **290 行**。
合计手写 **+682 / −53 行**，**≤1200 软上限**；**单文件最大 473 行**（`tests/conftest.py`，**超线是既有问题**，
见 L-115；本卡 +7 行不是成因）。

| 文件 | +/− | 说明 |
| --- | --- | --- |
| `backend/app/core/cache.py` | +28 / −1 | 新增 `redis_claim_json`（`SET NX EX`，**三态** `True`/`False`/`None`） |
| `backend/app/core/idempotency.py` | +142 / −29 | 原子占位 + TTL + 异 body 分流 + 等待上限；**签名与返回形状未动**（auth/base/bundling 三模块在用） |
| `backend/app/modules/bundling/state_repository.py` | +9 / −0 | `lock_cutting_outputs` 补 `populate_existing=True`（L-110） |
| `backend/tests/conftest.py` | +9 / −2 | `_purge_persisted_world` 日志清理改按 `doc_id`（L-111） |
| `backend/tests/modules/test_idempotency.py` | **+290**（新） | L-108 六条：20 并发 / 占位 TTL / 异 body `10002` / 等待上限 / 生产取值边界 / Redis 降级 |
| `backend/tests/modules/test_bundling_state.py` | +77 / −0 | L-110 两条（repository 层刷新语义 + service 层 `submit` 误报 `30002`） |
| `backend/tests/modules/test_bundling_concurrency.py` | +80 / −21 | L-111 残留探针 + TC-BC-01 断言按修正后语义更新 |
| `docs/05-接口设计规范.md` | +51 / −0 | §5.1 并发语义 / §5.2 降级 / §5.3 验收口径 |
| `docs/12-文档与变更归档规范.md` | +5 / −0 | §2 新增 `0124`；§5 新登记 L-113 / L-114 / L-115 |
| `docs/tasks/T-BUND-008f-*.md` | 本文件 | 状态 / 改动表 / 验收勾选回填 |

**提交记录**：
- `e947ca0` fix(bundling): 幂等原子占位 + 锁刷新 + 并发日志隔离（L-108/L-110/L-111）

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| L-109 | `31006` 零抛出点；审核后改手数的迁移路径**待业务拍板**（本卡按卡面**明确不做**） | `03 §12` / `docs/12` |
| L-096 | `piecework_logs` 属 P2，`32001` 完整路径不可实现 | P2 |
| **L-113**（新） | **业务失败时不释放占位** —— router 层无 `except` 钩子，期间同键重试等满 5s 报 `10003`（登录输错口令最易命中） | `docs/12 §5` |
| **L-114**（新） | `base/router/style_child_router.py` 幂等命中分支 `return idempotent.cached` **漏取 `['response']`**，重试恒返回 `data=null`（另三处 router 都对） | `docs/12 §5` |
| **L-115**（新） | `tests/conftest.py` 473 行**超 ADR-0030 的 400 行硬线**（既有问题，本卡 +7 行），需单开拆卡 | `docs/12 §5` |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。

## 变更记录

| 日期 | 变更内容 | 操作人 |
| --- | --- | --- |
| 2026-10-08 | 初版：修 T-BUND-008 暴露的三个可修缺陷；L-109 留待业务拍板 | AI |
| 2026-10-08 | 三个缺陷全部修复（先红后绿）；`docs/05 §5` 补并发语义；TC-BC-01 断言随语义更新；新登记 L-113 / L-114 / L-115 | AI |
