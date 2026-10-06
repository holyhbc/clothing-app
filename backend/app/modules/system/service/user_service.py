"""``SystemUserService`` 组合入口：仅继承 3 个内部 Mixin + ``__init__``。

3 个 Mixin 是「把一个 420 行类拆到多文件」的手段，不承载跨模块通用逻辑
（设计稿 §2.4）。对外类型名与构造签名保持不变（``SystemUserService(session, ctx)``）。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import AuthContext

from .user_guard_mixin import UserGuardMixin
from .user_query_mixin import UserQueryMixin
from .user_write_mixin import UserWriteMixin


class SystemUserService(UserQueryMixin, UserWriteMixin, UserGuardMixin):
    """用户管理。数据范围在 ``apply_data_scope`` 层强制（docs/07 §3.2）。"""

    def __init__(self, session: AsyncSession, ctx: AuthContext) -> None:
        self.session = session
        self.ctx = ctx
