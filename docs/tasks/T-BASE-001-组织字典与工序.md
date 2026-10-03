# T-BASE-001：组织、字典与工序主数据（含真删与内置库 seed）

| 项 | 内容 |
| --- | --- |
| 模块 | base |
| 负责人 | AI |
| 状态 | **`done`**（2026-10-03；5 道闸门宿主机与容器内全绿，369 测试 / 覆盖率 92.2%，`alembic check` 无漂移） |
| 优先级 | P0 |
| 依赖 | T-INFRA-004, T-AUTH-002 |
| 被依赖 | T-BASE-002, T-WEB-005 |
| 关联设计 | [docs/modules/00-P0地基.设计.md](../modules/00-P0地基.设计.md) §2.1 组 C、§4.4、§5（CC-2/CC-3）、§9（TC-B01~B05） |
| 关联 ADR | **ADR-0025**（字典真删权限口径 + 墓碑）、ADR-0020（商品分类） |
| 估算 | 1d |

## 目标

交付 9 个主数据资源的完整 CRUD + 候选搜索 + 导出，并让**「未被引用的字典项可真删、被引用只能停用」**这条业务规则在生产库权限下真正可用。

## 范围

**要做**：
- [ ] `alembic/versions/0003_base_org_dict.py`：组 C 十张表
  - `workshops` / `workshop_groups` / `warehouses` / `uom_units`
  - `product_categories`（内置 6 类，seed）
  - `colors` / `sizes`（+ `CREATE TYPE size_class`）/ `size_groups` / `size_group_items`
  - `operations`（04 §7.8.1 DDL）
  - **补 T-AUTH-001 遗留（两条 FK，缺一不可）**：
    - `ALTER TABLE users ADD CONSTRAINT fk_users_workshops`（04 §1 外键强制）
    - `ALTER TABLE role_workshops ADD CONSTRAINT fk_role_workshops_workshops`
      （T-AUTH-001 建该表时 `workshops` 还不存在，故未加 FK；**不加则数据范围过滤可能读到不存在的车间**）
  - **索引**：04 §5.1 的 `idx_colors_trgm` / `idx_sizes_trgm` GIN；`uq_colors_code` / `uq_sizes_code` / `uq_size_groups_name` / `uq_size_group_items` / `idx_size_group_items_sort_order` / `idx_sizes_active_sort_order` / `idx_size_groups_size_class` / `idx_operations_workshop_id_is_active` / `idx_styles_*` 暂缓（T-BASE-002）
  - **权限（ADR-0025）**：`GRANT DELETE ON colors, sizes, size_groups, size_group_items TO erp_app;`，其余表仍只 `SELECT/INSERT/UPDATE`
- [ ] `app/modules/base/{models,schemas,repository,service,router}.py`：9 个资源统一形状（§4.4）
  - 写权限差异：`operations` → `base:operation:manage`；`product_categories` → `base:category:manage`；其余 → `base:create/update/disable/delete`
  - `disable` **必填 `reason`**（缺 → `10002`）；`delete` 两分支（引用计数 0 → 真删；>0 → `20003` + `details.ref_count` + `references[]`）
  - 删除走**条件 DELETE**（`NOT EXISTS` 引用判定），`rowcount=0` → `20003`
  - `size_groups` 真删时**级联删 `size_group_items`** 并回传条数
  - `operations.operation_no` **不可改**；被 `style_operations`/`operation_rates`/`piecework_logs` 引用 → `20003`
  - 所有变更写 `document_logs`（`doc_type` 用 `Color`/`Size`/`SizeGroup`/`Operation`/`Workshop`/`ProductCategory`…）
- [ ] `app/cli/seed_baseline.py`（改）：字典 seed —— **16 色基础色卡 + 2 个码表 + 8 个尺码 + 8 条码表明细 + 6 个商品分类**（尺码 8 行而非 6 行，见 **ADR-0027**）（modules/01 §3.6.4 定稿清单）；**墓碑跳过**（ADR-0025 §决策 3）
- [ ] `app/cli/restore_builtin.py`（改）：显式恢复内置库并写 `action='RESTORE'` 解除墓碑
- [ ] `/api/v1/{resource}/exports`：9 个资源的 `.xlsx` 导出（`openpyxl` 流式 + 表头冻结 + 自动筛选），**与列表共用同一 service 方法**（07 §3.2 铁律 3）；权限 `base:export` **且** `system:export:manage`
- [ ] 测试：`test_base_dict_service.py`、`test_base_dict_router.py`

**不做**：
- 不建 `customers`/`styles`/`style_operations`/`operation_rates`（T-BASE-002）
- 不做 Excel **导入**（三步式，P1 起按 05 §9.3 逐模块补）
- 不做「款号一键带出尺码」接口（T-BASE-002）

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `backend/alembic/versions/0003_base_org_dict.py` | 新增 | 10 表 + FK + 索引 + GRANT DELETE |
| `backend/app/modules/base/__init__.py` | 新增 | 空 |
| `backend/app/modules/base/models.py` | 新增 | 组 C 模型 |
| `backend/app/modules/base/schemas.py` | 新增 | 9 资源 Schema |
| `backend/app/modules/base/repository.py` | 新增 | 只读查询（含 trgm 候选） |
| `backend/app/modules/base/service.py` | 新增 | CRUD + 真删 + 停用 + 导出 |
| `backend/app/modules/base/router.py` | 新增 | 9 资源 × (list/options/create/patch/disable/delete/exports) |
| `backend/app/cli/seed_baseline.py` | 修改 | 字典 seed + 墓碑跳过 |
| `backend/app/cli/restore_builtin.py` | 修改 | 恢复 + RESTORE 记录 |
| `backend/tests/modules/test_base_dict_service.py` | 新增 | service 测试 |
| `backend/tests/modules/test_base_dict_router.py` | 新增 | 接口 + 权限矩阵 |
| `backend/tests/integration/test_no_dangling_refs.py` | 新增 | 悬空引用巡检（ADR-0025） |

## 实现要点（必读规范）

- [ ] 遵守 docs/04 §7.4（字典表 DDL **逐字照抄**）、§5.1（trgm GIN，`EXPLAIN` 必须 Bitmap Index Scan）、§6.1/§6.2.1（**白名单 GRANT DELETE 例外**）、§6.2（迁移规范）
- [ ] 遵守 docs/modules/01 §2 R25/R26/R27（内置库 + 自由新增 + 删除规则）、§3.6.4（seed 清单定稿）、§4（三态 + 变更日志）、§5.2（幂等规则）、§7（并发表）
- [ ] 遵守 docs/05 §4（错误码只用已登记：`20003`/`10002`/`10003`/`10004`）、§9.1（导出）、§9.5（候选接口）
- [ ] 遵守 docs/03 §1.3（repository 只查不判断业务）、§1.5（反例：禁止先查后插、禁止 Router 查库）

## 验收标准

- [ ] **闸门 4 通过**
- [ ] 9 个资源的 list/options/create/patch/disable/delete/exports 全部通过接口测试
- [ ] TC-B03：字典删除两分支正确（引用 0 → 真删，行消失 + `document_logs` 留痕；引用 >0 → `20003` + `details.ref_count`）
- [ ] TC-B21：真删内置色 `WHT` → 重跑 `seed_baseline` → **不复活**；TC-B22：`restore_builtin` → 恢复且写 `RESTORE`
- [ ] TC-B24：`erp_app` 对 `workshops` 执行 `DELETE` → `InsufficientPrivilege`（白名单生效）
- [ ] `tests/integration/test_no_dangling_refs.py` 全库巡检 **0 行**
- [ ] CC-2（20 并发建同色码 → 恰好 1 行）、CC-3（删 vs 引用真并发 → 无悬空引用）通过
- [ ] 候选搜索 `EXPLAIN` 命中 `idx_colors_trgm` / `idx_sizes_trgm`（写测试断言 SQL 形态）
- [ ] seed 连跑 3 次行数一致；内置清单与 modules/01 §3.6.4 逐条一致（**16 色 / 2 码表 / 8 尺码 / 8 明细 / 6 分类**）
- [ ] `app/modules/base/service.py` 覆盖 ≥ 90%；新增行 100%
- [ ] 闸门 1/2/3/5 全绿

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-B01 | 用户改名 / 停用某内置项后重跑 seed | 名称与 `is_active` **不被覆盖** | |
| TC-B02 | `restore_builtin` | 只补缺失行，不动已有行 | |
| TC-B03 | 字典删除两分支 | 见验收 | |
| TC-B04 | 停用未填 `reason` | `10002`，`document_logs` 无该记录 | |
| TC-B05 | 停用尺码后查历史款号详情 | 仍能显示名称；新建款号选它 → `20001` | |
| TC-B21/22 | 墓碑与恢复 | 见验收 | |
| TC-B23 | `size_groups` 真删 | 级联删明细并回传条数 + 日志 | |
| TC-B24 | 非白名单表 DELETE | 权限拒绝 | |
| TC-B25 | `document_logs` 的 UPDATE/DELETE | 权限拒绝 | |
| TC-B26 | `size_groups` 删除 vs 建款引用并发 | 无悬空引用 | |
| TC-B31 | **补 FK 后**：`pg_constraint` 里存在 `fk_users_workshops` 与 `fk_role_workshops_workshops` | 两条都在（T-AUTH-001 遗留项验收） | |
| TC-B27 | 候选接口 `size=21` | 截断为 20 | |
| TC-B28 | `offset > 10000` | `10001` | |
| TC-B29 | `sort_by=unknown_field` | `10001`（不 500、不拼 SQL） | |
| TC-B30 | 导出行数 vs 列表 `total`（同筛选） | 相等；无 `base:export` → `12001` | |

## 实际改动（完成后回填）

| 文件 | 说明 |
| --- | --- |
| `alembic/versions/0004_base_org_dict.py` | 10 表 + `CREATE TYPE size_class` + trgm GIN + 8 个软删索引 + **补 T-AUTH 遗留的两条 FK** + ADR-0025 的 4 表 `GRANT DELETE` 白名单 |
| `app/modules/base/models.py` | 10 个模型，与迁移逐字对齐（含 9 处表注释） |
| `app/modules/base/resources.py` | **声明式资源注册表**：九个资源的差异集中声明一次 |
| `app/modules/base/schemas.py` | 写入侧逐资源 `Create`/`Patch`（`extra="forbid"`）+ 通用 `DictOut` |
| `app/modules/base/repository.py` | 只读查询（docs/03 §1.3 不判断业务）；候选谓词与 trgm 索引表达式**逐字相同** |
| `app/modules/base/service.py` | CRUD + 真删两分支 + 停用必填 reason + 审计日志 |
| `app/modules/base/router.py` | 按注册表生成 **45 条具体路径**（9 资源 × 5） |
| `app/core/excel.py` | openpyxl `write_only` 流式导出（表头冻结 + 自动筛选） |
| `app/core/db.py` | 新增 `unit_of_work`（全项目唯一事务入口） |
| `app/cli/seed_dicts.py` | 字典内置库（16 色 / 8 尺码 / 2 码表 / 8 明细 / 6 分类）+ **墓碑判定** |
| `tests/integration/test_no_dangling_refs.py` | CC-2 / CC-3 / CC-6 真并发 + 悬空引用巡检 |
| `scripts/reset-test-db.sh`、`scripts/gate.sh` | 重置测试库；闸门脚本消除静默失败并共用镜像 |

## 关键设计决策

| # | 决策 | 理由 |
| --- | --- | --- |
| 1 | **新增 ADR-0027**：`uq_sizes_code` → `UNIQUE(size_code, size_class)` | 内置两个码表共用 `L`/`XL`，单列唯一存不下。业务方选定，内置尺码 6 → 8 行。04 是 DDL 权威，改它必须留痕 |
| 2 | 基础资料**数据范围固定 FACTORY**（§4.4） | 颜色/尺码/车间是全厂共享主数据。若跟着用户 `data_scope` 走，SELF 范围的用户打开颜色下拉会是**空的** |
| 3 | 45 条**具体路径**而非 `/{resource_key}` 通配 | 通配会遮蔽后续所有顶层资源（`/styles` 会被抢走，FastAPI 靠注册顺序决定谁生效），且 OpenAPI 里前端发现不了 `/api/v1/colors` |
| 4 | 删除判定写成**一条 SQL 的条件 DELETE** | 消除"先查后删"的竞态（CC-3）。实测真正兜底的是外键，但条件 DELETE 提供可读的 20003 |
| 5 | 字典 seed 按 `document_logs` 的 DELETE/RESTORE 判**墓碑** | `ON CONFLICT DO NOTHING` 只在唯一键冲突时跳过；行被**物理删除**后没有冲突，不判墓碑就会复活（违反 ADR-0025） |
| 6 | `unit_of_work` 放 `core/db.py` 而非模块内 | base 模块写 `session.begin()` 时 13 个用例全报 "transaction already begun"——与 T-AUTH-002 同一个坑，说明它必须单点收口 |
| 7 | 事务助手用 savepoint + `commit()` 而非 `begin()` | autobegin 之后 `begin()` 会抛；savepoint 在既有事务里安全，测试侧只释放 savepoint |
| 8 | `size_class` 用真 PG enum 而非 `String(16)` | 与迁移里的 enum 类型对不上会导致 `alembic check` 长期报漂移，且枚举值失去数据库层约束 |

## 实测抓到的 9 个真缺陷

| # | 缺陷 | 现象 | 修法 |
| --- | --- | --- | --- |
| 1 | **`document_logs` 没有 ORM 模型** | `alembic check` 报 `remove_table` —— 任何人执行 `--autogenerate` 都会拿到 `drop_table('document_logs')`，一次误操作抹掉全厂审计日志 | 补模型 |
| 2 | **`BaseModel.remark` 类型漂移** | 无显式类型被推断成 VARCHAR，与 04 §2「remark text」冲突 | 显式 `Text()` |
| 3 | **模型有 FK、迁移没有** | `users.workshop_id` 声明 FK 而 `workshops` 不存在 → **任何**涉及 users 的 ORM 查询抛 `NoReferencedTableError`，登录功能打死 | 先去掉，本卡补迁移 + 模型 |
| 4 | **`data_scope` 是 String 列** | 重新查出来是 `str`，`.value` 抛 AttributeError | 统一 `DataScope(...)` 包一层 |
| 5 | **路由注册顺序** | `/exports`、`/options` 排在 `/{code}` 之后 → 被当成"编码叫 exports 的那一行" → 404 | 两者前移 |
| 6 | **写后重读拿到旧值** | `expire_on_commit=False` + identity map 使 UPDATE 后 `get_one` 返回旧实体。接口 200 但界面没变 | 每次写后 `refresh` |
| 7 | **字典真删后 seed 复活** | `ON CONFLICT DO NOTHING` 只在唯一键冲突时跳过 | 按 `document_logs` 判墓碑 |
| 8 | **restore 顺序反了** | 先 seed 后清墓碑 → 本次 seed 看到的还是墓碑 → 什么都没恢复却留下一条 RESTORE，"记录说恢复了、数据没回来" | 先清墓碑再 seed |
| 9 | **`ref_count_expression` 用了 PG 没有的 `one()`** | 一直没被执行到（colors 无引用检查器、sizes 在检查阶段就抛错），CC-3 的"先删后引用"用例第一次真正跑到它 | `select(literal(1))` |

另修两个**并发下的错误码错误**：
- 并发双删时第二个请求返回 `20003`「刚被引用」，实际是「行已被别人删掉」→ 改为先判存在性，给 `20001`
- FK `ON DELETE RESTRICT` 在并发下于 savepoint 释放时抛 `IntegrityError`，最初只 try 了 `execute`，异常穿透成 **500** → 捕获范围改为整个事务

## 测试有效性（反证）

本卡做了两轮**反向验证**，把"测试通过"和"测试真的在守"分开：

| 验证 | 做法 | 结果 |
| --- | --- | --- |
| `data_scope` 守卫 | 把 `DataScope(x).value` 回退成 `x.value` | 16 个用例转红 ✅ |
| **CC-3 条件 DELETE** | 去掉 `NOT EXISTS` 条件 | **仍然通过** ❌ → 说明竞态没被制造出来 |
| **FK 异常翻译** | 把 `except IntegrityError` 改成别的 | 用例转红 ✅ |
| 外键删除规则 | 声明式断言 `confdeltype` | `sizes→RESTRICT`、`size_groups→CASCADE` ✅ |

**CC-3 那次反证推翻了原设计**：真正保证"无悬空引用"的是**真实外键**，条件 DELETE 只是提前给出可读报错。已把结论写成声明式测试（断言外键的删除规则），而不是去执行一次注定失败的 DELETE（FK 违例会让事务 aborted，ORM 后续查询变成 `MissingGreenlet`，又脆又难读）。

CC-3 也重写成**三段**：引用先落地→删除被拒 20003；删除先落地→后续引用被 FK 拒；真并发（引用方先握住 `FOR KEY SHARE` 行锁，删除方确实被挡住，断言两者不可能都成功）。

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| — | `colors` 的引用检查器仍为空，引用方（`styles` / `style_colors` / `materials`）T-BASE-002 才建。届时必须**同时**补 `RefChecker` **和外键**，否则是裸奔 | T-BASE-002 强制验收项 |
| — | `operations` 的 `RefChecker`（`style_operations` / `operation_rates` / `piecework_logs`）同理 | P1 各模块 |
| — | `DictOut` 用一个大模型承载九个资源，读侧字段是"全 nullable"。写入侧仍逐资源精确 | 已知取舍，P2 视前端需要再拆 |
| L-028 | docs/04 §5.1 的 EXPLAIN 验收口径照字面无法满足，已按实测改写 | **已闭环** |
| W5 | ADR-0025 / ADR-0027 落地需回写 04 §6.1/§6.2.1/§7.4 | 归档阶段 |
| W3 | 基础资料主数据 DDL 需回写 04 §7 | 归档阶段 |
| — | `product_categories` 列名是 `sort`（照 04 §7.11 字面），与其他表 `sort_order` 不一致 | docs/12 待修文档 |

**提交记录**：见 `git log`（分 3 次提交）
