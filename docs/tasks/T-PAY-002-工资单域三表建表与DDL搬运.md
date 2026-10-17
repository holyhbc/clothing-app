# T-PAY-002：工资单域三表建表 + DDL 搬运

| 项 | 内容 |
| --- | --- |
| 模块 | payroll |
| 负责人 | backend-dev |
| 状态 | `done` |
| 优先级 | P2 |
| 依赖 | T-PAY-001（`piecework_settlements` 已建、迁移 0020） |
| 被依赖 | T-PAY-006/007/008 |
| 关联设计 | [`modules/05-工资.md`](../modules/05-工资.md) §3.4/§3.5/§3.6；[`modules/P2-工资-实施说明.md`](../modules/P2-工资-实施说明.md) §2/§3 |
| 关联规范 | `docs/04 §2`/`§3`/`§7.3`/`§7.6` |
| 关联 ADR | ADR-0010 |
| 估算 | 0.5d |

## 目标

建出 `payroll_sheets` / `payroll_details` / `bank_payment_files` 三张表，工资单域可写。

## 范围

**要做**：
- [x] 补 `bank_payment_files` 字段表（`modules/05 §3.6` 是散文，无类型）→ 搬进 `04 §7.6.6`
- [x] 搬运 `payroll_sheets` / `payroll_details` DDL 进 `04 §7.6.4`/`§7.6.5`
- [x] 迁移 **0021**：三表 + 索引 + 授权
- [x] `payroll_sheets.settlement_id` FK → `piecework_settlements(id)`（唯一，阶段一 1:1）
- [x] 守卫：三表加进 `P1_PENDING_TABLES`（补 DDL 同提交）→ 建表同提交删除；
      `FIELD_TABLE_SOURCES` 登记 `payroll_sheets`/`payroll_details` 源 = `modules/05 §3.4/§3.5`

**不做**：
- 不写 service / router（T-PAY-006 起）
- 不实现扣款项录入（Q-PL01 未定，`deduction_amount` 恒 0）

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `backend/alembic/versions/0021_payroll_sheet_tables.py` | 新增 | 三表 + 索引 + 授权 |
| `backend/app/modules/payroll/models.py` | 修改 | 加三张表 ORM |
| `backend/tests/modules/test_payroll_sheets_tables.py` | 新增 | 表结构测试 |
| `backend/tests/modules/test_docs_ddl_sync.py` | 修改 | 白名单增删 |
| `docs/04-数据库规范.md` | 修改 | §7.6 补三表 DDL（含 `bank_payment_files` 类型） |
| `docs/modules/05-工资.md` | 修改 | §3.6 补字段表（或标注 DDL 权威在 04 §7.6.6） |

## 实现要点

- [x] ⚠️ **`payroll_sheets.doc_date` 必填**：`04 §7.3` 单据公共列（`05 §4.2 update` 白名单、`05 §6.1` `sort`/`05 §8 S5` 排序依赖）；`modules/05 §3.4` 字段表**原漏列**（与 `§3.2` 同型，T-PAY-001 已在 `§3.2` 上发现）——建表时补进 `05 §3.4` 与 `04 §7.6.4`，并加同型断言
- [x] `payroll_sheets.status` 用 `document_status`（**不用**新建枚举；`PAID` 已在 0009 建出）
- [x] `bank_payment_files` 公共字段口径：建议按 `bundle_label_prints` append-only
      （仅 `id`+`created_at`+`created_by`），重导幂等返回同一行——**建表时定并写进 04 §7.6.6**
- [x] `payroll_details` 唯一键 `uq_payroll_details_sheet_employee_operation_style (doc_id, employee_id, style_no, operation_no)`
- [x] `CHECK (total_net = total_gross - total_deduction)`、`CHECK (net_amount = gross_amount - deduction_amount)`
- [x] 索引照 `05 §8`：`uq_payroll_sheets_doc_no`、`uq_payroll_sheets_settlement_id`、
      `idx_payroll_sheets_workshop_period`、`idx_payroll_details_doc_id`、`uq_bank_payment_files_sheet_id`
- [x] 金额列全 `numeric(18,4)`、单价 `numeric(12,6)`（`04 §4`，禁 float）

## 验收标准

- [x] 迁移 0021 往返 + `alembic check` 零漂移
- [x] 守卫白名单**全清**（6 张工资表都不在 `P1_PENDING_TABLES`）
- [x] 表结构测试：`settlement_id` 唯一 / 两个 CHECK 真拦 / `bank_payment_files` 唯一 / 授权
- [x] 闸门 1/2/4/5 全绿；闸门 3 定向全绿，全量仅 2 项**测试库污染**失败（非本卡，见遗留问题）

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-PAY-T10 | 三表列与 `04 §7.6` 一致 | 通过 | ✅ 通过（另加 append-only 列集 / 代发快照列断言） |
| TC-PAY-T11 | `payroll_sheets` 含 `04 §7.3` 单据公共列（含 `doc_date`） | 通过 | ✅ 通过（独立断言 `doc_date`） |
| TC-PAY-T12 | `uq_payroll_sheets_settlement_id` 挡一结算单出两工资单 | 唯一冲突 | ✅ 通过（配合法反证） |
| TC-PAY-T13 | `ck_payroll_sheets_net` 拦 `total_net ≠ gross - deduction` | CHECK 失败 | ✅ 通过（另加 `ck_payroll_sheets_amount` / `ck_payroll_details_net` + 反证） |
| TC-PAY-T14 | `uq_bank_payment_files_sheet_id` 挡重复导出 | 唯一冲突 | ✅ 通过（配合法反证） |
| TC-PAY-T15 | `downgrade 0020` / `upgrade` 往返 | 通过 | ✅ 通过（并断言 `PAID` 未被误删） |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `backend/alembic/versions/0021_payroll_sheet_tables.py` | +287 | 三表 + 索引 + 授权收口（新增） |
| `backend/app/modules/payroll/models/__init__.py` | +40 | 包重导出（新增，ADR-0031） |
| `backend/app/modules/payroll/models/settlement.py` | +243 | 结算域三表（由 `models.py` 拆出，新增） |
| `backend/app/modules/payroll/models/sheet.py` | +212 | 工资单域三表 ORM（新增） |
| `backend/app/modules/payroll/models.py` | -245 | **改包**（ADR-0031；导入面零改动） |
| `backend/app/core/scope.py` | +6 | 登记 `payroll_sheets` → `SCOPE_SPECS`（守卫逼出，同 T-PAY-001 先例） |
| `backend/tests/modules/test_payroll_sheets_tables.py` | +358 | 表结构测试 T10~T15（新增） |
| `backend/tests/factories/payroll.py` | +94 | `sheet_row` / `detail_row` / `bank_payment_file_row` / `seed_sheet` |
| `backend/tests/modules/test_docs_ddl_sync.py` | +4 | 记录 T-PAY-002 三表已建（白名单保持全清） |
| `backend/tests/modules/test_docs_ddl_sync_fields.py` | +5 | `FIELD_TABLE_SOURCES` 登记三表源 |
| `docs/04-数据库规范.md` | +138 | §7.6.4~§7.6.6 DDL + §7.6 抬头 |
| `docs/modules/05-工资.md` | +17 | §3.4 补 `doc_date`；§3.6 补字段表 + append-only 口径 |

**提交记录**：
- `<hash>` feat(payroll): 工资单域三表建表 + DDL 搬运（T-PAY-002）

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| Q-PL06（1:1 vs 合并）确认后可能改唯一键 | modules/05 §12 |
| 测试库 `erp_app` 被授予了 18 张表的 `DELETE`（规范白名单只有 6 张），导致 `test_material_tables::test_material_category_cannot_be_hard_deleted_by_app_role` 在**本机测试库**失败（非本卡代码问题，见 `docs/12` L-094） | docs/12 §5 |
| `test_scope.py` 守卫要求「有 `workshop_id` 的表必须登记数据范围」→ `payroll_sheets` 被逼登记 `SCOPE_SPECS`（超出卡面文件清单，同 T-PAY-001 先例） | 本卡说明 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。

## 变更记录

| 日期 | 变更内容 | 操作人 |
| --- | --- | --- |
| 2026-10-17 | 初版 | AI |
| 2026-10-17 | 完成：三表建表 + DDL 搬运 + 迁移 0021 + 分包 + 守卫同步；验证迁移往返/`alembic check` 零漂移、定向 140 passed、全量 1158 passed / 覆盖率 90.92%（2 个非本卡失败为测试库污染） | AI |
