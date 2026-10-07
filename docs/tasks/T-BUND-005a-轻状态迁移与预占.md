# T-BUND-005a：轻状态迁移 `submit`/`reject`/`withdraw`/`cancel` + 预占/释放

| 项 | 内容 |
| --- | --- |
| 模块 | bundling |
| 负责人 | backend-dev |
| 状态 | `done`（依赖 T-BASE-009-2 的 `cutting_outputs` 已于 T-BASE-009-2 落地） |
| 优先级 | P1 |
| 依赖 | T-BUND-003 + **T-BASE-009-2**（`cutting_outputs`） |
| 被依赖 | T-BUND-005b |
| 关联设计 | [`modules/03-打菲.md`](../modules/03-打菲.md) §4、§5.1 第 4 步、§7；[`P1-打菲-实施说明.md`](../modules/P1-打菲-实施说明.md) §4/§7 Q-B17 |
| 关联 ADR | [ADR-0016](../adr/0016-打菲按手与扫码得件数.md)、`docs/08 §2.2` |
| 估算 | 0.5d |

## 目标

补齐打菲单状态机中**不生成码**的四个动作，并把 `cutting_outputs.reserved_qty` 的预占/释放跑通。

## 范围

**要做**：
- [x] `service/state_mixin.py`：`submit` / `reject` / `withdraw` / `cancel`（照 `08 §2.1` 通用动作表；严格照 `docs/08 §2.2`，**不新增/改名动作**）
  - [x] `submit`：跑 `08 §2.2` 提交校验（款号+工序存在、来源裁剪单 `APPROVED`、
    每行 `bundle_qty > 0`、`Σ planned_qty ≤ 可用量` 否则 `30002`、码数=手数预检 `31004`、
    手序号冲突预检 `31005`）；**预占** `cutting_outputs.reserved_qty`（条件 UPDATE
    `available >= :qty`，`rowcount=0 → 30002`）；快照 `available_qty_before`
  - [x] `reject`：必填 `reason`；释放 `reserved_qty`
  - [x] `withdraw`：撤回人 = `created_by`；释放 `reserved_qty`
  - [x] `cancel`：必填 `cancelled_reason`；无码无库存副作用
- [x] 每个动作写 `document_logs`（谁/何时/从何状态到何状态/原因）
- [x] `backend/tests/modules/test_bundling_state.py`：四动作正常 + 异常 + 权限 + 并发

**不做**：
- `approve` / `reverse`（→ T-BUND-005b）
- 不新增状态迁移动作（严格照 `docs/08 §2.2`）
- 不实现改单联动（ADR-0021，→ Q-B19 单开卡）

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `backend/app/modules/bundling/service/state_mixin.py` | 新增 | 四动作 `submit`/`reject`/`withdraw`/`cancel` + `_apply_status`（组合进 `BundlingOrderService`） |
| `backend/app/modules/bundling/repository.py` | 修改 | 行锁/可用量查询 |
| `backend/app/modules/bundling/schemas.py` | 修改 | 动作入参（`reason` 必填等） |
| `backend/tests/modules/test_bundling_state.py` | 新增 | 状态迁移单测 |

## 实现要点（必读规范）

- [x] 严格照 `docs/08 §2.2` 的迁移表；**不新增/改名动作**
- [x] **加锁顺序**（`03 §7`）：先 `cutting_outputs` → `bundling_orders` → `bundles`，防死锁
- [x] 预占用条件 UPDATE，`rowcount=0 → 30002`（INV-6 不超发）
- [x] 乐观锁：`WHERE id=? AND version=? AND status=?`，`rowcount=0 → 10003`
- [x] 权限：`submit`→`bundling:submit`、`reject`→`bundling:reject`、
      `withdraw`→`bundling:update`、`cancel`→`bundling:cancel`（07 §2.2 已闭环，无新增）
- [x] 错误码引用 `31004`/`31005`/`30002`/`30001`/`10001`/`10002`/`10003`（05 §4 已登记）

## 验收标准

- [x] `uv run pytest tests/modules/test_bundling_state.py -q` 全部通过
- [x] `submit` 预占后 `v_cutting_output_available.available_qty` 减少；`reject`/`withdraw` 释放
- [x] 超可用量 `30002`，`bundles` 零新增、`cutting_outputs` 不变
- [x] 20 并发 `submit` 同一单：1 次成功，其余 `10003`/`30002`
- [x] 非法迁移（`APPROVED→submit`、终态再迁移）→ `30001`
- [x] 每次动作都有 `document_logs` 行（含原因）
- [x] 单文件 ≤400 行；闸门 1-4 本地预跑通过

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-ST-01 | `submit` 正常 | `SUBMITTED` + `reserved_qty` 增加 | ✅ |
| TC-ST-02 | `submit` 超量 | `30002`，无副作用 | ✅ |
| TC-ST-03 | `reject` 缺原因 | `10002` | ✅ |
| TC-ST-04 | `withdraw` 非制单人 | `12001`/`12002` | ✅（`12001`；范围校验在前，`12002` 走同一条 `assert_in_scope`） |
| TC-ST-05 | `cancel` | `CANCELLED` + 日志 | ✅ |
| TC-ST-06 | 并发 submit | 一成一败 | ✅（失败码落 `{10003, 30001, 30002}`，见 L-093） |
| TC-ST-07 | 日志 | `document_logs` 有对应行 | ✅ |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `backend/app/modules/bundling/service/state_mixin.py` | 新增 368 | 四动作 + `_assert_transition`（**按动作**登记迁移表）+ `_apply_status` + `_lock_for_transition`（03 §7 锁序） |
| `backend/app/modules/bundling/service/state_guard.py` | 新增 305 | 提交校验（`31004` 码数=手数 / `31005` 手序号冲突）与 `_reserve` / `_release`（成对预占释放） |
| `backend/app/modules/bundling/state_repository.py` | 新增 184 | 行锁 + 条件 UPDATE（`repository.py` 已 383 行，并进去就破 400 行硬线 → 见 L-095） |
| `backend/tests/modules/test_bundling_state.py` | 新增 395 | TC-ST-01~07 + 提交预检 + **20 并发 submit** |
| `backend/tests/factories/bundling.py` | +54 | `attach_output` / `read_reserved`（`build_world` 不写结转，提交类用例必须自己补） |
| `backend/app/modules/bundling/schemas.py` | +39 | `RejectIn` / `CancelIn` / `MAX_REASON` |
| `backend/app/modules/bundling/service/bundling_order_service.py` | +12 / -7 | 组合 `BundlingOrderService(CommonMixin, StateMixin, PreviewMixin)` |
| `backend/app/modules/bundling/service/__init__.py` | +10 / -3 | 重导出 `StateMixin` / `StateGuardMixin` / `OutputPlan` |
| `docs/12` | +6 | 变更记录 `0116` + 遗留 L-091~L-095 |
| **闸门** | — | 1 lint ✅ / 2 typecheck ✅ / 3 单测 ✅（本卡 9 条；全量 7 个既有失败见 L-094）/ 4 迁移往返 ✅ |

合计手写 1252 行代码 + 约 80 行文档；单文件最大 395 行（≤400，ADR-0030）。
**新增权限点 0 / 错误码 0 / 迁移 0 / 接口 0**（router 属 T-BUND-007）。

**提交记录**：
- `f23cde7` feat(bundling): 打菲单 submit/reject/withdraw/cancel + 预占/释放（T-BUND-005a）

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| Q-B17 | `cutting_outputs` 排期 | `P1-打菲-实施说明.md` §7 | ✅ 已闭环（T-BASE-009-2 迁移 0015） |
| Q-B19 | 码数 = 手数口径（B21 严格相等 vs「少打几手是常态」）与 `bundling:withdraw` 权限点归属 | `docs/12` L-091 / L-092 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。

## 变更记录

| 日期 | 变更内容 | 操作人 |
| --- | --- | --- |
| 2026-10-06 | 初版：四轻状态迁移 + 裁剪可用量预占/释放 | AI |
| 2026-10-07 | 实现 + 9 条用例全绿；`_assert_transition` 改为**按动作**登记（否则 `APPROVED→submit` 会被判合法）；新增 L-091~L-095 | AI |
