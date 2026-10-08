# T-BUND-011：审核后增手（`hand-increments`）—— 闭环 L-109

| 项 | 内容 |
| --- | --- |
| 模块 | bundling |
| 负责人 | backend-dev |
| 状态 | `todo` |
| 优先级 | P1 |
| 依赖 | T-BUND-005b（审核批量生成）、T-BUND-007a（router 骨架与 `version` 语义） |
| 被依赖 | T-BUND-009（前端「补打」入口）、T-BUND-010 |
| 关联设计 | [`adr/0033`](../adr/0033-审核后打菲单只可增手.md)、[`modules/03-打菲.md`](../modules/03-打菲.md) §2 B23、§4.1 |
| 关联规范 | `docs/05 §2/§3/§4`、`docs/08 §2.1.1`、`docs/10 §5` |
| 估算 | 0.5d |

## 目标

落 **ADR-0033**：审核后的打菲单**只可增手**，减手必须走 `reverse`。

## 范围

**要做**：

- [ ] `POST /api/v1/bundling-orders/{id}/hand-increments`
  - 入参**只有** `size_code`（或 `line_id`）+ `delta_hands`（**必须 > 0**）+ `version`；
        `extra="forbid"`。⚠️ **不接受** `hands` 全量替换、`color_code`、`cutting_size_line_id` 或任何其它字段
  - 权限点 `bundling:update`；`_require` 显式校验
  - 前置状态**只允许 `APPROVED`**（`DRAFT`/`REJECTED` 走既有 `PUT /lines`；其它态 → `30001`）
  - 单尺码手数上限 **99 仍生效**（`state_guard.MAX_HANDS_PER_SIZE_LINE`）→ 超限 `10001`
- [ ] 副作用（与 `approve` 对称地部分复用）：
      ① `hands_total += delta_hands`；② **只生成新增那几手的码**（手号从 `N+1` 起，
      **复用 005b 的 `generate_series` 批量路径**，禁止逐条 INSERT）；③ `cutting_outputs.bundled_qty += delta × qty_per_hand`；
      ④ 写 `document_logs`（记改前改后 + **新增手号区间**）
- [ ] **重跑不变式**（不是自动成立）：
      - **防超打**：`本次新增后 hands ≤ 裁剪该尺码可打手数` → 否则 `31004`
      - **手号连续无缺**：新增手号紧接原最大手号
      - **三层防线**：乐观锁 `version` + `uq_bundles_hand` + `uq_bundles_no`（`03 §7`）
      - 锁序「单据 → 码 → `cutting_outputs`」，与 `approve`/`reverse` **一致**（否则并发互等死锁）
- [ ] `ReverseIn` 同款 `HandIncrementIn` schema（必填校验在 **schema 层**，`05 §3`）
- [ ] `tests/modules/test_bundling_hand_increment.py`
- [ ] `pnpm generate:api`（生成物**同提交**，AGENTS §7.1 第 1 条）

**不做**：
- **减手** —— 一律 `30001` + 提示走 `reversals`。**不要实现减手**（ADR-0033 已定）
- **改 `bundle_qty`**（每手件数）—— 那是裁剪侧 `qty_per_hand` 的变更 = **Q-B19**，未单开卡
- `31006` —— **已作废**并退回 `DEFERRED_CODES`，**不要把它加回来**
- 前端「补打」按钮（→ T-BUND-009）

## 关键正确性要求（**这张卡最容易错的地方**）

**增手是纯增量，但它触到三条不变式，每条都要重新验一遍：**

1. **防超打（`31004`）不自动成立** —— `hands ≤ 裁剪可打手数` 这个不变式是在 `approve` 时验的。
   裁剪侧可能在这中间增了产量，所以**增手必须重跑**，不能假设。
2. **手号连续** —— 同 (色,码) 手号全局连续（Q-B13）。新增必须接在**原最大手号**之后，
   不是「按 delta 从 1 重编」。**这条写错的话标签上的「第 N 手」会重复**，
   而标签已经在车间流通、员工按 `bundle_no` 扫码 —— 重复的 `bundle_no` = 重复计件。
3. **并发增手两次** —— 两次同时增 1 手：必须产生 `N+1`、`N+2` 两手共 2 个码，
   **不能都写成 `N+1`**（那会撞 `uq_bundles_hand` 被整单回滚，即「两次都失败」）。

⚠️ **`hands_total` 是表头汇总**：只加 delta、**不要重算**（重算需要读全部明细，
在并发下会读到别的事务未提交的中间态）。这条与 T-BUND-007a 修的 `put_lines` 缺陷同类。

## 验收标准

- [ ] `uv run pytest -q tests/modules/test_bundling_hand_increment.py` 全通过
- [ ] `3 手 → +2 手` = 共 5 手、**新增 `XL04`/`XL05` 两个码**、`hands_total` 5、结转 `+2 × qty_per_hand`
- [ ] **`delta_hands ≤ 0` / 缺字段 → `10001`；`extra="forbid"` 拒绝多余字段**
- [ ] **减手（`delta_hands < 0`）→ 不实现**，按入参校验拒掉（`10001`）；已审核单改其它字段 → `30001`
- [ ] 增手后超打（裁剪侧未增产量）→ `31004` 且**零副作用**（表头/结转/码都不动）
- [ ] **20 并发各增 1 手 → 最终 `N+20` 手、20 个码、手号连续无缺**（不是「19 次 409」）
- [ ] `version` 过期 → `10003`；越权 → `12002` / `12001`
- [ ] 已打印/已计件码**完全不受影响**（`counted_at`/`bundle_qty`/`printed` 记录都不变）
- [ ] 路由顺序守卫在（`/hand-increments` 不能被 `/{order_id}` 抢走）
- [ ] **重跑 `pnpm generate:api` 两次无 diff**
- [ ] 单文件 ≤400 行；闸门 1-4 通过

## 测试清单

| # | 用例 | 期望 |
| --- | --- | --- |
| TC-HI-01 | `3 手 → +2` | 5 手、2 个新码、结转对账 |
| TC-HI-02 | 入参校验 | `delta ≤ 0` / 缺字段 / 多余字段 → `10001` |
| TC-HI-03 | 状态门 | 非 `APPROVED` → `30001` |
| TC-HI-04 | **增手后超打** | `31004` + 零副作用 |
| TC-HI-05 | 手号接续 | 跨多布批、跨尺码各编各的（Q-B13） |
| TC-HI-06 | **20 并发增手** | `N+20`、20 码、无缺号 |
| TC-HI-07 | `version` 乐观锁 | 过期 → `10003` |
| TC-HI-08 | 权限与范围 | `12001` / `12002` |
| TC-HI-09 | 已计件码不受影响 | `counted_at`/`bundle_qty` 不变 |
| TC-HI-10 | 日志 | 记改前改后 + 新增手号区间 |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| 待回填 | | |

**提交记录**：
- `<hash>` feat(bundling): 审核后增手 hand-increments（ADR-0033，闭环 L-109）

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| Q-B19 | 裁剪改单 → 打菲码联动（含 `qty_per_hand` 变更、`31006` 启用） | `03 §12`，未单开卡 |
| L-113 | 幂等业务失败时不释放占位 | 待排期 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。

## 变更记录

| 日期 | 变更内容 | 操作人 |
| --- | --- | --- |
| 2026-10-08 | 初版：落 ADR-0033（审核后只可增手，减手走 reverse），闭环 L-109 | AI |
