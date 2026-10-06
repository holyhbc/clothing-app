"""cutting 模块权限点（docs/07-认证与权限规范.md §2.2）。

单一来源为 ``app.common.permissions_registry.PERMISSIONS`` —— 本文件是其
``cutting`` 段，聚合顺序与内容由 ``tests/modules/test_permission_registry.py``
与 docs/07 §2.2 双向守护。本文件是纯代码拆分的产物，**不得单独改动**：
新增/修改权限点须改 docs/07 §2.2 后同步本段并重跑生成物。
"""

from app.common.permissions import PermissionSeed

PERMISSIONS_CUTTING: tuple[PermissionSeed, ...] = (
    PermissionSeed(
        code="cutting:approve", name="裁剪·审核", module="cutting", action="approve", sort_order=24
    ),
    PermissionSeed(
        code="cutting:cancel", name="裁剪·作废", module="cutting", action="cancel", sort_order=25
    ),
    PermissionSeed(
        code="cutting:create", name="裁剪·新建", module="cutting", action="create", sort_order=26
    ),
    PermissionSeed(
        code="cutting:export", name="裁剪·导出", module="cutting", action="export", sort_order=27
    ),
    PermissionSeed(
        code="cutting:import", name="裁剪·导入", module="cutting", action="import", sort_order=28
    ),
    PermissionSeed(
        code="cutting:read", name="裁剪·查看", module="cutting", action="read", sort_order=29
    ),
    PermissionSeed(
        code="cutting:reject", name="裁剪·驳回", module="cutting", action="reject", sort_order=30
    ),
    PermissionSeed(
        code="cutting:reverse",
        name="裁剪·反审核",
        module="cutting",
        action="reverse",
        sort_order=31,
    ),
    PermissionSeed(
        code="cutting:submit", name="裁剪·提交", module="cutting", action="submit", sort_order=32
    ),
    PermissionSeed(
        code="cutting:trace:read",
        name="裁剪·生产溯源查看",
        module="cutting",
        action="trace:read",
        sort_order=33,
    ),
    PermissionSeed(
        code="cutting:update", name="裁剪·修改", module="cutting", action="update", sort_order=34
    ),
    PermissionSeed(
        code="cutting:withdraw",
        name="裁剪·撤回",
        module="cutting",
        action="withdraw",
        sort_order=35,
    ),
)
