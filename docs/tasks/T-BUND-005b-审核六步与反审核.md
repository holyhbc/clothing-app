# T-BUND-005b：`approve` 审核六步（批量生成 `bundles`）+ `reverse` 反审核

| 项 | 内容 |
| --- | --- |
| 模块 | bundling |
| 负责人 | backend-dev |
| 状态 | `todo`（**Q-B15 / Q-B16 / Q-B02 / Q-B13 未闭环前不得定版**） |
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
- [ ] `service/approve_mixin.py`（或并入 `state_mixin.py`）：`approve`
  - 六步**顺序不可调换**（`03 §4.1`）：① 按手生成 `bundle_no`（复用 T-BUND-004 纯函数）
    ② 每手件数（按 Q-B15 结论）③ 码数=手数否则 `31004` ④ 手序号重复 `31005`
    ⑤ `uq_bundles_hand` 兜底 ⑥ 更新 `cutting_outputs`（`bundled_qty += 本单件数`，
    `reserved_qty` 清零）
  - **批量插入用 `INSERT … SELECT … FROM generate_series()`**，**禁止逐条 INSERT / ORM 循环**
  - 审核事务内跑 §5.3 的 4 条断言，任一不过整事务回滚
  - 制单人 ≠ 审核人；幂等：`Idempotency-Key`（05 §5）
- [ ] `reverse`：必填 `reason`；有计件流水 → `32003`；本单全部码置 `VOIDED` +
      `voided_at` + `void_reason`（**行保留、不删**）；`bundled_qty` 减回、`reserved_qty` 重新预占
- [ ] `backend/tests/modules/test_bundling_approve.py`：六步 + 断言 + 并发 + 反审核对称

**不做**：
- 单码作废 `void_code`（`bundling:code:void`）—— 可作为独立小卡，未列入本拆解（见待决）
- 改单联动（ADR-0021，→ Q-B19）

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `backend/app/modules/bundling/service/approve_mixin.py` | 新增 | 六步批量生成 + 断言 |
| `backend/app/modules/bundling/service.py` 或组合类 | 修改 | 组合 Mixin |
| `backend/app/modules/bundling/service/__init__.py` | 修改 | 导出面 |
| `backend/tests/modules/test_bundling_approve.py` | 新增 | 六步/断言/并发/反审核 |

## 实现要点（必读规范）

- [ ] 严格照 `docs/08 §2.2` 与 `03 §4.1`；**审核副作用与反审核反向副作用成对**
- [ ] `bundles` 批量插入前先回写 `hands_total` / `output_qty` / `balance_qty` /
      `planned_qty` 并写 `document_logs`（取整前后值）
- [ ] 并发：乐观锁 + `uq_bundles_hand` + `uq_bundles_no` 三层防线（`03 §7`）
- [ ] 生成后同事务跑 4 条断言：码数=手数、余数归位、手号 1..N 无缺、件数 ≤ 可用量
- [ ] **不接受前端传入 `hands` 值**，服务端 `generate_series` 统一展开
- [ ] 错误码：`31004`/`31005`/`32003`/`30002`/`10003`（均 05 §4 已登记）

## 验收标准

- [ ] `uv run pytest tests/modules/test_bundling_approve.py -q` 全部通过
- [ ] 1:2:2:1 → 6 手生成恰好 6 个码，手号 `L01/XL01/XL02/XXL01/XXL02/3XL01`
- [ ] 2000 手批量：SQL 语句数为明细行数量级，`bundles` 增 2000 行，手号连续无缺
- [ ] 码数异常 → `31004`（details 回「应有/当前」）；手号重复 → `31005`
- [ ] 反审核：码全 `VOIDED`、行保留、`cutting_outputs` 还原、日志 `REVERSE` + 原因
- [ ] 20 并发 `approve`：1 次成功，其余 `10003`，无重复手号、无缺号
- [ ] 单文件 ≤400 行；闸门 1-4 本地预跑通过

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-AP-01 | 1:2:2:1 | 6 码，手号连续 | |
| TC-AP-02 | 除不尽 `hands=3,out=100` | 3 码 × 33，余 1 归 `balance_qty` | |
| TC-AP-03 | 码数 ≠ 手数 | `31004`，零新增 | |
| TC-AP-04 | 手号重复 | `31005` | |
| TC-AP-05 | 制单人自审 | 拒绝 | |
| TC-AP-06 | 2000 手性能 | 语句数 ≈ 明细行数 | |
| TC-AP-07 | 反审核有计件 | `32003` | |
| TC-AP-08 | 反审核副作用对称 | 码 `VOIDED`、结转还原 | |
| TC-AP-09 | 并发审核 | 一成一败 | |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| 待回填 | | |

**提交记录**：
- `<hash>` feat(bundling): approve 审核六步批量生成 + reverse 反审核

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| Q-B15/Q-B16 | 每手件数算法 / 类型 | `P1-打菲-实施说明.md` §7 |
| Q-B02/Q-B13 | 手号模型 / 跨布批编法 | `03 §12` |
| void_code | 单码作废未单开卡 | 待排期 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。

## 变更记录

| 日期 | 变更内容 | 操作人 |
| --- | --- | --- |
| 2026-10-06 | 初版：审核六步 + 反审核对称副作用；批量 INSERT 禁止逐条 | AI |
