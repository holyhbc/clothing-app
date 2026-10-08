# T-BUND-007a：打菲单 router（生命周期 + 状态动作）

| 项 | 内容 |
| --- | --- |
| 模块 | bundling |
| 负责人 | backend-dev |
| 状态 | `done`（2026-10-08） |
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

- [x] `backend/app/modules/bundling/router.py` —— 13 端点超 400 行，**已按 ADR-0031 拆包**：
      `router/{__init__,deps,order_router,action_router}.py`（层序 `deps ← 内容 router ← __init__`，
      与 `base/router` 同款；最大文件 233 行）
- [x] 每个端点：`x-permission` 声明 + **函数体显式 `_require`**（`03 §2.1` 第 8 条：前端隐藏不是安全）
- [x] `main.py` 挂载
- [x] `docs/07 §2.2` 已有 12 个 `bundling:*` 权限点，**本卡不新增**
      ⚠️ **`withdraw` 用了 `bundling:withdraw`（07 §2.2 已登记）**，而 `08 §1.1` 与 `03 §6` 写的是
      `bundling:update` —— **L-092 ② 在本卡定案**（取权限点总表口径，否则它是永不生效的死点），
      规范侧待同步见 `docs/12` L-103
- [x] **补 `ReverseIn` schema**（闭环 `docs/12` L-098）：必填校验落 schema 层 → 缺失 `10001`（422）、
      全空白由 service `require_reason` 报 `10002`，两层分工见 `05 §3`
- [x] `tests/modules/test_bundling_router.py` + `test_bundling_router_actions.py`
      （28 个用例；拆两个文件是 ADR-0030 的 400 行硬线，共用助手放 `tests/factories/bundling_http.py`）

**不做**（T-BUND-007b）：`split`、`labels`、`label-prints`、`bundles` 三个码端点、`statistics`、`exports`。

## service 缺口（**未实现 → 路由不注册，不返回 501**）

`03 §6` 列了 22 个端点，但 service 侧**没有** `statistics` / `exports` / 码查询 / `void_code`。
按卡面「未实现不注册」处理，**不要为凑契约硬写空实现**：

| 缺口 | 归属 |
| --- | --- |
| `GET /bundles`、`GET /bundles/{bundle_no}`、`POST /bundles/{bundle_no}/voids` | T-BUND-007b |
| `GET /statistics`、`GET /exports` | T-BUND-007b |

## 实现要点（必读规范）

- [x] `docs/05 §3`：统一响应包装；**数量金额一律字符串**（出参统一走 `deps._order_payload` / `Str`）
- [x] `docs/05 §5`：`Idempotency-Key` 接在 **approvals**（照 `auth/router.py`：命中取
      `cached["response"]`；⚠️ **不是** `base/router/style_child_router.py` 那种 `return cached`
      —— 那样会返回 `data=null`，已登记 `docs/12` L-101）
- [x] 数据范围：表头按 `workshop_id`，全部经 service（`list_orders` → `apply_data_scope`；
      `get` / 六个动作 / `list_logs` → `assert_in_scope`），**router 里没有一行 where**
- [x] **路由顺序陷阱**（T-CUT-001c-1 教训）：顶层静态段必须声明在 `/{order_id}` **之前** ——
      TC-BD-08 立了守卫（遍历真实路由顺序而不是 OpenAPI 字典），007b 加 `/statistics` 时会挡住错位
- [x] `approvals` 的 `description` **写明 §4.1 六步与「码数 = 手数」断言**（03 §6 明确要求），
      另加「批量 INSERT + `generate_series`，禁止逐条」与「制单人 ≠ 审核人」
- [x] 版本号语义：`PATCH` / `PUT /lines` / 六个动作的响应都带**写完之后的新 `version`**
      （TC-BD-02 / TC-BD-09 / TC-BD-13 断言）；旧版本号 → `10003`

## 验收标准

- [x] `uv run pytest -q tests/modules/test_bundling_router*.py` → **28 passed**
- [x] 13 个端点齐（TC-BD-03 逐个比对 OpenAPI 里的方法 + 路径）；未实现的路径**不在 OpenAPI 里**，
      HTTP 上是 **404 / 422、绝不 501**（TC-BD-04，9 个路径参数化）
- [x] 无权限 `12001`（TC-BD-05，5 个写端点）、越权数据范围 `12002`（TC-BD-06，详情与日志）
- [x] `ReverseIn` 必填校验在 **schema 层**：缺 `reason` → **422 `10001`**（`details.fields[0].field`
      指向 `reason`）、全空白 → `10002`，两者都**不落库**（无动作日志、状态不变）—— TC-BD-12
      ⚠️ 卡面原写「缺失 → `10002`」：那要求字段**不**必填，而 L-098 的闭环恰恰要求它必填；
      且全仓的 422 一律映射 `10001`（`base/router/dict_handlers.py` 的停用端点同款）。已列入待修订条目
- [x] `approvals` 重复 `Idempotency-Key` 只生效一次（TC-BD-11：两次响应一致 + **只一条 APPROVE 日志**
      + **只生成一手码**；同键不同 body → `10002`）
- [x] **重跑 `pnpm generate:api` 两次无 diff**（md5 校验三次一致）；生成物与源码**同提交**
- [x] 单文件 ≤400 行（最大 323）；`scripts/gate.sh --host` 闸门 1-4 全绿（929 passed，覆盖率 91.69%）

## 实际改动（完成后回填）

**手写 +1415 / −9 行**（backend +1339、docs +76；ADR-0030 的 1200 软上限**超出 215 行**，
构成与拆分理由见 `docs/12` 0119）；
**生成物 +6427 / −3210 行**（`openapi.json` +5078/−3170、`schema.d.ts` +1349/−40，**不计入**）。

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `backend/app/modules/bundling/router/__init__.py` | +32 | 装配 + include 顺序（顺序即路由匹配顺序） |
| `backend/app/modules/bundling/router/deps.py` | +69 | 共享依赖、`_require`、`_order_payload`、Tags |
| `backend/app/modules/bundling/router/order_router.py` | +233 | 7 端点：建单 / 列表 / 详情 / PATCH / PUT lines / available-outputs / logs |
| `backend/app/modules/bundling/router/action_router.py` | +225 | 6 端点：submit / approve（含幂等键）/ reject / withdraw / reverse / cancel |
| `backend/app/modules/bundling/schemas.py` | +45 | `ApproveIn`、`ReverseIn`（L-098 闭环）、`AvailableOutputOut`（现 398 行，见 L-102） |
| `backend/app/modules/bundling/service/bundling_order_service.py` | +40/−3 | 新增 `list_logs`；**顺带修 `put_lines` 两个缺陷**（汇总未落库 / 明细未 flush） |
| `backend/app/modules/bundling/service/common.py` | +7/−1 | `DOC_TYPE_BUNDLING_ORDER` 常量（写日志与读日志同源） |
| `backend/app/modules/base/document_logs.py` | +48 | 新增 `list_document_logs_by_doc`（按 `doc_id` 查，范围由调用方单据强制） |
| `backend/app/main.py` | +2 | 挂载 `bundling_router` |
| `backend/tests/factories/bundling_http.py` | +83 | 两个测试文件共用的 HTTP 助手（禁止互相 import 测试模块） |
| `backend/tests/modules/test_bundling_router.py` | +323 | 读 / 草稿写 / 契约 / 权限与数据范围（TC-BD-01~08） |
| `backend/tests/modules/test_bundling_router_actions.py` | +242 | 六个状态动作 / 幂等键 / 必填原因（TC-BD-09~13） |
| `backend/openapi.json`（生成物） | +5078/−3170 | `cd frontend && pnpm generate:api` |
| `frontend/packages/shared/src/api/schema.d.ts`（生成物） | +1349/−40 | 同上（`permissions.ts` / `baseDictFields.ts` 无 diff） |

**提交记录**：
- `bd11eb2` feat(bundling): 打菲单 router 与六状态动作（T-BUND-007a）

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| L-097 | `super_admin` + `force=true` 强制审核/反审核未实现（`08 §4` 有此例外） | 待排期（T-BUND-007b 或单开卡） |
| — | 码查询 / 统计 / 导出 / 单码作废 | T-BUND-007b |
| L-092 ② → **本卡定案** | `withdraw` 用 `bundling:withdraw`（07 §2.2），与 `08 §1.1` / `03 §6` 的 `bundling:update` 不一致 | `docs/12` L-103（规范侧待同步） |
| L-101 | `base/router/style_child_router.py` 幂等命中返回整个缓存包装 → `data=null`（`auth/router.py` 是对的） | `docs/12` L-101（跨模块，本卡未修） |
| L-102 | `bundling/schemas.py` 已 398 行，007b 加码 / 统计出参前必须按 ADR-0031 拆包 | `docs/12` L-102 |
| — | `put_lines` 两个缺陷（汇总未落库 / 明细未 flush）已在本卡顺带修；根因是 T-BUND-003 的 service 单测用 `session.refresh()`（会顺带 autoflush）把问题盖住 | `docs/12` 0119 已记 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。

## 变更记录

| 日期 | 变更内容 | 操作人 |
| --- | --- | --- |
| 2026-10-07 | 由 T-BUND-007 拆出：007a（单据生命周期）/ 007b（预演+标签+码+统计导出），守 ADR-0030 的 400 行与 §7.1 的 1200 行 | AI |
| 2026-10-08 | 实现 + 测试 + 归档：router 拆包（4 文件）、`ReverseIn` 闭环 L-098、幂等键接线、007b 的 9 个路径断言 404、**顺带修 `put_lines` 两个被本卡暴露的缺陷**；手写 1415 行超 §7.1 软上限 215 行（13 端点 × 契约 docstring + 28 用例 + 400 行拆包），构成见 `docs/12` 0119 | AI |
