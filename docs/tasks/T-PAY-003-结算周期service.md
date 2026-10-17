# T-PAY-003：结算周期 service（四种建法 + 核对数 + 状态机）

| 项 | 内容 |
| --- | --- |
| 模块 | payroll |
| 负责人 | backend-dev |
| 状态 | `todo` |
| 优先级 | P2 |
| 依赖 | T-PAY-001 |
| 被依赖 | T-PAY-004（结算单按周期取数） |
| 关联设计 | [`modules/05-工资.md`](../modules/05-工资.md) §2 BR-PR-16~21、§4.3、§5.1、§6.1.1 |
| 关联规范 | `docs/08 §1.2`/`§4`、`docs/04 §2`/`§7.6`、`docs/05 §4`（`33003/33004/10006/10007`） |
| 关联 ADR | ADR-0010 |
| 估算 | 0.5d |

## 目标

把「工资结算周期」做成可用的 service：自定义 / 整月 / 两月 / 按裁剪打菲截取四种建法，
建周期返回区间内未结算流水条数与金额（核对数），并提供 `close` / `reopen` 极简状态机。

## 范围

**要做**：
- [ ] `create`：落库周期；区间部分重叠 → `33004`；`period_code` 重复 → 由唯一约束兜底
- [ ] `generate`（**预演不落库**）：`mode=month|bimonth|cutting_boundary`，返回区间 + `unsettled_log_count` / `unsettled_amount`（BR-PR-18）
- [ ] `close` / `reopen`：`payroll:period:manage` + **必填原因**（`10006`）+ `document_logs`（`doc_type='PayrollPeriod'`）
- [ ] 周期迁移统一走 `service.payroll_period.<action>()`，`WHERE id=? AND version=? AND status=?` 条件 UPDATE，`rowcount=0` → `10003`
- [ ] 取数核对 SQL（BR-PR-18 / `05 §8 S10`）：`count(*), sum(qty), sum(amount) WHERE work_date BETWEEN ? AND ? AND workshop_id=? AND payroll_id IS NULL AND deleted_at IS NULL`

**不做**：
- 不做结算单（T-PAY-004）；不做 router（T-PAY-009）
- 不解析 `period_code` 标签（BR-PR-20 禁止）

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `backend/app/modules/payroll/service/period.py` | 新增 | 周期 service |
| `backend/app/modules/payroll/service/__init__.py` | 新增/修改 | 导出 |
| `backend/tests/modules/test_payroll_period.py` | 新增 | 单元测试 |
| `backend/tests/factories/payroll.py` | 新增 | 夹具 |

## 实现要点

- [ ] 日期**从周期表读**，`generate` 的区间计算是唯一算日期的地方；`create` 接收显式区间
- [ ] `bimonth` 含起始月 + 下一月；`month` 的月末按自然月（大小月/2 月以实际月末为准）
- [ ] `cutting_boundary`：取该款裁剪单最早完成日 ~ 打菲单最晚完成日，`source='CUTTING_BOUNDARY'`，标签 `裁剪{起}-{止}`；款号不存在/无完成日 → `20001`
- [ ] `close` 前置：区间内无 `DRAFT`/`SUBMITTED` 结算单（有则提示先处理）
- [ ] 周期**无 delete**（INV-7）；`reopen` 后已结算数据不变（快照）

## 验收标准

- [ ] `pytest tests/modules/test_payroll_period.py -q` 全过
- [ ] TC-20~TC-24 / TC-27 / TC-28 / TC-29 / TC-31 落成
- [ ] 周期迁移写 `document_logs`（含 `from_status`/`to_status`/`reason`）
- [ ] 闸门 1-4 通过

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-20 | 整月边界取数（含首尾日，不含次日） | 通过 | |
| TC-21 | 两月一次跨月区间 + 标签 | 通过 | |
| TC-22 | 整月生成（大小月月末） | 通过 | |
| TC-23 | 同区间重复建周期被拒 | `33004` / 唯一约束 | |
| TC-24 | 区间部分重叠被拒 | `33004` | |
| TC-27 | 重开必填原因 + 权限 | `10002`/`12001`/成功 | |
| TC-28 | 按裁剪打菲完成日截取 | 区间与标签正确 | |
| TC-29 | `generate` 返回核对数且不落库 | 通过 | |
| TC-31 | 非法周期参数 | `10001`/CHECK | |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| | +0 / -0 | |

**提交记录**：
- `<hash>` feat(payroll): 结算周期 service（T-PAY-003）

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
