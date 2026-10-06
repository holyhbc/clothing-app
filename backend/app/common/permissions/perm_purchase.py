"""purchase 模块权限点（docs/07-认证与权限规范.md §2.2）。

单一来源为 ``app.common.permissions_registry.PERMISSIONS`` —— 本文件是其
``purchase`` 段，聚合顺序与内容由 ``tests/modules/test_permission_registry.py``
与 docs/07 §2.2 双向守护。本文件是纯代码拆分的产物，**不得单独改动**：
新增/修改权限点须改 docs/07 §2.2 后同步本段并重跑生成物。
"""

from app.common.permissions import PermissionSeed

PERMISSIONS_PURCHASE: tuple[PermissionSeed, ...] = (
    PermissionSeed(
        code="purchase:approve",
        name="采购·审核",
        module="purchase",
        action="approve",
        sort_order=72,
    ),
    PermissionSeed(
        code="purchase:arrival",
        name="采购·到货登记",
        module="purchase",
        action="arrival",
        sort_order=73,
    ),
    PermissionSeed(
        code="purchase:arrival:reverse",
        name="采购·到货红冲",
        module="purchase",
        action="arrival:reverse",
        sort_order=74,
    ),
    PermissionSeed(
        code="purchase:cancel", name="采购·作废", module="purchase", action="cancel", sort_order=75
    ),
    PermissionSeed(
        code="purchase:create", name="采购·新建", module="purchase", action="create", sort_order=76
    ),
    PermissionSeed(
        code="purchase:export", name="采购·导出", module="purchase", action="export", sort_order=77
    ),
    PermissionSeed(
        code="purchase:import", name="采购·导入", module="purchase", action="import", sort_order=78
    ),
    PermissionSeed(
        code="purchase:ledger:read",
        name="采购·采购台账查看",
        module="purchase",
        action="ledger:read",
        sort_order=79,
    ),
    PermissionSeed(
        code="purchase:read", name="采购·查看", module="purchase", action="read", sort_order=80
    ),
    PermissionSeed(
        code="purchase:reject", name="采购·驳回", module="purchase", action="reject", sort_order=81
    ),
    PermissionSeed(
        code="purchase:reverse",
        name="采购·反审核",
        module="purchase",
        action="reverse",
        sort_order=82,
    ),
    PermissionSeed(
        code="purchase:submit", name="采购·提交", module="purchase", action="submit", sort_order=83
    ),
    PermissionSeed(
        code="purchase:update", name="采购·修改", module="purchase", action="update", sort_order=84
    ),
)
