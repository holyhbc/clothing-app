# T-BASE-012：`users` 补身份证与银行卡三列（L-128，T-PAY-008 前置）

| 项 | 内容 |
| --- | --- |
| 模块 | base/system（`users` 主数据） |
| 负责人 | backend-dev |
| 状态 | `done` |
| 优先级 | P2（工资发放的前置；`docs/12` L-128） |
| 依赖 | 无 |
| 被依赖 | T-PAY-008（发放 `pay` 从 `users` 快照银行卡）、T-PAY-012（员工端工资条/发放演示） |
| 关联设计 | [`modules/05-工资.md`](../modules/05-工资.md) §3.5/§5.5、[`modules/01-基础资料.md`](../modules/01-基础资料.md) §3.1 |
| 关联规范 | `docs/04 §7.13.1`（`users` DDL）、`docs/07 §5`（敏感数据）、`docs/05 §3` |
| 关联 ADR | — |
| 估算 | 0.5d |

## 背景（为什么有这张卡）

T-PAY-006 实现工资单生成时发现：`05 §3.5`/`04 §7.6.5` 要求把
`id_card`/`bank_name`/`bank_account` **从 `users` 快照**到 `payroll_details`，而
`users`（迁移 0002 / `04 §7.13.1` / `app/modules/auth/models.py::User`）**没有这三列**
（见 `docs/12` L-128）。结果快照恒为 `None`，**T-PAY-008 的发放**（要求 `bank_account`
非空否则 `10002`）与**代发 CSV** 必然失败。

用户 2026-10-18 拍板：**采用方案 ①** —— 给 `users` 补三列（迁移 + 回写 `01 §3.1`/
`04 §7.13.1`），作为 T-PAY-008 的前置。

## 目标

给 `users` 增 `id_card` / `bank_name` / `bank_account`（可空），并在**现有用户管理接口**
（`system:user:manage`）里可维护，使工资发放能取到非空银行卡资料。

## 范围

**要做**：
- [x] 迁移 **0022**：`ALTER TABLE users ADD COLUMN id_card varchar(32)`、`bank_name varchar(128)`、`bank_account varchar(64)`（均 `NULL`；不做唯一/非空）
- [x] `app/modules/auth/models.py::User` 补三个 `Mapped[str | None]`
- [x] `docs/04 §7.13.1` 的 `CREATE TABLE users` 补三列 + 注释（代发快照来源）
- [x] `docs/modules/01 §3.1` 的 `users` 字段表补三行
- [x] `system/schemas.py`：`UserCreate`/`UserPatch` 增三个**可选**入参；`UserOut` 增三个输出字段（⚠️ PII：仅 `system:user:manage` 可见；`id_card`/`bank_account` 属敏感，`docs/07 §5`）
- [x] `system/service/user_write_mixin.py`（create/update）+ 查询侧：落库与回显三字段
- [x] 测试：`tests/modules/test_system_user_bank.py`（新建/更新落库、`UserOut` 回显、PATCH 局部更新、越权不可见/不可写）；补一条 **T-PAY 侧快照回归**：`users` 有卡后 `PayrollSheetService.create` 的快照非空

**不做**：
- 不做银行卡的独立加密/脱敏存储（阶段一按普通字段落库；如需加密另立卡）
- 不做员工自助维护银行卡（M1 员工端；`05 §1.2` 边界）
- 不改 `phone`/`password_hash` 的既有保密口径

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `backend/alembic/versions/0022_user_bank_fields.py` | 新增 | 三列 |
| `backend/app/modules/auth/models.py` | 修改 | `User` 三字段 |
| `backend/app/modules/system/schemas.py` | 修改 | `UserCreate`/`UserPatch`/`UserOut` |
| `backend/app/modules/system/service/user_write_mixin.py` | 修改 | create/update 落库 |
| `backend/app/modules/system/service/user_query_mixin.py` | 修改 | 出参映射 |
| `backend/tests/modules/test_system_user_bank.py` | 新增 | 维护/回显/权限 + **快照非空回归 TC-BANK-01~04**（原计划的 `test_payroll_sheet.py` 回归改落此处，避免与既有同行用例重复） |
| `backend/tests/modules/test_payroll_sheet.py` | **未改** | 快照回归落在新建的 `test_system_user_bank.py`（TC-BANK-04） |
| `docs/04-数据库规范.md` / `docs/modules/01-基础资料.md` | 修改 | DDL/字段表回写 |

## 实现要点

- [x] 迁移可回滚（`downgrade` 删三列）；无数据迁移（存量行三列 `NULL`）
- [x] 三列**可空**：不发工资的人（财务/仓管）无需银行卡
- [x] `id_card` 长度 32、`bank_account` 64、`bank_name` 128（与 `04 §7.6.5` 快照列一致）
- [x] `UserOut` 增字段后，前端系统管理页用户表单可维护（本卡只做后端；前端表单字段归 T-PAY-011 或 system 页卡，按需）
- [x] 守住单文件 ≤400、OpenAPI 契约重生成与源码同提交

## 验收标准

- [x] 迁移 0022 往返 + `alembic check` 零漂移
- [x] `test_system_user_bank.py` 全过；`UserOut` 含三字段
- [x] **T-PAY-008 前置验证**：`users` 有 `bank_account` 后，T-PAY-006 的 `_bank_snapshot` 能取到非空值（回归用例）
- [x] 契约重生成（`openapi.json`/`schema.d.ts`）与源码同提交、重跑无 diff
- [x] 闸门 1-5 全绿

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-BANK-01 | 新建用户带银行卡三字段 | 落库且 `UserOut` 回显 | ✅ passed |
| TC-BANK-02 | PATCH 局部更新（只改 `bank_account`） | 其余字段不动 | ✅ passed |
| TC-BANK-03 | 无 `system:user:manage` 访问 | `12001` | ✅ passed（单建唯一 code 角色，避免 T-PIECE-003 假绿） |
| TC-BANK-04 | 工资单快照回归（`users` 有卡） | 快照非空 | ✅ passed（`id_card`/`bank_name`/`bank_account` 三列均非空） |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `backend/alembic/versions/0022_user_bank_fields.py` | +58 / -0（新） | `users` 加三列（可空）；`down_revision="0021"`；`downgrade` 删列 |
| `backend/app/modules/auth/models.py` | +4 / -1（最终 400 行） | `User` 补三 `Mapped[str \| None]`（列注释与迁移逐字一致） |
| `backend/app/modules/system/schemas.py` | +14 / -0 | `UserCreate`/`UserPatch` 各增三可选入参；`UserOut` 增三出参 |
| `backend/app/modules/system/service/user_write_mixin.py` | +16 / -1 | create 落库；patch 白名单加三字段；**修复 `patch_user` 既有 bug**（见下） |
| `backend/app/modules/system/service/user_query_mixin.py` | +5 / -0 | `_out` 映射三字段 |
| `backend/tests/modules/test_system_user_bank.py` | +209 / -0（新） | TC-BANK-01~04 |
| `docs/04-数据库规范.md` | +11 / -0 | §7.13.1 `users` DDL 补三列 + 代发快照注释 |
| `docs/modules/01-基础资料.md` | +6 / -0 | §3.1 字段表补三行 + 说明 |
| `backend/openapi.json`（生成物） | +108 / -0 | 重生成 |
| `frontend/packages/shared/src/api/schema.d.ts`（生成物） | +27 / -0 | 重生成 |

> ⚠️ **修复的既有 bug**：`patch_user` 原把日志 diff `changes = {字段: {"from":..,"to":..}}` 直接
> `update(User).values(**changes)` —— asyncpg 报 `invalid input ... (expected str, got dict)`，
> 即**用户 PATCH 成功路径从未工作过**（此前仅有用例覆盖 version 冲突 / extra=forbid，未跑成功路径）。
> 本卡拆出 `values`（目标值，供 UPDATE）与 `changes`（from/to，供 `document_logs`），TC-BANK-02 首次覆盖并变绿。

**提交记录**：
- 未提交（按任务指令「不 commit」）。建议提交信息：`feat(base): users 补身份证与银行卡三列（T-BASE-012）`

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| 银行卡/身份证是否需要加密/脱敏（PII） | docs/07 §5（另立卡） |
| 员工自助维护银行卡（员工端） | modules/05 §1.2 边界 |
| 用户 PATCH 成功路径此前长期无测试（`patch_user` 的 `.values(diff-dict)` bug 直到本卡才被覆盖） | 本卡已修复 + TC-BANK-02 覆盖；已在 docs/12 变更记录留痕 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。

## 变更记录

| 日期 | 变更内容 | 操作人 |
| --- | --- | --- |
| 2026-10-18 | 初版：L-128 闭环（用户拍板方案①），T-PAY-008 前置 | AI |
| 2026-10-18 | done：迁移 0022 + 模型/接口/测试/契约/文档；修复 `patch_user` 既有 bug；闸门 1-5 全绿 | AI |
