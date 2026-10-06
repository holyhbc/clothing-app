"""payroll 模块权限点（docs/07-认证与权限规范.md §2.2）。

单一来源为 ``app.common.permissions_registry.PERMISSIONS`` —— 本文件是其
``payroll`` 段，聚合顺序与内容由 ``tests/modules/test_permission_registry.py``
与 docs/07 §2.2 双向守护。本文件是纯代码拆分的产物，**不得单独改动**：
新增/修改权限点须改 docs/07 §2.2 后同步本段并重跑生成物。
"""

from app.common.permissions import PermissionSeed

PERMISSIONS_PAYROLL: tuple[PermissionSeed, ...] = (
    PermissionSeed(
        code="payroll:approve", name="工资·审核", module="payroll", action="approve", sort_order=53
    ),
    PermissionSeed(
        code="payroll:cancel", name="工资·作废", module="payroll", action="cancel", sort_order=54
    ),
    PermissionSeed(
        code="payroll:create", name="工资·新建", module="payroll", action="create", sort_order=55
    ),
    PermissionSeed(
        code="payroll:export", name="工资·导出", module="payroll", action="export", sort_order=56
    ),
    PermissionSeed(
        code="payroll:pay", name="工资·发放", module="payroll", action="pay", sort_order=57
    ),
    PermissionSeed(
        code="payroll:period:manage",
        name="工资·结算周期维护",
        module="payroll",
        action="period:manage",
        sort_order=58,
    ),
    PermissionSeed(
        code="payroll:read", name="工资·查看", module="payroll", action="read", sort_order=59
    ),
    PermissionSeed(
        code="payroll:reject", name="工资·驳回", module="payroll", action="reject", sort_order=60
    ),
    PermissionSeed(
        code="payroll:reverse",
        name="工资·反审核",
        module="payroll",
        action="reverse",
        sort_order=61,
    ),
    PermissionSeed(
        code="payroll:settle", name="工资·结算", module="payroll", action="settle", sort_order=62
    ),
    PermissionSeed(
        code="payroll:submit", name="工资·提交", module="payroll", action="submit", sort_order=63
    ),
    PermissionSeed(
        code="payroll:update", name="工资·修改", module="payroll", action="update", sort_order=64
    ),
    PermissionSeed(
        code="payroll:withdraw",
        name="工资·撤回",
        module="payroll",
        action="withdraw",
        sort_order=65,
    ),
)
