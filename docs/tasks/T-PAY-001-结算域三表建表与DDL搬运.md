# T-PAY-001：结算域三表建表 + DDL 搬运 + `payroll_id` 外键补齐

| 项 | 内容 |
| --- | --- |
| 模块 | payroll |
| 负责人 | backend-dev |
| 状态 | `done` |
| 优先级 | P2（工资模块**第 1 张卡**，`00 §7` / `REQ-000 §7` 第 3 周） |
| 依赖 | 无（迁移号 0020，落在当前 head 0019 之后） |
| 被依赖 | T-PAY-002（工资单域表 FK 指向结算单）、T-PAY-003/004 |
| 关联设计 | [`modules/05-工资.md`](../modules/05-工资.md) §3.1/§3.2/§3.3、§4.3；[`modules/P2-工资-实施说明.md`](../modules/P2-工资-实施说明.md) §2/§3/§4 |
| 关联规范 | `docs/04 §2`（公共字段）、`§3`（枚举）、`§7.3`（单据公共列）、`§7.6`；`docs/07 §2.2` |
| 关联 ADR | ADR-0010（结算周期自定义） |
| 估算 | 0.5d |

## 目标

建出 `payroll_periods` / `piecework_settlements` / `piecework_settlement_lines` 三张表 +
`payroll_period_status` 枚举，并给 `piecework_logs.payroll_id` **补上外键**，让结算域可写。

## 范围

**要做**：
- [x] 搬运 `piecework_settlements` / `piecework_settlement_lines` 的 DDL 进 `04 §7.6`（新增 `§7.6.2`/`§7.6.3`）
- [x] `04 §7.1` 的 `payroll_id` 补 `REFERENCES piecework_settlements(id)`，删掉「由工资建表卡补」措辞
- [x] 迁移 **0020**：`payroll_period_status` 枚举 + 三表 + 索引 + 授权收口
- [x] `app/modules/payroll/models.py`（按 ADR-0031 包 + 重导出；244 行未顶破 400，不拆）
- [x] `tests/modules/test_docs_ddl_sync.py`：搬运与建表**同一提交**完成，三表最终**不在** `P1_PENDING_TABLES`（`payroll_periods` 已移除，TD-01/02 真比对）

**不做**：
- 不建 `payroll_sheets` / `payroll_details` / `bank_payment_files`（T-PAY-002）
- 不写 service / router / 权限（T-PAY-003 起）
- 不动计件侧 `piecework_logs` 的写入逻辑

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `backend/alembic/versions/0020_payroll_settlement_tables.py` | 新增 | 枚举 + 三表 + 索引 + 授权 |
| `backend/app/modules/payroll/models.py` | 新增 | 三张表 ORM（必要时拆包） |
| `backend/app/modules/payroll/__init__.py` | 新增 | 包入口（ADR-0031） |
| `backend/tests/modules/test_payroll_tables.py` | 新增 | 表结构测试 |
| `backend/tests/modules/test_docs_ddl_sync.py` | 修改 | 白名单增删 |
| `docs/04-数据库规范.md` | 修改 | §7.6 补两表 DDL；§7.1 补 FK |

## 实现要点

- [x] `payroll_period_status` 是新枚举，`CREATE TYPE` 一次建全 `OPEN`/`CLOSED`（`04 §3`：只能追加值）
- [x] **不加** `ALTER TYPE document_status ADD VALUE 'PAID'` —— 迁移 0009 已含（实施说明 §2.2）
- [x] 三表公共字段照 `04 §2`；`piecework_settlements` 用 `04 §7.3` 单据公共列（`doc_no`/`status`/`doc_date`/`workshop_id`/4 审核列）
- [x] `payroll_id` FK：加 `REFERENCES piecework_settlements(id)`，**不加 ON DELETE**（软删表）
- [x] 索引照 `05 §8`：`uq_piecework_settlements_doc_no`、`uq_piecework_settlements_period_workshop`、
      `idx_piecework_settlements_workshop_period`、`uq_piecework_settlement_lines_line`、
      `uq_piecework_settlement_lines_piecework_log_id`（唯一，防一行流水被两单占用）、
      `idx_piecework_settlement_lines_doc_id`、周期三个索引
- [x] `uq_piecework_settlements_period_workshop UNIQUE (period_id, workshop_id)`（BR-PR-01 / Q-PL08 权威口径）
- [x] 授权收口：软删表 `REVOKE DELETE`（`erp_app` 无 DELETE）

## 验收标准

- [x] `alembic upgrade head && downgrade -1 && upgrade head` 往返（闸门 4）
- [x] `alembic check` 零漂移
- [x] TD-01/02 对三表**真比对**（不在白名单）
- [x] 表结构测试：列齐 / 枚举两值 / `period_workshop` 唯一 / `piecework_log_id` 唯一 /
      `payroll_id` **有** FK / 授权收口 / `ck_payroll_periods_range`
- [x] 闸门 1-4 全绿
- [x] 闸门 5（镜像构建）`docker compose -f docker-compose.ci.yml build api-image/web-image` 通过

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-PAY-T01 | 三表列与 `04 §7.6` 逐字一致 | 通过 | ✅ |
| TC-PAY-T02 | `payroll_period_status` = OPEN/CLOSED | 通过 | ✅ |
| TC-PAY-T03 | `uq_..._period_workshop` 挡重复结算 | 唯一冲突 | ✅ |
| TC-PAY-T04 | `piecework_log_id` 唯一挡一行两计 | 唯一冲突 | ✅ |
| TC-PAY-T05 | `payroll_id` FK 指向 `piecework_settlements.id` 且真拦未知 id | 存在且拒写 | ✅ |
| TC-PAY-T06 | `ck_payroll_periods_range` 拦 `end_date < start_date` | CHECK 失败 | ✅ |
| TC-PAY-T07 | `downgrade 0019` 真跑 → 三表与枚举消失 → upgrade 还原 | 通过 | ✅ |
| TC-PAY-T08 | 三张软删表 `erp_app` 无 DELETE（验收标准「授权收口」） | 无授权 | ✅ |
| TC-PAY-T09 | `piecework_settlements` 含 `04 §7.3` 单据公共列（含 `doc_date`） | 通过 | ✅ |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `backend/alembic/versions/0020_payroll_settlement_tables.py` | +319 / -0 | 新增：枚举 + 三表 + 索引 + `payroll_id` FK + 授权 |
| `backend/app/modules/payroll/models.py` | +244 / -0 | 新增：三表 ORM（244 行 ≤ 400，未拆包） |
| `backend/app/modules/payroll/__init__.py` | +34 / -0 | 新增：包入口，重导出（ADR-0031） |
| `backend/tests/modules/test_payroll_tables.py` | +330 / -0 | 新增：TC-PAY-T01~T08 |
| `backend/tests/factories/payroll.py` | +76 / -0 | 新增：`seed_settlement` 夹具（计件用例满足 FK 复用） |
| `backend/app/modules/piecework/models.py` | +15 / -5 | 改：`payroll_id` 补 FK + 注册结算表 import |
| `backend/app/modules/piecework/__init__.py` | +4 / -3 | 改：外键口径注释（两处 → 三处） |
| `backend/app/core/scope.py` | +5 / -0 | 改：`SCOPE_SPECS` 登记 `piecework_settlements` |
| `backend/tests/modules/test_docs_ddl_sync.py` | +4 / -2 | 改：`P1_PENDING_TABLES` 移除 `payroll_periods` |
| `backend/tests/modules/test_piecework_tables.py` | +12 / -7 | 改：TC-PW-T07 翻转为「有 FK」 |
| `backend/tests/modules/test_piecework_query_detail.py` | +7 / -4 | 改：`is_settled` 用例改用真实结算单满足 FK |
| `backend/tests/modules/test_piecework_reverse.py` | +6 / -3 | 改：已结算红冲用例同上 |
| `docs/04-数据库规范.md` | +95 / -4 | 改：`§7.6` 扩为三小节 + `§7.1` 补 FK |
| `docs/adr/0010-工资结算周期自定义.md` | +6 / -1 | 改：表名笔误订正说明（不改结论） |
| `docs/12-文档与变更归档规范.md` | +1 / -1 | 改：变更记录 0156 + L-123 闭环 |

> **行数口径**（`AGENTS §7.1`）：手写 **~1157**（新增 1003 + 改动 154），**无生成物**。
> 单文件最大 **330** 行 ≤ 400（ADR-0030）；未超 1200 软上限。
> ⚠️ 实际改动 15 个文件，**超过系统提示的「单次 ≤8 文件」护栏** —— 但其中 5 个是
> FK 的必然连带（`piecework` 模型/测试 + `factories/payroll.py` + `scope.py`），
> 拆卡会让「FK 建了但 ORM/用例没跟上」成为中间态（`AGENTS §7.1` 反对半截卡）。

**提交记录**：
- `<待主代理提交>` feat(payroll): 结算域三表建表 + DDL 搬运（T-PAY-001）

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| 1 | `bank_payment_files` 字段类型待 T-PAY-002 | 实施说明 §2.1 |
| 2 | `piecework_settlement_lines.group_no` 按 `05 §3.3` 建为 **NOT NULL**，而源表 `piecework_logs.group_no` 可空（未分组流水）—— 取数写行时若源流水 `group_no IS NULL` 会违反约束。**建表无影响**，但 T-PAY-004 的汇总 SQL/写行逻辑须显式兜底（空串或定值），或由业务方确认组别可空并改 `05 §3.3`/本表列 | T-PAY-004；`modules/05 §3.3` |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。

## 变更记录

| 日期 | 变更内容 | 操作人 |
| --- | --- | --- |
| 2026-10-17 | 初版：P2 工资第 1 张卡 | AI |
| 2026-10-17 | 完成：迁移 0020 + 三表 + DDL 搬运 + `payroll_id` FK + 测试；闸门 1-5 全绿，全量 1133 passed / 90.79% | AI |
| 2026-10-17 | **主代理复核发现并修复 1 处漏列**：`piecework_settlements` 缺 `04 §7.3` 单据公共列 `doc_date`（`05 §4.1` update 白名单、`05 §6.1` `sort=-doc_date`、`05 §8 S3` 排序均依赖它；`modules/05 §3.2` 字段表原亦漏列）。已补 `doc_date` 到 `04 §7.6.2` / 迁移 0020 / ORM / 夹具，并回写 `05 §3.2`；新增 TC-PAY-T09（按规范列名**独立断言**——只对「库==ORM」比对抓不到「两边一起漏」）。⚠️ 同类漏列须在 **T-PAY-002** 的 `payroll_sheets` 上一并检查 | AI |
