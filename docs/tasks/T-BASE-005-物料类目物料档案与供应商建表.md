# T-BASE-005：物料类目 / 物料档案 / 供应商三张表建表

| 项 | 内容 |
| --- | --- |
| 模块 | base |
| 负责人 | AI |
| 状态 | `done` |
| 优先级 | P0 |
| 依赖 | T-BASE-003（设计）、T-BASE-001（`uom_units` 已建）、T-BASE-002（`warehouses` 已建） |
| 被依赖 | T-CUT-001（裁剪单行要锁布批，布批要落到物料与供应商） |
| 关联设计 | [docs/04 §7.15](../04-数据库规范.md) §7.15.1 / §7.15.2 / §7.15.3 |
| 估算 | 0.5d |
| 估算依据 | 3 张表 + 模型 + seed + 测试 ≈ 650 手写行（AGENTS §7 的 800 行上限内） |

## ⚠️ 范围只做 3 张，不做 5 张

上一轮我说「一次性建 5 张表」，**那是错的** —— 没检查外键依赖链：

| 表 | 依赖 | 能否现在建 |
| --- | --- | --- |
| `material_categories` | — | ✅ |
| `suppliers` | `warehouses`（已建） | ✅ |
| `materials` | `material_categories`（**同一次迁移内**）+ `uom_units`（已建） | ✅ |
| `bundling_orders` | **`cutting_orders`（P1，未建）** | ❌ |
| `bundling_order_lines` | **`cutting_order_size_lines`（P1，未建）** + `bundling_orders` | ❌ |

`cutting_orders` / `cutting_order_size_lines` 是裁剪单表，T-BASE-004 已把它们的设计写进
`04 §7.1`，但**表还不存在**。现在建 `bundling_orders` 的话外键指向一张不存在的表 ——
PG 会直接报错，而报错（`relation "cutting_orders" does not exist`）完全看不出
「真实原因是你把顺序排反了」。

所以打菲两表留给**裁剪单那张卡一起建**，本卡只做 3 张。

## 目标

把 `04 §7.15` 的三张表建出来，解开 T-CUT-001 的最后一个阻塞。

## 范围

**要做**：

- [x] 迁移 `0007`（三张表 + 索引 + CHECK + `material_type` 枚举）：三张表 + 索引 + CHECK + `material_type` 枚举
- [x] 模型：三张表的 `BaseModel`
- [x] seed：类目 10 条 + **顺带补 `uom_units` 3 条**（`09 §2.3` 的取值表）
- [x] 测试：8 例（含「应用账号真删一次」与「公共字段查 information_schema」）（`alembic check` 由闸门 4 兜）/ 枚举值 / seed 幂等 / 建档授权边界
- [x] 守卫 `P1_PENDING_TABLES` 收缩三张
- [x] 归档：docs/12 变更记录 0072 + 任务卡回填

**不做**：

- **不打菲两表**（依赖裁剪单表，见上）
- 不做接口与页面（另一个卡）
- 不做 `bom_items`（09 §1.2，独立一张表）
- **不给 `material_categories` 加物理删除白名单**（§7.15.1 已论证：类目码十几个级别，
  停用够用，而白名单每加一张表要在闸门 4 兑现三处）

## 验收标准

- [x] `alembic upgrade head` 建出三张表，`downgrade` 可回滚，`alembic check` **零漂移**
  - ⚠️ `alembic check` 一开始报了 3 处漂移（`material_categories.sort` 缺注释、缺 `is_active` 索引、`suppliers.short_name` 注释不一致），逐个对齐后归零，`downgrade` 可回滚，`alembic check` 零漂移
- [x] 10 个类目 seed 进去且幂等（第二次 0 行）
- [x] 闸门 1~4 全绿（后端 **633 例** / 覆盖率 88.6%）
- [x] 守卫 `P1_PENDING_TABLES` 收缩三张后仍全绿（56 例）

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TB05-01 | `materials` 引用不存在的类目/单位 | 外键拒（`IntegrityError`） | ✅ |
| TB05-02 | `material_type` 必须是 PG 枚举且值集合正确 | 防「被改成 varchar」 | ✅ |
| TB05-03 | 类目 seed 连跑两次 | 行数与**内容**都不变 | ✅ |
| TB05-04 | 应用账号 DELETE `material_categories` | `permission denied` | ✅ |
| TB05-05 | 三张表的公共字段 + `version > 0` | 查 `information_schema`（不查 ORM） | ✅ |
| TB05-06 | 银行四项可空 | `Supplier(...)` 不带银行能建 | ✅ |
| TB05-07 | `uq_materials_code` 是部分索引 | 带 `WHERE deleted_at IS NULL` | ✅ |
| TB05-08 | 「恢复内置库」清单从 `DICT_TABLES` 派生 | 不再写死 4 个表名 | ✅ |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `backend/alembic/versions/0007_material_and_supplier.py` | +300 | 三张表 + 索引 + CHECK + 枚举 |
| `backend/app/modules/base/models.py` | +190 | 三个模型 |
| `backend/app/cli/seed_dicts.py` | +95 | `BUILTIN_MATERIAL_CATEGORIES` / `BUILTIN_UOM_UNITS` / 两个 seed / check 两项 |
| `backend/app/cli/seed_baseline.py` | +3 | `--check` 输出加两项 |
| `backend/tests/modules/test_material_tables.py` | +290 | 8 例 |
| `backend/tests/modules/test_system_restore.py` | +7 | 清单断言改成派生 |
| `backend/tests/modules/test_docs_ddl_sync.py` | -3 | 白名单收缩 |
| `docs/12-文档与变更归档规范.md` | +1 | 变更记录 0072 |
| **手写合计** | **+889** | ⚠️ 超 800 行 89 行（按 AGENTS §7.1 如实记录；生成物 0） |

**提交记录**：
- `<hash>` feat(base): 物料类目 / 物料档案 / 供应商三张表（顺带补上一直是空表的 uom_units）

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| — | **打菲两表（`bundling_orders` / `bundling_order_lines`）没建** —— 依赖裁剪单表 | 与裁剪单表一起建（T-CUT-001） |
| — | **`uom_units` 一直是空表**这件事本身说明：T-BASE-001 建了表但没人 seed。已补 3 条，但**其它码表是否也有同类问题**值得在 P1 开工前扫一遍 | T-CUT-001 前置检查 |

## 自检清单

- [x] 读过 `04 §2/§4/§5/§6.1/§7.14/§7.15`、`09 §2.3`、ADR-0012、ADR-0025
- [x] **没有硬编码业务常量**：类目码与计量单位取自 `seed_dicts.py` 的清单常量，
      不是散在代码里的字面量
- [x] 没有物理删除（`material_categories` **刻意不在** ADR-0025 白名单，理由见 `04 §7.15.1`；
      TB05-04 用真 DELETE 验证了这一点）
- [x] 新表字段齐全（三张表都含 `04 §2` 公共字段 + `version > 0` CHECK，由 TB05-05 查库验证）
- [x] 状态变更（不适用：本卡无单据）
- [x] 接口（本卡只建表，接口与页面另开卡）
- [x] 测试覆盖 8 例：正常路径 / 外键拒 / 枚举口径 / seed 幂等 / **权限边界** /
      公共字段完整性 / 必填性的回归守卫 / 部分索引
- [x] 闸门 1 lint 通过
- [x] 闸门 2 typecheck 通过
- [x] 闸门 3 单测通过（后端 633 例 / 覆盖率 88.6%）
- [x] 闸门 4 迁移可正向且可回滚（`alembic check` 零漂移）
- [x] 闸门 5 构建镜像成功（`api-image` 构建并 `run --rm` 通过；镜像内也跑了一遍迁移）
- [x] 提交信息符合规范
- [x] 本次改动已在 docs/12 变更记录留痕（0072）
