# T-BUND-001：打菲四表建表 + `bundle_label_prints` DDL 补齐

| 项 | 内容 |
| --- | --- |
| 模块 | bundling |
| 负责人 | backend-dev |
| 状态 | `done` |
| 优先级 | P1（阻塞 T-BUND-002~010） |
| 依赖 | 无（迁移号需避让 T-BASE-009-2，见「实现要点」） |
| 被依赖 | T-BUND-002、T-BUND-003、T-BUND-004、T-BUND-005a、T-BUND-006、T-BUND-007 |
| 关联设计 | [`docs/modules/03-打菲.md`](../modules/03-打菲.md) §3.1/§3.2/§3.3/§3.4、[`P1-打菲-实施说明.md`](../modules/P1-打菲-实施说明.md) §2/§3 |
| 关联 ADR | [ADR-0016](../adr/0016-打菲按手与扫码得件数.md)、[ADR-0020](../adr/0020-商品分类与手数直接输入.md)、[ADR-0031](../adr/0031-模块文件结构按职责拆分.md)、[ADR-0030](../adr/0030-提交体量上限放宽到1200行.md) |
| 估算 | 0.5d |

## 目标

把打菲模块的四张表真正建出来，让 `T-BUND-002` 起的 service 有表可写；
同时补齐 `bundle_label_prints` 在 `04 §7` 缺失的 DDL（闭环 `docs/12` L-072 的 DDL 部分）。

## 范围

**要做**：
- [x] **`docs/04 §7.16` 追加 `bundle_label_prints` 的 `CREATE TABLE`** —— ✅ 已由规范变更
      **0111**（2026-10-06）完成（字段照 `modules/03 §3.4` 搬运，append-only 公共字段口径）；
      **本卡不再重复改 `docs/04`**
- [ ] `backend/tests/modules/test_docs_ddl_sync_fields.py` 的 `FIELD_TABLE_SOURCES` 增加
      `bundle_label_prints`（字段表源 `modules/03` §3.4），使 TD4-01 双向往返守卫覆盖它
- [ ] 迁移（建卡时 `head+1`，当前 0013 → 0014；若 0014 已被 T-BASE-009-2 占用则顺延）：
      `bundle_status` PG 枚举 + `bundling_orders` + `bundling_order_lines` + `bundles` + `bundle_label_prints`
- [ ] 新建 `backend/app/modules/bundling/`：`__init__.py` + `models/` 包
      （`models/order.py` = `BundlingOrder`/`BundlingOrderLine`；`models/bundle.py` =
      `bundle_status` 枚举 + `Bundle`/`BundleLabelPrint`；`models/__init__.py` 聚合重导出）
- [ ] `backend/tests/modules/test_bundling_tables.py`：表结构 + 枚举 + CHECK 真拦
- [ ] `P1_PENDING_TABLES` **先补登 `bundle_label_prints`**（DDL 已进 `04 §7.16` 但表未建，缺此登记 TD-04 会报「未知表」）；建表后与 `bundling_orders` / `bundling_order_lines` / `bundles` 一并删除（TD-02 从此真比对）
- [ ] 归档：`docs/12` 变更记录加行

**不做**：
- 不写任何 service / router / schema（T-BUND-003 起）
- 不动 `cutting_outputs`（`T-BASE-009-2`）
- **不改 `bundles` DDL**（`modules/03 §3.3` 明写「照抄 04 §7.0，本模块不得改写」）
- 不落 `bundle_no` 格式正则 CHECK（待决 Q-B18 未闭环，见 `P1-打菲-实施说明.md` §7）
- 不新开单据号计数器表（复用 `cutting_doc_no_sequences`，见 T-BUND-002）

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `docs/04-数据库规范.md` | — | §7.16 的 `bundle_label_prints` DDL 与 `hands integer` 已由规范变更 **0111** 完成，本卡**不再修改本文件** |
| `backend/tests/modules/test_docs_ddl_sync_fields.py` | 修改 | 字段表源 + 白名单同步 |
| `backend/tests/modules/test_docs_ddl_sync.py` | 修改 | `P1_PENDING_TABLES` 先补登 `bundle_label_prints`，建表后删四表 |
| `backend/alembic/versions/00NN_bundling_tables.py` | 新增 | 枚举 + 四表 + 索引 |
| `backend/app/modules/bundling/__init__.py` | 新增 | 包声明（`register_all_models` 靠 pkgutil 发现） |
| `backend/app/modules/bundling/models/__init__.py` | 新增 | 聚合重导出（照 `cutting/models/__init__.py`） |
| `backend/app/modules/bundling/models/order.py` | 新增 | `BundlingOrder` / `BundlingOrderLine` |
| `backend/app/modules/bundling/models/bundle.py` | 新增 | `bundle_status` + `Bundle` / `BundleLabelPrint` |
| `backend/tests/modules/test_bundling_tables.py` | 新增 | 表结构 8~12 例 |

> **体量说明**：本卡 9 个文件，超 `REQ-000 §3` 的 ≤5 文件目标。原因是「四表模型 + DDL 补齐 +
> 守卫同步」不可拆成两个可独立验收的提交（只搬 DDL 不建表，守卫会要求把表留在 `P1_PENDING_TABLES`，
> 等于再造一次 §7.16 的悬空）。文件数以 **单文件 ≤400 行** 为硬约束（ADR-0031 §3.3），本卡全部远低于。

## 实现要点（必读规范）

- [ ] 遵守 `docs/04-数据库规范.md`：公共字段（§2 / `app/common/models.py` 的
      `IdMixin`+`TimestampMixin`+`SoftDeleteMixin`+`VersionMixin`/`RemarkMixin`）、单据公共列（§7.3）
- [ ] **建表照 `04 §7.0` 与 `04 §7.16`**，不照 `modules/03` 字段表（后者保留读语义）；
      两侧差异已在 §3 注明
- [ ] `bundling_orders` / `bundling_order_lines` 走 `04 §7.3` 单据公共列 +
      `uq_bundling_orders_doc_no` / `uq_bundling_order_lines_line`
- [ ] 枚举按 `AGENTS §5` 走 PG enum；`bundle_status = ('ACTIVE','VOIDED')`
- [ ] 索引照 `modules/03 §8` 的查询场景，**禁止为不存在的查询建索引**：
      `idx_bundling_orders_workshop_doc_date` / `idx_bundling_orders_status_workshop` /
      `idx_bundling_orders_style_operation_date` / `idx_bundling_order_lines_doc_size` /
      `idx_bundling_order_lines_cutting` / `uq_bundles_no` / `uq_bundles_hand` /
      `idx_bundles_counted_pending` / `idx_bundles_line_id` / `idx_bundles_cutting` /
      `idx_bundle_label_prints_bundle_no`
- [ ] **迁移号避让**：先 `uv run alembic heads`；`T-BASE-009-2` 计划占 0014，谁先落地谁取 `head+1`
- [ ] ✅ `bundling_order_lines.hands` 类型已由规范对齐：`04 §7.16` 现为 **`integer`**（2026-10-06
      对齐 ADR-0020，Q-B16 已闭环，变更 0111）。**本卡直接按 `integer` 建表**，无需另开迁移对齐

## 验收标准

- [ ] `uv run pytest tests/modules/test_bundling_tables.py -q` 全部通过
- [ ] `uv run pytest tests/modules/test_docs_ddl_sync.py tests/modules/test_docs_ddl_sync_fields.py -q` 全部通过
- [ ] `uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head && uv run alembic check` 输出 `No new upgrade operations detected.`
- [ ] 四张表公共字段按 `04`：`bundling_orders` / `bundling_order_lines` / `bundles` 含完整公共字段 + `version`；`bundle_label_prints` 为 **append-only**，只有 `id` + `created_at` + `created_by`（**无** `version` / `deleted_at` / `updated_*`，Q-B14 已闭环）
- [ ] `bundles` 的 `uq_bundles_hand UNIQUE (doc_id, color_code, size_code, hands)` 与
      `ck_bundles_qty` / `ck_bundles_cnt` / `ck_bundles_one` 真拦（测试里插违反行验证）
- [ ] `P1_PENDING_TABLES` 不再含 `bundling_orders` / `bundling_order_lines` / `bundles` / `bundle_label_prints`
- [ ] 本次新增/修改文件单文件 ≤400 行
- [ ] 闸门 1-4 本地预跑通过

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-B01 | 四表存在且列齐全（查 `information_schema`，非只看 ORM） | 通过 | |
| TC-B02 | `bundle_status` 枚举值域 = `ACTIVE`/`VOIDED` | 通过 | |
| TC-B03 | `uq_bundles_hand` 真拦重复 `(doc,color,size,hands)` | `IntegrityError` | |
| TC-B04 | `ck_bundles_qty` 真拦小数/非正 `bundle_qty` | `IntegrityError` | |
| TC-B05 | `ck_bundles_cnt` 真拦 `counted_qty > bundle_qty` | `IntegrityError` | |
| TC-B06 | `bundle_label_prints` 索引存在 | 通过 | |
| TC-B07 | `alembic downgrade -1` 能干净删四表 + 枚举 | 通过 | |
| TC-B08 | TD4-01 对 `bundle_label_prints` 双向往返 | 通过 | |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `backend/alembic/versions/0014_bundling_tables.py` | 389 | `bundle_status` 枚举 + 四表 + 索引 + 权限收口；down 先子后父删表并 `DROP TYPE IF EXISTS bundle_status` |
| `backend/app/modules/bundling/__init__.py` | 9 | 包声明（`register_all_models` 靠 pkgutil 发现） |
| `backend/app/modules/bundling/models/__init__.py` | 40 | 聚合重导出（照 `cutting/models/__init__.py`） |
| `backend/app/modules/bundling/models/order.py` | 242 | `BundlingOrder` / `BundlingOrderLine` |
| `backend/app/modules/bundling/models/bundle.py` | 209 | `bundle_status` 枚举 + `Bundle` / `BundleLabelPrint`（append-only） |
| `backend/tests/modules/test_bundling_tables.py` | 364 | TC-B01~B08（CHECK 真拦 + 真跑 downgrade） |
| `backend/tests/modules/test_docs_ddl_sync.py` | 781 | P1_PENDING_TABLES 删四表；新增 `DOC_PUBLIC_*` 常量供 TD-02 处理 §7.3 约定 |
| `backend/tests/modules/test_docs_ddl_sync_fields.py` | 258 | `FIELD_TABLE_SOURCES` 增 `bundle_label_prints`；`DOC_PUBLIC_*` 改为从 `test_docs_ddl_sync` import |

> ⚠️ `test_docs_ddl_sync.py` 由 748 → 781 行，超 `ADR-0030` 的 400 行单文件线 ——
> 该文件**建卡前已是越线存量**（`docs/12 §5` L-081 登记 746），本卡按卡片要求
> 必须改它（P1_PENDING_TABLES），且 TD-02 真比对这两张用 §7.3 约定的表需要
> `DOC_PUBLIC_*` 常量。拆测试文件的专门卡未开，故按 L-081「存量随各自卡迁移」口径处理。

**提交记录**：
- `<hash>` db(bundling): 打菲四表建表（迁移 0014）+ bundling 模块骨架

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| Q-B14 | ✅ **已闭环 2026-10-06**：append-only，公共字段仅 `id` + `created_at` + `created_by`（无 `version`/`deleted_at`/`updated_*`），DDL 见 `04 §7.16` | — |
| Q-B16 | ✅ **已闭环 2026-10-06（规范对齐 ADR-0020）**：`hands` 为 `integer`，建表直接按 integer | — |
| L-072 | DDL 部分已闭环 2026-10-06（`04 §7.16` 已补 `bundle_label_prints`，变更 0111）；索引/查询守卫随本卡建表落地 | — |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。

## 变更记录

| 日期 | 变更内容 | 操作人 |
| --- | --- | --- |
| 2026-10-06 | 初版：四表建表 + `bundle_label_prints` DDL 补齐，明确迁移号避让 T-BASE-009-2 | AI |
| 2026-10-06 | 同步规范变更 0111：`04 §7.16` 的 DDL 与 `hands integer` 已由规范完成（本卡不再改 `docs/04`）；Q-B14/Q-B16 闭环并从阻塞遗留移除；补 `P1_PENDING_TABLES` 需先登记 `bundle_label_prints` | AI |
