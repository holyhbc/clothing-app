# T-BASE-006：库存基础表建表（落实 `00 §7` 方案 A）

| 项 | 内容 |
| --- | --- |
| 模块 | base |
| 负责人 | AI |
| 状态 | `done` |
| 优先级 | P0 |
| 依赖 | T-BASE-005、`00 §7` 方案 A |
| 被依赖 | T-CUT-001（`cutting_order_lines.stock_id` → `material_stocks`） |
| 关联设计 | `docs/04 §7.2.0`、`§7.10`、`modules/06 §3` |
| 关联 ADR | ADR-0011（批次实际成本）、ADR-0012（采购用途/门幅/重量）、ADR-0018（WIP） |
| 估算 | 1d |
| 提交范围 | `db` + `test` + `docs` |

## ⚠️ 范围死守（这是本卡最重要的一行）

只建**基础表** + 三个能力（列批次、锁一行、记流水）。
**不做**出入库单 / 调拨 / 盘点 / 成本结转 —— 那些是 P3。

理由写在 `docs/00 §7`：**建表卡的范围必须写死**，否则 P1 会顺手把 P3 的核心做完，
阶段边界就没了。

## 业务决策（2026-10-04）

| # | 问题 | 决策 |
| --- | --- | --- |
| D1 | 同缸同匹但不同价怎么表达 | **不存在这种情况**（业务：「现在不存在同缸不同价，一般同缸都是一样」）→ 唯一键不含 `batch_no`；改价走红冲重入 |
| D2 | 面料批次颜色列叫什么 | `color_code`，**不加外键到 `colors`**（业务：「面料批次颜色和成品衣服颜色不一样，但可以调用颜色表里面的颜色」→ 共用取值但不等价，加外键会在库层面暗示等价） |

## 范围

- [x] 迁移 `0008`：`material_stocks` / `wip_stocks` / `wip_ledger_lines`
- [x] 模型三张表（`WipLedgerLine` 用 `IdMixin`，**不用** `BaseModel`/`AuditMixin`）
- [x] `04 §7.2.0` 与 `modules/06 §3.1` **双向对齐**（建表前发现互有 4 列遗漏 + 1 处命名不一致）
- [x] `SCOPE_SPECS` 登记 `wip_stocks`（守卫抓到）
- [x] 测试 9 例
- [x] 守卫 `P1_PENDING_TABLES` 收缩三张
- [x] 归档：docs/12 变更记录 0074

## 不做

- 不做出入库 / 调拨 / 盘点 / 成本结转（P3）
- 不做 `stock_ledgers` / `stock_ledger_lines` / `stock_reservations` / `finished_goods_stocks`
  （P3；`material_stocks.locked_qty` 的注释提到 `stock_reservations`，但没有外键）
- 不做选批接口（P1 后续卡）
- 不做页面与接口

## 验收标准

- [x] `alembic upgrade head` 建出三张表，`downgrade` 可回滚，`alembic check` **零漂移**
- [x] `dye_lot_no` / `bolt_no` 是 `NOT NULL`（查 `information_schema` 验证）
- [x] 应用账号不能 UPDATE/DELETE `wip_ledger_lines`（真去试）
- [x] 闸门 1~5 全绿

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TB06-01 | `dye_lot_no` / `bolt_no` 必须 `NOT NULL` | 查 `information_schema` | ✅ |
| TB06-02 | 唯一键不含 `batch_no` | 集合 == 4 列 | ✅ |
| TB06-03 | 选批索引是部分索引且含 `stock_qty > locked_qty` | BR-ST-17 | ✅ |
| TB06-04 | `wip_ledger_lines` 无 `deleted_at`/`version`/`updated_*` | append-only | ✅ |
| TB06-05 | 应用账号 UPDATE/DELETE 流水 | `permission denied` | ✅ |
| TB06-06 | 两张软删表有公共字段 + `version > 0` | 查库 | ✅ |
| TB06-07 | `purpose` 是 PG 枚举且值集合正确 | 防「被改成 varchar」 | ✅ |
| TB06-08 | `out_qty > in_qty` 被 CHECK 拒 | `ck_wip_qty` | ✅ |
| TB06-09 | 三张表都在 `Base.metadata` | 定位用 | ✅ |

## 实际改动

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `backend/alembic/versions/0008_stock_basis.py` | +330 | 三张表 + 索引 + 枚举 |
| `backend/app/modules/base/models.py` | +215 | 三个模型 |
| `backend/app/core/scope.py` | +12 | `wip_stocks` 数据范围 |
| `backend/tests/modules/test_stock_basis.py` | +290 | 9 例 |
| `docs/04-数据库规范.md` | +30 | `§7.2.0` 补齐 + `dye_lot_no` NOT NULL |
| `docs/modules/06-库存.md` | +6 | `§3.1`/`§3.2` 与 04 对齐 |
| `docs/12` | +1 | 0074 |

**提交记录**：
- `feat(base)`: 迁移 0008 库存基础表（方案 A）

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| — | 选批接口（`GET /stocks/materials/lots`）未做 | T-CUT-001 内的接口卡 |
| — | `material_stocks` 的数据范围按**仓库**隔离，未登记 `SCOPE_SPECS`（仓库不是车间） | P3 |
| — | `stock_reservations`（锁定明细）未建，`locked_qty` 目前无处同步 | P3（裁剪单要真正锁批时需要） |

## 自检清单

- [x] 读过 `04 §2/§4/§5/§7.2.0/§7.4.2/§7.10`、`09 §1.6`、ADR-0011/0012/0018/0019
- [x] 没有硬编码业务常量（枚举值集中在迁移与 seed 清单）
- [x] 没有物理删除（库存是业务数据，`wip_ledger_lines` 显式 `REVOKE UPDATE, DELETE`）
- [x] 新表字段齐全（三张表的公共字段都由测试查库验证）
- [x] 状态变更（不适用：本卡无单据）
- [x] 接口（本卡只建表，接口与页面另开卡）
- [x] 测试覆盖 9 例：必填性 / 唯一键列集 / 索引条件 / append-only / 权限边界 /
      公共字段 / 枚举口径 / CHECK 生效 / 模型注册
- [x] 闸门 1 lint 通过
- [x] 闸门 2 typecheck 通过
- [x] 闸门 3 单测通过（后端 643 例）
- [x] 闸门 4 迁移可正向且可回滚（`alembic check` 零漂移）
- [x] 闸门 5 构建镜像成功（`api-image`）
- [x] 提交信息符合规范
- [x] 本次改动已在 docs/12 变更记录留痕（0074）
