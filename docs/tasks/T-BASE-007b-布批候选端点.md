# T-BASE-007b：布批候选端点（`available_qty > 0` + FIFO）

| 项 | 内容 |
| --- | --- |
| 模块 | base |
| 状态 | `done` |
| 优先级 | P1 |
| 依赖 | T-BASE-007a（物料 / 供应商候选）、T-BASE-006（`material_stocks` 已建） |
| 被依赖 | **T-CUT-001c-3（裁剪单新建页「级联选料」的第三级）** |
| 关联设计 | [05-接口设计规范.md](../05-接口设计规范.md) §9.5.2 末行、[modules/06-库存.md](../modules/06-库存.md) BR-ST-17 / BR-ST-25、C38 |
| 估算 | 0.4d |

## 目标

`GET /material-stocks/options` 可用：裁剪单新建页能按 **供应商 → 面料 → 缸号/匹号**
三级选到布批，且候选里**只出现还剩布的批次**、按 FIFO 排序、显示门幅与可用量。

## 范围

**要做**：

- [x] `StockBatchOptionOut`（继承 `OptionOut`，前端 `<Combo>` 的 `fetchOptions` 契约不变）
- [x] `MaterialOptionsService.list_stock_batch_options`
- [x] 端点 `x-permission: stock:read` + `_require`，OpenAPI 标签「库存」
- [x] 把 `Str` 抽成共享片段 `app/core/pydantic_types.py`
- [x] 12 例测试

**不做**：

- 布批的增删改（由**到货登记 / 采购单审核**产生，ADR-0012：无独立入库单）
- 库存台账与成本追溯（modules/06 P3）

## 三条最容易做错的

| # | 陷阱 | 正确做法 |
| --- | --- | --- |
| 1 | 把 `available_qty > 0` 写成「取出来在 Python 里比」 | 必须写成 SQL 里的 `stock_qty - locked_qty > 0`，与部分索引 `idx_material_stocks_pick` 的谓词**逐字一致**；改一边不改另一边就从索引扫描退化成全表扫 |
| 2 | 给 `purpose` 加 `= 'NORMAL'` 过滤 | **BR-ST-25 明写** `REWORK_RECEIPT`（返修布）可被裁剪单正常选批领用，TC-35 ① 也要求两批都可选。这个「看起来很合理」的过滤会让返修布永远领不出去，且**不报任何错** |
| 3 | 用 `stmt.or_(...)` 写 `q` 的模糊匹配 | SQLAlchemy 2.0 已移除该 API。它要**真的传了 `q`** 才炸 —— 只按 `material_id` / 缸号筛的用例全绿、用户手输关键字就 500。被 **mypy** 抓到，不是被测试抓到 |

## 验收标准

- [x] `pytest tests/modules/test_material_stock_options.py -q` 全过（15 例）
- [x] `ruff` / `mypy app` 通过
- [x] `pnpm generate:api` 连跑两次无 diff
- [x] 闸门 1-5 全绿（760 passed）

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-B07-01/02/03 | 物料只返面料辅料 / 模糊 / 停用 `disabled`；供应商模糊 + `sub` 简称 | `05 §9.5.2` | ✅ |
| TC-B07-04 | ★ 只列 `available_qty > 0` | `05 §9.5.2` 加粗 | ✅ |
| TC-B07-05 | ★ FIFO（入库日升序） | BR-ST-17 ③ | ✅ |
| TC-B07-06 | ★ 不过滤 `purpose`，返修布可选 | BR-ST-25 / TC-35 ① | ✅ |
| TC-B07-07 | 候选不返回 `unit_cost` | C38 | ✅ |
| TC-B07-08 / 08b | `dye_lot_no` 精确匹配；`q` 模糊匹配缸号与匹号 | `05 §9.5.2` | ✅ |
| TC-B07-09 | 布批要 `stock:read`，物料要 `base:read`（**双向各一条用例**） | `07 §2.2` | ✅ |
| TC-B07-10/11/12 | `size > 20` → 422；软删不进候选；OpenAPI 契约 | `05 §9.5.2` | ✅ |

## 实际改动

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `backend/tests/modules/test_material_stock_options.py` | +287 | 12 例（007a 那 3 例同文件，合并成 15 例） |
| `backend/app/modules/base/service.py` | +85 | `list_stock_batch_options` |
| `backend/app/modules/base/router.py` | +44 | 端点 + 「库存」标签 |
| `backend/app/modules/base/schemas.py` | +32 | `StockBatchOptionOut` |
| `backend/app/core/pydantic_types.py` | +26 | 共享 `Str` 片段 |
| `backend/app/modules/cutting/schemas.py` | +8/-13 | `Str` 改为复用共享片段 |
| `backend/openapi.json`、`frontend/.../schema.d.ts` | 生成物 | `openapi-typescript` |

**手写行数 ≈ 498**（生成物 2 个文件不计入 `AGENTS §7.1` 的 800 行）。

## 抓到的四个坑

| # | 坑 | 为什么难发现 |
| --- | --- | --- |
| 1 | `stmt.or_(...)`（SQLAlchemy 1.x API） | 只在**真的传了 `q`** 时抛 `AttributeError`；所有只传 `material_id` 的用例全绿。是 mypy 报的，不是测试。补 `TC-B07-08b` 把这条路径钉住 |
| 2 | `role="custom"` 复用**同一个角色行**、权限往里追加 | 「正向 / 反向」权限两条放在同一条用例里，反向那条必然假失败（拿到并集），而失败信息是「expected 403, got 200」 |
| 3 | `headers` 塞进 `**params` | 会变成查询参数 `?headers={...}`，症状是 401 —— 报错指向令牌，与「参数传错位置」毫无关系 |
| 4 | 测试数据把 `in_date` / `deleted_at` 写成字符串 | asyncpg 不做转换，报 `'str' object has no attribute 'toordinal'` |

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| — | `RETURN` / `SAMPLE` 的布能不能被裁（BR-ST-25 只明确了返修布可以） | docs/12 **L-080** |
| — | `05 §9.5.2` 说物料候选返回 `spec`，表里没有该列 | docs/12 **L-079** |
| — | 与 `modules/06` 的 `GET /stocks/materials/lots`（P3 完整选批，含 FEFO/FIFO 规则参数）的关系 | 两者**并存**：本端点是**候选下拉**（Combo），那个是**单据选批**（带 `rule` / 仓库 / 分页）。实现后者时不要合并 |
| — | 裁剪单新建页（三层明细编辑器） | T-CUT-001c-3 |
