# T-BASE-001：组织、字典与工序主数据（含真删与内置库 seed）

| 项 | 内容 |
| --- | --- |
| 模块 | base |
| 负责人 | AI |
| 状态 | `todo` |
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
  - **补 T-AUTH-001 遗留**：`ALTER TABLE users ADD CONSTRAINT fk_users_workshops`（04 §1 外键强制）
  - **索引**：04 §5.1 的 `idx_colors_trgm` / `idx_sizes_trgm` GIN；`uq_colors_code` / `uq_sizes_code` / `uq_size_groups_name` / `uq_size_group_items` / `idx_size_group_items_sort_order` / `idx_sizes_active_sort_order` / `idx_size_groups_size_class` / `idx_operations_workshop_id_is_active` / `idx_styles_*` 暂缓（T-BASE-002）
  - **权限（ADR-0025）**：`GRANT DELETE ON colors, sizes, size_groups, size_group_items TO erp_app;`，其余表仍只 `SELECT/INSERT/UPDATE`
- [ ] `app/modules/base/{models,schemas,repository,service,router}.py`：9 个资源统一形状（§4.4）
  - 写权限差异：`operations` → `base:operation:manage`；`product_categories` → `base:category:manage`；其余 → `base:create/update/disable/delete`
  - `disable` **必填 `reason`**（缺 → `10002`）；`delete` 两分支（引用计数 0 → 真删；>0 → `20003` + `details.ref_count` + `references[]`）
  - 删除走**条件 DELETE**（`NOT EXISTS` 引用判定），`rowcount=0` → `20003`
  - `size_groups` 真删时**级联删 `size_group_items`** 并回传条数
  - `operations.operation_no` **不可改**；被 `style_operations`/`operation_rates`/`piecework_logs` 引用 → `20003`
  - 所有变更写 `document_logs`（`doc_type` 用 `Color`/`Size`/`SizeGroup`/`Operation`/`Workshop`/`ProductCategory`…）
- [ ] `app/cli/seed_baseline.py`（改）：字典 seed —— **16 色基础色卡 + 2 个码表 + 6 个尺码 + 8 条码表明细 + 6 个商品分类**（modules/01 §3.6.4 定稿清单）；**墓碑跳过**（ADR-0025 §决策 3）
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
- [ ] seed 连跑 3 次行数一致；内置清单与 modules/01 §3.6.4 逐条一致（**16 色 / 2 码表 / 6 尺码 / 8 明细 / 6 分类**）
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
| TC-B27 | 候选接口 `size=21` | 截断为 20 | |
| TC-B28 | `offset > 10000` | `10001` | |
| TC-B29 | `sort_by=unknown_field` | `10001`（不 500、不拼 SQL） | |
| TC-B30 | 导出行数 vs 列表 `total`（同筛选） | 相等；无 `base:export` → `12001` | |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| | +0 / -0 | |

**提交记录**：
- `<hash>` feat(base): 组织、字典与工序主数据（含真删与内置库 seed） …

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| W5 | ADR-0025 落地需回写 04 §6.1/§6.2.1/§7.4 | 归档阶段 |
| W3 | 基础资料主数据 DDL 需回写 04 §7 | 归档阶段 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。
