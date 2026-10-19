# T-PAY-008：发放 `pay` + 代发文件 CSV + 付款凭证收敛点 + SSE

| 项 | 内容 |
| --- | --- |
| 模块 | payroll |
| 负责人 | backend-dev |
| 状态 | `done` |
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
- [x] `pay`：APPROVED→**PAID**，`payroll:pay`；前置：所有 `payroll_details.bank_account` 非空（否则 `10002`）
- [x] 生成代发文件：写 `bank_payment_files`（幂等，`payroll_sheet_id` 唯一）+ CSV（UTF-8 **带 BOM**，6 列见 `05 §6.4`）
- [x] 副作用：`VoucherPort.create_payment(sheet)`（收敛点，同 T-PAY-007 空适配器）、记 `paid_at`
- [x] 推 SSE `payroll_settled`（复用 `GET /events/stream`，**不新建路由**）
- [x] 导出接口与列表**共用同一 service 方法**（`07 §3.2` 铁律 3）

**不做**：
- 不做银行网银对接 / 代发回盘（`05 §1.2` 本期不做）
- 不实现扣款（Q-PL01）
- 不做 router（归 T-PAY-009）；本卡只把导出 service 方法备好

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `backend/app/modules/payroll/service/pay.py` | 新增 | `PayrollPayMixin`：`pay` + 代发导出 |
| `backend/app/modules/payroll/service/bank_file.py` | 新增 | 代发 CSV 渲染 / 记录构造（纯函数，pay 与导出共用） |
| `backend/app/modules/payroll/events.py` | 新增 | 发 `payroll_settled` |
| `backend/app/modules/payroll/voucher_port.py` | 修改 | 加 `create_payment` |
| `backend/app/modules/payroll/service/sheet_state.py` | 修改 | 加 `pay` 权限点与迁移 `APPROVED→PAID` |
| `backend/app/modules/payroll/service/sheet.py` | 修改 | 组合类继承 `PayrollPayMixin` |
| `backend/app/modules/payroll/service/__init__.py` | 修改 | 重导出 `BankFileExport` |
| `backend/app/common/enums.py` | 修改 | `DocumentAction.PAY`（`document_logs.action` 不是 PG enum，无需迁移） |
| `backend/tests/factories/payroll_pay.py` | 新增 | 发放用例共用测试世界 |
| `backend/tests/modules/test_payroll_pay.py` | 新增 | TC-07/09、TC-PAY-PAY01/02 |
| `backend/tests/modules/test_payroll_bank_file.py` | 新增 | 权限 / 数据范围 / 草稿导出 / 唯一冲突回退 |

## 实现要点

- [x] CSV 金额为**字符串**两位小数（`05 §6.4`）；账号前导 0 处理（字符串 + 整字段加引号）
- [x] 重复导出幂等返回同一文件（`bank_payment_files` 唯一）
- [x] `PAID` 后禁止 `reverse`（`33001`，T-PAY-007 已实现）；已 `PAID` 再 `pay` → `33001`
- [x] SSE 是**实时提示非真值来源**（`05 §8`）：`data` 不解析、不写 store

## 验收标准

- [x] TC-07 / TC-09 落成
- [x] `status=PAID`、`paid_at` 有值、CSV 6 列、金额合计 = `total_net`
- [x] 缺银行资料 `10002`，不发不生成凭证
- [x] 闸门 1-4 通过

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-07 | 发放产出代发文件 | `PAID` + CSV 6 列 + 合计=net | ✅ `test_tc07_pay_produces_bank_file` |
| TC-09 | 缺银行资料拒发 | `10002` + 不写文件 + 不调端口 | ✅ `test_tc09_missing_bank_account_10002` |
| TC-PAY-PAY01 | 重复导出幂等 | 同一文件（同 id / 同内容） | ✅ `test_tc_pay_pay01_repeat_export_idempotent` |
| TC-PAY-PAY02 | 已 `PAID` 再 `pay` | `33001` | ✅ `test_tc_pay_pay02_paid_again_33001` |
| — | 权限 / 数据范围 / 草稿导出 / 唯一冲突回退 | `12001` / `12002` / `10008` / 重查既有行 | ✅ `test_payroll_bank_file.py`（5 例） |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `backend/app/modules/payroll/service/pay.py` | +372 | `PayrollPayMixin`（`pay` / `bank_file_export` / 幂等取建 / 校验） |
| `backend/app/modules/payroll/service/bank_file.py` | +142 | CSV 渲染（BOM / 6 列 / 金额字符串 / 引号）/ 记录构造 |
| `backend/app/modules/payroll/events.py` | +69 | `payroll_settled` 事件 + `SettledEvent` 快照 |
| `backend/app/modules/payroll/voucher_port.py` | +18 / -1 | Protocol + 空适配器加 `create_payment` |
| `backend/app/modules/payroll/service/sheet_state.py` | +11 | `pay` 权限点 + `APPROVED→PAID` 迁移 |
| `backend/app/modules/payroll/service/sheet.py` | +9 / -3 | 组合类继承 `PayrollPayMixin` |
| `backend/app/modules/payroll/service/__init__.py` | +5 | 重导出 `BankFileExport` |
| `backend/app/common/enums.py` | +7 | `DocumentAction.PAY` |
| `backend/tests/factories/payroll_pay.py` | +176 | 共用测试世界 |
| `backend/tests/modules/test_payroll_pay.py` | +183 | TC-07/09、TC-PAY-PAY01/02 |
| `backend/tests/modules/test_payroll_bank_file.py` | +122 | 边界分支 5 例 |
| **合计（手写）** | **约 +1111 / -4** | 单文件最大 `pay.py` **372** ≤400；未超 1200 软上限 |

**提交记录**：
- `<hash>` feat(payroll): 发放 + 代发文件 + 付款凭证收敛点（T-PAY-008）
  （本次按用户要求**不 commit**，hash 待提交后回填）

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| Q-PL09 付款凭证待 M9 | docs/12 L-124 |
| `05 §8` 未定义 `payroll_settled` 字段集；`08 §1.1` 无 `pay` 动作名（本卡补 `DocumentAction.PAY`） | docs/12 L-132 |
| 代发文件三处口径规范未定（按员工一行 / 导出即建记录 / 前置只校验 `bank_account`） | docs/12 L-133 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。

- [x] 读过本任务对应 docs 规范
- [x] 无硬编码业务常量（金额/税率/单价）
- [x] 无物理删除（`bank_payment_files` append-only）
- [x] 新表字段齐全（本卡无新表；`bank_payment_files` 由 T-PAY-002 建）
- [x] 状态变更走 service 迁移方法 + 写 `document_logs`
- [x] 接口权限 / 错误码 / OpenAPI 标签（本卡无 router，`require_permission` 归 T-PAY-009）
- [x] 测试覆盖正常 + 异常 + 权限拒绝（并发回退分支已覆盖）
- [x] 闸门 1 lint / 2 typecheck / 4 迁移可回滚（无迁移，`alembic check` 零漂移）
- [x] 提交信息符合规范；手写 ≤1200 且单文件 ≤400
- [x] 已在 docs/12 变更记录留痕

## 变更记录

| 日期 | 变更内容 | 操作人 |
| --- | --- | --- |
| 2026-10-17 | 初版 | AI |
| 2026-10-19 | 实现完成：`pay` + 代发 CSV + 付款凭证收敛点 + SSE；无 router / 无迁移 | AI |
