"""base 模块权限点（docs/07-认证与权限规范.md §2.2）。

单一来源为 ``app.common.permissions_registry.PERMISSIONS`` —— 本文件是其
``base`` 段，聚合顺序与内容由 ``tests/modules/test_permission_registry.py``
与 docs/07 §2.2 双向守护。本文件是纯代码拆分的产物，**不得单独改动**：
新增/修改权限点须改 docs/07 §2.2 后同步本段并重跑生成物。
"""

from app.common.permissions import PermissionSeed

PERMISSIONS_BASE: tuple[PermissionSeed, ...] = (
    PermissionSeed(
        code="base:category:manage",
        name="基础资料·商品分类维护",
        module="base",
        action="category:manage",
        sort_order=1,
    ),
    PermissionSeed(
        code="base:create", name="基础资料·新建", module="base", action="create", sort_order=2
    ),
    PermissionSeed(
        code="base:delete", name="基础资料·删除", module="base", action="delete", sort_order=3
    ),
    PermissionSeed(
        code="base:disable", name="基础资料·停用", module="base", action="disable", sort_order=4
    ),
    PermissionSeed(
        code="base:export", name="基础资料·导出", module="base", action="export", sort_order=5
    ),
    PermissionSeed(
        code="base:import", name="基础资料·导入", module="base", action="import", sort_order=6
    ),
    PermissionSeed(
        code="base:operation:manage",
        name="基础资料·工序主数据维护",
        module="base",
        action="operation:manage",
        sort_order=7,
    ),
    PermissionSeed(
        code="base:rate_template:manage",
        name="基础资料·款号工序模板复制",
        module="base",
        action="rate_template:manage",
        sort_order=8,
    ),
    PermissionSeed(
        code="base:ratio:manage",
        name="基础资料·尺码比例维护",
        module="base",
        action="ratio:manage",
        sort_order=9,
    ),
    PermissionSeed(
        code="base:read", name="基础资料·查看", module="base", action="read", sort_order=10
    ),
    PermissionSeed(
        code="base:update", name="基础资料·修改", module="base", action="update", sort_order=11
    ),
)
