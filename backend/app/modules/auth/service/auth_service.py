"""``AuthService`` 组合入口：仅继承 3 个内部 Mixin + ``__init__``。

3 个 Mixin 是「把一个 424 行类拆到多文件」的手段，不承载跨模块通用逻辑
（设计稿 §2.6）。对外类型名与构造签名保持不变（``AuthService(session)``）。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from .login_mixin import LoginMixin
from .read_mixin import ReadMixin
from .token_mixin import TokenMixin


class AuthService(ReadMixin, LoginMixin, TokenMixin):
    """认证业务。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
