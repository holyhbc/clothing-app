# T-PIECE-001：计件两表建表 + DDL 搬运（`piecework_logs` / `piecework_bindings`）

| 项 | 内容 |
| --- | --- |
| 模块 | piecework |
| 负责人 | backend-dev |
| 状态 | `done` |
| 优先级 | P1（P2 计件的**第 1 张卡**，REQ-000 §7 第 3 周） |
| 依赖 | T-BUND-001（`bundles` 已建，迁移 0014）、T-BASE-004（`users` / `workshops` / `operations`） |
| 被依赖 | T-PIECE-002（`piecework_log_type` + service）、工资结算卡 |
| 关联设计 | [`modules/04-计件.md`](../modules/04-计件.md) §3.1/§3.2/§3.3、§7、§11；[`modules/05-工资.md`](../modules/05-工资.md) §3.2 |
| 关联规范 | `docs/04 §7.1`（计件流水）、`docs/07 §2.1`（员工工序绑定）、`docs/10 §5.1`（并发） |
| 关联 ADR | ADR-0016（一码一手）、ADR-0017（按布批）、ADR-0009（单价按工序） |
| 估算 | 0.5d |

## 目标

把 `piecework_logs`（计件流水，append-only）与 `piecework_bindings`（员工工序绑定）
两张表建出来，并让 `docs/04 §7` 的 DDL 与模型**逐字对齐**。

## 为什么先建表而不是先写 service

- **DDL 已定稿**：`04 §7.1` 是全仓少数几张**完整可执行**的 DDL（不像 §7.4/§7.8
  那样用「公共字段见 §2」作占位），照抄即可，不存在设计风险；
- **待决问题不阻塞建表**：Q-PW03（返工件）、Q-PW12（剩余手提醒）、
  Q-PW13（`bundle_qty` 同名不同义）全是 **service 层口径**问题，
  表结构不依赖它们（见下方「本卡刻意不定的三件事」）；
- 有先例：T-BASE-008「库存与裁剪缺表 DDL 搬运」、T-BASE-009「P1 缺表建表」。

## 范围

**要做**：
- [x] `docs/04 §7.1` 补 `scanned_at` / `device_id` / `idempotency_key` 三列
      （`04-计件 §3.1` 标了 `*`，属「本模块落地补充列」）+ 补 `REFERENCES` 子句
- [x] `docs/04 §7` 新增一小节承载 `piecework_bindings`
      （⚠️ 它此前**只在 `07 §2.1` 与 `04-计件 §3.2` 里**，`04 §7` 没有 DDL ——
      而 TD-01 守卫要求「已建的表必须在 `04 §7` 有 DDL」）
- [x] 迁移 `0019`：`piecework_log_type` 枚举 + 两张表 + 索引 + 授权收口
- [x] `app/modules/piecework/models.py` + 包 `__init__`
- [x] 从 `tests/modules/test_docs_ddl_sync.py::P1_PENDING_TABLES` 删掉
      `piecework_logs`（表建了就该删，否则 TD-04 的合法集合被污染）
- [x] 表结构测试（列 / 约束 / 索引 / 授权 / CHECK 真的拦得住）

**不做**：
- **不写 service / router / 权限**（下一张卡）
- **不建 `payroll_periods` / `piecework_settlements`**（工资阶段，`05-工资 §3`）
- 不动 `bundles.counted_*` 的回写逻辑（那是计件 service 的事）

## 本卡刻意不定的三件事（留给下一卡，且必须在那之前定）

| # | 问题 | 为什么建表不需要它 |
| --- | --- | --- |
| 1 | **Q-PW03 返工件怎么计** | 只影响 `SUPPLEMENT` 流水的**服务层**走向；表上只是一个 `log_type` 取值 |
| 2 | **Q-PW13 `bundle_qty` 同名不同义** | `piecework_logs` 里**没有** `bundle_qty` 这一列 —— 件数是 `qty`，单价是 `unit_price`，两个名字都不与 `bundles.bundle_qty` / `style_operations.bundle_qty` 撞 |
| 3 | **`qty DEFAULT 1` 的口径风险** | 列上照 04 §7.1 保留 `DEFAULT 1` 作为兜底，但**服务层必须显式写 `bundles.bundle_qty`**。建表卡无法保证这点，只能在服务层卡里用测试断言（TC-39）钉死 |

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `backend/alembic/versions/0019_piecework_tables.py` | 新增 | 枚举 + 两表 + 索引 + 授权 |
| `backend/app/modules/piecework/models.py` | 新增 | 两张表的 ORM |
| `backend/app/modules/piecework/__init__.py` | 新增 | 包入口（ADR-0031） |
| `backend/tests/modules/test_piecework_tables.py` | 新增 | 表结构测试 |
| `backend/tests/modules/test_docs_ddl_sync.py` | 修改 | 删白名单里的 `piecework_logs` |
| `docs/04-数据库规范.md` | 修改 | §7.1 补列/补 FK；新增 `piecework_bindings` 小节 |

## 实现要点

- [x] **`payroll_id` 只建列、不建 FK**：`piecework_settlements` 是工资阶段的表
      （`05-工资 §3.2`），本卡还不存在，建 FK 会让迁移直接失败。
      ⚠️ 必须在 `04 §7.1` 注明「FK 留给工资卡」，否则读的人会以为漏建了。
- [x] **`bundle_id` 建 FK**：`04-计件` BR-PW-30 明确要求 `REFERENCES bundles(id)
      ON DELETE RESTRICT`（BR-PW-30 的三条理由里第一条就是「`bundle_no` 是可变
      字符串格式，FK 不会被格式变更打断」）。
- [x] **`reversal_of_id` 自引用 FK**：`ON DELETE RESTRICT` —— 红冲行指向原流水，
      原流水被删会让红冲失去依据。
- [x] **枚举只建不加值**：AGENTS §5「枚举一律 PG enum + 迁移里新增值（禁止改
      enum 值本身）」。`SCAN/MANUAL/REVERSAL/SUPPLEMENT` 四个值一次建全，
      后续新增走 `ALTER TYPE ... ADD VALUE`。
- [x] **公共字段不带 FK**：`created_by` / `updated_by` 是 UUID 但**不建外键** ——
      与 `bundling_*` 四表同款（人事离职后要能改 user 行，建 FK 会卡死）。
- [x] **授权收口**：`piecework_logs` 是软删表 → `REVOKE DELETE`；
      `piecework_bindings` 是可解绑的软删表 → 同样 `REVOKE DELETE`。

## 验收标准

- [x] `alembic upgrade head` → `downgrade -1` → `upgrade head` 往返（闸门 4）
- [x] `alembic check` 零漂移（模型与迁移一致）
- [x] TD-01/02/04/05 四条 DDL 同步守卫对这两张表**真比对**（不在白名单里）
- [x] 表结构测试覆盖：列齐 / 幂等唯一键 / 符号 CHECK / 整件 CHECK /
      `bundle_id` FK 存在 / `payroll_id` **无** FK / 枚举四值 / 授权收口
- [x] 闸门 1 lint、闸门 2 typecheck、闸门 3 单测（**1022 passed / 91.44%**，基线 992）

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-PW-T01 | 两表存在且列与 `04 §7` 逐字一致 | 通过 | ✅ |
| TC-PW-T02 | `piecework_log_type` 四值 | SCAN/MANUAL/REVERSAL/SUPPLEMENT | ✅ |
| TC-PW-T03 | `uq_piecework_logs_idem` 挡住重复扫码 | 唯一冲突 | ✅ |
| TC-PW-T04 | `ck_piecework_logs_sign` 拦「非红冲行为负」**与「红冲为正」** | CHECK 失败 | ✅ 反验过：删掉 CHECK → 4 条全红 |
| TC-PW-T05 | `ck_piecework_logs_whole` 拦 28.5 件、**放行整数件** | CHECK 失败 / 放行 | ✅ 破坏 + 反证各一条 |
| TC-PW-T06 | `bundle_id` FK 指向 `bundles.id` 且 RESTRICT，**且真拦未知码** | 存在且拒写 | ✅ 只读约束定义证明不了「真拦」，补了反证 |
| TC-PW-T07 | `payroll_id` **无** FK（工资表未建） | `pg_constraint` 里 0 条 | ✅ |
| TC-PW-T08 | `erp_app` 对两张表**无 DELETE** | 权限拒绝 | ✅ |
| TC-PW-T09 | `downgrade 0018` 真跑 → 两表与枚举消失 → upgrade 还原 | 通过 | ✅ |
| TC-PW-T10/11 | 两表已移出 `scope.PENDING_TABLES` 且仍在 `SCOPE_SPECS`；绑定表 `SELF` 列是 `user_id` | 通过 | ✅ |
| TC-PW-B01~B06 | 绑定表：唯一键含 `work_date`、`bundle_no` 无 FK、`is_active` 解绑、SELF 列 | 通过 | ✅ |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `backend/app/modules/piecework/models.py` | +245 | 两张表的 ORM |
| `backend/app/modules/piecework/__init__.py` | +44 | 包入口（ADR-0031；`alembic/env.py` 靠它遍历注册模型） |
| `backend/alembic/versions/0019_piecework_tables.py` | +297 | 枚举 + 两表 + 9 个索引 + 授权收口 |
| `backend/tests/factories/piecework.py` | +231 | 夹具 + `information_schema` 目录查询 |
| `backend/tests/modules/test_piecework_tables.py` | +298 | 流水表 29 条中的 18 条 |
| `backend/tests/modules/test_piecework_bindings_table.py` | +142 | 绑定表 11 条 |
| `backend/app/core/scope.py` | +15 −2 | `SCOPE_SPECS` 补绑定表；`PENDING_TABLES` 移出 `piecework_logs` |
| `backend/tests/modules/test_docs_ddl_sync.py` | +2 −2 | 白名单移出 `piecework_logs` |
| `docs/04-数据库规范.md` | +56 −3 | §7.1 补三列 + `REFERENCES` + 外键取舍表；新增 §7.1.1 |

**行数构成**：手写新增约 +1330 / 删除 −12，**无生成物**。⚠️ 超出 1200 行软上限，
构成与拆分理由（AGENTS §7.1 第 2 条）：

- **+231 的夹具**（`tests/factories/piecework.py`）与 **+440 的两个测试文件**合计 671 行
  是**测试代码**，其中约 210 行是注释与 docstring —— 每条都在说「这条守卫挡的是什么」
  与「为什么不能反过来写」，而不是复述断言；
- **DDL 搬运占 56 行文档**：不搬的话 TD-01 守卫在建表那一刻就会红
  （`piecework_bindings` 此前只在 `07 §2.1` 与 `04-计件 §3.2` 里，`04 §7` 没有）；
- 单个文件最大 298 行，**未超 ADR-0030 的 400 行硬线**（最初 495 行的
  `test_piecework_tables.py` 已拆成「夹具 + 流水表 + 绑定表」三个文件）。

**提交记录**：
- `<hash>` feat(piecework): 计件两表建表 + DDL 搬运（T-PIECE-001）

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| `payroll_id` 的 FK 推迟到工资卡 | 本卡「不做」清单；工资建表卡开工时补 |
| Q-PW03/12/13/14/15 五项待决 | `docs/modules/04-计件.md` §12（**下一卡开工前必须有答案**） |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。

## 变更记录

| 日期 | 变更内容 | 操作人 |
| --- | --- | --- |
| 2026-10-12 | 初版：P2 第 1 张卡 | AI |
| 2026-10-12 | 置 `done`。两处计划外发现：① `piecework_bindings` 从未登记 `SCOPE_SPECS`，被 `test_scope.py::test_scope_specs_declares_workshop_resources` 在**全量跑**时逼出来（单跑该用例是绿的 —— 它只 import `auth.models`，靠别的用例先注册模型）；② `piecework_logs` 还在 `scope.PENDING_TABLES` 里，而它 0005 就已「前瞻登记」进 `SCOPE_SPECS` —— 建表即须移出 | AI |