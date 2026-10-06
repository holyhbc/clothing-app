"""bundling 模块权限点（docs/07-认证与权限规范.md §2.2）。

单一来源为 ``app.common.permissions_registry.PERMISSIONS`` —— 本文件是其
``bundling`` 段，聚合顺序与内容由 ``tests/modules/test_permission_registry.py``
与 docs/07 §2.2 双向守护。本文件是纯代码拆分的产物，**不得单独改动**：
新增/修改权限点须改 docs/07 §2.2 后同步本段并重跑生成物。
"""

from app.common.permissions import PermissionSeed

PERMISSIONS_BUNDLING: tuple[PermissionSeed, ...] = (
    PermissionSeed(
        code="bundling:approve",
        name="打菲·审核",
        module="bundling",
        action="approve",
        sort_order=12,
    ),
    PermissionSeed(
        code="bundling:cancel", name="打菲·作废", module="bundling", action="cancel", sort_order=13
    ),
    PermissionSeed(
        code="bundling:code:void",
        name="打菲·作废打菲码",
        module="bundling",
        action="code:void",
        sort_order=14,
    ),
    PermissionSeed(
        code="bundling:create", name="打菲·新建", module="bundling", action="create", sort_order=15
    ),
    PermissionSeed(
        code="bundling:export", name="打菲·导出", module="bundling", action="export", sort_order=16
    ),
    PermissionSeed(
        code="bundling:print", name="打菲·打印", module="bundling", action="print", sort_order=17
    ),
    PermissionSeed(
        code="bundling:read", name="打菲·查看", module="bundling", action="read", sort_order=18
    ),
    PermissionSeed(
        code="bundling:reject", name="打菲·驳回", module="bundling", action="reject", sort_order=19
    ),
    PermissionSeed(
        code="bundling:reverse",
        name="打菲·反审核",
        module="bundling",
        action="reverse",
        sort_order=20,
    ),
    PermissionSeed(
        code="bundling:submit", name="打菲·提交", module="bundling", action="submit", sort_order=21
    ),
    PermissionSeed(
        code="bundling:update", name="打菲·修改", module="bundling", action="update", sort_order=22
    ),
    PermissionSeed(
        code="bundling:withdraw",
        name="打菲·撤回",
        module="bundling",
        action="withdraw",
        sort_order=23,
    ),
)
