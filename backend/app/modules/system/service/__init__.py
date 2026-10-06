"""用户与角色管理服务包（原 ``system/service.py`` 按职责拆分，设计稿 §2.4）。

包内结构：

- :mod:`common`：模块常量、``logger``、``permission_groups``（不依赖任何 Mixin）
- :mod:`user_query_mixin` / :mod:`user_write_mixin` / :mod:`user_guard_mixin`：
  ``SystemUserService`` 按读 / 写 / 守卫拆出的 3 个 Mixin
- :mod:`user_service`：组合类 ``SystemUserService``
- :mod:`role_service`：``SystemRoleService``

本 ``__init__`` 只做聚合重导出，保证 ``from app.modules.system.service import X``
零改动。原模块 docstring 逐字保留在 :mod:`common`。
"""

from __future__ import annotations

from .common import MODULE_NAMES, PERMISSION_NAMES, logger, permission_groups
from .role_service import SystemRoleService
from .user_service import SystemUserService

__all__ = [
    "MODULE_NAMES",
    "PERMISSION_NAMES",
    "SystemRoleService",
    "SystemUserService",
    "logger",
    "permission_groups",
]
