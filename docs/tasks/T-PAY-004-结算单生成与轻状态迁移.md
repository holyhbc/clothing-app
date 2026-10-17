# T-PAY-004：结算单生成 + 轻状态迁移

| 项 | 内容 |
| --- | --- |
| 模块 | payroll |
| 负责人 | backend-dev |
| 状态 | `todo` |
| 优先级 | P2 |
| 依赖 | T-PAY-001、T-PAY-003 |
| 被依赖 | T-PAY-005（审核回写）、T-PAY-006（生成工资单） |
| 关联设计 | [`modules/05-工资.md`](../modules/05-工资.md) §2.1、§3.2/§3.3、§4.1、§5.2 |
| 关联规范 | `docs/08 §1.1`/`§1.2`、`docs/05 §5`（幂等） |
| 关联 ADR | ADR-0010 |
| 估算 | 0.5d |

## 目标

按「周期区间 + 车间」把未结算流水汇总成结算单（单头 + 逐条行），并实现
`submit` / `withdraw` / `reject` / `cancel` 四个轻状态迁移（不含审核）。

## 范围

**要做**：
- [ ] `create`：校验周期 `OPEN`（`CLOSED` → `33003`）；查该「周期 + 车间」是否已有单（幂等返回）；
      按 `05 §2.1` SQL 取未结算流水 → 写单头 + 行（每流水一行，含 `group_no`，快照 `unit_price`/`amount`）；单号 `PW`
- [ ] 单头汇总 `total_qty`/`total_amount`/`employee_count`/`line_count`/`group_count`（service 重算，不信前端）
- [ ] 轻迁移：`submit`（DRAFT/REJECTED→SUBMITTED，`total_amount > 0`）、`withdraw`（SUBMITTED→DRAFT）、
      `reject`（SUBMITTED→REJECTED，必填原因）、`cancel`（DRAFT/REJECTED→CANCELLED，必填原因）
- [ ] 幂等键 `period_id+workshop_id` + `Idempotency-Key`（`05 §6.3`）

**不做**：
- 不实现 `approve`/`reverse`（T-PAY-005，涉及 `payroll_id` 回写）
- 不做 router（T-PAY-009）

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `backend/app/modules/payroll/service/settlement.py` | 新增 | 生成 + 轻迁移 |
| `backend/app/modules/payroll/state.py` | 新增 | 结算单迁移表（`08 §3.1` 声明式） |
| `backend/tests/modules/test_payroll_settlement.py` | 新增 | 单元测试 |

## 实现要点

- [ ] 取数日期从周期表读，不接受前端传日期（BR-PR-20）
- [ ] `uq_piecework_settlements_period_workshop` 兜底并发重复结算（P1）
- [ ] 行上 `piecework_log_id` 唯一（P3 兜底）；本卡只写行、**不回写流水 `payroll_id`**
- [ ] 周期在 `submit` 时二次校验仍 `OPEN`（期间被关 → `33003`）
- [ ] 金额/数量 `Decimal`，`total_qty` 恒整数（BR-PR-22）

## 验收标准

- [ ] `pytest tests/modules/test_payroll_settlement.py -q` 全过
- [ ] TC-01 / TC-02 / TC-25 落成
- [ ] 幂等命中返回既有单（`code=0`），并发才 `33002`
- [ ] 闸门 1-4 通过

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-01 | 结算单生成（跨 2 组别 10 条流水） | 1 张 PW、行数=10、`group_no` 正确 | |
| TC-02 | 结算唯一约束 / 幂等 | 幂等返回 / `33002` | |
| TC-25 | CLOSED 周期加结算单被拒 | `33003` | |
| TC-PAY-S01 | `submit` 汇总金额 ≤0 被拒 | 拒绝 | |
| TC-PAY-S02 | `cancel` 终态不可再迁移 | 拒绝（R6） | |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| | +0 / -0 | |

**提交记录**：
- `<hash>` feat(payroll): 结算单生成与轻状态迁移（T-PAY-004）

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| Q-PL08 粒度未定，当前按二元组实现 | modules/05 §12 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。

## 变更记录

| 日期 | 变更内容 | 操作人 |
| --- | --- | --- |
| 2026-10-17 | 初版 | AI |
