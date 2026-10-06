"""认证令牌签发 / 刷新 / 登出（原 ``auth/service.py`` 292-378）。

:class:`TokenMixin` 承载 access + refresh 的签发、轮换与吊销；它交叉调用
:mod:`read_mixin` 的角色查询，用 ``TYPE_CHECKING`` 前置声明避免运行期循环 import。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import AuthChannel, DataScope
from app.core.db import unit_of_work
from app.core.errors import BusinessError, ErrorCode
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_token,
    refresh_token_expiry,
)
from app.modules.auth.models import AuthRefreshToken, User

from .common import IssuedTokens


class TokenMixin:
    """认证令牌：签发 / 刷新轮换 / 登出吊销。"""

    session: AsyncSession

    if TYPE_CHECKING:

        async def get_role_ids(self, user_id: UUID) -> list[str]: ...

    # ---------------------------------------------------------------- 令牌

    async def _issue_tokens(
        self, *, user: User, channel: AuthChannel, device_id: str | None
    ) -> IssuedTokens:
        """签发 access + refresh 并登记 refresh 摘要。**必须在事务内调用。**"""
        role_ids = await self.get_role_ids(user.id)
        # 注意用 DataScope(...) 包一层：users.data_scope 映射的是 String(16) 而不是
        # PG enum，所以从库里读回来的值是普通 str，直接 .value 会 AttributeError。
        # （模型注解写的是 Mapped[DataScope]，但没有 TypeDecorator 时运行期并不保证。）
        access_token, expires_in = create_access_token(
            user_id=str(user.id),
            role_ids=role_ids,
            data_scope=DataScope(user.data_scope).value,
        )
        refresh_token = generate_refresh_token()
        expires_at = refresh_token_expiry(channel)
        self.session.add(
            AuthRefreshToken(
                user_id=user.id,
                token_hash=hash_token(refresh_token),
                channel=channel.value,
                device_id=device_id,
                expires_at=expires_at,
            )
        )
        return IssuedTokens(
            access_token=access_token,
            expires_in=expires_in,
            refresh_token=refresh_token,
            refresh_expires_at=expires_at,
        )

    async def refresh(self, refresh_token: str) -> IssuedTokens:
        """用 refresh token 换新的 access token，**并轮换 refresh token**。

        轮换是防重放的关键：旧 token 一旦被用就立即 ``revoked_at``，再次使用
        直接 11002。若不轮换，token 泄漏后攻击者可长期续期且用户毫无察觉。

        :raises BusinessError: ``11002`` 无效/已吊销/过期、``11004`` 账号停用
        """
        stmt = select(AuthRefreshToken).where(
            AuthRefreshToken.token_hash == hash_token(refresh_token)
        )
        result = await self.session.execute(stmt)
        record = result.scalar_one_or_none()
        now = datetime.now(tz=UTC)

        if record is None or record.revoked_at is not None or record.expires_at <= now:
            raise BusinessError(ErrorCode.TOKEN_INVALID, "登录凭证已失效，请重新登录")

        user = await self.session.get(User, record.user_id)
        if user is None or not user.is_active:
            raise BusinessError(ErrorCode.ACCOUNT_DISABLED, "账号已停用，请联系管理员")

        async with unit_of_work(self.session):
            # 旧 token 立即吊销 + 新 token 写入，同一 savepoint 保证不会出现
            # "两个都有效"的中间态
            record.revoked_at = now
            tokens = await self._issue_tokens(
                user=user,
                channel=AuthChannel(record.channel),
                device_id=record.device_id,
            )
        return tokens

    async def logout(self, user_id: UUID) -> int:
        """登出：吊销该用户全部未吊销 refresh token，返回吊销条数。

        全部吊销而不是只吊当前这一个：用户点"退出登录"的语义是"把这个人踢下线"。
        多设备同时登录时只踢一个会让另一台继续有效，用户会认为登出没生效。
        """
        async with unit_of_work(self.session):
            result = await self.session.execute(
                update(AuthRefreshToken)
                .where(
                    AuthRefreshToken.user_id == user_id,
                    AuthRefreshToken.revoked_at.is_(None),
                )
                .values(revoked_at=datetime.now(tz=UTC)),
                execution_options={"synchronize_session": False},
            )
        # rowcount 只在 CursorResult（DML）上存在，Session.execute 的静态返回类型
        # 是 Result[Tuple]，所以这里必须断言类型，不能靠 no-any-return 蒙混
        return int(cast("CursorResult[Any]", result).rowcount or 0)

    async def revoke_all_tokens(self, user_id: UUID) -> int:
        """吊销该用户全部 refresh token。"""
        return await self.logout(user_id)
