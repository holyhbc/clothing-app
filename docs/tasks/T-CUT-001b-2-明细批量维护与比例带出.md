# T-CUT-001b-2：裁剪单三层明细的批量维护 + 比例带出

| 项 | 内容 |
| --- | --- |
| 模块 | cut |
| 状态 | `done` |
| 优先级 | P1 |
| 依赖 | T-CUT-001b-1（草稿态 `create` / `get` / `list` 已就位） |
| 被依赖 | T-CUT-001c（接口与页面） |
| 关联设计 | [modules/02-裁剪.md](../modules/02-裁剪.md) §6 PUT 三条、§7 并发六条、§2 C14/C18~C22/C26~C30/C38 |
| 估算 | 0.5d |

## 目标

裁剪单的三层明细能被**增量维护**（不是只能整单重建）：改表头、删单、三条批量
替换接口、按比例带出建议、切录入模式 —— 且全部带乐观锁 + 级联软删 + 汇总重算。

## 范围

**要做**：

- [ ] `patch`：仅表头，`version` 必传，仅 `DRAFT` / `REJECTED`（C14）
- [ ] `delete`：仅 `DRAFT`，**三层级联软删**（AGENTS §2.1 禁物理删除）
- [ ] `PUT /lines`：布批行**全量替换**，`version` 必传
- [ ] `PUT /lines/{line_id}/colors`：行内颜色全量替换
- [ ] `PUT /size-lines`：尺码明细全量替换（按 `line_color_id` 定位）
- [ ] `GET /suggest-lines`：按 `style_color_size_ratios` 带出手数建议 + **写 `ratio_snapshot`**
- [ ] `POST /entry-mode`：切颜色级模式 + 二次确认 + 清 `hands`（C27）
- [ ] C19：比例完全未配 → `20006`；部分缺配 → **不拦**只提示；比例含款号没有的尺码 → `20007`
- [ ] C20：`Σhands` 与 `Σratio` 偏离 > 20% → **黄色提示不拦**
- [ ] C38：`fabric_qty > available_qty` → `40006`（草稿态**就拦**，不拖到审核）
- [ ] 并发：乐观锁冲突 + 同尺码 `size_line_no` 并发分配

**不做**（留给 b-3 / 001c）：

- **状态机任何动作** → b-3
- **锁批**（`material_stocks.locked_qty` 累加）→ b-3（`stock_ledgers` 未建）
- **导入 / 导出 / 统计** → 001c
- **Router** → 001c（本卡只有 service 层，与 b-1 同款）

## 三条最容易做错的地方

| # | 陷阱 | 正确做法 |
| --- | --- | --- |
| 1 | **全量替换 = 软删旧行 + 插新行**，不是 `DELETE` | 应用账号对四张表**无 DELETE 权限**（`04 §6.2.1`），物理删会撞 `permission denied`。旧行 `deleted_at = now()` + `version + 1` |
| 2 | `size_line_no` **由服务端分配**，不接受前端传 | §7 明确「在**锁住该颜色行**的前提下分配（防两个请求拿到同一个号）」。同尺码可重复，所以分配的号是 `max(现存) + 1` 而不是 `count + 1`（删过行会撞号） |
| 3 | 汇总重算要**自底向上**且**包含被软删的行之外的全部现存行** | 只重算「本次替换的那几行」是最容易犯的错 —— 别的行的汇总会留在旧值上 |

## 验收标准

- [ ] `pytest tests/modules/test_cutting_service.py -q` 全过（b-1 的 24 例不许回归）
- [ ] `alembic check` 零漂移
- [ ] 闸门 1-5 全绿

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-C01b2-01 | `patch` 版本不符 → `10003` | `04 §2` 乐观锁 | |
| TC-C01b2-02 | `patch` 只改表头，**不动**三层明细 | §6 | |
| TC-C01b2-03 | 非 `DRAFT` 状态 `patch` → `30001` | C14 | |
| TC-C01b2-04 | `delete` 三层**全部**软删，且无物理删 | AGENTS §2.1 | |
| TC-C01b2-05 | 删单后 `get` → 不存在 | INV-7 | |
| TC-C01b2-06 | `PUT /lines` 替换后汇总重算正确 | C6 | |
| TC-C01b2-07 | `PUT /size-lines` 后**颜色/行/头**三级汇总都对 | C6 | |
| TC-C01b2-08 | `size_line_no` 由服务端分配，前端传了被拒 | §7 | |
| TC-C01b2-09 | 同尺码多行仍合法（分配不撞号） | C30 | |
| TC-C01b2-10 | 比例完全未配 → `20006` | C19① | |
| TC-C01b2-11 | 比例**部分**缺配 → 不拦，`missing_size_codes` 提示 | C19② | |
| TC-C01b2-12 | 比例含款号没有的尺码 → `20007` | C19③ | |
| TC-C01b2-13 | `suggest-lines` 写 `ratio_snapshot`，**不动**比例主数据 | C29 | |
| TC-C01b2-14 | 切模式**未确认** → 拒；已确认 → 清 `hands` 并留痕 | C27 | |
| TC-C01b2-15 | `fabric_qty > available_qty` → `40006` | C38 | |
| TC-C01b2-16 | **并发**改同一单 → 只有一个成功，另一个 `10003` | `10 §5.4` | |

## 实际改动

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `backend/alembic/versions/0011_cutting_partial_uniques.py` | +89 | 三个唯一键 → 部分唯一索引 |
| `backend/app/modules/cutting/service.py` | +520 | `patch`/`delete`/三条 PUT/`suggest`/`entry-mode` + 条件 UPDATE 乐观锁 |
| `backend/app/modules/cutting/schemas.py` | +150 | 6 个入参/出参模型 |
| `backend/app/modules/cutting/repository.py` | +95 | 三层带锁查询 |
| `backend/app/modules/cutting/models.py` | +40 | 三处 `UniqueConstraint` → `Index` |
| `backend/tests/modules/test_cutting_service2.py` | +700 | 23 例 |
| `backend/tests/factories/cutting.py` | +278 | 夹具工厂（新建） |
| `backend/tests/conftest.py` | +35 | `cutting_world` / `cutting_world_persisted` |
| `backend/tests/modules/test_stock_basis.py` | +18 | `_material()` 幂等 + 补 flush |
| `backend/tests/modules/test_cutting_tables.py` | +22 | TC-C01a-09 加「必须部分索引」断言 |
| `docs/04` / `docs/12` / 任务卡 | +130 | 见 docs/12 的 0077 |

**手写行数 ≈ 2070**，按 `AGENTS §7.1` 拆两个提交：

| 提交 | 内容 | 为什么这么切 |
| --- | --- | --- |
| `feat(cut)` | 迁移 0011 + service + schemas + repository + models + 23 例 + 夹具 | 迁移与模型必须同提交（拆开 `alembic check` 报漂移）；且 TC-C01a-09 的新断言要求库里已是部分索引才绿 |
| `docs(cut)` | `04 §7.7.2` 同步 + docs/12 + 任务卡 | 纯文档 |

## 抓到的四类缺陷

| # | 缺陷 | 怎么发现的 |
| --- | --- | --- |
| 1 | **`04 §7.7.2` 的三个唯一键是普通 UNIQUE**，与 §6 的全量替换语义不可兼得 | 写测试真跑 → `duplicate key ... (doc_id, line_no)` |
| 2 | **我自己写的假乐观锁**（先读后写，`commit` 发的 UPDATE 不带 version 谓词） | 并发用例断言「只应 1 个 ok」，实际 2 个 |
| 3 | `_recalc_all` 读**进入前**加载的 `order.lines` → 表头汇总变 0 | `test_fabric_qty_at_available_is_allowed` 拿到 0 |
| 4 | `get_lines_for_update` 没滤软删 → 已删行参与求和 | 同上，表头耗料 96+100=196 |

## 踩到的坑

| # | 坑 | 处理 |
| --- | --- | --- |
| 1 | 软删后插新行撞部分唯一索引 | 必须**先 flush 软删、再插**：同一次 flush 里 PG 按执行顺序判索引，INSERT 先跑就撞 |
| 2 | session 是 `autoflush=False`，SELECT 看不到未落库的 INSERT | `_recalc_all` 开头加 flush |
| 3 | 夹具放工厂模块 → ruff F811 逐个函数报 | 夹具本体放 `conftest.py`，工厂模块只留建造逻辑 |
| 4 | 并发用例的世界在未提交事务里 → 并发任务**看不见** | 加 `cutting_world_persisted`（真提交版），款号/缸号必须唯一 |
| 5 | 真提交用例的清理用软删 → 行仍在，`select(Style) == []` 断言红 | 清理改用**迁移账号物理删**（仓库既定做法，见 `conftest.ddl_session`） |
| 6 | 持久化夹具返回 ORM 实体 → GC 时 `ResourceWarning: unclosed socket`，pytest 把 unraisable warning 变失败 | 夹具返回**纯 id**，`wid()` 同时支持实体与 id |
| 7 | `style_sizes.size_name` NOT NULL、`style_color_size_ratios.style_id` NOT NULL | 工厂补齐；后者印证「外键在 id 上、业务键冗余」 |
| 8 | `uq_style_color_size_ratios` 也是**硬**唯一键 | 登记见下（同 0011 的病因，但那张表是主数据、本卡不建） |

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| — | `uq_style_color_size_ratios`（0005 建的）也是**硬**唯一索引，与「比例是主数据、要能停用后重配」冲突 | T-BASE-002 的后续卡（与 0011 同一病因，0011 未动它是因为本卡不建该表） |
| — | 状态机任何动作、锁批 | T-CUT-001b-3 |
| — | 接口与页面 | T-CUT-001c |