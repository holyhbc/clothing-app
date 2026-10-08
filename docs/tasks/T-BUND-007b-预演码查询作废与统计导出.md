# T-BUND-007b：拆分预演 / 码查询 / 单码作废 / 统计导出

| 项 | 内容 |
| --- | --- |
| 模块 | bundling |
| 负责人 | backend-dev |
| 状态 | `done`（2026-10-08） |
| 优先级 | P1 |
| 依赖 | T-BUND-006（标签 service 已就绪）、T-BUND-005b（码已生成）、T-BUND-007a（router 骨架与 `main.py` 挂载） |
| 被依赖 | T-BUND-008、T-BUND-009、T-BUND-010 |
| 关联设计 | [`modules/03-打菲.md`](../modules/03-打菲.md) §6、§10 权限矩阵、§5.2 码规则 |
| 估算 | 0.5d |

## 目标

补齐 T-BUND-007a 留下的 service 缺口，并暴露**辅助能力**端点：拆分预演、标签两接口、码查询、
单码作废、统计、导出。

## 范围

**要做**：

- [x] **补 service 方法**（本卡的主体 —— `03 §6` 列了这些端点但 service 侧还没有）：
  - [x] `list_bundles(...)` / `get_bundle(bundle_no)`：码列表与单码详情
        （出参照 `03 §6` 那一行的字段清单：`hands` / `hands_total_of_size` /
        `cutting_size_line_id` + 所属缸号匹号回查 / `counted_qty` / `counted_by_name` / `counted_at`）
  - [x] `void_code(bundle_no, reason)`：**已计件 → `32003`**；码置 `VOIDED` + `voided_at` +
        `void_reason`，**行保留不删**（B12，不可恢复）
  - [x] `statistics(...)`：按 `03` 的统计口径（先核对 `03 §6` 那行，别自行发明维度）
  - [x] `export_orders(...)`：导出（`bundling:export` 权限点已存在）
- [x] `backend/app/modules/bundling/router/aux_router.py` 增补 **8** 个端点（卡面写 7，但列举了 8 条路径；按列举实现）：
      `split`（`bundling:read`）、`labels` / `label-prints`（`bundling:print`）、
      `GET /bundles` / `GET /bundles/{bundle_no}`（`bundling:read`）、
      `POST /bundles/{bundle_no}/voids`（`bundling:code:void`）、
      `statistics`（`bundling:read`）、`exports`（`bundling:export`）
- [x] `tests/modules/test_bundling_router2.py`（命名沿用仓库既有 `-2` 惯例，勿与 007a 撞名）

**不做**：
- 统计/导出的**前端**（→ T-BUND-009/010）
- 打印模板与浏览器打印（→ T-BUND-010）
- L-097 的 `super_admin` + `force=true`（单开卡，勿塞进本卡）

## 实现要点（必读规范）

- [x] `docs/05 §3`：统一响应包装；**数量金额一律字符串**（`numeric(14,3)` 出参即 `"60.000"`）；CSV 用 `text/csv; charset=utf-8-sig`
- [x] `docs/05 §5`：`Idempotency-Key` 用于 **voids / label-prints**（同键同 body 回首次结果）
- [x] 码的数据范围：`bundles` 无 `workshop_id`。**读了实现：`apply_data_scope` 只认本表列**，
      于是扩展了 `core/scope.py`（新增 `ScopeVia` / `ScopeSpec.via`，由 scope 层自己 join）而不是手写 where；
      详情 / 作废走「父单 `assert_in_scope`」，守卫见 `tests/modules/test_scope_via.py`
- [x] **路由顺序陷阱**：`/statistics`、`/exports` 必须声明在 `/{order_id}` **之前**（T-CUT-001c-1 教训）
      —— `aux_router` 整体 include 在 `order_router` 之前，守卫只比**一段**的路径
- [x] 单码作废与整单 `reverse` 是**两件事**：单码作废只 VOIDED 一个码，**一行 `cutting_outputs` 都不碰**；
      `reverse` 动整单结转（TC-BX-07 断言作废前后 `bundled_qty` 相等）
- [ ] `04 §7.16` 的 `print_seq` 无唯一索引 —— **本卡不开迁移**：正确的键是
      `(doc_id, bundle_no, print_seq)`（一次批量登记多手共用一个 `print_seq`，按单列建唯一索引会直接把
      批量登记挡掉），属规范决策 + 需先确认存量无重复，见 **L-105 ⑤**

## 验收标准

- [x] `uv run pytest -q tests/modules/test_bundling_router2.py` 全通过（11 passed）
- [x] `statistics` / `exports` 的统计维度与 `03 §6` 一致（先核对文档再写；5 处口径差登记 L-105）
- [x] 单码作废：码 `VOIDED` 行保留；已计件 → `32003`；结转**不变**（TC-BX-07 断言 `bundled_qty` 前后相等）
- [x] 越权查码 → `12002`（经 `doc_id` 回查车间后拦截；列表侧则返回空列表）
- [x] 重复 `Idempotency-Key` 的 voids 只生效一次（`VOID_CODE` 日志恰 1 条）
- [x] **重跑 `pnpm generate:api` 两次无 diff**（md5 比对通过）；生成物与源码**同提交**
- [x] 单文件 ≤400 行（最大 `code_repository.py` 385、`test_bundling_router2.py` 387）；闸门 1-4 通过

## 实际改动（完成后回填）

**手写 24 个文件，+2991 / −500 行；生成物 2 个文件，+5048 / −1404 行（AGENTS §7.1 第 2 条：超 1200 软上限，写明构成与拆分理由）**

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `bundling/schemas/{__init__,order_schemas,code_schemas,stat_schemas}.py` | +805（替代 `schemas.py` 398） | **按 ADR-0031 拆包，闭环 L-102**；`__init__` 重导出，`from ...schemas import X` 零改动 |
| `bundling/code_repository.py` | +385 | 码列表 / 详情 / 锁码 / 条件 UPDATE / 统计聚合 |
| `bundling/service/code.py` | +285 | `list_bundles` / `get_bundle` / `void_code`（单码作废） |
| `bundling/service/stat.py` | +227 | `statistics` / `export_orders` + CSV 纯函数 |
| `bundling/router/aux_router.py` | +378 | 八个端点（`split`/标签两接口/码三端点/`statistics`/`exports`） |
| `core/scope.py` | +63 / −5 | **新增 `ScopeVia` + `ScopeSpec.via`**（`bundles` 的车间经父单回查） |
| `bundling/service/approve_assert.py` | +62 / −9 | `32003` 判定收敛成唯一一处 `_assert_hands_not_counted` |
| `bundling/service/preview.py` | +64 / −16 | `preview_split` 支持可选 body（`03 §5.5` 的试算路径） |
| `bundling/label_repository.py` | +62 / −1 | 接收 `hand_total_by_size` / `list_print_records` / `PrintRecordRow` |
| `core/errors.py` + `tests/test_errors_registry.py` | +21 / −2 | 登记 `32003`；32 段进 `IMPLEMENTED_SEGMENTS`，其余五码逐条 `DEFERRED` |
| `common/enums.py` | +7 | `DocumentAction.VOID_CODE`（08 §2.2 的「作废码」行） |
| `bundling/router/{__init__,deps}.py` | +37 / −10 | `aux_router` 排第一（路由顺序）+ `_csv_response`（BOM） |
| `bundling/service/{__init__,bundling_order_service}.py` | +27 / −8 | 组装 `CodeMixin` / `StatMixin` |
| `tests/modules/test_bundling_router2.py` | +387 | 11 条接口层用例（TC-BX-01~10） |
| `tests/modules/test_scope_via.py` | +73 | `via` 通路守卫（主 `test_scope.py` 已 370 行，不塞） |
| `tests/modules/test_bundling_router.py` | +33 / −56 | 契约守卫改成「逐个查 13 个」；未实现清单只剩 `/hands`；路由顺序守卫挪到 007b |
| `tests/factories/bundling_{http,approve}.py` | +75 / −1 | `approved_order` / `mark_code_counted` |
| `backend/openapi.json`、`frontend/…/schema.d.ts` | **+5048 / −1404（生成物，不计数）** | `cd frontend && pnpm generate:api`（连跑两次无 diff） |

**拆分理由**：ADR-0030 的 400 行硬线（`schemas.py` 已 398、`test_scope.py` 已 370）+ 八个端点各自要带契约级 docstring。

**提交记录**：
- `9f5393c` feat(bundling): 码查询/单码作废/统计导出与辅助端点（T-BUND-007b）
- `<本卡 docs 提交>` docs(bundling): 回填 T-BUND-007b 提交 hash（沿用 T-BUND-007a 的两次提交惯例）

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| L-097 | `super_admin` + `force=true` 未实现 | 待排期（仍未做） |
| L-096 | 计件流水（`piecework_logs`）属 P2：「未红冲流水」那条判据在 P2 前不可实现。**部分闭环**：`32003` 已登记且 `counted_at` 那条判据（`03 §7` / TC-10）现在就能拦 | L-096（已更新） |
| L-102 | `schemas.py` 398 行 → 拆包 | ✅ 已闭环 |
| **L-104** | **`GET /hands` 没有任何卡片认领**：007b 卡面只列 8 个端点不含它，但 `03 §11` TC-30 与 T-BUND-008 必测依赖它 | L-104（新建） |
| **L-105** | **`03 §6` 五处口径差**：`statistics` 路径（顶层 vs `{id}`）、`exports` 权限点（`bundling:export`）、统计只数 ACTIVE 码、`hands_total_of_size` 不看 status、`print_seq` 唯一索引的正确键 | L-105（新建） |

## 本卡实际口径（实现取舍，供 review 核对）

| 项 | 口径 | 依据 |
| --- | --- | --- |
| `GET /statistics` | **顶层静态段**（`/bundling-orders/statistics`），聚合款号 × 尺码，只数 `ACTIVE` 码，日期落单据 | `03 §6` 的维度原话 + 路由顺序规则（`03 §6` 的 `{id}` 写法见 L-105 ①） |
| `GET /exports` | 打菲单 CSV，`bundling:export`，复用列表 service 翻页取完，超 1 万行 `11011` | `03 §6` + `05 §9.1` + `07 §3.2` 铁律 3 |
| 单码作废 | 只 VOIDED 一个码 + 写 `VOID_CODE` 日志；**不动结转、不改单据状态** | `08 §2.2`「作废码」行 + B12 |
| `32003` 判据 | `counted_at`（现在）+ 未红冲流水（P2） | `03 §7` + TC-08 / TC-10 |
| 「共 M 手」 | `max(hands)`，**不看 status** | 手序号唯一键不看状态（见 L-105 ④） |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。

## 变更记录

| 日期 | 变更内容 | 操作人 |
| --- | --- | --- |
| 2026-10-07 | 初版：接 007a 拆出的辅助能力端点 + 补 service 缺口 | AI |
| 2026-10-08 | 实现完成：8 端点 + service 缺口 + `schemas.py` 拆包（L-102）+ `ScopeVia`；新增遗留 L-104 / L-105 / L-106，更新 L-096 | AI |
