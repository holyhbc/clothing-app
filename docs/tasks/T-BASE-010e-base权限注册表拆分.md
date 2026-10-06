# T-BASE-010e：拆分 permissions_registry.py → 11 模块文件 + 聚合入口

| 项 | 内容 |
| --- | --- |
| 模块 | base |
| 负责人 | backend-dev |
| 状态 | done |
| 优先级 | P0 |
| 依赖 | 无 |
| 被依赖 | T-BASE-010f |
| 关联设计 | docs/modules/base-重构拆分.设计.md §2.5, §2.6, §8 |
| 关联 ADR | docs/adr/0030-提交体量上限放宽到1200行.md（第 3 条）、docs/adr/0008-权限点命名与总表.md |
| 估算 | 0.5d |

## 目标

将 `backend/app/common/permissions_registry.py`（**真实 850 行**；⚠️ 初版误把字节数 27020 当行数）拆分为 11 个 `perm_*.py` + 数据类 + 1 个聚合入口，单文件 ≤400 行。

## 范围

**要做**（真实权限点分布：base 11、bundling 12、cutting 12、finance 17、payroll 13、piecework 6、purchase 13、sales 12、self 4、stock 15、system 7，共 **122**）：
- [ ] 创建 `backend/app/common/permissions/__init__.py`：`PermissionSeed`(34-48)/`RoleSeed`(50-56) 数据类定义（下沉以切断循环导入）
- [ ] 创建 `perm_base.py`（11）、`perm_bundling.py`（12）、`perm_cutting.py`（12）、`perm_finance.py`（17）、`perm_payroll.py`（13）、`perm_piecework.py`（6）、`perm_purchase.py`（13）、`perm_sales.py`（12）、`perm_self.py`（4）、`perm_stock.py`（15）、`perm_system.py`（7）
- [ ] 重写 `backend/app/common/permissions_registry.py`：聚合入口
  - `PERMISSIONS` **按原顺序**拼接：base → bundling → cutting → finance → payroll → piecework → purchase → sales → self → stock → system
  - 原样保留 `ROLES`（668-837）
  - 保留 `permission_codes`/`permissions_by_module`/`SELF_PERMISSION_CODES`/`role_codes`/`resolve_role_permissions`（649-668、838-850）
  - 重导出 `PermissionSeed`/`RoleSeed`
- [ ] 删除原 `permissions_registry.py` 内联定义（或重命名为 `.bak` 待验证后删）

**不做**：
- 不改变任何权限点 code、名称、module、action、sort_order、顺序
- 不改变 `ROLES` 内置角色定义与 `resolve_role_permissions` 逻辑
- **不使用原稿里不存在的 `auth:*`/`arap:*`**；补齐原稿漏掉的 `purchase` 与 `self`

## 将要改动的文件

| 文件 | 类型 | 说明 | 预估行数 |
| --- | --- | --- | --- |
| `backend/app/common/permissions/__init__.py` | 新增 | `PermissionSeed`/`RoleSeed` 数据类 | ~35 |
| `backend/app/common/permissions/perm_base.py` | 新增 | base 11 | ~65 |
| `backend/app/common/permissions/perm_bundling.py` | 新增 | bundling 12 | ~70 |
| `backend/app/common/permissions/perm_cutting.py` | 新增 | cutting 12 | ~70 |
| `backend/app/common/permissions/perm_finance.py` | 新增 | finance 17 | ~95 |
| `backend/app/common/permissions/perm_payroll.py` | 新增 | payroll 13 | ~75 |
| `backend/app/common/permissions/perm_piecework.py` | 新增 | piecework 6 | ~40 |
| `backend/app/common/permissions/perm_purchase.py` | 新增 | purchase 13 | ~75 |
| `backend/app/common/permissions/perm_sales.py` | 新增 | sales 12 | ~70 |
| `backend/app/common/permissions/perm_self.py` | 新增 | self 4 | ~30 |
| `backend/app/common/permissions/perm_stock.py` | 新增 | stock 15 | ~85 |
| `backend/app/common/permissions/perm_system.py` | 新增 | system 7 | ~50 |
| `backend/app/common/permissions_registry.py` | 重写 | 聚合入口 | ~330 |
| `backend/app/common/permissions_registry.py.bak` | 备份 | 原 850 行文件 | - |

## 实现要点（必读规范）

- [ ] 遵守 docs/07-认证与权限规范.md §2.2：权限点命名 `模块:动作`、两级动作词、自助权限点内建
- [ ] 遵守 docs/adr/0008-权限点命名与总表.md：122 个权限点总表不变
- [ ] 每个 `perm_*.py` 导出 `PERMISSIONS_<MODULE>: tuple[PermissionSeed, ...]`，并从 `app.common.permissions` 导入 `PermissionSeed`
- [ ] 聚合入口拼接顺序**逐字保持**（见上）；顺序即界面展示顺序，错序会导致生成物 diff
- [ ] 现有导入零改动：`from app.common.permissions_registry import PERMISSIONS, ROLES, SELF_PERMISSION_CODES, permission_codes, role_codes, resolve_role_permissions`（`seed_baseline.py`/`restore_builtin.py`/`system/service.py`/`tests/conftest.py` 等）

## 验收标准

- [x] `uv run pytest tests/modules/test_permission_registry.py -q` 全部通过（41 passed 含 `test_seed_cli.py`）
- [x] `uv run pytest tests/modules/test_seed_cli.py -q` 全部通过
- [x] 权限点总数 = 122（与 ADR-0008 一致），且顺序与拆分前逐个相同（逐元素比对 HEAD 旧值）
- [x] `ROLES` 与拆分前逐个相同（字段逐元素比对）
- [x] 本次新增/重写的 13 个文件单文件 ≤400 行（最大 279）
- [x] 闸门 1-4 本地预跑通过（`scripts/gate.sh --host`）

## 测试清单

| # | 用例 | 期望 | 结果 |
|---|------|------|------|
| TC-01 | 权限点注册总数 | `len(PERMISSIONS) == 122` | ✅ 122 |
| TC-02 | 权限点顺序 | 与拆分前 `PERMISSIONS` 逐元素相同 | ✅ 逐元素相同（`sort_order` 1..122 无重复） |
| TC-03 | 内置角色权限解析 | `resolve_role_permissions(RoleSeed("super_admin", ...))` 返回全部 | ✅ 122；10 角色逐角色解析结果不变 |
| TC-04 | 各模块权限点前缀 | `base`/`bundling`/`cutting`/`finance`/`payroll`/`piecework`/`purchase`/`sales`/`self`/`stock`/`system` 计数正确 | ✅ 11/12/12/17/13/6/13/12/4/15/7 |
| TC-05 | 种子数据同步 | `seed_baseline` 写入无报错、数量 122 | ✅ `seed_baseline --check`：权限点 122 / 角色 10 |
| TC-06 | 前端/文档一致性守卫 | `test_permission_registry.py` 比对 docs/07 §2.2 通过 | ✅ 41 passed（两个定向文件）+ 全量 792 passed |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `backend/app/common/permissions/__init__.py` | 37 | 新增：`PermissionSeed`/`RoleSeed` 下沉（import `DataScope`），切断循环导入 |
| `backend/app/common/permissions/perm_base.py` | 61 | 新增：base 11 |
| `backend/app/common/permissions/perm_bundling.py` | 64 | 新增：bundling 12 |
| `backend/app/common/permissions/perm_cutting.py` | 60 | 新增：cutting 12 |
| `backend/app/common/permissions/perm_finance.py` | 131 | 新增：finance 17 |
| `backend/app/common/permissions/perm_payroll.py` | 63 | 新增：payroll 13 |
| `backend/app/common/permissions/perm_piecework.py` | 50 | 新增：piecework 6 |
| `backend/app/common/permissions/perm_purchase.py` | 71 | 新增：purchase 13 |
| `backend/app/common/permissions/perm_sales.py` | 52 | 新增：sales 12 |
| `backend/app/common/permissions/perm_self.py` | 40 | 新增：self 4 |
| `backend/app/common/permissions/perm_stock.py` | 67 | 新增：stock 15 |
| `backend/app/common/permissions/perm_system.py` | 61 | 新增：system 7 |
| `backend/app/common/permissions_registry.py` | 279（原 850，diff -615/+44） | 重写：聚合入口（按原顺序拼接）+ `ROLES` + 查询函数，重导出 `PermissionSeed`/`RoleSeed` |
| **合计** | **1036 行 / 13 文件**（净 +186 vs 850） | 单文件最大 279（≤400，ADR-0030） |

**提交记录**：
- `173ee07` refactor(auth): 拆分 permissions_registry.py(850 行) 为 11 个 perm 模块 + 聚合入口（≤400 行）

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| | | docs/12 §遗留问题清单 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。

## 变更记录

| 日期 | 变更内容 | 操作人 |
|------|---------|--------|
| 2026-10-06 | 修正行数口径（原误将字节 27020 当行数，真实 850）；按真实前缀（base/bundling/cutting/finance/payroll/piecework/purchase/sales/self/stock/system）重排为 11 个 perm 文件；删除不存在的 `auth:*`/`arap:*`，补 `purchase`/`self`；明确 PERMISSIONS 顺序与 ROLES 不变 | AI |
