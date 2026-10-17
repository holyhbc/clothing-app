# T-PAY-009：权限 / 33 段错误码 / `SCOPE_SPECS` + 管理端 router

| 项 | 内容 |
| --- | --- |
| 模块 | payroll |
| 负责人 | backend-dev |
| 状态 | `todo` |
| 优先级 | P2 |
| 依赖 | T-PAY-004~008 |
| 被依赖 | T-PAY-010、T-PAY-011 |
| 关联设计 | [`modules/05-工资.md`](../modules/05-工资.md) §6.1、§6.1.1、§9、§10 |
| 关联规范 | `docs/05 §3~§6`、`docs/07 §2.2/§3`、`docs/04 §3` |
| 关联 ADR | ADR-0008 |
| 估算 | 0.5d |

## 目标

把工资 service 接到 HTTP：管理端全部端点 + 权限声明 + 数据范围，并落地 33 段错误码、
按 `05 §10` 下发 `RoleSeed` 工资权限点。

## 范围

**要做**：
- [ ] 管理端 router（`05 §6.1` / `§6.1.1`）：结算单、工资单、周期全套端点，OpenAPI tag=`工资`
- [ ] 每个端点 `require_permission(...)` + `apply_data_scope` + 统一响应包装 + 错误码声明
- [ ] **错误码**：`ErrorCode` 整段补 `33001~33004`；把 `"33"` 加进 `IMPLEMENTED_SEGMENTS`（守卫要求整段补齐）
- [ ] **`60001`/`60002` 不登记**（归 M9，"60" 段保持未实现，见 Q-PL09）
- [ ] **`RoleSeed` 按 `05 §10` 下发**（R4）：`workshop_supervisor` 补 `payroll:read/settle/approve/reverse`；
      `line_leader` 补 `payroll:read`；`accountant` 补 `payroll:read/pay/export`；
      `factory_manager` 补 `payroll:period:manage/pay/reverse/export`
- [ ] `app/core/scope.py`：`SCOPE_SPECS` 登记新表（结算单/工资单 `WORKSHOP`；`payroll_periods` 全厂不过滤）
- [ ] 前端契约重生成（`openapi.json` / `schema.d.ts` / `permissions.ts`）与源码**同提交**

**不做**：
- 不做员工端 `/self/payroll/**`（T-PAY-012）
- 不新增权限点（`07 §2.2` 已登记齐全）

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `backend/app/modules/payroll/router.py` | 新增 | 管理端端点 |
| `backend/app/modules/payroll/schemas.py` | 新增 | 出入参 |
| `backend/app/core/errors.py` | 修改 | 33 段 |
| `backend/app/common/permissions_registry.py` | 修改 | `RoleSeed` 下发 |
| `backend/app/core/scope.py` | 修改 | `SCOPE_SPECS` |
| `backend/tests/modules/test_payroll_router.py` | 新增 | 权限/范围 |
| `backend/tests/test_errors_registry.py` | 修改 | `IMPLEMENTED_SEGMENTS` |

## 实现要点

- [ ] 幂等：`POST /settlements`、`POST /sheets`、`POST /payroll-periods`（`05 §6.3`）
- [ ] 列表/详情/导出共用 service 方法（`07 §3.2`）
- [ ] `GET /payroll/reconciliation` 数据范围 `FACTORY`，含「周期内未结算流水条数应归零」
- [ ] 路由顺序：`/payroll-periods/generate` 等静态段须排在 `/{id}` 之前

## 验收标准

- [ ] TC-14 / TC-15 落成（权限 5 条）
- [ ] `test_errors_registry.py` 全绿（33 段整段实现）
- [ ] 权限 403 / 数据范围 403/空列表
- [ ] 契约重生成后重跑无 diff
- [ ] 闸门 1-5 全绿

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-14 | 权限矩阵 5 条 | 401/403 `12001`/403 `12002`/403/200 | |
| TC-15 | 保密与导出权限 | 403/只返本人/403 `12001` | |
| TC-PAY-R01 | 33 段错误码全部可抛 | 通过 | |
| TC-PAY-R02 | `RoleSeed` 与 `05 §10` 一致 | 逐角色断言 | |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| | +0 / -0 | |

**提交记录**：
- `<hash>` feat(payroll): 权限/错误码/数据范围 + 管理端 router（T-PAY-009）

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| L-024 角色矩阵仍未收敛到 `07 §2.4` | docs/12 L-024 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。

## 变更记录

| 日期 | 变更内容 | 操作人 |
| --- | --- | --- |
| 2026-10-17 | 初版 | AI |
