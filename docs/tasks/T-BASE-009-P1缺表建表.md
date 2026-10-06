# T-BASE-009：P1 缺表建表（按业务模块分三张卡）

| 项 | 内容 |
| --- | --- |
| 模块 | base / cut / stock（三张卡各属一个模块，**刻意不合成一张**） |
| 负责人 | AI |
| 状态 | **第 1 张卡 `done`**；第 2、3 张 `pending` |
| 优先级 | P1（**阻塞 T-CUT-001b-3 审核八步**） |
| 依赖 | T-BASE-008（6 张表的 DDL 已搬进 `04 §7`） |
| 被依赖 | T-CUT-001b-3（状态机）、T-CUT-001c-3a（MASTER 前端） |
| 关联设计 | [modules/06-库存.md](../modules/06-库存.md) §3.3/§3.4/§3.5/§3.7、[modules/02-裁剪.md](../modules/02-裁剪.md) §3.6/§3.7、[modules/01-基础资料.md](../modules/01-基础资料.md) §3.3 |
| 估算 | 3 × 0.5d |

## 为什么拆成三张卡而不是一张

6 张表分属 **3 个业务模块**，而 `AGENTS §6` 要求「单次会话改动不超过 1 个业务模块」。
一张卡做完就是跨 3 个模块；而合成一张则只能得到一个 1200 行、跨 3 模块的提交 ——
那种提交的 review 体验正是 ADR-0030 想避免的。

| # | 卡 | 模块 | 表 | 迁移 | 模型落点 |
| --- | --- | --- | --- | --- | --- |
| **1** | **T-BASE-009-1（= 本卡，已完成）** | stock | `stock_ledgers` / `stock_ledger_lines` / `stock_reservations` | 0012 + 0013 | **新建** `app/modules/stock/models.py` |
| 2 | T-BASE-009-2 | cut | `cutting_outputs` / `cutting_scrap_records` | 0014 | `app/modules/cutting/models.py`（现 696 行，会破 400 硬线，见下） |
| 3 | T-BASE-009-3 | base | `bom_items` | 0015 | `app/modules/base/models.py`（现 1302 行，早已越线，登记在 L-081） |

⚠️ **第 2、3 张卡会撞单文件 400 行硬线**（`cutting/models.py` 696 → 约 790；
`base/models.py` 1302 → 约 1330）。两条路：① 直接加，只登记不修（存量违规已在 L-081）；
② 先把 `cutting/models.py` 按「单据三层 / 结转与布头」拆成两个文件（新增 `models_outputs.py`，
但 `docs/03 §1.3` 说「每模块固定 5 个文件」）。
**开工前需要拍板**，已登记为 L-089。

---

# 第 1 张卡：库存台账与锁定（已完成）

## 目标

把 `04 §7.2.5` 的三张表真正建出来，让 T-CUT-001b-3 的审核第 ①② 步与 `submit` 锁批有表可写。

## 范围（已完成）

- [x] **开工前先改规范**：A1 写进 `04 §7.2.5` 的台账 DDL 用了完整 `04 §2` 公共字段，
      与 `app/common/models.py` 的 append-only 口径冲突 → 先改 04（DDL / 索引谓词 / 视图过滤），再写迁移
- [x] 迁移 **0012**：`stock_ledgers` + `stock_ledger_lines` + 3 个 PG 枚举
      （`stock_type` / `stock_direction` / `stock_doc_type`）+ `v_stock_cost_check`
- [x] 迁移 **0013**：`stock_reservations` + `v_stock_reconciliation`
- [x] 新建 `app/modules/stock/`（`docs/01 §4.2` 已列 `stock/` 为并列模块）
- [x] `tests/factories/stock.py` + `test_stock_ledger_tables.py`（表结构）
      + `test_stock_views_and_grants.py`（视图与权限）= **17 例**
- [x] `P1_PENDING_TABLES` 删掉 3 张表
- [x] 归档：docs/12 变更记录 0090 + 新登记 L-088

**不做**：写入台账的业务逻辑、锁批与释放（→ T-CUT-001b-3）；`MaterialStock` 搬家（L-088）；
`stock_ledger_lines.material_id` 放宽（P3 建成衣栈时）。

## 为什么两个迁移而不是一个

`stock_reservations` 对两张台账表**零外键零依赖**；而台账与明细之间有 FK +
生命周期依赖（明细必须挂在一条台账上，`v_stock_cost_check` 同时依赖两者），
合在一个迁移里才有一个可回滚单元。拆开的代价是一次多出来的 revision，
收益是两个文件都在**单文件 400 行硬线**之内。

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-BL-01 | 两张台账表**没有** `deleted_at` / `version` / `updated_*` | 通过 | ✅ |
| TC-BL-01b | `stock_reservations` **有**完整公共字段 + `ck_*_version_positive` | 通过 | ✅ |
| TC-BL-02 | `stock_ledgers` 唯一键**不含 `stock_id`** | 通过 | ✅ |
| TC-BL-03 | `stock_id` / `source_doc_id` **不建外键**；`warehouse_id` 建了 | 通过 | ✅ |
| TC-BL-04 | `stock_ledger_lines.material_id` **NOT NULL** | 通过 | ✅ |
| TC-BL-05 | `ck_stock_ledger_lines_amount` 真拦（新插一行） | 通过 | ✅ |
| TC-BL-06 | 三个 PG 枚举的值域（模型侧 + 库里） | 通过 | ✅ |
| TC-BL-07a | 自洽时两个对账视图**恒为 0 行** | 通过 | ✅ |
| TC-BL-07b | 结存无对应台账 → `v_stock_reconciliation` **报出来** | 通过 | ✅ |
| TC-BL-07c | 台账无批次明细 / 数量不等 → `v_stock_cost_check` **报出来** | 通过 | ✅ |
| TC-BL-08 | 应用账号不能 UPDATE/DELETE 两张 append-only 表、不能 DELETE 锁定表 | 通过 | ✅ |
| TC-BL-08b | **对照**：应用账号**能** UPDATE `stock_reservations`（释放锁） | 通过 | ✅ |
| TC-BL-09 | `uq_stock_ledger_lines_batch` 是**普通** UNIQUE 而非部分索引 | 通过 | ✅ |
| — | 闸门 4：`downgrade -1` ×2 → `upgrade head` → `alembic check` 零漂移 | 通过 | ✅ |

⚠️ **「恒为 0 行」与「能报出异常」必须同时测，缺一不可**：只测前者，视图可以是一个
`WHERE false` 的空壳，永远绿；只测后者，证明不了它在正常状态下不误报。
而两者代价不对称 —— 误报是「发不了版」，漏报是「数据已经错了而没人知道」。

## 实际改动

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `backend/alembic/versions/0012_stock_ledgers.py` | +396（新建） | 台账 + 明细 + 3 枚举 + 成本对账视图 |
| `backend/app/modules/stock/models.py` | +340（新建） | 3 模型 + 3 枚举 |
| `backend/tests/modules/test_stock_ledger_tables.py` | +338（新建） | 表结构 10 例 |
| `backend/tests/factories/stock.py` | +138（新建） | `make_material` / `make_ledger` |
| `backend/tests/modules/test_stock_views_and_grants.py` | +200（新建） | 视图与权限 7 例 |
| `backend/alembic/versions/0013_stock_reservations.py` | +185（新建） | 锁定表 + 数量对账视图 |
| `backend/app/modules/stock/__init__.py` | +11（新建） | 包声明（`register_all_models` 靠它） |
| `docs/04-数据库规范.md` | ±40 | **append-only 口径修正**（DDL / 索引谓词 / 视图） |
| `backend/tests/modules/test_docs_ddl_sync.py` | −4 | 白名单删 3 张表 |
| `docs/12-文档与变更归档规范.md` | +8 | 变更记录 0090 + L-088 |
| **手写合计** | **≈ +1650** | ⚠️ **超 1200 软上限**（理由见下） |

> ⚠️ **体量说明**：手写 +1650 行，超 `AGENTS §7.1` 的 1200 软上限约 38%。
> 拆不动的部分：① 两个迁移文件加起来 581 行，是 Alembic 的固有粒度
> （一次 revision 一个文件，拆成 4 个 revision 会让「建库存台账」这个动作在
> `alembic history` 里散成 4 行）；② 17 例测试里有相当篇幅是**「为什么这条断言存在」**
> 的注释（TC-BL-01/02 各自对应一个「照抄就炸」的坑），删掉注释能让文件短一半，
> 但下一次有人问「凭什么台账不能软删」时就只能靠 git blame。
> 若要压到 1200 以内，唯一不损失信息的做法是**把第 2、3 张卡再推后**——
> 它们本来就是独立提交，不影响这条提交的可二分性。

## 自检清单（第 1 张卡）

- [x] 读过 `04 §2/§5/§7.2/§7.2.4/§7.2.5`、
      `app/common/models.py`（`WipLedgerLine` 的 append-only 理由）、
      迁移 `0004`/`0008`/`0009` 的 enum / PK / REVOKE 写法、`docs/01 §4.2`
- [x] 没有硬编码业务常量（枚举值域、CHECK、列类型全部照 `04 §7.2.5`）
- [x] 没有物理删除（`REVOKE UPDATE, DELETE` 从权限层挡住 append-only 表）
- [x] 新表字段齐全（append-only 表按 `04 §2` 例外只留 3 列，理由写进 04 与模型注释）
- [x] 状态变更（不适用）
- [x] 接口（不适用，本卡只建表）
- [x] 测试覆盖：17 例，含 4 条「权限 / CHECK 真会拦」的用例
- [x] 闸门 1 lint 通过
- [x] 闸门 2 typecheck 通过（56 文件 0 问题）
- [x] 闸门 3 单测通过（后端 **792 passed** / 覆盖率 89.69%）
- [x] 闸门 4 迁移可正向且可回滚（0013 → 0012 → 0011 → 0013，`alembic check` 零漂移）
- [x] 提交信息符合规范
- [x] **单文件均 ≤ 400 行**（最大 396 行 = 迁移 0012）
- [x] 本次改动已在 docs/12 变更记录留痕（0090 + L-088）

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| L-076 | **待建表的清单只剩 3 张**（`cutting_outputs` / `cutting_scrap_records` / `bom_items`） | 第 2、3 张卡 |
| L-088 | `MaterialStock` / `WipStock` / `WipLedgerLine` 仍在 `base/models.py`，与 `docs/01 §4.2` 的 `stock/` 规划不符 | P3 库存 |
| L-089 | 第 2、3 张卡会撞 `cutting/models.py`（696）与 `base/models.py`（1302）的 400 行硬线 | 本卡，待拍板 |
| — | `stock_ledger_lines.material_id` **P3 建成衣栈时必须 `DROP NOT NULL`** | P3 库存 |
| — | `stock_reservations` 与 `locked_qty` 一致性无对账视图（L-086） | T-CUT-001b-3 |