"""权限点与内置角色的**基础类型**（docs/07-认证与权限规范.md §2.2 / §2.3）。

``PermissionSeed`` / ``RoleSeed`` 从原 ``permissions_registry.py`` 下沉到本包
``__init__``，用于切断循环导入：各 ``perm_*`` 子模块从本包导入 ``PermissionSeed``，
聚合入口 ``permissions_registry`` 从本包导入两个数据类并拼接各子模块的元组。

放在 ``__init__`` 而不是单独模块，是因为它必须**先于**任何 ``perm_*`` 被加载，
而子模块又都 ``from app.common.permissions import PermissionSeed`` —— 若数据类住在
某个 ``perm_*`` 里，就会形成「子模块 ↔ 子模块」的循环。
"""

from dataclasses import dataclass

from app.common.enums import DataScope


@dataclass(frozen=True, slots=True)
class PermissionSeed:
    """一条权限点。"""

    code: str
    name: str
    module: str
    action: str
    sort_order: int


@dataclass(frozen=True, slots=True)
class RoleSeed:
    """一个内置角色。"""

    code: str
    name: str
    data_scope: DataScope
    description: str
    #: 该角色拥有的权限点 code；``None`` 表示"全部"（仅 super_admin）
    permissions: tuple[str, ...] | None = None
