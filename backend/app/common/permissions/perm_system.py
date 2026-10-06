"""system 模块权限点（docs/07-认证与权限规范.md §2.2）。

单一来源为 ``app.common.permissions_registry.PERMISSIONS`` —— 本文件是其
``system`` 段，聚合顺序与内容由 ``tests/modules/test_permission_registry.py``
与 docs/07 §2.2 双向守护。本文件是纯代码拆分的产物，**不得单独改动**：
新增/修改权限点须改 docs/07 §2.2 后同步本段并重跑生成物。
"""

from app.common.permissions import PermissionSeed

PERMISSIONS_SYSTEM: tuple[PermissionSeed, ...] = (
    PermissionSeed(
        code="system:config:manage",
        name="系统·系统配置管理",
        module="system",
        action="config:manage",
        sort_order=116,
    ),
    PermissionSeed(
        code="system:export:manage",
        name="系统·导出总开关",
        module="system",
        action="export:manage",
        sort_order=117,
    ),
    PermissionSeed(
        code="system:import:manage",
        name="系统·导入总开关",
        module="system",
        action="import:manage",
        sort_order=118,
    ),
    PermissionSeed(
        code="system:log:view",
        name="系统·日志查看",
        module="system",
        action="log:view",
        sort_order=119,
    ),
    PermissionSeed(
        code="system:role:manage",
        name="系统·角色管理",
        module="system",
        action="role:manage",
        sort_order=120,
    ),
    PermissionSeed(
        code="system:user:manage",
        name="系统·用户管理",
        module="system",
        action="user:manage",
        sort_order=121,
    ),
    PermissionSeed(
        code="system:workshop:manage",
        name="系统·车间管理",
        module="system",
        action="workshop:manage",
        sort_order=122,
    ),
)
