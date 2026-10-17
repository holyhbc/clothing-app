# T-PAY-005：结算单审核 / 反审核（`payroll_id` 回写 / 释放）

| 项 | 内容 |
| --- | --- |
| 模块 | payroll |
| 负责人 | backend-dev |
| 状态 | `todo` |
| 优先级 | P2 |
| 依赖 | T-PAY-004 |
| 被依赖 | T-PAY-006、T-PAY-010 |
| 关联设计 | [`modules/05-工资.md`](../modules/05-工资.md) §4.1、§4.4、§5.2/§5.6、§5.7 |
| 关联规范 | `docs/08 §1.2 R4/R5`、`§3.3`、`§4`；`docs/04 §7.1` |
| 关联 ADR | ADR-0005（计件流水不可变）、ADR-0010 |
| 估算 | 0.5d |

## 目标

实现结算单 `approve`（审核即占用：同事务回写每条流水 `payroll_id`）与
`reverse`（反审核：同事务清空 `payroll_id`，流水重新可结算），副作用严格对称。

## 范围

**要做**：
- [ ] `approve`：SUBMITTED→APPROVED，权限 `payroll:approve`；前置：制单人≠审核人（自审 `10005`）、
      行 `piecework_log_id` 未被其他结算单占用、重算 `total_qty/total_amount` 与行汇总一致
- [ ] `approve` 副作用：**同事务** `UPDATE piecework_logs SET payroll_id=:sid WHERE id IN (行) AND payroll_id IS NULL`，
      `rowcount` 与行数核对，不等则整体回滚
- [ ] `reverse`：APPROVED→SUBMITTED，权限 `payroll:reverse`；**必填原因**；
      下游工资单已 `APPROVED`/`PAID` → `33001`；副作用：清空全部行对应流水的 `payroll_id`
- [ ] 乐观锁：`WHERE id=? AND version=? AND status=?`，`rowcount=0` → `10003`
- [ ] `document_logs` 记 `occupied_log_count` / `released_log_count`

**不做**：
- 不做工资单（T-PAY-006）；不做 router（T-PAY-009）
- 不实现凭证（结算单不生成凭证）

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `backend/app/modules/payroll/service/settlement_approve.py` | 新增 | 审核/反审核 |
| `backend/app/modules/payroll/service/settlement.py` | 修改 | 接线 |
| `backend/tests/modules/test_payroll_settlement_approve.py` | 新增 | 副作用对称测试 |

## 实现要点

- [ ] 审核副作用与状态变更**同一事务**（`08 §1.2 R4`）
- [ ] 反审核**不删**行与单（R5），只清 `payroll_id`
- [ ] 下游校验：查 `payroll_sheets WHERE settlement_id=? AND status IN ('APPROVED','PAID')`
- [ ] 条件 UPDATE 带 `payroll_id IS NULL`（P3 兜底，`04 §7.1` 要点）

## 验收标准

- [ ] TC-03 / TC-26 落成：审核后未结算查询为 0；反审核后重新可结算
- [ ] 副作用快照对比（`08 §5` 第 6 条）：审核→反审核后 `payroll_id` 全部回 `NULL`
- [ ] 自审 `10005`；乐观锁并发一成一败
- [ ] 闸门 1-4 通过

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-03 | 审核回写 / 反审核释放 | `payroll_id` 全写入 / 全清空 | |
| TC-26 | `payroll_id IS NULL` 保证不重复结算 | `33002` + 唯一索引兜底 | |
| TC-PAY-A01 | 制单人自审 | `10005` | |
| TC-PAY-A02 | 下游工资已审核再反审核结算单 | `33001` | |
| TC-PAY-A03 | 并发审核/反审核 | 一成一败 `10003` | |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| | +0 / -0 | |

**提交记录**：
- `<hash>` feat(payroll): 结算单审核/反审核（T-PAY-005）

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| | | docs/12 §遗留问题清单 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。

## 变更记录

| 日期 | 变更内容 | 操作人 |
| --- | --- | --- |
| 2026-10-17 | 初版 | AI |
