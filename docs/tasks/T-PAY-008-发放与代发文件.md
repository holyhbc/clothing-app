# T-PAY-008：发放 `pay` + 代发文件 CSV + 付款凭证收敛点 + SSE

| 项 | 内容 |
| --- | --- |
| 模块 | payroll |
| 负责人 | backend-dev |
| 状态 | `todo` |
| 优先级 | P2 |
| 依赖 | T-PAY-007 |
| 被依赖 | T-PAY-009/010/011/012 |
| 关联设计 | [`modules/05-工资.md`](../modules/05-工资.md) §3.6、§4.2、§5.5、§6.3、§6.4 |
| 关联规范 | `docs/08 §2.4`、`docs/05 §8`（SSE）、`docs/04 §7.6` |
| 关联 ADR | — |
| 估算 | 0.5d |

## 目标

工资单 `approve` 后 `pay`：校验银行资料、生成代发文件记录 + CSV、记 `paid_at`、置 `PAID`、
调付款凭证收敛点、推 SSE `payroll_settled`。

## 范围

**要做**：
- [ ] `pay`：APPROVED→**PAID**，`payroll:pay`；前置：所有 `payroll_details.bank_account` 非空（否则 `10002`）
- [ ] 生成代发文件：写 `bank_payment_files`（幂等，`payroll_sheet_id` 唯一）+ CSV（UTF-8 **带 BOM**，6 列见 `05 §6.4`）
- [ ] 副作用：`VoucherPort.create_payment(sheet)`（收敛点，同 T-PAY-007 空适配器）、记 `paid_at`
- [ ] 推 SSE `payroll_settled`（复用 `GET /events/stream`，**不新建路由**）
- [ ] 导出接口与列表**共用同一 service 方法**（`07 §3.2` 铁律 3）

**不做**：
- 不做银行网银对接 / 代发回盘（`05 §1.2` 本期不做）
- 不实现扣款（Q-PL01）

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `backend/app/modules/payroll/service/pay.py` | 新增 | 发放 + 代发文件 |
| `backend/app/modules/payroll/voucher_port.py` | 修改 | 加 `create_payment` |
| `backend/app/modules/payroll/events.py` | 新增 | 发 `payroll_settled` |
| `backend/tests/modules/test_payroll_pay.py` | 新增 | 发放测试 |

## 实现要点

- [ ] CSV 金额为**字符串**两位小数（`05 §6.4`）；账号前导 0 处理
- [ ] 重复导出幂等返回同一文件（`bank_payment_files` 唯一）
- [ ] `PAID` 后禁止 `reverse`（`33001`，T-PAY-007 已实现）
- [ ] SSE 是**实时提示非真值来源**（`05 §8`）：`data` 不解析、不写 store

## 验收标准

- [ ] TC-07 / TC-09 落成
- [ ] `status=PAID`、`paid_at` 有值、CSV 6 列、金额合计 = `total_net`
- [ ] 缺银行资料 `10002`，不发不生成凭证
- [ ] 闸门 1-4 通过

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-07 | 发放产出代发文件 | `PAID` + CSV 6 列 + 合计=net | |
| TC-09 | 缺银行资料拒发 | `10002` | |
| TC-PAY-PAY01 | 重复导出幂等 | 同一文件 | |
| TC-PAY-PAY02 | 已 `PAID` 再 `pay` | `33001` | |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| | +0 / -0 | |

**提交记录**：
- `<hash>` feat(payroll): 发放 + 代发文件 + 付款凭证收敛点（T-PAY-008）

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| Q-PL09 付款凭证待 M9 | docs/12 L-124 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。

## 变更记录

| 日期 | 变更内容 | 操作人 |
| --- | --- | --- |
| 2026-10-17 | 初版 | AI |
