"""stock 模块权限点（docs/07-认证与权限规范.md §2.2）。

单一来源为 ``app.common.permissions_registry.PERMISSIONS`` —— 本文件是其
``stock`` 段，聚合顺序与内容由 ``tests/modules/test_permission_registry.py``
与 docs/07 §2.2 双向守护。本文件是纯代码拆分的产物，**不得单独改动**：
新增/修改权限点须改 docs/07 §2.2 后同步本段并重跑生成物。
"""

from app.common.permissions import PermissionSeed

PERMISSIONS_STOCK: tuple[PermissionSeed, ...] = (
    PermissionSeed(
        code="stock:approve", name="库存·审核", module="stock", action="approve", sort_order=101
    ),
    PermissionSeed(
        code="stock:cancel", name="库存·作废", module="stock", action="cancel", sort_order=102
    ),
    PermissionSeed(
        code="stock:cost:view",
        name="库存·查看成本",
        module="stock",
        action="cost:view",
        sort_order=103,
    ),
    PermissionSeed(
        code="stock:create", name="库存·新建", module="stock", action="create", sort_order=104
    ),
    PermissionSeed(
        code="stock:export", name="库存·导出", module="stock", action="export", sort_order=105
    ),
    PermissionSeed(code="stock:in", name="库存·入库", module="stock", action="in", sort_order=106),
    PermissionSeed(
        code="stock:in:manual",
        name="库存·手动完单入库",
        module="stock",
        action="in:manual",
        sort_order=107,
    ),
    PermissionSeed(
        code="stock:out", name="库存·出库", module="stock", action="out", sort_order=108
    ),
    PermissionSeed(
        code="stock:read", name="库存·查看", module="stock", action="read", sort_order=109
    ),
    PermissionSeed(
        code="stock:reverse", name="库存·反审核", module="stock", action="reverse", sort_order=110
    ),
    PermissionSeed(
        code="stock:stocktake", name="库存·盘点", module="stock", action="stocktake", sort_order=111
    ),
    PermissionSeed(
        code="stock:submit", name="库存·提交", module="stock", action="submit", sort_order=112
    ),
    PermissionSeed(
        code="stock:transfer", name="库存·调拨", module="stock", action="transfer", sort_order=113
    ),
    PermissionSeed(
        code="stock:update", name="库存·修改", module="stock", action="update", sort_order=114
    ),
    PermissionSeed(
        code="stock:wip:read",
        name="库存·在制品查看",
        module="stock",
        action="wip:read",
        sort_order=115,
    ),
)
