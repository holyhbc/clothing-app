# T-PIECE-006：工位机操作员候选端点 + `piecework:count` 角色授权

| 项 | 内容 |
| --- | --- |
| 模块 | piecework（后端）+ 权限 seed |
| 负责人 | backend-dev |
| 状态 | `done` |
| 优先级 | P2（工位机扫码页 T-PIECE-007 的前置；不解决则页面写出来也用不了） |
| 依赖 | T-PIECE-001/002/003/004（计件表 + 扫码 + 只读 + 纠错，均闭环） |
| 被依赖 | T-PIECE-007（工位机扫码页） |
| 关联设计 | [`modules/04-计件.md`](../modules/04-计件.md) §5.1（链路 A 步骤 1「选定操作员」）、§6.1、§10（权限矩阵）；[`07-认证与权限规范.md`](../07-认证与权限规范.md) §3.2（数据范围铁律） |
| 关联规范 | `docs/05 §9.5.2`（候选下拉：label 必须是「工号 姓名」）、`docs/07 §2.2`（权限点） |
| 估算 | 0.5d |

## 背景：为什么单独一张卡

链路 A（`04 §5.1` 步骤 1）要求组长在工位机「选定本工位的 `workstation_no` + 默认 `operation_no` + **操作员**」，但两处缺口让这步无法落地：

1. **没有面向非管理员的「员工候选」端点**。全仓只有 `GET /system/users/options`，而它要求 `system:user:manage` —— 给组长开这个权限点等于把「系统用户管理」整个打开。
2. **`piecework:count` 没有授予任何业务角色**。`permissions_registry.py` 里它只被定义（`perm_piecework.py:13`）、未被任何 `RoleSeed` 引用；实测 `line_leader` 的权限集是 `{base:read, piecework:read, cutting:read, bundling:read}`。而 `04 §10` 权限矩阵写的是 `line_leader` / `workshop_supervisor` **工位机计件 ✅** —— 即按现码组长扫码会直接 `12001` 无权限。

两者都是**规范层面的缺口**，不是实现细节（见本卡开头的决策记录）。

## 决策（2026-10-15，业务方 / 架构确认）

- **A. 操作员候选**：**新增 scoped 端点** `GET /api/v1/piecework/operators`，权限 `piecework:read`，**走 `apply_data_scope(User)`**，只回本数据范围内的在职员工。不复用 `/system/users/options`（权限过宽）。
- **B. `piecework:count` 授权**：**按 `04 §10` 修 seed** —— 给 `factory_manager` / `workshop_supervisor` / `line_leader` / `piecework_settler` 补 `piecework:count`。这同时是 `docs/12` **L-024**（角色权限集是保守集合、待业务确认）的**部分推进**。

## 目标

1. `GET /api/v1/piecework/operators`：给工位机/补录页一个**数据范围内**的操作员下拉，`label = "工号 姓名"`（05 §9.5.2），停用员工标 `disabled`。
2. 修 `permissions_registry.py` 的 4 个角色权限集，使 `04 §10` 矩阵在代码侧成立。

## 不做什么

| 项 | 原因 |
| --- | --- |
| 工位机页面本身 | 独立卡 T-PIECE-007（后端端点本卡先用测试验收） |
| 工位号（`workstation_no`）主数据 | 无 `workstations` 表；链路 A 里它是页面配置项，由前端记忆（见 07 卡） |
| 按角色过滤操作员（只列 `employee`） | 业务未定「组长/主管能否自己计件」；本卡不发明规则，先列**范围内全部在职用户**并在 §12 登记待确认 |
| `system:user:manage` 的拆分 | 不在本卡范围；本卡只是**不依赖**它 |

## 验收标准

- [x] `GET /api/v1/piecework/operators`：`q`（≤64）/ `workshop_id`(UUID, 可选) / `size`(1..20) / `offset`
- [x] 权限：无 `piecework:read` → `12001`；函数体显式 `_require` + `x-permission` + OpenAPI `tags=["计件"]`
- [x] 数据范围：`WORKSHOP` 只回本车间；`GROUP` 只回本组；`FACTORY` 全厂；`SELF` 只回自己
- [x] 返回 `ApiResponse[list[OperatorOptionOut]]`，`value` 是**用户 UUID**、`label="工号 姓名"`、停用 `disabled=true`
- [x] seed：`factory_manager` / `workshop_supervisor` / `line_leader` / `piecework_settler` 均含 `piecework:count`
- [x] 测试：权限拒绝 / 四种数据范围 / 关键字命中工号或姓名 / 停用标记 / 端点 shape
- [x] 文档：`04 §6.1` 加端点行、`§10` 补注、`§12` 关 Q-PW17 + L-024 推进段

## 风险

| 风险 | 缓解 |
| --- | --- |
| 角色授权变更影响既有测试 | 实测「无权限→`12001`」用例用的是 `role="custom"` 且只给 `piecework:read`（`test_piecework_count_router.py:51`），不依赖内置角色；跑全量确认 |
| 端点泄露范围外员工 | `apply_data_scope` 是 service 第一行（07 §3.2 铁律 1）；`users` 已在 `SCOPE_SPECS` 登记（`scope.py:188`） |
| 新端点破坏契约 | `openapi.json` / `schema.d.ts` 同提交重生成 |

## 实施结果

**2026-10-15 完成（变更 `docs/12` 0149）。**

### 交付物

- **端点** `GET /api/v1/piecework/operators`：
  - `query_service.py::PieceworkQueryService.operators` —— 第一行 `apply_data_scope(select(User), User, self.ctx)`；可选 `workshop_id` 只再收窄；`q` 命中 `employee_no` 或 `name`（`ilike`）；`order_by(employee_no)` + `offset/limit`；模块级 `OPERATOR_MAX_OFFSET = 10000`（超出报 `10001`，候选下拉靠关键字翻页而非深翻页）。
  - `query_router.py` 声明式端点（`_require(ctx, "piecework:read", ...)` + `openapi_extra={"x-permission": "piecework:read"}` + `tags` 归属计件）。
  - `schemas.py::OperatorOptionOut`（`value: UUID` / `label` / `sub` / `disabled`）。
- **seed**：`permissions_registry.py` 给 `factory_manager` / `workshop_supervisor` / `line_leader` / `piecework_settler` 补 `piecework:count`（与 `04 §10` 矩阵一致）。
- **契约**：`pnpm run generate:api` 重生成 `openapi.json` / `schema.d.ts` / `permissions.ts`（后者的 `ROLE_PERMISSIONS` 随之更新），与源码同提交、重跑无 diff。

### 关键结论

- **无需新迁移**：迁移 `0002_auth.py` 动态 import registry，新库按新绑定建；存量库跑 `python -m app.cli.seed_baseline` 幂等补齐（实测 `权限绑定新增 4`，零迁移）。
- **两处静默失败的对称**：漏 `apply_data_scope` → 多列别人的工人（越权）；漏角色授权 → 空下拉（不可用）。都不报错，只有用例能拦。故测试同时断言「换 token 真的少人」与「内建 `line_leader` 真能计件」。
- `line_leader` 的数据范围是 `GROUP`，端点约束测试用 `GROUP`；`04 §10` 矩阵继续保留在 `docs/12` L-024（矩阵尚未收敛到 `07 §2.4`）。

### 闸门

| 闸门 | 结果 |
| --- | --- |
| 1 lint | ✅ `ruff check` + `ruff format --check` |
| 2 typecheck | ✅ `mypy app`（188 files） |
| 3 单测 | ✅ 全量 **1091 passed / 90.97%**；前端 typecheck / lint / 248 passed / build 全绿 |
| 4 迁移往返 | ✅ `alembic check`：`No new upgrade operations detected`（本卡无迁移） |
| 5 镜像构建 | ✅ `docker compose -f docker-compose.ci.yml build api-image`（runtime target） |

### 行数

手写 **392** = 实现 `app/` 106 + 测试 `tests/` 217 + 文档 69；生成物 **313**（`openapi.json` 191 + `schema.d.ts` 118 + `permissions.ts` 4）不计入。单文件最大 `test_piecework_operators.py` **215** 行，无一超 400（ADR-0030）。

