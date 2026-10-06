"""piecework 模块权限点（docs/07-认证与权限规范.md §2.2）。

单一来源为 ``app.common.permissions_registry.PERMISSIONS`` —— 本文件是其
``piecework`` 段，聚合顺序与内容由 ``tests/modules/test_permission_registry.py``
与 docs/07 §2.2 双向守护。本文件是纯代码拆分的产物，**不得单独改动**：
新增/修改权限点须改 docs/07 §2.2 后同步本段并重跑生成物。
"""

from app.common.permissions import PermissionSeed

PERMISSIONS_PIECEWORK: tuple[PermissionSeed, ...] = (
    PermissionSeed(
        code="piecework:count",
        name="计件·扫码计件",
        module="piecework",
        action="count",
        sort_order=66,
    ),
    PermissionSeed(
        code="piecework:export",
        name="计件·导出",
        module="piecework",
        action="export",
        sort_order=67,
    ),
    PermissionSeed(
        code="piecework:manual",
        name="计件·手工补录",
        module="piecework",
        action="manual",
        sort_order=68,
    ),
    PermissionSeed(
        code="piecework:rate:manage",
        name="计件·单价维护",
        module="piecework",
        action="rate:manage",
        sort_order=69,
    ),
    PermissionSeed(
        code="piecework:read", name="计件·查看", module="piecework", action="read", sort_order=70
    ),
    PermissionSeed(
        code="piecework:reverse",
        name="计件·反审核",
        module="piecework",
        action="reverse",
        sort_order=71,
    ),
)
