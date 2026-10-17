# T-PAY-007：工资单审核 / 反审核 + 计提凭证收敛点

| 项 | 内容 |
| --- | --- |
| 模块 | payroll |
| 负责人 | backend-dev |
| 状态 | `todo` |
| 优先级 | P2 |
| 依赖 | T-PAY-006 |
| 被依赖 | T-PAY-008 |
| 关联设计 | [`modules/05-工资.md`](../modules/05-工资.md) §4.2、§5.4、§5.6；[`modules/P2-工资-实施说明.md`](../modules/P2-工资-实施说明.md) §8 Q-PL09 |
| 关联规范 | `docs/08 §1.2 R4/R5`、`§2.4`；`docs/05 §4`（`33001`） |
| 关联 ADR | — |
| 估算 | 0.5d |

## 目标

工资单 `approve`（生成计提凭证）与 `reverse`（红冲计提凭证）。**凭证生成收敛到唯一
`VoucherPort` 调用点**：M9 未建表前用空适配器，`accrual_voucher_no` 落 `NULL` + `TODO`。

## 范围

**要做**：
- [ ] `approve`：SUBMITTED→APPROVED，`payroll:approve`；制单人≠审核人；`total_net = total_gross - total_deduction`
- [ ] `approve` 副作用：调 `VoucherPort.create_accrual(sheet)`（借贷平衡校验由 M9 实现，P2 只留调用点）
- [ ] `reverse`：APPROVED→SUBMITTED，`payroll:reverse`，**必填原因**；`status != PAID`（已发放 → `33001`）；
      副作用：`VoucherPort.reverse_accrual(sheet)`
- [ ] `VoucherPort` 协议（`Protocol`）+ `NullVoucherGateway`（返回 `None`，记 `TODO(业务待确认: Q-PL09)`）
- [ ] 回写 `accrual_voucher_no`（空适配器下为 `NULL`）

**不做**：
- **不建凭证引擎**（M9 范围，见 Q-PL09 选项 A）
- 不登记 `60001`（借贷不平，归 M9；"60" 段保持未实现）
- 不做 `pay`（T-PAY-008）

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `backend/app/modules/payroll/service/sheet_approve.py` | 新增 | 审核/反审核 |
| `backend/app/modules/payroll/voucher_port.py` | 新增 | `VoucherPort` 协议 + 空适配器 |
| `backend/tests/modules/test_payroll_sheet_approve.py` | 新增 | 副作用/收敛点测试 |

## 实现要点

- [ ] **唯一凭证收敛点**：全模块只有 `voucher_port.py` 一处 import 凭证相关；M9 接入时只改这里
- [ ] `approve` 与计提同事务；`reverse` 与红冲同事务
- [ ] 空适配器**不得静默吞错**：M9 未接入时返回 `None` 并 WARN，不抛异常（发放流程可继续）
- [ ] `document_logs` 含金额与（若有的）凭证号

## 验收标准

- [ ] TC-08（已发放不可反审核 `33001`）落成
- [ ] `approve`/`reverse` 副作用对称（`08 §5` 第 6 条）
- [ ] `VoucherPort` 契约测试：空适配器返回 `None`、`accrual_voucher_no` 为 `NULL`、不抛异常
- [ ] ⚠️ **TC-06 的凭证断言标「待 M9」**，不得声称全绿（Q-PL09 未拍板前）
- [ ] 闸门 1-4 通过

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-08 | 对 `PAID` 工资单 `reverse` | `33001`，状态不变 | |
| TC-PAY-V01 | `approve` 调用 `VoucherPort.create_accrual` 一次 | 通过 | |
| TC-PAY-V02 | 空适配器下 `accrual_voucher_no` 为 `NULL` | 通过 | |
| TC-PAY-V03 | `reverse` 调用 `reverse_accrual` 一次 | 通过 | |
| TC-PAY-V04 | 并发审核/反审核 | 一成一败 `10003` | |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| | +0 / -0 | |

**提交记录**：
- `<hash>` feat(payroll): 工资单审核/反审核 + 计提凭证收敛点（T-PAY-007）

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| Q-PL09：凭证未生成；M9 建表后是否回补历史计提 | modules/05 §12 / docs/12 L-124 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。

## 变更记录

| 日期 | 变更内容 | 操作人 |
| --- | --- | --- |
| 2026-10-17 | 初版 | AI |
