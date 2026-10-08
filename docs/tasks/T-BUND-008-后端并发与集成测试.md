# T-BUND-008：打菲后端并发/集成测试（唯一约束、超产、一码一员工、超可打菲数）

| 项 | 内容 |
| --- | --- |
| 模块 | bundling |
| 负责人 | qa |
| 状态 | `done`（2026-10-08；TC-20~TC-36 后端可自动化部分全部落成用例） |
| 优先级 | P1 |
| 依赖 | T-BUND-007 |
| 被依赖 | T-BUND-010（E2E 前） |
| 关联设计 | [`modules/03-打菲.md`](../modules/03-打菲.md) §7 并发、§11 TC-20~TC-36、§11.3 ADR-0016 必测对照 |
| 关联规范 | `docs/10-测试规范.md` §5（计件/库存/成本必写并发场景） |
| 估算 | 0.5d |

## 目标

把 `03 §11` 的 TC-20~TC-36 中**后端可自动化**的部分落成测试，
重点覆盖并发、唯一约束、码数=手数、超产 `32006`、一码一员工 `32001`、超可打菲数 `30002`。

## 范围

**要做**：
- [x] `backend/tests/modules/test_bundling_concurrency.py`（**新增，231 行**）
  - [x] 20 并发同 `Idempotency-Key` 审核 → 仅 1 次生成（**TC-12**，卡面原写 TC-34，已有）
  - [x] 并发两张**不同**的单抢同一尺码剩余量 → 一张 `30002`、INV-6 不破（**TC-13**）
- [x] `backend/tests/modules/test_bundling_concurrency_codes.py`（**新增，352 行**）
  - [x] 反审核后 20 并发审核 → 0 成功、20 个 `31005`（**TC-25 + TC-29** + 03 §7 第三层防线）
  - [x] 并发「计件回写 `counted_at`」vs「反审核」→ `32003` / 整单作废，绝不半单作废（**TC-35 替身**）
  - [x] 20 并发单码作废同一码 → 只 1 次生效、1 条 `VOID_CODE`、结转不动（03 §7 重复点击）
- [x] `backend/tests/modules/test_bundling_rules.py`（**新增，191 行**）
  - [x] TC-20 余数不出码（**审核落库层**）、TC-22 裁剪尾数一个码都不出
  - [x] TC-21 / TC-23 零手 / 负手 / 小数 → `10001` + 字段级定位（**接口边界层**）
- [x] `backend/tests/modules/test_bundling_rules_codes.py`（**新增，309 行**）
  - [x] TC-25 手号占用与新单重编、TC-28③ 已计件码件数不动、TC-31 权限两条、TC-33 部分生产可见
- [x] 补 `backend/tests/factories/bundling_concurrency.py`（**新增，285 行**：真提交夹具 + 精确清理）

**不做**：
- 前端/E2E（→ T-BUND-010）
- 不修改被测实现；发现缺陷另开修复提交（不得改断言让它变绿）
- `32001` 的计件侧完整路径属计件模块，本卡只覆盖打菲侧写 `counted_at` 后的拒绝逻辑

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `backend/tests/factories/bundling_concurrency.py` | 新增 | 真提交夹具 + 精确清理（原计划的「改 `bundling.py`」改为**新开**：它真提交，而 `bundling.py` 只回滚，混在一起会让读的人以为并发也共享事务） |
| `backend/tests/modules/test_bundling_concurrency.py` | 新增 | 申请侧并发 2 例 |
| `backend/tests/modules/test_bundling_concurrency_codes.py` | 新增 | 码侧并发 3 例 |
| `backend/tests/modules/test_bundling_rules.py` | 新增 | 整件口径 + 入参边界 5 例 |
| `backend/tests/modules/test_bundling_rules_codes.py` | 新增 | 码侧规则 4 例 |

## 实现要点（必读规范）

- [x] 真提交用例的清理用迁移账号（`purge_side_effects` 走 `erp_ddl`；软删行仍在，**不靠软删清理**）
- [x] 并发用例必须**真并发**：`asyncio.gather` + `asyncio.Barrier` + **每任务独立物理连接**
      （`NullPool`；接口层另加 `pooled_client` 每请求一条 session），不得用串行调用伪装
- [x] `filterwarnings=error`：踩到并解决了 `ResourceWarning: unclosed socket`（队列池把
      `dispose()` 之后才开的 session 的连接留在池里没人关）→ 改 `NullPool`
- [x] 错误码断言精确到 `code` 与 `details`（`31005` 精确断到 `details["constraint"]`）

## 验收标准

- [x] `uv run pytest -q tests/modules/test_bundling_concurrency.py tests/modules/test_bundling_rules.py` 全部通过
      （连同两个新增文件 `test_bundling_concurrency_codes.py` / `test_bundling_rules_codes.py` 共 **15 passed**）
- [x] 并发用例均为真并发（`asyncio.gather` + `asyncio.Barrier` + **每任务独立连接**）且稳定复现（连跑 4~8 次不 flaky）
- [x] `32006` 超产 / `32001` 一码一员工 → **依赖 P2**（`piecework_logs` 未建表，L-096，`ErrorCode` 里
      `32` 段除 `32003` 外仍在 `DEFERRED_CODES`）；本卡只覆盖「打菲侧 `counted_at` 已写 → 拒绝」这一段
      （`32003`），**未为此建表、未伪造计件流水**
- [x] `30002` 超可打菲数：正例（并发一成一败）+ 反例（单张超量零副作用）均有
- [x] 测试覆盖率：bundling 模块 service 行覆盖 **92.1%**（阈值 90%）；模块整体 92.79% → **94%**
- [x] 单文件 ≤400 行（最大 352）；闸门 1-5 全绿


## TC 对照表（先盘点，只补「待补」）

> 盘点方法：逐条打开 14 个既有测试文件核对**实际断言**（不照抄印象）。已覆盖的一条都没动
> —— 重写等于把已验证的断言换成新的、更弱的断言。

| TC | 场景（03 §11） | 结论 | 依据 / 落点 |
| --- | --- | --- | --- |
| **TC-12** | 重复提交审核（同 `Idempotency-Key`，20 并发） | **已有（串行）+ 待补（并发）** | 串行：`test_bundling_router_actions.test_approve_idempotency_key_applies_once`。**本卡补并发版** `TC-BC-01`：仅 1 次生成（APPROVE 日志 1 条 + 码数 == 手数 + 手号 `1..N`）；⚠️「19 次返回首次结果（200）」当前**不成立**，见「实现缺陷」① |
| **TC-13** | 并发两张打菲单抢同一尺码剩余可打菲数 | **待补 → 已补** | 零覆盖。`TC-BC-02`：两张单各要 120、结转只给 120 → 一成功一 `30002`，`reserved_qty==120`、`bundled_qty==0`、`output-waste-bundled-reserved==0`、SUBMIT 日志 1 条 |
| **TC-20** | 余数不出码（`hands=5, qty_per_hand=2`） | **部分已有（纯函数层）+ 待补（审核落库层）→ 已补** | 已有：`test_bundling_split.test_tc_sp_02`（`split_size_line` 纯函数）。**本卡补**审核事务层：恰好 5 个码各 2 件、`Σ=10`、`balance_qty=0`、手号 `XL01..XL05` |
| **TC-21** | 零手 / 非正整数入参 → `10001` / `31003` | **部分已有（纯函数层）+ 待补（接口边界层）→ 已补** | 已有：`test_tc_sp_05` / `test_tc_sp_05b`（`10001`）、`test_tc_sp_06`（`31003`）。**本卡补** HTTP 层：`hands=0` / `-3` → 422 `10001` + `lines.0.hands` 字段定位 + 零单落库；`hands=1.5` → 422 `10001`（不静默取整） |
| **TC-22** | 裁剪尾数无码可扫 | **待补 → 已补** | 零覆盖。⚠️ 口径修正：`cutting_order_size_lines.balance_qty` 是 `integer`（ADR-0020），`03 §11` 写的 `balance_qty=0.6` 存不进去。按 ADR-0020 之后的真实形态验同一件事：`hands=5 × qty_per_hand=2` 人工指定 `output_qty=12` → 尾 2 件留裁剪侧，打菲只出 5 个码 |
| **TC-23** | `hands=0` 的行提交 → `10001` | **部分已有 + 待补 → 已补** | 与 TC-21 同一条 HTTP 用例（`hands=0` 参数化用例之一）；补的断言含「一单都不建」 |
| **TC-24** | 1:2:2:1 → 6 手 = 6 码 | **已有** | `test_bundling_approve.test_approve_generates_one_code_per_hand`（含每尺码 `1..N` 无缺号）+ `test_tc_sp_03` |
| **TC-25** | 同尺码多手不重复；反审核后手号仍占号、新单可重编 | **部分已有 + 待补 → 已补** | 已有：`test_approve_generates_one_code_per_hand`（`XL01`/`XL02`）、`test_tc_sp_04`。**本卡补**：同单反审核后再审核 → `31005` 且 `details["constraint"]` 证明**来自库层唯一约束**（预检只查 `ACTIVE`、压根没参与）；新单从 `XL01` 重编、旧码仍 `VOIDED`；并发版 `TC-BC-03`（20 并发全 `31005`） |
| **TC-26** | 扫码得该手件数（单码详情） | **已有** | `test_bundling_router2.test_bundle_detail_carries_hand_context`（含 `hands` / `hands_total_of_size` / `bundle_qty` / `cutting_size_line_id` / 缸号） |
| **TC-27** | 码数 ≠ 裁剪手数 → `31004`；超可用量 → `30002` | **已有** | `test_over_production_is_rejected_without_side_effects`（`31004` + `details` + 零副作用）+ `test_submit_over_available_is_rejected_without_side_effects`（`30002`） |
| **TC-28** | 改每手件数同步 `bundle_qty`；已计件码不可改 | **①② 不可达（实现缺口）；③ 待补 → 已补** | `ErrorCode.BUNDLE_COUNTED_HAND_LOCKED = 31006` 已登记（T-BUND-007b）但**全仓无抛出点**；`patch` 只改表头、`put_lines` 只允许 `DRAFT`/`REJECTED` → 已审核单改不了手数。**本卡只断 ③**：`patch` / `put_lines` / `reverse` / `void_code` 四个可达入口逐一验「已计件码 `bundle_qty` 不动、计数不动、两手都仍 `ACTIVE`」。①② 见「实现缺陷」② |
| **TC-29** | 手序号重复 → `31005` + `conflicts[]` | **已有** | `test_duplicated_hand_is_rejected`（approve）+ `test_submit_prechecks_hands_and_conflicts`（submit）+ `test_tc_sp_07/08`（预演 `conflicts[]`）。并发侧由 `TC-BC-03` 补 |
| **TC-30** | 未计件手清单 `?doc_id=&counted=false` + 命中索引 | **已有** | `test_bundling_counted.py` TC-30-01~07（含 `EXPLAIN` 断言 `idx_bundles_counted_pending`） |
| **TC-31** | `/split` 零写库 + 权限 | **部分已有 + 待补权限两条 → 已补** | 已有：`test_tc_sp_09_preview_writes_nothing`（service 逐格比对三张表）、`test_bundling_router2.test_split_preview_is_read_only`（HTTP）、`test_tc_sp_10`（`12002`）。**本卡补**：`line_leader`（有 `bundling:read`）调 `/split` → **200**；`employee` → **403 `12001`** |
| **TC-32** | 一码一员工的数据模型约束（`32001` 的前置） | **已有（模型层）；`32001` 依赖 P2** | `test_ck_bundles_cnt_blocks_overcount`（`counted_qty > bundle_qty` 被拒）+ `test_ck_bundles_one_blocks_counted_without_worker` + `test_void_code_rejects_counted_and_is_idempotent`（先 `FOR UPDATE` 锁码再判 `counted_at`）。`32001` 本身属计件侧（P2） |
| **TC-33** | 部分生产后剩余量可见（`28/60`） | **部分已有 + 待补详情面 → 已补** | 已有：`test_partially_produced_hand_is_not_pending`（`counted=false` 不含它）。**本卡补** `GET /bundles/{bn}`：`bundle_qty=60.000` / `counted_qty=28.000` / `counted_at` 非空 / `counted_by_name` **等于真用户姓名** / `bundle_no` 不变 / 未计件清单只含第 1 手 |
| **TC-34** | 20 并发 `approve` 同一单 → 1 成功 | **已有** | `test_bundling_approve_concurrency.test_concurrent_approve_one_wins`（独立引擎真提交，断言 1 成功 19 败 + 手号 `1..N` + APPROVE 日志 1 条） |
| **TC-35** | 并发：改手数 vs 正在计件 | **原口径依赖 P2 + `31006` 未实现 → 已补同锁序纪律的替身** | 卡面那条不可测（同 TC-28 ①②）。**本卡补**：`TC-BC-04` —— 确定性半边（`counted_at` 已写 → 反审核 `32003` + 一码未作废 + 结转不变 + 无 `REVERSE` 日志）与并发半边（`32003` vs 整单作废，**绝不半单作废**，恒断 `counted_qty <= bundle_qty` 与 `ck_bundles_one`） |
| **TC-36** | 标签打印留痕含手号 | **已有** | `test_bundling_label.test_register_print_appends_row_and_bumps_qty`（落 `hands_seq=3` + 「共 3 手」快照）+ `test_export_labels_carries_hand_seq_total_and_qty`（含「第 N 手 / 共 M 手」与件数）+ `test_register_print_requires_hands_seq`（缺 → `10002`） |

**统计（17 条 TC）**：**完全已有 8 条**（TC-24 / 26 / 27 / 29 / 30 / 32 / 34 / 36，一条未动）、
**部分已有·本卡补齐 9 条**（TC-12 / 13 / 20 / 21 / 22 / 23 / 25 / 28③ / 31 / 33 / 35 中的缺口部分）。
**没有一条 TC 处于「完全未覆盖且没补」的状态**；其中 **3 处标「依赖 P2」**（TC-28①② 的 `31006`、
TC-32 的 `32001`、TC-35 的原口径「改手数 vs 计件」）—— 本卡只为它们覆盖了「打菲侧
`counted_at` 已写 → 拒绝」这一段，**未建表、未伪造计件流水**。

## 测试清单

| # | 用例 | 期望 | 结果 | 落点 |
| --- | --- | --- | --- | --- |
| TC-BC-01 | **20 并发**同 `Idempotency-Key` 审核（TC-12） | 1 成功 19 败；码只生成一次；手号无缺无重 | ✅ 通过（失败码 ∈ `{10003, 30001}`、HTTP 全 409） | `test_bundling_concurrency.py` |
| TC-BC-02 | 并发两张单抢同一尺码余量（TC-13） | 一成功一 `30002`；INV-6 不破 | ✅ 通过 | `test_bundling_concurrency.py` |
| TC-BC-03 | 反审核后 20 并发审核（TC-25 + TC-29） | **0 成功、20 个 `31005`**（唯一约束兜底） | ✅ 通过 | `test_bundling_concurrency_codes.py` |
| TC-BC-04 | 计件回写 vs 反审核（TC-35 替身） | `32003` / 整单作废；绝不半单作废 | ✅ 通过（确定性 + 并发各一） | `test_bundling_concurrency_codes.py` |
| TC-BC-05 | 20 并发单码作废同一码 | 1 次生效、19 个 `31002`、1 条日志、结转不动 | ✅ 通过 | `test_bundling_concurrency_codes.py` |
| TC-BC-06 | 一码一员工 `32001` | 计件侧拦 | ⚠️ **依赖 P2**（`piecework_logs` 未建表，L-096）；本卡只覆盖 `32003` 那一段 | — |
| TC-BC-07 | 超产 `32006` | 计件侧拦 | ⚠️ **依赖 P2**，同上 | — |
| TC-BC-08 | `/split` 零写库 | 行数不变 | ✅ 已有（`test_tc_sp_09`），本卡只补权限两条 | `test_bundling_rules_codes.py` |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `backend/tests/factories/bundling_concurrency.py` | **+285 / −0**（新增） | 真提交夹具：`concurrency_engine`（`NullPool`，每 session 一条独立连接）、`make_persisted_order`、`reviewer_headers`（复用 seed 的 `super_admin`，不新建角色）、`pooled_client`（每请求一条独立 session）、`purge_side_effects`（按 `doc_id` / `employee_no` **精确**清 `document_logs` / `users`） |
| `backend/tests/modules/test_bundling_concurrency.py` | **+231 / −0**（新增） | `TC-BC-01`（TC-12，20 并发同幂等键，接口层）、`TC-BC-02`（TC-13，抢余量） |
| `backend/tests/modules/test_bundling_concurrency_codes.py` | **+352 / −0**（新增） | `TC-BC-03`（反审核后 20 并发全 `31005`）、`TC-BC-04`（计件回写 vs 反审核）、`TC-BC-05`（20 并发作废同码） |
| `backend/tests/modules/test_bundling_rules.py` | **+191 / −0**（新增） | TC-20（审核落库层 5 手×2 件）、TC-22（裁剪尾数不出码）、TC-21 / TC-23（零手 / 负手 / 小数 → `10001` + 字段定位） |
| `backend/tests/modules/test_bundling_rules_codes.py` | **+309 / −0**（新增） | TC-25（手号占用 + 新单重编）、TC-28③、TC-31 权限两条、TC-33（部分生产详情） |
| **手写合计** | **+1368 / −0** | 5 个文件，**超 1200 软上限 168 行**；拆分理由见 §7.1 第 2 条：① 拆 4 个测试文件是 ADR-0030 的 400 行硬线逼出来的（单文件写完必越线，且并发用例与回滚型用例的夹具不同，混在一起读的人会以为并发也共享事务）；② 并发工厂单开是因为它**真提交 + 自带清理**，与只回滚的 `bundling.py` 关注点相反；③ 用例 docstring 里那些「为什么这条不能弱断言」的说明是交付物不是注释（例：TC-BC-03 为什么必须精确断 `31005`、TC-BC-04 为什么不能断「VOIDED 的码一定没被计件」）。**生成物零 diff**（本卡没动任何端点/字段，`openapi.json` 与 `schema.d.ts` 不变） |
| `app/**`（被测实现） | **0 / −0** | **未改一行**（AGENTS §2.1；发现的缺陷见下表，另开修复提交） |
| `docs/tasks/T-BUND-008-…md` | 回填 | 状态 / TC 对照表 / 测试清单结果 / 实际改动 / 遗留问题 |
| `docs/12-文档与变更归档规范.md` | +6 行 | §2 变更记录 `0123` 一行 + §5 遗留问题 L-108~L-112 五条 |

**提交记录**：
- `d009f53` test(bundling): 并发与规则用例补齐 03 §11 缺口（T-BUND-008）

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| ① | **幂等键在并发下不生效**：`core/idempotency.py` 的 `load_idempotent` 是「读缓存 → 没有就继续」，无原子占位（SETNX），20 个并发请求同时读到空缓存、全部跑业务。**真正兜住「只生成一次」的是状态机条件 UPDATE，不是幂等键**。故 `03 §11` TC-12「19 次返回首次结果（200）」当前不成立（串行重试成立）。症状：网络抖动下 20 个并发审核里 19 个拿到 409 而不是 200 —— 数据安全，但用户体验是「点了没反应」 | `docs/12` **L-108** |
| ② | **`31006`（已计件的码不可改手数）登记了却无实现**：`ErrorCode.BUNDLE_COUNTED_HAND_LOCKED` 在 T-BUND-007b 进了枚举，`approve` / `patch` / `put_lines` 里**没有任何抛出点**；且 `patch` 只改表头、`put_lines` 只允许 `DRAFT`/`REJECTED` —— **已审核的单根本改不了手数**，`03 §2 B23` 与 TC-28①② 落不了地。要落 B23 得先定「已审核单允许改手数」的迁移路径（是加 `APPROVED→DRAFT`？还是反审核后改？）——**这是业务决策，不能自行发明** | `docs/12` **L-109** |
| ③ | **`lock_cutting_outputs` 的 `SELECT ... FOR UPDATE` 没带 `populate_existing=True`**（同文件的 `lock_order_for_transition` 带了）。同一 session 内先读过 `cutting_outputs` 再调 `submit` 时，返回的是**旧值**，`_reserve` 第一层「先查可用量」会按旧值误判不足、报出与真实余量不符的 `30002`。生产每请求一个 session 碰不到（`submit` 内它是第一次读该表），但任何长生命周期 session（worker / CLI / 批处理）会踩到 | `docs/12` **L-110** |
| ④ | **`conftest._purge_persisted_world` 清不掉并发用例的 `document_logs`**：那段按 `doc_no = '{款号}'` 删，而打菲日志的 `doc_no` 是 `BD-…`，**条件永远匹配不上** —— 每跑一次并发用例就往共享测试库里留一批日志。本卡新增的 `purge_side_effects` 按 `doc_id` 精确清自己那批；**既有两条并发用例（`test_bundling_approve_concurrency` / `test_bundling_state`）的日志仍在漏**（L-099 未闭环），本卡按「不顺手重构」未动 | `docs/12` **L-111** |
| ⑤ | `03 §11` **TC-22 的口径已过时**：它写「裁剪单 `output_qty=12`、`balance_qty=0.6`」，但 ADR-0020 之后 `cutting_order_size_lines.balance_qty` 是 `integer`，`0.6` 存不进去。本卡按 ADR-0020 之后的真实形态（尾数 = 整数件）测同一件事，**未改文档**（改规范需另开卡 + `docs/12` 变更记录） | `docs/12` **L-112** |

## 自检清单

对照 `AGENTS.md` §9 逐条确认：

- [x] 读过本任务对应的 docs 规范（`03 §7` / `§11` / `§11.3`、`10 §5` / `§2.2`、`04 §7.0`、ADR-0016 / 0017）
- [x] 没有硬编码业务常量（`MAX_HANDS_PER_SIZE_LINE` 等仍走 `state_guard`；本卡未加新常量）
- [x] 没有物理删除（真提交夹具的清理用**迁移账号**删测试行，不改业务语义；被测实现零改动）
- [x] 状态变更走 service 迁移方法且写日志（TC-BC-01/02/03/05 都额外断言日志条数）
- [x] 接口权限声明 / 错误码 / OpenAPI（**未新增任何端点或错误码**；TC-31 验的是既有 `/split` 的 `bundling:read`）
- [x] 测试覆盖正常 + 异常 + 权限拒绝 + 并发（并发 5 例、规则 9 例、权限 2 条、唯一约束 2 条）
- [x] 闸门 1 lint 通过 / 2 typecheck 通过 / 3 单测 956 passed、覆盖率 91.71%
- [x] 闸门 4 迁移可正向且可回滚（`up → down → up` + `alembic check` 无漂移）
- [x] 闸门 5 构建镜像成功（`api-image` 导入 + `web-image` `nginx -t` + 首页 200）
- [x] 提交信息符合规范
- [x] 手写行数 ≤ 1200 —— **实为 1368，超软上限 168 行**，已在「实际改动」里写明行数构成与拆分理由
- [x] **没有单个文件超 400 行**（最大 `test_bundling_concurrency_codes.py` 352 行）
- [x] 本次改动已在 `docs/12` 变更记录留痕（`0123`）

## 变更记录

| 日期 | 变更内容 | 操作人 |
| --- | --- | --- |
| 2026-10-06 | 初版：并发 + 规则测试，映射 03 §11 的可自动化用例 | AI |
| 2026-10-08 | 盘点 14 个既有测试文件后**只补 15 条缺口**（并发 5 + 规则 10），已覆盖的 8 条一条未动；回填 TC 对照表、5 条实现缺陷（L-108~L-112）、行数构成与提交 hash | qa |
