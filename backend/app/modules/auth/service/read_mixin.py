"""认证读路径与登录锁定（原 ``auth/service.py`` 96-177）。

:class:`ReadMixin` 只承载**读**与锁定计数助手；登录在 :mod:`login_mixin`，
令牌在 :mod:`token_mixin`。``redis_get_int`` / ``redis_incr_with_ttl`` 在本
模块命名空间被运行时查找，故 ``test_auth_service.py`` 的 monkeypatch 目标必须
指向本模块（设计稿 §2.8）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import get_redis, redis_get_int, redis_incr_with_ttl
from app.core.errors import BusinessError, ErrorCode
from app.modules.auth.models import AuthLoginLog, Role, RoleWorkshop, User, UserRole

from .common import _LOCK_KEY_PREFIX, LOCK_MINUTES, MAX_LOGIN_FAILURES, logger


class ReadMixin:
    """认证读：用户 / 角色 / 车间 + 登录锁定计数。"""

    session: AsyncSession

    # ------------------------------------------------------------------ 查

    async def get_active_user(self, user_id: UUID) -> User:
        """取用户并校验可用性。:func:`app.core.permissions.get_auth_context` 依赖它。

        :raises BusinessError: ``11001`` 用户不存在、``11004`` 已停用
        """
        user = await self.session.get(User, user_id)
        if user is None:
            raise BusinessError(ErrorCode.UNAUTHORIZED, "登录已失效，请重新登录")
        if not user.is_active:
            raise BusinessError(ErrorCode.ACCOUNT_DISABLED, "账号已停用，请联系管理员")
        return user

    async def get_role_ids(self, user_id: UUID) -> list[str]:
        """用户已授予的角色 ID 列表（放进 JWT 载荷，供后续查权限用）。"""
        stmt = (
            select(UserRole.role_id).where(UserRole.user_id == user_id).order_by(UserRole.role_id)
        )
        rows = await self.session.execute(stmt)
        return [str(item) for item in rows.scalars().all()]

    async def get_roles(self, user_id: UUID) -> list[Role]:
        """用户已授予的角色实体（``/me`` 用）。按 code 排序保证响应稳定。"""
        stmt = (
            select(Role)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user_id, Role.deleted_at.is_(None))
            .order_by(Role.code)
        )
        rows = await self.session.execute(stmt)
        return list(rows.scalars().all())

    async def get_allowed_workshop_ids(self, role_ids: list[str] | None) -> frozenset[UUID]:
        """``WORKSHOP`` 范围下可见的车间 = 各角色授予的车间的并集。

        返回空集时查不到任何数据，这是**正确**行为（docs/07 §3.2 明确要求），
        不是 bug。
        """
        if not role_ids:
            return frozenset()
        stmt = select(RoleWorkshop.workshop_id).where(
            RoleWorkshop.role_id.in_([UUID(item) for item in role_ids])
        )
        rows = await self.session.execute(stmt)
        return frozenset(rows.scalars().all())

    # ------------------------------------------------------------ 登录锁定

    async def is_login_locked(self, employee_no: str) -> bool:
        """该工号当前是否被锁定。**只读，不改计数。**"""
        count = await redis_get_int(f"{_LOCK_KEY_PREFIX}{employee_no}")
        if count is not None:
            return count >= MAX_LOGIN_FAILURES
        return await self._count_recent_failures(employee_no) >= MAX_LOGIN_FAILURES

    async def _count_recent_failures(self, employee_no: str) -> int:
        """降级路径：统计最近窗口内的登录失败数（Redis 不可用时用）。"""
        since = datetime.now(tz=UTC) - timedelta(minutes=LOCK_MINUTES)
        stmt = (
            select(func.count())
            .select_from(AuthLoginLog)
            .where(
                AuthLoginLog.employee_no == employee_no,
                AuthLoginLog.is_success.is_(False),
                AuthLoginLog.created_at >= since,
            )
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def _register_failure(self, employee_no: str) -> None:
        """登记一次失败（Redis 自增 +1 并设 TTL）。"""
        await redis_incr_with_ttl(f"{_LOCK_KEY_PREFIX}{employee_no}", LOCK_MINUTES * 60)

    async def _clear_failures(self, employee_no: str) -> None:
        """登录成功后清零失败计数。"""
        client = get_redis()
        if client is None:
            return
        try:
            await client.delete(f"{_LOCK_KEY_PREFIX}{employee_no}")
        except (RedisError, OSError, TimeoutError):
            logger.warning("清理登录失败计数失败", extra={"employee_no": employee_no})
