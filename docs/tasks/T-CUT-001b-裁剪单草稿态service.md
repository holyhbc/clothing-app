# T-CUT-001b：裁剪单草稿态的 service 层

| 项 | 内容 |
| --- | --- |
| 模块 | cut |
| 负责人 | |
| 状态 | `done` |
| 优先级 | P1 |
| 依赖 | T-CUT-001a（迁移 0009 四张表已建） |
| 被依赖 | T-CUT-001c（接口与页面） |
| 关联设计 | [docs/modules/02-裁剪.md](../modules/02-裁剪.md) §2 C1~C6/C13/C18~C22/C26~C30/C32~C35/C38、§6 |
| 关联 ADR | [0013](../adr/0013-裁剪尺码比例与手数.md)、[0014](../adr/0014-裁剪明细三种录入模式.md)、[0017](../adr/0017-裁剪按布批与行内多颜色.md)、[0020](../adr/0020-商品分类与手数直接输入.md) |
| 估算 | 本卡（b-1）0.5d；全卡拆 3 张 |

## 目标

裁剪单在**草稿态**能被程序化地建出来、改表头、查回三层结构 —— 数量口径与
ADR-0017 / ADR-0020 完全一致，且**不信任前端**（汇总一律服务端重算）。

## ⚠️ 为什么这张卡被拆成三张

原定的 001b 范围是「数量口径 + 取整 + 乐观锁 + 锁批 + 状态机七步」。开工前逐条查依赖
（`08 §2.1` 审核八步）发现**四项无法实现**：

| 审核步 | 依赖 | 状态 |
| --- | --- | --- |
| ① 写 `stock_ledger_lines` | 库存台账 | ❌ **连字段表都没有**（`modules/06 §1` 只有两行文字） |
| ② 布头入库 | 台账 + `cutting_scrap_records` | ⚠️ 后者有字段表，`04 §7` 无 DDL |
| ④ 行耗料下限校验 | `bom_items` | ⚠️ 有字段表（`modules/01 §3.3`），`04 §7` 无 DDL |
| ⑦ 写 `cutting_outputs` | 裁剪结转 | ⚠️ 有字段表，`04 §7` 无 DDL |
| 反审核 ① | `bundles.counted_at` | ❌ 表未建 |

所以拆成：

| 卡 | 范围 | 依赖 |
| --- | --- | --- |
| **T-CUT-001b-1（本卡）** | 迁移 0010 取号表 + 4 个错误码 + `schemas.py` + `repository.py` + `service.create/get/list` + **三级汇总重算** + 取整口径 + 乐观锁 | 无（只碰已建的表） |
| T-CUT-001b-2 | `patch` / `delete`（级联软删）+ `PUT lines` / `colors` / `size-lines` + `suggest-lines` + `entry-mode` 切换 + C19/C20 校验 + 并发测试 | 001b-1 |
| T-CUT-001b-3 | `submit` / `approve` 八步 / `reject` / `withdraw` / `reverse` / `cancel` + 状态机 + `document_logs` | **`bom_items` + `stock_ledgers` + `cutting_outputs` + `bundles` 全部建表后** |

## 规范修正（本卡开工前，业务已确认）

| # | 修正 | 内容 |
| --- | --- | --- |
| 1 | **行 `output_qty` 口径（业务确认 2026-10-04，口径 A）** | `modules/02 §6` ① 原写「服务端算 `Σ(颜色 Σ尺码出数)`」，代进 C34 会让 `balance_qty` **恒为 0**、「行余量」概念消失；`08 §2.1` ③「求和得行 `output_qty`」同此问题。**改为**：行 `output_qty` 由**用户按铺布实耗正向录入**（与 `fabric_qty` 同性质），服务端只校验 `≥ Σ明细`；`balance_qty = 行 output_qty − Σ明细` 是真实的整批零头。已同步改 `§6 ①`、`C34`、`C5`、`§5.1` 示例加读法澄清、`08 §2.1` ③ |
| 2 | **`numeric` / `floor` 残留（ADR-0020 之前的）** | `modules/02 §3.4` 与 `§6 ③` 的 `hands` / `qty_per_hand` 仍写 `numeric(14,4)` +「可小数 1.5 手」，`output_qty` 写 `numeric(14,3)` +「`floor(hands × qty_per_hand)`」。与同文档 C13/C20/C21、`04 §7.7.2`、ADR-0020 及**已建的 integer 列**全部冲突 → 全部改为 `integer` + 精确乘法 |
| 3 | **`bom_items` 尺码粒度（业务确认 2026-10-04，选项 ③）** | 阶段一**不按尺码分维度**，等真出现「误报 `30002`」再加 `size_code` 列。理由与「将来加列要改哪三处」写进 `modules/01 §3.3`；登记为 Q-B14 |
| 4 | **`bom_items` 外键** | `modules/01 §3.3` 写 `style_no FK→styles`，而 `styles.uq_styles_no` 是部分索引 → **建不出来**。T-DOCS-003 修了三处漏了这一处（因为它在 `04 §7` 没有 DDL，守卫 TD5-01 查不到）→ 改为 `style_id` + `style_no` 冗余 |

## 范围

**要做**（001b-1）：

- [ ] 迁移 `0010`：`cutting_doc_no_sequences`（C1 取号，`CT-{YYYYMMDD}-{6 位}`）
- [ ] `ErrorCode` 补 `30002` / `30006` / `40001` / `40006`（`05 §4` 已登记，代码里**还没有**）
- [ ] `app/modules/cutting/schemas.py`：三层嵌套 payload
- [ ] `app/modules/cutting/repository.py`：三层读取 + `apply_data_scope`
- [ ] `app/modules/cutting/service.py`：`create` / `get` / `list`
- [ ] **三级汇总重算**（C6）：头 ← 行 ← 颜色 ← 尺码明细
- [ ] **取整口径**（C13/C21）：`output_qty = hands × qty_per_hand`，整数精确值，**不 floor**
- [ ] **行余量**（C34）：`balance_qty = 行 output_qty − Σ明细`，负数 → `30002`
- [ ] 乐观锁（`version`）
- [ ] 测试

**不做**（001b-1）：

- **`patch` / `delete` / `PUT lines` / `PUT colors` / `PUT size-lines`** → 001b-2
- **`suggest-lines` / `entry-mode` 切换 / 比例快照** → 001b-2
- **状态机任何动作**（`submit` / `approve` / …）→ 001b-3
- **锁批 / 台账 / WIP 写入** → 001b-3
- **C38 的 `fabric_qty > available_qty → 40006`** → 001b-2（草稿态是提示，审核才拒）
- **接口与页面** → T-CUT-001c
- **`document_logs`** → 001b-3（只有状态变更才写日志）

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `backend/alembic/versions/0010_cutting_doc_no_sequence.py` | 新增 | 取号计数器 |
| `backend/app/core/errors.py` | 修改 | 4 个错误码 + HTTP 映射 |
| `backend/app/core/numbering.py` | 修改 | `take_cutting_doc_no` |
| `backend/app/modules/cutting/models.py` | 修改 | `CuttingDocNoSequence` |
| `backend/app/modules/cutting/schemas.py` | 新增 | |
| `backend/app/modules/cutting/repository.py` | 新增 | |
| `backend/app/modules/cutting/service.py` | 新增 | |
| `backend/tests/modules/test_cutting_service.py` | 新增 | |
| `docs/02` / `docs/05` / `docs/modules/02` | 修改 | 口径登记 |
| `docs/12-文档与变更归档规范.md` | 修改 | 变更记录 |

## 实现要点（必读规范）

- [ ] `03 §1.4` service 正例：事务边界**只在** `async with self.session.begin():` 第一行，
      事务内只用 `flush`，`commit` 由 unit-of-work 完成
- [ ] `03 §1.1` 第 7 条：状态/类型枚举集中，**禁止散落 `Literal` 或魔法字符串**
      （`MASTER` / `UNIFORM` / `MANUAL` 必须用 `CuttingEntryMode`）
- [ ] `03 §1.1` 第 8 条：**禁止 float**，数量用 `Decimal`，`hands` 用 `int`
- [ ] `INV-8` / `docs/07 §3.2` 铁律：列表与详情**必须**过 `apply_data_scope` /
      `assert_in_scope`，**Router 里不许过滤**
- [ ] `C6`：头汇总由 service 重算并**覆盖入参**（不信任前端）
- [ ] `04 §5`：不要建「为不存在的查询」的索引

### 三个容易踩的坑（本卡已识别）

| # | 坑 | 处理 |
| --- | --- | --- |
| 1 | `hands` / `qty_per_hand` 是 **`integer`**（ADR-0020），而 `modules/02 §3.4` 说的是 `numeric(14,4)`「可小数」 | 一律用 `int`；**禁止** `Decimal` 与 `floor`。误用 `floor` 会让 `1.5 手` 这种非法值静默通过 |
| 2 | 行 `output_qty` 是**正向录入值**，不是 `Σ明细` | service 校验 `行 output_qty ≥ Σ明细` 而不是覆盖它。写成覆盖 = `balance_qty` 恒 0（这正是本次修掉的原缺陷） |
| 3 | C1「生成即占用、永不复用」 | 取号在**同一事务内**完成；事务回滚时号被退回是**可接受**的（单据没建成，号没被看见过），但**不能**先取号再开事务 |

## 验收标准

- [ ] `uv run pytest tests/modules/test_cutting_service.py -q` 全过
- [ ] `alembic upgrade head && downgrade -1 && upgrade head` 通过
- [ ] `alembic check` 零漂移
- [ ] 闸门 1-5 全绿

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-C01b-01 | `doc_no` 格式 `CT-YYYYMMDD-6 位` | 09 §2.1 | |
| TC-C01b-02 | 同日 20 并发取号不重复 | C1 | |
| TC-C01b-03 | 三级汇总：头 = Σ行 = Σ颜色 Σ尺码 | C6 | |
| TC-C01b-04 | 头汇总**覆盖**前端传入的错值 | C6「不信任前端」 | |
| TC-C01b-05 | `output_qty = hands × qty_per_hand` 精确整数，**不 floor** | C13/C21 | |
| TC-C01b-06 | `balance_qty = 行 output_qty − Σ明细`，负数 → `30002` | C34 | |
| TC-C01b-07 | 人工指定出数 → 颜色转 `MANUAL` + 差额进 `balance_qty` | C13/C28 | |
| TC-C01b-08 | 同尺码多行合法（`size_line_no` 区分） | C30 | |
| TC-C01b-09 | 一行多色合法 | C32 | |
| TC-C01b-10 | 乐观锁：版本不符 → `12003` | `04 §2` | |
| TC-C01b-11 | 数据范围：越权取单 → `12002` | `07 §3.2` 铁律 2 | |
| TC-C01b-12 | `hands <= 0` → `10001` | C22 | |
| TC-C01b-13 | 改行**不写**比例主数据（零污染） | C29 | |
| TC-C01b-14 | 4 个新错误码在 `ErrorCode` 与 `HTTP_STATUS_BY_CODE` 里 | `05 §4` 逐项一致 | |

## 实际改动

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `backend/alembic/versions/0010_cutting_doc_no_sequence.py` | +84 | 取号计数器 |
| `backend/app/modules/cutting/schemas.py` | +304 | 三层嵌套入参 + 三层出参 |
| `backend/app/modules/cutting/repository.py` | +193 | 三层读取 + 列表 + `apply_data_scope` |
| `backend/app/modules/cutting/service.py` | +382 | `create` / `get` / `list_orders` + 三个 `recalc_*` |
| `backend/tests/modules/test_cutting_service.py` | +768 | 24 例 |
| `backend/app/modules/cutting/models.py` | +78 | `CuttingDocNoSequence` + 4 组 `relationship` |
| `backend/app/core/numbering.py` | +95 | `take_doc_no` / `format_doc_no` |
| `backend/app/core/errors.py` | +28 | 8 个错误码 + HTTP 映射 |
| `backend/app/common/models.py` | +36 | **共享的** `register_all_models` |
| `backend/alembic/env.py` | −16 / +8 | 改用共享入口 |
| `backend/tests/test_errors_registry.py` | +62 | 守卫扩段 + 两条反向守卫 |
| `backend/tests/modules/test_docs_ddl_sync.py` | +15 | 改用共享入口 |
| `docs/04` / `docs/08` / `docs/modules/01` / `docs/modules/02` | +160 | 见下「规范修正」 |
| `docs/12` | +4 | 0076 + L-076/077/078 |

**手写行数 ≈ 2210（无生成物）**，按 `AGENTS §7.1` 拆两个提交：

| 提交 | 内容 | 行数 | 为什么这么切 |
| --- | --- | --- | --- |
| `docs(cut)` | 口径修订 + 任务卡 + docs/12 | ≈ 400 | 纯文档 |
| `feat(cut)` | 迁移 0010 + 取号 + 8 个错误码 + schemas/repository/service + 24 例 | ≈ 1810 | **不可再拆**：`test_errors_registry` 的新守卫要求 `30xxx` 六个码全部实现才绿，而它们只在 service 里被用到；`test_docs_ddl_sync` 的 TD-01 要求计数器表在 `04 §7` 有 DDL 才绿，那在 docs 提交里 |

## 规范修正（本卡改的，都在 docs 里留了「为什么」）

| # | 文件 | 改了什么 |
| --- | --- | --- |
| 1 | `modules/02` C34 | 行 `output_qty` 口径 → **业务选 A**（正向录入）；作废「取 floor(可裁件数)」那句（ADR-0013 残留） |
| 2 | `modules/02` §6 ① | 行 `output_qty`「谁算」：服务端算 → **前端传、服务端校验 `≥ Σ明细`** |
| 3 | `modules/02` C5 | 标注 `output_qty > 0` **不在 DB CHECK**（草稿态会误杀，`04 §7.7.2` 已改） |
| 4 | `modules/02` §5.1 | 加「读法澄清」：示例里的「小计」是 Σ明细，行 `output_qty` 是另一个数 |
| 5 | `docs/08` §2.1 | 审核步数七步 → **八步**；第 ③ 步加口径澄清；第 ⑥ 步写明行 `output_qty` 是正向录入值 |
| 6 | `modules/02` §3.4 / §6 ③ | `hands` / `qty_per_hand` / `output_qty` / `balance_qty` 由 `numeric` → **`integer`**，取消 `floor`（ADR-0020 残留） |
| 7 | `modules/01` §3.3 | `bom_items.style_no FK→styles` → **`style_id` + `style_no` 冗余**（部分索引不可引用）；并登记 **Q-B14** 尺码粒度决策 ③ |
| 8 | `docs/04` §3 | 补一句事实：`document_status` 由迁移 0009 建出 |
| 9 | `docs/04` §7.17 | **新增**：单据号计数器 DDL + 不用 PG `SEQUENCE` 的三条理由 |

## 抓到并修掉的两条守卫漏洞

| # | 漏洞 | 后果 | 修法 |
| --- | --- | --- | --- |
| 1 | `test_generic_segments_fully_implemented` 只 parametrize `10/11/12` | `20xxx/30xxx/40xxx` 在 `05 §4` 登记却**一个都没实现**，没有任何守卫会报 —— 而裁剪的错误码全在这个范围 | 扩成 `IMPLEMENTED_SEGMENTS` + 两条反向守卫（未实现段不许有码 / `DEFERRED_CODES` 双向校验）。⚠️ 按**码**而非按段推迟：`40xxx` 横跨阶段（`40001`/`40006` 现在就要，`40002`~`40005`/`40007` 等 P3） |
| 2 | `test_docs_ddl_sync._model_tables()` 靠 `import app.main` **碰运气**注册模型 | `app.main` 只 import 三个 router；裁剪的 router 属 T-CUT-001c，于是它当时注册不上，守卫报「表既没建也不在白名单」而**真相是表早就建好了**。症状**依赖用例执行顺序**（全量跑绿、单跑红）—— 「换个顺序就红」等于没有测试 | 模型注册抽成**共享入口** `app/common/models.py::register_all_models`，`alembic/env.py` 与守卫都用它 |

## 踩到的坑

| # | 坑 | 处理 |
| --- | --- | --- |
| 1 | `lazy="noload"` 在 SQLAlchemy 2.1 **已废弃**，且它的行为正是「静默返回 `None`」 | 改 `lazy="raise_on_sql"`（反向导航本来就不该读） |
| 2 | 事务边界写成 `session.begin()` | `app/core/db.py` 的注释明写踩过（13 个用例全红 `A transaction is already begun`）。改 `unit_of_work` |
| 3 | `recalc_*` 依赖 `line.colors` 回读 | 写入路径上刚 `add` 的行没进过任何 SELECT → 改成**传显式列表**，让汇总函数连 session 都不需要 |
| 4 | 并发取号测试**真提交**，把计数器推前了 20 | 导致下一条用例断言绝对值时红。改成断言**相对关系**（连号 + 新的一天必然 000001）。⚠️ 试过「先删计数器行」—— `erp_app` 无 DELETE 权限，撞 `permission denied` |
| 5 | 测试里 import 别的模块的**私有** helper | 改成自建 fixture：改那边会连带弄坏那边，从这边 import 则那边一改名这边就红 |

## 测试清单

见文件头表格（24 例，全部 ✅）。**24 / 24 通过。**

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| L-076 | **`stock_ledgers` / `stock_ledger_lines` 连字段表都没有** —— 阻塞 T-CUT-001b-3 的审核第 ① 步，与已闭环的 L-071 同类，但**没有捷径**（台账的字段口径必须一次定对） | `docs/12` §5 L-076 |
| L-077 | **`bundles` 未建** → `reverse` 的「已计件拒绝 `30004`」无法实现。只阻塞 b-3 | `docs/12` §5 L-077 |
| L-078 | `08 §2.1` ③ 与 `modules/02 §5.1` 示例的**措辞**仍可能被读成「行 `output_qty` = Σ明细」—— 而那正是本次修掉的缺陷。示例里两行数字相同（都是 610），所以澄清句也不够有说服力 | `docs/12` §5 L-078，建议 T-CUT-001c 改成「Σ明细 610 / 行可出件数 615 / 行余量 5」 |
| — | `bom_items` / `cutting_outputs` / `cutting_scrap_records` **有字段表、`04 §7` 无 DDL** | T-BASE-007（与 L-076 同一批做） |
| — | `PUT lines` / `colors` / `size-lines` / `suggest-lines` / `entry-mode` / 状态机 | T-CUT-001b-2 / b-3 |

## 自检清单

- [x] 读过 `03 §1.1/§1.4`、`04 §2/§3/§4/§5/§7.3/§7.7.2/§7.7.3`、`07 §3.2`、`08 §2.1`、`09 §4.2`、`10 §2.3`、
      `modules/01 §3.3`、`modules/02 §2/§3/§5/§6/§12`
- [x] 没有硬编码业务常量（枚举在 `CuttingEntryMode`；`MASTER`/`UNIFORM`/`MANUAL` 无一处裸字符串）
- [x] 没有物理删除（本卡无删除；`erp_app` 对计数器表本就无 DELETE）
- [x] 新表字段齐全（计数器表**刻意豁免** `04 §2`，理由写在迁移头注与模型 docstring）
- [x] 状态变更走了 service 层迁移方法且写了日志 —— **不适用**（本卡只建草稿态，状态机在 b-3）
- [x] 接口有权限声明 + 错误码 + OpenAPI 标签 —— **不适用**（本卡无接口）
- [x] 测试覆盖 24 例：取号格式/并发/按天重置 / 三级汇总 / 不信任前端 / 精确整数乘法 /
      行余量（正负两侧）/ MANUAL 自动切换 / 同尺码多行 / 一行多色 / 数据范围 / 非整数手数 /
      款号停用 / 快照反查 / C29 零污染 / 列表分页与深分页 / 软删不可见
- [x] 并发场景 ✅（同日 20 并发取号，用独立引擎真提交 —— docs/10 §2.3 要求）
- [x] 闸门 1 lint 通过
- [x] 闸门 2 typecheck 通过
- [x] 闸门 3 单测通过（693 例）
- [x] 闸门 4 迁移可正向且可回滚（`alembic check` 零漂移）
- [x] 闸门 5 构建镜像成功（`api-image`）
- [x] 提交信息符合规范
- [x] 本次改动已在 docs/12 变更记录留痕（0076）