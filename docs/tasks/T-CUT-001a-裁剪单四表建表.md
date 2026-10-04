# T-CUT-001a：裁剪单四张表的迁移 + 模型

| 项 | 内容 |
| --- | --- |
| 模块 | cut |
| 负责人 | |
| 状态 | `done` |
| 优先级 | P1 |
| 依赖 | T-BASE-006（`material_stocks` / `wip_stocks` 已建）、T-BASE-005（`materials` / `suppliers` 已建）、T-BASE-002（`styles` 已建） |
| 被依赖 | T-CUT-001b（service 层：数量口径 / 取整 / 乐观锁 / 锁批）、T-CUT-001c（接口与页面）、T-BUND-001（打菲单表，`source_cutting_order_id` 外键指向本卡） |
| 关联设计 | [docs/modules/02-裁剪.md](../modules/02-裁剪.md) §3.0~§3.4 |
| 关联 ADR | [0013](../adr/0013-裁剪尺码比例与手数.md)、[0017](../adr/0017-裁剪按布批与行内多颜色.md)、[0020](../adr/0020-商品分类与手数直接输入.md)、[0022](../adr/0022-采购台账与库存供应商维度.md)、[0023](../adr/0023-生产单据页面重排与溯源链.md) |
| 估算 | 0.5d |

## 目标

裁剪单的四层表（表头 / 布批行 / 行内颜色 / 尺码明细）在库里真实存在，
且 `bundling_orders.source_cutting_order_id`、`bundles.cutting_size_line_id`
这两根悬空的外键指向的目标表不再是「谁都没定义过的表」。

## 范围

**要做**：

- [x] 迁移 `0009`：两个枚举（`document_status` / `cutting_entry_mode`）+ 四张表 + 索引 + 权限
- [x] 模型 `app/modules/cutting/models.py`（4 个类），注释写清每一处「为什么这样」
- [x] `docs/04 §7.7.2` 的三处修正（见下「规范修正」）
- [x] 测试 `tests/modules/test_cutting_tables.py`
- [x] 从 `test_docs_ddl_sync.py` 的 `P1_PENDING_TABLES` 删掉四张表
- [x] 从 `app/core/scope.py` 的 `PENDING_TABLES` 删掉 `cutting_orders`
      （`SCOPE_SPECS` 里**早已登记**，见该文件「前瞻登记」的注释）

**不做**（明确排除，防止顺手扩大）：

- **service 层**：数量口径（`fabric_qty` / `output_qty` 汇总重算）、`floor` 取整、
  乐观锁 UPDATE、锁批（`material_stocks.locked_qty` 累加）—— 全部留给 **T-CUT-001b**
- **单据状态机流转**（`submit` / `approve` / `reject` / `reverse` / `cancel`）——
  `docs/08 §2.1` 的审核七步留给 **T-CUT-001b**
- **接口与页面**、OpenAPI、`permissions.ts` —— 留给 **T-CUT-001c**
- **`cutting_outputs` / `cutting_scrap_records`**（modules/02 §3.6 / §3.7）：
  它们是审核时的下游产物，本卡不涉及
- **`stock_reservations`**（P3，`locked_qty` 的同步明细）
- **`bundles` / `bundling_orders` / `bundling_order_lines`**（T-BUND-001）

## 规范修正（本卡发现，动手前与用户确认过）

`04 §7.7.2` 的 `cutting_orders` DDL 有三处缺陷，都在**照抄建表就会失败**或**留下静默缺口**：

| # | 缺陷 | 为什么必须改 | 依据 |
| --- | --- | --- | --- |
| 1 | `status cutting_status` | **全仓从未定义 `cutting_status`**（只有基线提交 `3cca53b` 里那一次，之后再没出现过，连 `CREATE TYPE` 都没有）→ 照抄建表报 `type "cutting_status" does not exist`。而 `08 §1.1` 的状态机与 `modules/02 §3.1` 字段表写的都是 `document_status` | 改的是笔误，不是决策 |
| 2 | 缺 `approved_by` / `approved_at` / `rejected_reason` / `cancelled_reason` | `04 §7.3` 规定「所有单据表共享」这 8 列；`§7.16` 打菲单已按同款方式处理过；`modules/02 §3.1` 也列了这 4 列；`AGENTS §2.2` 要求反审核必填原因。**不补的后果**：001b 做状态机时必然要回来 `ALTER TABLE` 补，而那比一次建对贵 | `04 §7.3` + `§7.16` 先例 + `modules/02 §3.1` |
| 3 | `workshop_id` 没有 `REFERENCES workshops(id)` | 本仓所有带车间列的已建表都有这根外键。不加的后果：能传入不存在的车间 id，而 `apply_data_scope` 拿它 `IN (...)` 过滤时**静默返回空**，症状与「车间主管看不到数据」完全一样 | `0004` / `0005` / `0008` 既有做法 |
| 4 | **`ck_cutting_orders_qty` 写 `fabric_qty > 0 AND output_qty > 0`** | ⚠️⚠️ **写测试时真撞出来的**（前三条是动手前发现的，这条是 `test_draft_order_can_be_inserted` 报的 `CheckViolationError`）。这两列**同样有 `DEFAULT 0`**，草稿态也必然是 0 → **任何新建的草稿单都插不进库**。它与文档里已注明的 `hands_total` 是**同一个病**，但那个矛盾**只写在 `hands_total` 的注释里**，照着改那一条就会漏掉这一条 | 与 `hands_total` 同款处置：改 `>= 0`，`> 0` 由 service 在 `submit` 校验（`modules/02 §2` C6/C8）；`04 §4` 本身也要求 `CHECK (amount >= 0)` |
| 5 | `cutting_order_size_lines` 在 `...公共字段` **之外**又显式写了 `remark` | 照抄建表报 `DuplicateColumnError: A column with name 'remark' is already present`。两处是**同一列**（`04 §2` 的 `remark` 就是「备注，长度不设限」），删掉显式那份 | `04 §2` |

> ⚠️ **第 1 条的另一面**：`document_status` 这个 PG 枚举**在此之前根本不存在**
> （库里只有 `data_scope` / `material_type` / `purchase_purpose` / `size_class`），
> 且没有任何迁移建过它。所以本卡顺带把它建出来 —— 含 `PAID`（`08 §1.1` REV-2026-10
> 要求它在通用枚举里，工资单那张卡直接能用）。
>
> ⚠️ **第 6 处修正：`04 §7.7.2` 末尾那段 `ALTER TABLE cutting_order_colors RENAME`
> 迁移说明是空文**。P0 从没建过任何业务单据表（`modules/00-P0地基.设计` 明写
> 「P0 没有任何业务单据」），所以那张旧表**从来不存在**，没有可 RENAME 的对象。
> 已在文档里注明「别照它写迁移」，但保留了那段文字 —— 它记录的是 ADR-0017 的
> **设计意图**（为什么从单据级下沉到行内颜色级），不是可执行的迁移步骤。

## 顺带补的三处索引（`04 §5` 要求，文档里没写）

| 索引 | 场景 | 出处 |
| --- | --- | --- |
| `idx_cutting_orders_status_workshop (status, workshop_id)` | 待审核列表（主管工作台）`WHERE workshop_id=? AND status='SUBMITTED'` | `modules/02 §8` 第 3 行点名要这个索引，`§7.7.2` 却漏了 |
| `idx_cutting_size_lines_line (line_id)` | `cutting_order_size_lines.line_id` 是**冗余外键**（打菲 / 计件按布批反查），`04 §5` 要求「所有被用作 JOIN 的外键建索引」 | `04 §5` + `§7.7.2` 该列的注释 |
| `ix_<table>_deleted_at`（四张表各一个） | `SoftDeleteMixin` 上声明了 `index=True`，每张软删表都要有；列表查询恒带 `WHERE deleted_at IS NULL`，没索引就是全表扫 | `SoftDeleteMixin` 定义 + `0008` 先例 |

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `backend/alembic/versions/0009_cutting_orders.py` | 新增 | 迁移：2 枚举 + 4 表 + 索引 + `REVOKE DELETE` |
| `backend/app/modules/cutting/__init__.py` | 新增 | 空壳包（`alembic/env.py` 靠 `pkgutil` 遍历） |
| `backend/app/modules/cutting/models.py` | 新增 | 4 个模型 |
| `backend/app/core/scope.py` | 修改 | `PENDING_TABLES` 删 `cutting_orders` |
| `backend/tests/modules/test_docs_ddl_sync.py` | 修改 | `P1_PENDING_TABLES` 删 4 张表 |
| `backend/tests/modules/test_cutting_tables.py` | 新增 | 建表守卫 |
| `docs/04-数据库规范.md` | 修改 | §7.7.2 三处修正 + 2 个索引 |
| `docs/12-文档与变更归档规范.md` | 修改 | 变更记录 + 遗留问题 |

## 实现要点（必读规范）

- [x] 遵守 `04 §2`（公共字段 + `ck_<table>_version_positive`）、`§3`（枚举）、
      `§4`（数量 `numeric(14,3)`、金额 `numeric(18,4)`、**禁 float**）、`§5`（索引）、
      `§6.2.1`（应用账号无 DELETE）、`§7.3`（单据公共列）、`§7.7.2`（本卡权威 DDL）
- [x] 遵守 `08 §1.1`（状态迁移图 —— 本卡只建列，不写迁移）
- [x] `modules/02 §3.0` 明写「**权威 DDL 在 `04 §7.7.2`，本模块不得改写 DDL**」
      → 与 `modules/02` 字段表冲突时以 `04` 为准（见「遗留问题」）
- [x] 遵守 AGENTS §2.1：无硬删除、无 `session.commit()`、无硬编码业务常量

### 本卡踩过的坑 / 刻意为之的写法

| # | 坑 | 处理 |
| --- | --- | --- |
| 1 | **`CREATE TYPE IF NOT EXISTS` 不存在**（PG 只有 `CREATE TABLE IF NOT EXISTS`）。实测报 `syntax error at or near "NOT"` | 先查 `pg_type` 再决定建不建（抄 `0008` 的 `_create_purchase_purpose`） |
| 2 | **`postgresql.ENUM(..., create_type=False)` 两侧都要** | 迁移里手写 `CREATE TYPE` 而模型默认 `create_type=True` → SQLAlchemy 会**自己再发一条** `CREATE TYPE`，撞车报 `type already exists`。`0004` / `0007` / `0008` 各踩过一次 |
| 3 | **`comment=` 必须逐字一致**，否则 `alembic check` 逐条报漂移 | 写完跑 `alembic check` 归零 |
| 4 | **`styles.style_no` 是部分唯一索引，不能被外键引用** | `cutting_orders.style_id` 走 FK、`style_no` 冗余（`§7.7.2` 已如此写，照抄） |
| 5 | **四张表都要 `BaseModel`**（不是 `IdMixin`） | 子表也要软删 + 乐观锁：裁剪单审核后要反审核、要留痕，不是 append-only |
| 6 | **`cutting_orders` 已在 `SCOPE_SPECS`** | `scope.py` 里是「前瞻登记」，建完要从 `PENDING_TABLES` 移出（`0005` 对 `style_color_size_ratios` 同样处理） |

## 验收标准

- [x] `uv run pytest tests/modules/test_cutting_tables.py -q` 全过
- [x] 四张表从 `P1_PENDING_TABLES` 移出后，`test_docs_ddl_sync.py` 全绿。
      ⚠️ 顺带更正了该白名单注释里的**一句错话**：它原写「留着不删 → 文档写错的列名
      再也不会被抓，因为白名单把它排除在列比对之外」，而 TD-02 **只按
      `table in _model_tables()` 决定比不比对、从不查白名单**。那句话会让下一个人
      以为「删不删白名单决定了守卫跑不跑」—— 该查的时候不查。已改成真实的两条理由：
      TD-04 的合法集合被污染 / TD-05 会对着已建的表断言「它还没建」
- [x] `alembic upgrade head && downgrade -1 && upgrade head` 通过
- [x] `alembic check` 零漂移
- [x] 闸门 1-5 全绿

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-C01a-01 | `status` 列类型是 PG 枚举 `document_status`（不是 varchar） | 查 `pg_type` | ✅ |
| TC-C01a-02 | `document_status` 六个值齐全（含 `PAID`） | `pg_enum` | ✅ |
| TC-C01a-03 | 四张表都有 `04 §2` 公共字段 + `ck_*_version_positive` | 查库 | ✅ |
| TC-C01a-04 | 三层外键链完整（size_line → line_color → line → order） | `information_schema` | ✅ |
| TC-C01a-05 | `workshop_id` **有** FK 指向 `workshops` | 防「传不存在车间 id」 | ✅ |
| TC-C01a-06 | `hands` / `qty_per_hand` / `output_qty` 是 **integer** | ADR-0020 | ✅ |
| TC-C01a-07 | `ck_cutting_orders_hand` 是 `>= 0` 而非 `> 0` | 草稿态必然是 0 | ✅ |
| TC-C01a-08 | `ck_cutting_size_hands` 拒 `hands <= 0` | 真去 INSERT | ✅ |
| TC-C01a-09 | 三个唯一键的列集正确（行 / 颜色 / 尺码明细） | 防 ADR-0017 层级错位 | ✅ |
| TC-C01a-10 | `idx_cutting_orders_scope` / `idx_cutting_orders_trace` 是**部分索引** | `04 §5` | ✅ |
| TC-C01a-11 | `idx_cutting_size_lines_line` 存在（冗余外键也要索引） | `04 §5` | ✅ |
| TC-C01a-12 | 应用账号**不能** DELETE 四张表 | `permission denied` | ✅ |
| TC-C01a-13 | 四个模型都在 `Base.metadata` | 定位用 | ✅ |
| TC-C01a-14 | `cutting_orders` 已不在 `scope.PENDING_TABLES` | 建完就要移出 | ✅ |

## 实际改动

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `backend/alembic/versions/0009_cutting_orders.py` | +731 | 2 枚举 + 4 表 + 11 索引 + `REVOKE DELETE` |
| `backend/app/modules/cutting/models.py` | +569 | 4 个模型 + `CuttingEntryMode` + 两个 `SAEnum` 常量 |
| `backend/app/modules/cutting/__init__.py` | +6 | 空壳包（`alembic/env.py` 靠 `pkgutil` 遍历） |
| `backend/tests/modules/test_cutting_tables.py` | +728 | 20 例 |
| `backend/tests/modules/test_docs_ddl_sync.py` | +18 / -4 | 白名单删 4 张表 + 更正注释里的一句错话 |
| `backend/app/core/scope.py` | +5 / -3 | `PENDING_TABLES` 删 `cutting_orders` |
| `docs/04-数据库规范.md` | +82 / -11 | §7.7.2 五处修正 + 三个索引 + §3 一句事实 |
| `docs/12-文档与变更归档规范.md` | +4 | 变更记录 0075 + 遗留 L-073/074/075 |
| `docs/tasks/T-CUT-001a-*.md` | +168 | 本卡 |

**手写行数合计 ≈ 2300 行**（无生成物）。按 `AGENTS §7.1` 拆成 3 个提交：

| 提交 | 内容 | 行数 | 为什么这么切 |
| --- | --- | --- | --- |
| `docs(base)` | 04 §7.7.2 修正 + 任务卡 + docs/12 | ≈ 260 | 纯文档，可跳过设计稿那一步（`AGENTS §1` 例外） |
| `feat(cut)` | 迁移 0009 + 模型 + `scope.py` | ≈ 1310 | **迁移与模型必须同提交** —— 拆开的话中间那个 commit 上 `alembic check` 必然报漂移（迁移建了表而模型没有，或反过来），而 `alembic check` 是闸门 4 的一半 |
| `test(cut)` | 20 例 + `P1_PENDING_TABLES` 清理 | ≈ 750 | 守卫清理必须排在建表之后：表还没建就把名字从白名单删掉，TD-04 会立刻红 |

> ⚠️ `feat(cut)` 那 1310 行**超过 `AGENTS §7.1` 的 800 行上限，且无法再拆** ——
> 迁移与模型是同一件事实的两种表述，拆开会产生一个 `alembic check` 报漂移的提交，
> 那比超行数糟得多（超行数只是 review 费劲，漂移的提交会误导 `git bisect`）。
> 按 `§7.1` 第 2 条在这里写明构成。

**提交记录**：
- `docs(base)`: 04 §7.7.2 修五处「照抄建表就失败 / 静默留坑」的缺陷 + T-CUT-001a 任务卡
- `feat(cut)`: 迁移 0009 裁剪单四表 + 模型（含之前根本不存在的 `document_status` 枚举）
- `test(cut)`: 20 例建表守卫 + 四张表移出待建白名单

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| L-073 | **`modules/02 §3.1` 字段表与 `04 §7.7.2` 的 DDL 不一致** —— 字段表多出 `color_group` / `color_code` / 单据级 `ratio_snapshot` / `fabric_id` / `warehouse_id` / `utilization_rate` / `marker_no` / `source_sales_order_id` / `need_final_approval` 9 列，DDL 一个都没有。按 `modules/02 §3.0`「权威 DDL 在 04」的裁决本卡**一律不建**。其中 `warehouse_id`（扣料仓库）与 `fabric_id`（主面料）值得单独确认：行上的 `stock_id` 已经隐含锁定了仓库，所以**不阻塞** 001b，但报表口径可能想要 | `docs/12` §5 L-073 |
| L-074 | **`modules/02 §8` 的索引名与 `04 §7.7.2` 对不上** —— §8 写 `idx_cutting_orders_workshop_doc_date` / `idx_cutting_orders_style_no_doc_date`，`§7.7.2` 写 `idx_cutting_orders_scope` / `idx_cutting_orders_trace`；§8 还引用了不存在的 `uq_material_stocks_dim`（实际是 `uq_material_stocks_lot`）。本卡以 `04 §7.7.2` 为准 | `docs/12` §5 L-075 |
| L-075 | `modules/02 §3.2` 建议的 `idx_cutting_order_lines_doc_lot (doc_id, dye_lot_no, bolt_no)` 未建 —— `uq_cutting_order_lines (doc_id, line_no)` 的最左前缀已覆盖 `doc_id`，而「按布批定位」那条查询实际走的是 `material_stocks` 的唯一键。等 001b 真写审核逻辑时按实测决定要不要加 | `docs/12` §5 L-075 |

## 自检清单

- [x] 读过 `04 §2/§3/§4/§5/§6.2.1/§7.3/§7.7.1~§7.7.4`、`modules/02 §3/§8/§12`、
      `08 §1.1`、ADR-0013/0017/0020/0022/0023
- [x] 没有硬编码业务常量（枚举值集中在迁移头部的两个元组里，模型侧不重复定义）
- [x] 没有物理删除（四张表都是业务数据，迁移显式 `REVOKE DELETE`）
- [x] 新表字段齐全（四张表的公共字段由测试查库验证，不靠 ORM 自证）
- [x] 状态变更走了 service 层迁移方法且写了日志 —— **不适用**（本卡只建 `status` 列，
      迁移逻辑在 001b）
- [x] 接口有权限声明 + 错误码 + OpenAPI 标签 —— **不适用**（本卡无接口）
- [x] 测试覆盖 14 例：枚举口径 / 公共字段 / 外键链 / 必填类型 / CHECK 生效 /
      唯一键列集 / 部分索引 / 冗余外键索引 / 权限边界 / 模型注册 / 待建清单移出
- [x] 并发场景 —— **不适用**（本卡无业务逻辑，锁批在 001b）
- [x] 闸门 1 lint 通过
- [x] 闸门 2 typecheck 通过
- [x] 闸门 3 单测通过
- [x] 闸门 4 迁移可正向且可回滚（`alembic check` 零漂移）
- [x] 闸门 5 构建镜像成功
- [x] 提交信息符合规范
- [x] 本次改动已在 docs/12 变更记录留痕