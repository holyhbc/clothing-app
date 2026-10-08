# T-BUND-008f：修并发/幂等/测试隔离三处缺陷（L-108 / L-110 / L-111）

| 项 | 内容 |
| --- | --- |
| 模块 | bundling（+ `core/idempotency.py`、`tests/conftest.py`） |
| 负责人 | backend-dev |
| 状态 | `todo` |
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
- [ ] ① `core/idempotency.py`：原子占位 + TTL + 同键同 body/异 body 分流 + 等待上限
- [ ] ② `state_repository.py`（或对应文件）：`lock_cutting_outputs` 补 `populate_existing=True`
- [ ] ③ `tests/conftest.py`：`_purge_persisted_world` 按 `doc_id` 删日志
- [ ] 三条回归用例（每条都要**先红后绿**：先写断言、跑出红，再改实现）
- [ ] `docs/05 §5`：把幂等的并发语义写进规范（当前只写了串行语义）

**不做**：
- **L-109**（`31006` 零抛出点、`03 §2 B23` 与 TC-28①② 落不了地）—— **需业务拍板**：
  「审核后手数录错」要不要支持改？支持的话迁移路径是什么（码已生成且不可恢复，B12）？
  **不许自行发明规则**，已登记 `docs/12` L-109 待决。
- 不改任何既有断言来「让它变绿」

## 验收标准

- [ ] `uv run pytest -q tests/modules/test_idempotency.py tests/modules/test_bundling_concurrency*.py` 全通过
- [ ] **20 并发同幂等键 → 恰好 1 次执行、19 次回首次结果**（不是 19 个 `409`）
- [ ] 同键**不同** body → `10002`
- [ ] 执行中途崩溃留下的占位**有 TTL**，TTL 过后同键可重新执行（有测试证明）
- [ ] 等待首次结果有**超时上限**，超时报明确错误码
- [ ] 同 session 先读 `cutting_outputs` 再 `submit` → **成功**（当前会红）
- [ ] **连跑两次**并发用例后 `document_logs` 残留 **0 行**
- [ ] `docs/05 §5` 已写明并发语义
- [ ] 单文件 ≤400 行；闸门 1-4 通过

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| 待回填 | | |

**提交记录**：
- `<hash>` fix(bundling): 幂等原子占位 + 锁刷新 + 并发日志清理（L-108/L-110/L-111）

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| L-109 | `31006` 零抛出点；审核后改手数的迁移路径**待业务拍板** | `03 §12` / `docs/12` |
| L-096 | `piecework_logs` 属 P2，`32001` 完整路径不可实现 | P2 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。

## 变更记录

| 日期 | 变更内容 | 操作人 |
| --- | --- | --- |
| 2026-10-08 | 初版：修 T-BUND-008 暴露的三个可修缺陷；L-109 留待业务拍板 | AI |
