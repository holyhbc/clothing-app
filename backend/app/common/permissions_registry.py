"""权限点与内置角色的**单一来源**（docs/07-认证与权限规范.md §2.2 / §2.3）。

为什么需要这个文件：权限点要在**三处**保持一致 ——
    1. ``permissions`` 表的 seed 数据（迁移 0002 与 ``seed_baseline``）
    2. 前端常量 ``packages/shared/enums/permissions.ts``
    3. docs/07 §2.2 的表格

docs/07 §2.2 明确「三处不一致视为闸门 1 失败」，因此把 (1) 的内容收敛到这个
模块，让「代码侧的真相」只有一份；测试
``tests/modules/test_permission_registry.py`` 负责把它与 docs/07 §2.2 双向比对。

⚠️ 数据来源与口径：
    - 权限点：**逐条抄自 docs/07 §2.2**，共 122 个（含 4 个 ``self:*``）
    - 两个**已作废**的码不得入库（它们只出现在 docs/07 的"作废说明"里，不是权限点）：
        * ``self:piecework:count`` —— "员工端不开放计件写入"（REV-2026-10）
        * ``bundling:void_code`` —— 领域动作必须写成两级，现行是 ``bundling:code:void``
    - 内置角色：docs/07 §2.3 的 10 个（含数据范围）
    - 非 super_admin 角色的权限集合：docs/07 §2.3 只有文字描述，**完整矩阵分散在各
      模块文档的 §10**，此处按描述给保守集合，已登记 L-024 待业务方确认

新增权限点的流程（docs/07 §6）：
    1. 先改 docs/07 §2.2 表格
    2. 在本文件加一条（迁移与 seed 自动带上）
    3. 更新前端 ``packages/shared/enums/permissions.ts``
    4. 跑测试确认三处一致
"""

# ---------------------------------------------------------------------------
# 以上是拆分前 ``permissions_registry.py`` 的模块 docstring，**要点逐字保留**。
# 拆分后本文件是聚合入口：数据类下沉到 ``app.common.permissions``，权限点按模块
# 拆到 11 个 ``perm_*.py``，本文件按原顺序拼接并原样保留 ``ROLES`` 与全部查询函数。
# 拼接顺序 = 界面展示顺序（base→bundling→cutting→finance→payroll→piecework→
# purchase→sales→self→stock→system），**不得改成字典序**。
# 见 docs/modules/base-重构拆分.设计.md §2.5。
# ---------------------------------------------------------------------------

from app.common.enums import DataScope
from app.common.permissions import PermissionSeed, RoleSeed
from app.common.permissions.perm_base import PERMISSIONS_BASE
from app.common.permissions.perm_bundling import PERMISSIONS_BUNDLING
from app.common.permissions.perm_cutting import PERMISSIONS_CUTTING
from app.common.permissions.perm_finance import PERMISSIONS_FINANCE
from app.common.permissions.perm_payroll import PERMISSIONS_PAYROLL
from app.common.permissions.perm_piecework import PERMISSIONS_PIECEWORK
from app.common.permissions.perm_purchase import PERMISSIONS_PURCHASE
from app.common.permissions.perm_sales import PERMISSIONS_SALES
from app.common.permissions.perm_self import PERMISSIONS_SELF
from app.common.permissions.perm_stock import PERMISSIONS_STOCK
from app.common.permissions.perm_system import PERMISSIONS_SYSTEM

PERMISSIONS: tuple[PermissionSeed, ...] = (
    PERMISSIONS_BASE
    + PERMISSIONS_BUNDLING
    + PERMISSIONS_CUTTING
    + PERMISSIONS_FINANCE
    + PERMISSIONS_PAYROLL
    + PERMISSIONS_PIECEWORK
    + PERMISSIONS_PURCHASE
    + PERMISSIONS_SALES
    + PERMISSIONS_SELF
    + PERMISSIONS_STOCK
    + PERMISSIONS_SYSTEM
)


def permission_codes() -> frozenset[str]:
    """全部权限点 code 集合。"""
    return frozenset(item.code for item in PERMISSIONS)


def permissions_by_module() -> dict[str, tuple[PermissionSeed, ...]]:
    """按模块分组，便于前端生成下拉与角色配置树。"""
    grouped: dict[str, list[PermissionSeed]] = {}
    for item in PERMISSIONS:
        grouped.setdefault(item.module, []).append(item)
    return {module: tuple(items) for module, items in grouped.items()}


#: 员工自助权限点（代码内置于 AuthContext，不经角色表授权，docs/07 §2.2）
SELF_PERMISSION_CODES: frozenset[str] = frozenset(
    item.code for item in PERMISSIONS if item.module == "self"
)

ROLES: tuple[RoleSeed, ...] = (
    RoleSeed(
        code="super_admin",
        name="系统管理员",
        data_scope=DataScope.FACTORY,
        description="所有权限，操作全审计，不可删除",
        permissions=None,
    ),
    RoleSeed(
        code="factory_manager",
        name="厂长",
        data_scope=DataScope.FACTORY,
        description="全部业务只读 + 审批终审，无系统管理",
        permissions=(
            "base:read",
            "cutting:read",
            "cutting:approve",
            "cutting:reject",
            "bundling:read",
            "bundling:approve",
            "bundling:reject",
            "piecework:read",
            "payroll:read",
            "payroll:approve",
            "stock:read",
            "sales:read",
            "finance:ar:read",
            "finance:ap:read",
            "purchase:read",
            "system:log:view",
        ),
    ),
    RoleSeed(
        code="workshop_supervisor",
        name="车间主管",
        data_scope=DataScope.WORKSHOP,
        description="本车间单据审批、计件确认",
        permissions=(
            "base:read",
            "base:update",
            "base:ratio:manage",
            "cutting:read",
            "cutting:approve",
            "cutting:reject",
            "bundling:read",
            "bundling:approve",
            "bundling:reject",
            "bundling:print",
            "piecework:read",
            "stock:read",
            "purchase:read",
        ),
    ),
    RoleSeed(
        code="line_leader",
        name="组长",
        data_scope=DataScope.GROUP,
        description="本组计件录入与查看",
        permissions=("base:read", "piecework:read", "cutting:read", "bundling:read"),
    ),
    RoleSeed(
        code="warehouse_keeper",
        name="仓管",
        data_scope=DataScope.FACTORY,
        description="出入库/调拨/盘点，**无 stock:cost:view**（成本不可见）",
        permissions=(
            "base:read",
            "base:create",
            "base:update",
            "base:import",
            "base:export",
            "stock:read",
            "stock:in",
            "stock:out",
            "stock:transfer",
            "stock:stocktake",
            "purchase:read",
        ),
    ),
    RoleSeed(
        code="accountant",
        name="财务",
        data_scope=DataScope.FACTORY,
        description="往来、收付款、凭证",
        permissions=(
            "base:read",
            "base:create",
            "base:update",
            "base:export",
            "finance:ar:read",
            "finance:ap:read",
            "finance:receipt",
            "finance:payment",
            "finance:settle",
            "finance:statement",
            "finance:export",
            "finance:voucher:read",
            "finance:voucher:create",
            "finance:voucher:update",
            "finance:voucher:post",
            "finance:voucher:reverse",
            "finance:period:close",
            "finance:period:reopen",
            "sales:read",
            "purchase:read",
            "stock:read",
        ),
    ),
    RoleSeed(
        code="purchaser",
        name="采购",
        data_scope=DataScope.FACTORY,
        description="采购单、供应商；颜色/尺码只读",
        permissions=(
            "base:read",
            "base:create",
            "base:update",
            "base:import",
            "base:export",
            "purchase:read",
            "purchase:create",
            "purchase:update",
            "purchase:submit",
            "purchase:approve",
            "purchase:arrival",
            "purchase:arrival:reverse",
            "purchase:import",
            "purchase:export",
            "purchase:ledger:read",
        ),
    ),
    RoleSeed(
        code="merchandiser",
        name="跟单",
        data_scope=DataScope.SELF,
        description="自己款号进度（款号维度隔离靠 styles.merchandiser_id）",
        permissions=("base:read", "sales:read", "cutting:read", "bundling:read"),
    ),
    RoleSeed(
        code="piecework_settler",
        name="计件员",
        data_scope=DataScope.FACTORY,
        description="单价维护、工资结算、款号工序模板复制",
        permissions=(
            "base:read",
            "base:update",
            "base:rate_template:manage",
            "base:export",
            "piecework:read",
            "piecework:rate:manage",
            "piecework:export",
            "payroll:read",
            "payroll:create",
            "payroll:update",
            "payroll:submit",
            "payroll:settle",
            "payroll:export",
            "payroll:period:manage",
        ),
    ),
    RoleSeed(
        code="employee",
        name="员工",
        data_scope=DataScope.SELF,
        description="仅员工端自助接口（/api/v1/self/**）",
        permissions=(),
    ),
)


def role_codes() -> frozenset[str]:
    """全部内置角色 code 集合。"""
    return frozenset(role.code for role in ROLES)


def resolve_role_permissions(role: RoleSeed) -> tuple[str, ...]:
    """展开某角色实际拥有的权限点 code。

    ``permissions=None`` 表示"全部权限"（仅 super_admin，docs/07 §2.3）。
    """
    if role.permissions is None:
        return tuple(sorted(permission_codes()))
    return tuple(sorted(role.permissions))


__all__ = [
    "PERMISSIONS",
    "ROLES",
    "SELF_PERMISSION_CODES",
    "PermissionSeed",
    "RoleSeed",
    "permission_codes",
    "permissions_by_module",
    "resolve_role_permissions",
    "role_codes",
]
