"""认证服务包（原 ``auth/service.py`` 按职责拆分，设计稿 §2.6）。

包内结构：

- :mod:`common`：模块 docstring、``logger``、锁常量、``IssuedTokens``/``LoginOutcome``、
  ``build_lock_key``（不依赖任何 Mixin）
- :mod:`read_mixin`：用户 / 角色 / 车间查询 + 登录锁定助手（含 Redis 降级）
- :mod:`login_mixin`：``authenticate`` / ``_record_login`` / ``change_password``
- :mod:`token_mixin`：``_issue_tokens`` / ``refresh`` / ``logout`` / ``revoke_all_tokens``
- :mod:`auth_service`：组合类 ``AuthService``

本 ``__init__`` 只做聚合重导出，保证 ``from app.modules.auth.service import X``
零改动。原模块 docstring 逐字保留在 :mod:`common`。
"""

from __future__ import annotations

from .auth_service import AuthService
from .common import (
    LOCK_MINUTES,
    MAX_LOGIN_FAILURES,
    IssuedTokens,
    LoginOutcome,
    build_lock_key,
)

__all__ = [
    "LOCK_MINUTES",
    "MAX_LOGIN_FAILURES",
    "AuthService",
    "IssuedTokens",
    "LoginOutcome",
    "build_lock_key",
]
