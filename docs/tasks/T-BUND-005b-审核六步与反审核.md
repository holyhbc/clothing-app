# T-BUND-005b：`approve` 审核六步（批量生成 `bundles`）+ `reverse` 反审核

| 项 | 内容 |
| --- | --- |
| 模块 | bundling |
| 负责人 | backend-dev |
| 状态 | `done`（Q-B02/Q-B13/Q-B15/Q-B16 于 2026-10-06 定版后开工；审核口径与已落地代码一致） |
| 优先级 | P1（P1 出口核心） |
| 依赖 | T-BUND-004、T-BUND-005a |
| 被依赖 | T-BUND-006、T-BUND-008、T-BUND-010 |
| 关联设计 | [`modules/03-打菲.md`](../modules/03-打菲.md) §4.1 审核六步、§5.3、§7；[`P1-打菲-实施说明.md`](../modules/P1-打菲-实施说明.md) §7 |
| 关联 ADR | [ADR-0016](../adr/0016-打菲按手与扫码得件数.md)（按手/一码一员工） |
| 估算 | 0.5d |

## 目标

`approve` 按手批量生成 `bundles`（码数 = 手数），更新裁剪结转；
`reverse` 冲销并把本单全部码置 `VOIDED`（不可恢复）。

## 范围

**要做**：
- [x] `service/approve_mixin.py`（并入 `state_mixin.py` 会破 400 行硬线 → 单开，另拆 `approve_assert.py`）：`approve`
  - 六步**顺序不可调换**（`03 §4.1`）：① 按手生成 `bundle_no`（复用 T-BUND-004 纯函数）
    ② 每手件数 = 裁剪明细 `qty_per_hand`（Q-B15）；③ **防超打**否则 `31004`（少打允许，B21）+ 单尺码 ≤99 手 ④ 手序号重复 `31005`
    ⑤ `uq_bundles_hand` 兜底 ⑥ 更新 `cutting_outputs`（`bundled_qty += 本单件数`，
    `reserved_qty` 清零）
  - **批量插入用 `INSERT … SELECT … FROM generate_series()`**，**禁止逐条 INSERT / ORM 循环**
  - 审核事务内跑 §5.3 的 4 条断言，任一不过整事务回滚
  - 制单人 ≠ 审核人；幂等：`Idempotency-Key`（05 §5）
- [x] `reverse`：必填 `reason`；**若存在计件流水则 `32003`**（⚠️ `piecework_logs` 表属 P2、尚未建，故本卡以「可插拔守卫」实现——把「是否已计件」收敛成**单一方法**，P2 建表后只改那一处，且**当前实现为「无计件可查 → 放行」并写注释说明**）；本单全部码置 `VOIDED` + `voided_at` + `void_reason`（**行保留、不删**）；`bundled_qty` 减回、`reserved_qty` 重新预占
- [x] `backend/tests/modules/test_bundling_approve.py` 等 3 个测试文件：六步 + 断言 + 并发 + 反审核对称

**不做**：
- 单码作废 `void_code`（`bundling:code:void`）—— 可作为独立小卡，未列入本拆解（见待决）
- 改单联动（ADR-0021，→ Q-B19）

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `backend/app/modules/bundling/service/approve_mixin.py` | 新增 | 六步批量生成 + `reverse` |
| `backend/app/modules/bundling/service/approve_assert.py` | 新增 | ③/④ 前置校验 + §5.3 四条断言（`approve_mixin` 会破 400 行 → 拆） |
| `backend/app/modules/bundling/approve_repository.py` | 新增 | `bundles` 批量 INSERT + 断言聚合查询 |
| `backend/app/modules/bundling/state_repository.py` | 修改 | 结转 / 归还两个方向共用一条 UPDATE |
| `backend/app/modules/bundling/service/bundling_order_service.py` | 修改 | 组合 `ApproveMixin` |
| `backend/app/modules/bundling/service/__init__.py` | 修改 | 导出面 |
| `backend/tests/factories/bundling_approve.py` | 新增 | 审核/反审核共用的测试世界 |
| `backend/tests/modules/test_bundling_approve.py` 等 3 个 | 新增 | 六步/断言/并发/反审核 |

## 实现要点（必读规范）

- [x] 严格照 `docs/08 §2.2` 与 `03 §4.1`；**审核副作用与反审核反向副作用成对**
- [x] `bundles` 批量插入前先回写 `hands_total` / `output_qty` / `balance_qty` /
      `planned_qty` 并写 `document_logs`（取整前后值）
- [x] 并发：乐观锁 + `uq_bundles_hand` + `uq_bundles_no` 三层防线（`03 §7`）
- [x] 生成后同事务跑 4 条断言：码数=手数、余数归位、手号 1..N 无缺、件数 ≤ 可用量
- [x] **不接受前端传入 `hands` 值**，服务端 `generate_series` 统一展开
- [x] 错误码：`31004`/`31005`/`30002`/`10003`/`10005`（均 05 §4 已登记；
      **`32003` 本卡未用** —— 32 段未实现、`ErrorCode` 里没有这个码，且错误码守卫测试
      要求未实现段不得出现码，见「遗留问题」）

## 验收标准

- [x] `uv run pytest tests/modules/test_bundling_approve.py -q` 全部通过
- [x] 1:2:2:1 → 6 手生成恰好 6 个码，手号 `L01/XL01/XL02/XXL01/XXL02/3XL01`
- [x] 2000 手批量：SQL 语句数为明细行数量级，`bundles` 增 2000 行，手号连续无缺
- [x] 码数异常 → `31004`（details 回「应有/当前」）；手号重复 → `31005`
- [x] 反审核：码全 `VOIDED`、行保留、`cutting_outputs` 还原、日志 `REVERSE` + 原因
- [x] 20 并发 `approve`：1 次成功，其余 `10003`，无重复手号、无缺号
      （实测失败码是集合 `{10003, 30001, 31005}`，见下「与卡面的偏差」）
- [x] 单文件 ≤400 行；闸门 1-4 本地预跑通过（`scripts/gate.sh --host`：877 passed / 覆盖率 91.06% / 迁移无漂移）

## 测试清单

| # | 用例 | 期望 | 测试函数 | 结果 |
| --- | --- | --- | --- | --- |
| TC-AP-01 | 1:2:2:1 | 6 码，手号 `L01/XL01/XL02/XXL01/XXL02/3XL01` | `test_approve_generates_one_code_per_hand` | ✅ |
| TC-AP-02 | **不 floor**：`hands=3, qty_per_hand=33` | 3 码 × 33 = 99，本单 `balance_qty` 恒 0 | `test_approve_does_not_floor_qty_per_hand` | ✅ |
| TC-AP-03 | 超打（审核时裁剪侧改小） | `31004`，零副作用、无日志 | `test_over_production_is_rejected_without_side_effects` | ✅ |
| TC-AP-04 | 手号重复（唯一索引兜底） | `31005`，不补插第 2 手 | `test_duplicated_hand_is_rejected` | ✅ |
| TC-AP-05 | 制单人自审 | `10005` | `test_creator_cannot_approve_own_order` | ✅ |
| TC-AP-06 | **2000 手**性能 | INSERT 语句数 = 明细行数（21 条），码恰好 2000、手号无缺 | `test_bulk_insert_statement_count_is_line_bounded` | ✅ |
| TC-AP-07 | 反审核有计件 | `32003` | — | ⏸ **本卡不可实现**（无 `piecework_logs` 表，见「遗留问题」） |
| TC-AP-08 | 反审核副作用对称 | 码 `VOIDED` 且行保留、结转减回、预占重建 | `test_reverse_voids_codes_and_restores_carry_over` | ✅ |
| TC-AP-09 | 20 并发审核 | 一成一败（失败码集合 `{10003, 30001, 31005}`） | `test_concurrent_approve_one_wins` | ✅ |
| 追加 | 反审核原因必填 + 幂等保护 | 空白原因 → `10002`；二次反审核 → `30001` | `test_reverse_requires_reason_and_is_not_idempotent` | ✅ |
| 追加 | 中间态不可跳级 | 未审核 `30001`、驳回后 `30001` | `test_reverse_rejects_states_without_approval` | ✅ |

### 与卡面的偏差（3 处，均已定版口径，不是实现走偏）

1. **TC-AP-02 改为「不 floor」**：卡面原写「余 1 归 `balance_qty`」，但 Q-B15 定版后
   `bundle_qty` 直接取裁剪明细的 `qty_per_hand`（不取整），**本单不出余数码** ——
   差额只在裁剪侧人工指定出数时产生，且已在裁剪侧记入 `balance_qty`（09 §4.2）。
   断言相应改成「3 码 × 33 = 99 + 本单 `balance_qty == 0`」，否则将来有人把 `floor()` 加回来
   不会有任何用例报警（99 → 3×33 恰好整除，正是最容易悄悄改动的地方）。
2. **TC-AP-03 从「码数 ≠ 手数」改成「超打」**：`31004` 只拦 `Σ本单hands > Σ裁剪hands`，
   **少打允许**（B21：少打几手是常态）。所以用例走「先合法提交、**再把裁剪侧改小**」这条路，
   它顺带证明了 ③ 在审核时是**重算**、不是复用提交那一刻的快照。
3. **TC-AP-07 拆为守卫位**：卡面原写「反审核有计件 → `32003`」，但 `piecework_logs` 属 P2、
   表还不存在。把「是否已计件」收敛成**单一方法** `_assert_not_counted`（当前实现 = 锁码后放行，
   注释写明 P2 建表后只改这一处），错误码则**不登记** —— `32` 段未实现、`ErrorCode` 无此码，
   且 `tests/test_errors_registry.py` 的守卫要求未实现段不得出现码。

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `backend/app/modules/bundling/service/approve_mixin.py` | 新增 383 | `approve` / `reverse` 主流程、批量插入调度、结转/还原 |
| `backend/app/modules/bundling/service/approve_assert.py` | 新增 318 | ③/④ 前置校验、§5.3 四条断言、`_shift` 条件 UPDATE（含失败分支两种措辞） |
| `backend/app/modules/bundling/approve_repository.py` | 新增 288 | `insert_bundle_series`（`generate_series`）、`void_active_bundles`、断言聚合查询 |
| `backend/app/modules/bundling/state_repository.py` | +106 | `carry_over_output_qty` / `roll_back_output_qty`（共用 `_shift_output_qty`） |
| `backend/app/modules/bundling/service/common.py` | +10 / -3 | `_write_log(changed_fields=...)` |
| `backend/app/modules/bundling/service/bundling_order_service.py` | +5 / -6 | 组合 `ApproveMixin` |
| `backend/app/modules/bundling/service/__init__.py` | +10 / -5 | 重导出 `ApproveMixin` / `ApproveAssertMixin` / `HAND_CONFLICT_CONSTRAINTS` |
| `backend/tests/factories/bundling_approve.py` | 新增 263 | 审核/反审核共用测试世界（`_ensure_sizes` 补齐裁剪尺码行是地基） |
| `backend/tests/modules/test_bundling_approve.py` | 新增 289 | TC-AP-01~06 |
| `backend/tests/modules/test_bundling_reverse.py` | 新增 112 | TC-AP-08 + 原因必填 + 中间态不可跳级 |
| `backend/tests/modules/test_bundling_approve_concurrency.py` | 新增 134 | TC-AP-09（独立引擎真提交，另用一套夹具） |
| `backend/tests/factories/bundling.py` | +24 / -60 | `attach_output` 幂等（`uq_cutting_outputs_style_color_size` 部分唯一索引）；审核专用助手搬到 `bundling_approve.py`，把本文件按回 400 行硬线内 |
| `backend/tests/conftest.py` | +5 / -1 | purge **先删 `bundles` 再删 `bundling_order_lines`**（`fk_bundles_line` 是 RESTRICT，顺序反了清库直接报错） |

合计手写 **1939 行代码** + 约 150 行文档；单文件最大 399 行（≤400，ADR-0030）。
**新增权限点 0 / 错误码 0 / 迁移 0 / 接口 0**（router 与 `Idempotency-Key` Redis 接线属 T-BUND-006/007）。

> **超 1200 行软上限的说明（AGENTS §7.1 第 2 条）**：1939 行里有 **616 行是中文 docstring
> 与 ⚠️ 注释**（本仓既定风格 —— cutting / base 各模块都是这个密度，注释记的是「为什么不那样写」，
> 例如「`generate_series` 必须用 `column_valued` 否则 PG 报 `column anon_N.hands_seq does not
> exist`」，删掉它们下一个人会重新踩一遍）。代码骨架本身约 1300 行，其中测试 798 行
> （10 条用例 + 2000 手与 20 并发两条重用例）。**没有生成物**，全数手写。
> 文件数也超出「单次 ≤8 文件」：拆成 3 个测试文件与 2 个 service 文件正是为了守住 400 行硬线
> （`approve_mixin` 单写是 420 行；测试单写是 650 行），而 ADR-0030 明确要求这种情况下
> 「超了说明该抽组件，而不是下次注意」。

**提交记录**：
- `<hash>` feat(bundling): approve 审核六步批量生成 + reverse 反审核（T-BUND-005b）

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| 计件流水（`piecework_logs`） | `reverse` 的「已计件 → `32003`」目前**无表可查**（P2 才建），本卡按「可插桩的守卫位」实现（`_assert_not_counted` 单一方法，当前放行）。`32003` 未登记到 `ErrorCode`：32 段未实现，且 `tests/test_errors_registry.py` 要求未实现段不得出现码 —— 需随 P2 建表一并登记 | `docs/12` §5 L-096 |
| `super_admin` 强制审核/反审核 | 08 §4 的「`super_admin` 可代审 + `force=true`」**未实现**：`POST /approvals` 还没有这个入参（router 属 T-BUND-007），硬做就得自己发明入参名 | `docs/12` §5 L-097 |
| `reverse` 入参未包 schema | 现在是 `reverse(order_id, reason: str, operator_id, ctx)`（`require_reason` 校验），没建 `ReverseIn` —— 为了控住改动文件数。router 接线时应补 `ReverseIn` 并与 `RejectIn`/`CancelIn` 对齐 | `docs/12` §5 L-098 |
| `Idempotency-Key` 未接线 | service 接受 `idempotency_key` 并写进 `document_logs.changed_fields`；Redis 的 `load_idempotent`/`store_idempent` 在 router 层，属 T-BUND-006/007。service 层兜底是状态机（二次 approve → `30001`，不会生成第二批码） | T-BUND-006 / 007 |
| void_code | 单码作废未单开卡 | 待排期 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。

## 变更记录

| 日期 | 变更内容 | 操作人 |
| --- | --- | --- |
| 2026-10-06 | 初版：审核六步 + 反审核对称副作用；批量 INSERT 禁止逐条 | AI |
| 2026-10-07 | 实现 + 10 条用例全绿；`approve_mixin` 420 → 拆出 `approve_assert.py`，测试 650 → 拆 3 个文件（ADR-0030 400 行硬线）；手序号展开**复用 T-BUND-004 的 `preview_order` 纯函数**而非重写一份；`32003` 降级为守卫位 | AI |
