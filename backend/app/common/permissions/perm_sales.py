"""sales 模块权限点（docs/07-认证与权限规范.md §2.2）。

单一来源为 ``app.common.permissions_registry.PERMISSIONS`` —— 本文件是其
``sales`` 段，聚合顺序与内容由 ``tests/modules/test_permission_registry.py``
与 docs/07 §2.2 双向守护。本文件是纯代码拆分的产物，**不得单独改动**：
新增/修改权限点须改 docs/07 §2.2 后同步本段并重跑生成物。
"""

from app.common.permissions import PermissionSeed

PERMISSIONS_SALES: tuple[PermissionSeed, ...] = (
    PermissionSeed(
        code="sales:approve", name="销售·审核", module="sales", action="approve", sort_order=85
    ),
    PermissionSeed(
        code="sales:cancel", name="销售·作废", module="sales", action="cancel", sort_order=86
    ),
    PermissionSeed(
        code="sales:export", name="销售·导出", module="sales", action="export", sort_order=87
    ),
    PermissionSeed(
        code="sales:import", name="销售·导入", module="sales", action="import", sort_order=88
    ),
    PermissionSeed(
        code="sales:order:create",
        name="销售·新建订单",
        module="sales",
        action="order:create",
        sort_order=89,
    ),
    PermissionSeed(
        code="sales:read", name="销售·查看", module="sales", action="read", sort_order=90
    ),
    PermissionSeed(
        code="sales:reject", name="销售·驳回", module="sales", action="reject", sort_order=91
    ),
    PermissionSeed(
        code="sales:reverse", name="销售·反审核", module="sales", action="reverse", sort_order=92
    ),
    PermissionSeed(
        code="sales:ship", name="销售·发货", module="sales", action="ship", sort_order=93
    ),
    PermissionSeed(
        code="sales:submit", name="销售·提交", module="sales", action="submit", sort_order=94
    ),
    PermissionSeed(
        code="sales:update", name="销售·修改", module="sales", action="update", sort_order=95
    ),
    PermissionSeed(
        code="sales:withdraw", name="销售·撤回", module="sales", action="withdraw", sort_order=96
    ),
)
