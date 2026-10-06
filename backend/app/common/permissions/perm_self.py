"""self 模块权限点（docs/07-认证与权限规范.md §2.2）。

单一来源为 ``app.common.permissions_registry.PERMISSIONS`` —— 本文件是其
``self`` 段，聚合顺序与内容由 ``tests/modules/test_permission_registry.py``
与 docs/07 §2.2 双向守护。本文件是纯代码拆分的产物，**不得单独改动**：
新增/修改权限点须改 docs/07 §2.2 后同步本段并重跑生成物。
"""

from app.common.permissions import PermissionSeed

PERMISSIONS_SELF: tuple[PermissionSeed, ...] = (
    PermissionSeed(
        code="self:payroll:read",
        name="员工自助·我的工资",
        module="self",
        action="payroll:read",
        sort_order=97,
    ),
    PermissionSeed(
        code="self:piecework:bind",
        name="员工自助·绑定计件工序",
        module="self",
        action="piecework:bind",
        sort_order=98,
    ),
    PermissionSeed(
        code="self:piecework:read",
        name="员工自助·piecework:read",
        module="self",
        action="piecework:read",
        sort_order=99,
    ),
    PermissionSeed(
        code="self:profile:read",
        name="员工自助·个人信息查看",
        module="self",
        action="profile:read",
        sort_order=100,
    ),
)
