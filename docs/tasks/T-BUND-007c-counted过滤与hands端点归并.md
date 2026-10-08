# T-BUND-007c：补 `counted` 过滤（未计件手清单）与 `/hands` 端点归并决策

| 项 | 内容 |
| --- | --- |
| 模块 | bundling |
| 负责人 | backend-dev |
| 状态 | `done` |
| 优先级 | P1（**TC-30 / TC-33 / T-BUND-008 与 Q-B09 都卡在这一条**） |
| 依赖 | T-BUND-007b（`GET /bundles` 已就绪） |
| 被依赖 | T-BUND-008（并发与集成测试）、T-BUND-009（前端「未计件手」清单页） |
| 关联设计 | [`modules/03-打菲.md`](../modules/03-打菲.md) §6 `/hands` 行、§7 TC-30、Q-B09 |
| 估算 | 0.25d（**小卡，别把它撑大**） |

## 目标

`03 §6` 承诺的 **`GET /{id}/hands` 按手列表（含 `counted=false` 未计件手清单）至今没实现** ——
这是 ADR-0016 的**验证方式**（「未计件手数可见」），也是 Q-B09（部分生产剩余量可见）的唯一入口。
TC-30、TC-33 与 T-BUND-008 全部依赖它。

## 本卡要做的决策（先决策，再写码）

`GET /bundles`（007b 已实现）**已经支持** `doc_id` / `style_no` / `color_code` / `size_code` 过滤，
出参也已含 `hands` / `hands_total_of_size` / `bundle_qty` / `counted_qty` / `counted_at` / `status`。

**结论：`/hands` 是 `GET /bundles?doc_id={id}` 的重复定义，应归并，不新增端点。**

理由：`doc_id` 已是现有过滤项，能力完全被覆盖；而 `/hands` 多一套路径就多一份
OpenAPI 契约、多一套前端调用、一处路由顺序坑（T-CUT-001c-1 教训），
**却没有任何 `/bundles` 做不到的事**。`03 §6` 写 `/hands` 时是当独立页设计的，
现在 `GET /bundles` 已经长成了它。

**所以本卡的实质是：给 `GET /bundles` 加 `counted` 过滤，并在 `03 §6` 记明归并。**

## 范围

**要做**：

- [x] `GET /bundles` 增加 `counted` 查询参数（**三态**）：
      不传 = 全部；`false` = **未计件**（`counted_at IS NULL`，**必须命中
      `idx_bundles_counted_pending`**，见 `docs/04` 索引登记）；`true` = 已计件
- [x] 校验：`counted` 只能取 `true`/`false`，非法值 → `10001`（由 middleware 的 `RequestValidationError` → `10001` 处理器统一兜住，无需新码）
- [x] `03 §6`：把 `/hands` 行标注为**已归并进 `GET /bundles`**（划掉 + 写明 `?doc_id={id}&counted=false` 即等价原 `/hands`），
      写明「`?doc_id={id}&counted=false` 即等价原 `/hands`」，并同步 §7 TC-30 的指向
- [x] 测试（`tests/modules/test_bundling_counted.py`，TC-30-01~07）：`counted=false` 只回未计件手且走对索引；`counted=true` 只回已计件；
      非法值 `10001`；作废码（`status=VOIDED`）默认**要**排除还是包含？
      —— **口径：默认排除 `VOIDED`**（作废码不是「还没计件的手」），单独用 `status` 参数取
- [x] `pnpm generate:api`（**跑两次、零 diff** —— 见下方「生成物」一节）

**不做**：
- Q-B09 的 ②「剩余手清单」独立报表、③ 到期提醒、④ 追加新手自动结转 ——
  **未获业务确认，不实现**（`03 §12` Q-B09 ⏳）
- `bundles.partial_at` 字段 —— 同上，未确认

> ✅ 三条「不做」项均**未实现**：`03 §12` Q-B09 仍为 ⏳，本卡只把该行里的 `/hands` 路径引用改成 `GET /bundles`（纯指向更新，未加任何 `partial` 过滤值 / 报表 / 提醒）。

## 实现要点

- [x] 过滤走 `apply_data_scope` 的 `via` 通路（ADR-0032），`counted` 条件与之**并列 AND**（TC-30-06）
- [x] 索引 `idx_bundles_counted_pending` 已在 0014 建成；`EXPLAIN` 已确认 `counted=false` 走它（**结果见下方「EXPLAIN 证据」一节**）
- [x] 出参数量一律字符串（`05 §3`，实测 `bundle_qty` 出参为 `"60.000"`）；`hands` / `counted_qty` 的语义别混（`hands` 是手号不是件数）

## 验收标准

- [x] `uv run pytest -q tests/modules/test_bundling_counted.py` 全通过（7 passed）
- [x] `counted=false` 返回**全部未计件手**（含部分生产的，`counted_at` 非空但
      `counted_qty < bundle_qty` 的**也要算已计件**——按 `counted_at` 判，不按 `counted_qty`）
- [x] **`EXPLAIN` 证据入卡**（TC-30 明写「命中 `idx_bundles_counted_pending`」）→ 见下方「EXPLAIN 证据」
- [x] `03 §6` 已标注 `/hands` 归并、`03 §7` TC-30 指向已更新（另同步 `§6` 说明第 ⑤ 条、`§7` 查询→索引表、TC-33、Q-B09 的路径引用）
- [x] 单文件 ≤400 行（`code_repository.py` 加完口径曾达 401 行 → 按 ADR-0031 把作废写路径拆到 `code_void_repository.py`，现 339 / 92；`code_schemas.py` 本卡未动，仍 352）；闸门 1-5 通过

## 实际改动（完成后回填）

**手写 +362 / −98 行；生成物 0 行（`pnpm generate:api` 跑两次零 diff）。**

| 文件 | +/− | 行数 | 说明 |
| --- | --- | --- | --- |
| `backend/app/modules/bundling/code_repository.py` | +25 / −71 | 339 | `BundleListQuery.filters()`：**默认排除 `status=VOIDED`**（不传 `status` 时补 `status='ACTIVE'`）+ `counted` 谓词的判据注释；**按 ADR-0031 把作废写路径拆出去**（−71 行是搬运，不是删逻辑） |
| `backend/app/modules/bundling/code_void_repository.py` | 新增 92 | 92 | 从上一文件**纯搬运**：`VoidTargetRow` / `lock_bundle_for_void` / `mark_bundle_voided`。⚠️ 加完口径后 `code_repository.py` 曾达 **401 行**，越 ADR-0030 的 400 行硬线 |
| `backend/app/modules/bundling/service/code.py` | +1 / −2 | 285 | 两个 import 改指新文件（零逻辑变化） |
| `backend/tests/modules/test_bundling_counted.py` | 新增 215 | 215 | TC-30-01~07（见下表） |
| `docs/modules/03-打菲.md` | +10 / −9 | — | §6 `/hands` 行划掉 + 标注归并、§6 说明第 ⑤ 条（三态 + 默认排除 + 索引前提）、§6 `GET /bundles` 行加厚、§7 查询→索引表的 ★ 行、§7 TC-30 / TC-33、§12 Q-B09 的路径引用 |
| `docs/12-文档与变更归档规范.md` | +3 / −1 | — | §2 新增 **0122**；§5 L-104 标记闭环、**L-107 新增**（枚举型查询参数非法值 → 500 而非 `10001`，007b 引入、本卡撞见未改） |
| 本卡 | +16 / −15 | — | 状态 / 勾选 / 实际改动表 / 本节 |
| `backend/openapi.json`、`frontend/packages/shared/src/**` | **0 / 0** | — | **生成物零 diff**：`counted` 查询参数 007b 已在契约里，本卡没加端点也没加参数 —— 契约不变本身就是归并决策的收益（AGENTS §7.1 第 1 条：生成物与源码同提交，此处即「无需改动」） |

⚠️ `code_schemas.py` **本卡未动**（仍 352 行）：`counted` 是**查询参数**不是响应模型字段，出参零变化，故不碰那个已贴 400 行线的文件。

## EXPLAIN 证据（TC-30 明写「命中 `idx_bundles_counted_pending`」）

**数据分布（一次事务内灌、末尾 ROLLBACK，测试库零残留）**：`bundling_orders` 4000 张（各 100 手 × 60 件）
+ `bundles` **40 万行**，其中 25% 已计件（`counted_qty=28 < 60`，即**部分生产**）、5% `VOIDED`，`ANALYZE` 后取
**单据级**查询（`doc_id = ?` + `size_code='XL'` + `workshop_id` + `counted_at IS NULL` + `status='ACTIVE'`，
即 `_scoped_stmt()` + `BundleListQuery(counted=False).filters()` 渲染出的生产 SQL）：

```
########## 列表（counted=false，ORDER BY bundle_no LIMIT 20） ##########
Limit  (cost=286.60..286.65 rows=20) (actual time=0.261..0.264 rows=20 loops=1)
  Buffers: shared hit=81
  ->  Sort  (Sort Key: bundles.bundle_no, Sort Method: top-N heapsort)
        ->  Nested Loop Left Join  (actual rows=75)
              ->  Index Scan using pk_bundling_orders on bundling_orders  (rows=1)
                    Index Cond: (id = '<单号>')  Filter: (deleted_at IS NULL AND workshop_id = ...)
              ->  Bitmap Heap Scan on bundles  (actual rows=75)  Heap Blocks: exact=75
                    Recheck Cond: ((doc_id = '<单号>') AND (size_code = 'XL') AND (deleted_at IS NULL)
                                   AND (status = 'ACTIVE') AND (counted_at IS NULL))
                    ->  Bitmap Index Scan on idx_bundles_counted_pending  (cost=0.00..5.13 rows=71)
                          Index Cond: ((doc_id = '<单号>') AND (size_code = 'XL'))
Execution Time: 0.352 ms

########## 计数（count_bundles 的子查询包 count） ##########
Aggregate  (actual time=0.144..0.145 rows=1)
  ->  Nested Loop  (actual rows=75)
        ->  Index Scan using pk_bundling_orders ...
        ->  Bitmap Heap Scan on bundles  (Heap Blocks: exact=75)
              ->  Bitmap Index Scan on idx_bundles_counted_pending  (cost=0.00..5.13 rows=71)
Execution Time: 0.206 ms
```

**结论**：两条都命中 **`idx_bundles_counted_pending`**，且 **`bundles` 侧没有 Seq Scan**（40 万行只碰 81 个
buffer / 75 个 heap block）。

⚠️ **改前的对照（这条是「默认排除 VOIDED」不能省的证据）**：同一份数据、同一段 SQL，只把
`AND status = 'ACTIVE'` 去掉，规划器就退化成

```
->  Index Scan using uq_bundles_no on bundles
      Filter: ((deleted_at IS NULL) AND (counted_at IS NULL) AND (doc_id = ...) AND (size_code = 'XL'))
```

—— `counted_at IS NULL` 从 **Index Cond 掉回 Filter**，`idx_bundles_counted_pending` **完全不出现在计划里**。
原因即 04 §7.0 那个部分索引的 `indexpred` 是 `deleted_at IS NULL AND status = 'ACTIVE' AND counted_at IS NULL`
**三条**，`deleted_at IS NULL` 由 `apply_data_scope` 自动加（INV-7），`status = 'ACTIVE'` **只能靠列表的默认排除**
——两条都到位，规划器才认得出这个部分索引可用。这也是 TC-30-07 那条测试用 `SET LOCAL enable_seqscan = off`
的原因：**它验的是「谓词可证」，而代价估算由上面这份 40 万行的计划作证**（小表上规划器本来就会选顺序扫描）。

⚠️ 取证脚本是 `/tmp` 下的一次性脚本，**未入库**：它要用 `session_replication_role = replica` 关掉外键触发器
才能不搭「裁剪→打菲」整条祖孙链就灌数，而那需要超级用户（`erp_ddl` 没有该权限）。**常驻的护栏是 TC-30-07**
（每次 CI 都跑，索引将来被谁改坏即刻红）；上面这份计划是一次性取证。

## 测试（`tests/modules/test_bundling_counted.py`）

| 用例 | 场景 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-30-01 | `counted=false` | 只回未计件手，每行含 `hands`/`hands_total_of_size`(=4)/`bundle_qty`(=60) | ✅ |
| TC-30-02 | `counted=true` / 不传 | 只回已计件手 / 两者 + 未计件 | ✅ |
| TC-30-03 | `counted=abc` | `10001`（HTTP 422） | ✅ |
| TC-30-04 | 部分生产（`counted_at` 非空、`counted_qty=1<60`） | **不算未计件**（`counted=false` 不含它） | ✅ |
| TC-30-05 | 作废手 | 默认**排除**；`status=VOIDED` 单独取得到 | ✅ |
| TC-30-06 | 越权车间 | 列表空（经 `via` 回查）、详情 `12002` | ✅ |
| TC-30-07 | `EXPLAIN`（`enable_seqscan=off`） | 计划含 `idx_bundles_counted_pending` | ✅ |

⚠️ **先红后绿**：写完测试、未改代码时 **5 failed / 2 passed** —— TC-30-01/02/04/05 报的是
`assert {3, 4} == {3}`（作废的第 4 手混进了 `counted=false`），TC-30-07 报的是计划里根本没有那个索引名。
补上默认排除后全绿。

**提交记录**：
- `1598e31` feat(bundling): bundles 增加 counted 过滤 + /hands 端点归并（T-BUND-007c）

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。

## 变更记录

| 日期 | 变更内容 | 操作人 |
| --- | --- | --- |
| 2026-10-08 | 初版：补 `counted` 过滤（TC-30 的实质缺口）+ `/hands` 归并决策，闭环 L-104 | AI |
| 2026-10-08 | 实施完成：`filters()` 默认排除 `VOIDED` + 三态 `counted`（不新增端点）、测试 TC-30-01~07、`EXPLAIN` 证据入卡、按 ADR-0031 拆 `code_repository.py`（401 → 339 + 92）、`03 §6/§7/§12` 与 `docs/12` 0122/L-104 同步 | AI |
