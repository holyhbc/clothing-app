# T-PAY-006：工资单生成 + 轻状态迁移 + 银行资料快照

| 项 | 内容 |
| --- | --- |
| 模块 | payroll |
| 负责人 | backend-dev |
| 状态 | `todo` |
| 优先级 | P2 |
| 依赖 | T-PAY-002、T-PAY-005 |
| 被依赖 | T-PAY-007 |
| 关联设计 | [`modules/05-工资.md`](../modules/05-工资.md) §3.4/§3.5、§4.2、§5.3 |
| 关联规范 | `docs/04 §4`/`§7.3`、`docs/05 §3` |
| 关联 ADR | ADR-0010 |
| 估算 | 0.5d |

## 目标

由已审核结算单汇总生成工资单（每人每款号每工序一行），快照单价与银行资料，
并实现 `update` / `submit` / `withdraw` / `reject` / `cancel` 轻迁移。

## 范围

**要做**：
- [ ] `create`：来源结算单须 `APPROVED` 且周期仍 `OPEN`（否则 `33003`）；未生成过（`settlement_id` 唯一）
- [ ] 按 `(employee_id, style_no, operation_no)` 分组汇总结算行 → `payroll_details`（`qty`/`unit_price`/`gross_amount`）
- [ ] 从 `users` 快照 `id_card`/`bank_name`/`bank_account` 到行上（发放后改卡不影响历史）
- [ ] 单头 `total_gross`/`total_deduction`/`total_net`（`deduction_amount` 恒 0，Q-PL01）
- [ ] 轻迁移 `update`/`submit`/`withdraw`/`reject`/`cancel`（`05 §4.2`）
- [ ] 单号 `PR`

**不做**：
- 不做 `approve`/`reverse`/`pay`（T-PAY-007/008）
- 不重读基础资料取价（`05 §5.3` 明写单价取结算行明细值）

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `backend/app/modules/payroll/service/sheet.py` | 新增 | 生成 + 轻迁移 |
| `backend/app/modules/payroll/service/settlement.py` | 修改 | 复用汇总 |
| `backend/tests/modules/test_payroll_sheet.py` | 新增 | 单元测试 |

## 实现要点

- [ ] 汇总粒度 `uq_payroll_details_sheet_employee_operation_style`
- [ ] `qty` 恒整数（BR-PR-22）；`unit_price` 取结算行快照值
- [ ] `net_amount = gross_amount - deduction_amount`（service 计算 + 测试断言）
- [ ] 缺 `bank_account`/`id_card` 不阻断生成（发放时才 `10002`）

## 验收标准

- [ ] TC-04 / TC-05 落成
- [ ] `Σgross_amount` = 结算行金额；`qty` = 结算行汇总
- [ ] 闸门 1-4 通过

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-04 | 单价快照不被改价影响 | 不变 | |
| TC-05 | 工资单汇总粒度（2 款号 3 工序） | 6 行，金额一致 | |
| TC-PAY-P01 | 来源结算单未审核 | `33003`/拒绝 | |
| TC-PAY-P02 | 同结算单重复生成 | 幂等返回 | |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| | +0 / -0 | |

**提交记录**：
- `<hash>` feat(payroll): 工资单生成与轻状态迁移（T-PAY-006）

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| Q-PL01 扣款未定（`deduction_amount` 恒 0） | modules/05 §12 |
| Q-PL06 1:1 未定 | modules/05 §12 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。

## 变更记录

| 日期 | 变更内容 | 操作人 |
| --- | --- | --- |
| 2026-10-17 | 初版 | AI |
