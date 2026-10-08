# T-BUND-007a：打菲单 router（生命周期 + 状态动作）

| 项 | 内容 |
| --- | --- |
| 模块 | bundling |
| 负责人 | backend-dev |
| 状态 | `todo` |
| 优先级 | P1 |
| 依赖 | T-BUND-003/004/005a/005b（service 方法已就绪） |
| 被依赖 | T-BUND-007b、T-BUND-008、T-BUND-009 |
| 关联设计 | [`modules/03-打菲.md`](../modules/03-打菲.md) §6 接口清单、§10 权限矩阵；`docs/05`、`docs/07` |
| 估算 | 0.5d |

## 目标

把**打菲单本身**的 13 个端点挂到 HTTP：查询、草稿写、六个状态动作。
T-BUND-006（标签）与 T-BUND-007b（预演/码/统计导出）不在本卡。

## 范围

**要做**（service 方法已存在，直接接 router）：

| 端点 | service 方法 | 权限点 |
| --- | --- | --- |
| `GET /bundling-orders` | `list_orders` | `bundling:read` |
| `POST /bundling-orders` | `create` | `bundling:create` |
| `GET /bundling-orders/{id}` | `get` | `bundling:read` |
| `PATCH /bundling-orders/{id}` | `patch` | `bundling:update` |
| `PUT /bundling-orders/{id}/lines` | `put_lines` | `bundling:update` |
| `GET /bundling-orders/{id}/available-outputs` | `available_outputs` | `bundling:read` |
| `GET /bundling-orders/{id}/logs` | 复用 `document_logs` 查询 | `bundling:read` |
| `POST /bundling-orders/{id}/submissions` | `submit` | `bundling:submit` |
| `POST /bundling-orders/{id}/approvals` | `approve` | `bundling:approve` |
| `POST /bundling-orders/{id}/rejections` | `reject` | `bundling:reject` |
| `POST /bundling-orders/{id}/withdrawals` | `withdraw` | `bundling:withdraw` |
| `POST /bundling-orders/{id}/reversals` | `reverse` | `bundling:reverse` |
| `POST /bundling-orders/{id}/cancellations` | `cancel` | `bundling:cancel` |

- [ ] `backend/app/modules/bundling/router.py`（**超 400 行按 ADR-0031 拆 `order_router.py` + `action_router.py`**）
- [ ] 每个端点：`x-permission` 声明 + **函数体显式 `_require`**（`03 §2.1` 第 8 条：前端隐藏不是安全）
- [ ] `main.py` 挂载
- [ ] `docs/07 §2.2` 已有 12 个 `bundling:*` 权限点，**本卡不新增**
- [ ] **补 `ReverseIn` schema**（闭环 `docs/12` L-098：`reverse` 现在收裸 `str`，必填校验落在 service 层，
      违反 `05 §3`「必填校验应在 schema 层」）
- [ ] `tests/modules/test_bundling_router.py`

**不做**（T-BUND-007b）：`split`、`labels`、`label-prints`、`bundles` 三个码端点、`statistics`、`exports`。

## service 缺口（**未实现 → 路由不注册，不返回 501**）

`03 §6` 列了 22 个端点，但 service 侧**没有** `statistics` / `exports` / 码查询 / `void_code`。
按卡面「未实现不注册」处理，**不要为凑契约硬写空实现**：

| 缺口 | 归属 |
| --- | --- |
| `GET /bundles`、`GET /bundles/{bundle_no}`、`POST /bundles/{bundle_no}/voids` | T-BUND-007b |
| `GET /statistics`、`GET /exports` | T-BUND-007b |

## 实现要点（必读规范）

- [ ] `docs/05 §3`：统一响应包装；**数量金额一律字符串**
- [ ] `docs/05 §5`：`Idempotency-Key` 用于 **approvals / reversals**（`app/core/idempotency.py` 有现成实现）
- [ ] 数据范围：表头按 `workshop_id`，用 `core/scope.py` 的 `apply_data_scope` / `assert_in_scope`（**不要自己写过滤**）
- [ ] **路由顺序陷阱**（T-CUT-001c-1 教训）：静态段必须声明在 `/{order_id}` **之前**
- [ ] `approvals` 的 `description` **必须写明 §4.1 六步与「码数=手数」断言**（`03 §6` 明确要求）
- [ ] 版本号语义：`PATCH` 与六个动作**每次写完必须把响应里的新 `version` 存下来**（T-CUT-001c-2a 教训）

## 验收标准

- [ ] `uv run pytest -q tests/modules/test_bundling_router.py` 全通过
- [ ] 13 个端点齐；未实现的路径**路由不存在**（`404`，不返回 501）
- [ ] 无权限 `12001`、越权数据范围 `12002`
- [ ] `ReverseIn` 必填校验在 **schema 层**（缺 `reason` → `10002`，且不落库）
- [ ] `approvals` 重复 `Idempotency-Key` 只生效一次
- [ ] **重跑 `pnpm generate:api` 两次无 diff**；生成物与源码**同提交**（`AGENTS §7.1` 第 1 条）
- [ ] 单文件 ≤400 行；闸门 1-4 本地预跑通过

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| 待回填 | | |

**提交记录**：
- `<hash>` feat(bundling): 打菲单 router 与六状态动作（T-BUND-007a）

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| L-097 | `super_admin` + `force=true` 强制审核/反审核未实现（`08 §4` 有此例外） | 待排期（T-BUND-007b 或单开卡） |
| — | 码查询 / 统计 / 导出 / 单码作废 | T-BUND-007b |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。

## 变更记录

| 日期 | 变更内容 | 操作人 |
| --- | --- | --- |
| 2026-10-07 | 由 T-BUND-007 拆出：007a（单据生命周期）/ 007b（预演+标签+码+统计导出），守 ADR-0030 的 400 行与 §7.1 的 1200 行 | AI |
