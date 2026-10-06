"""认证登录与改口令（原 ``auth/service.py`` 181-288、382-419）。

:class:`LoginMixin` 承载口令登录（含失败计数与日志）与改口令；它交叉调用
:mod:`read_mixin` 的锁定助手与 :mod:`token_mixin` 的签发方法，用 ``TYPE_CHECKING``
前置声明避免运行期循环 import。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import AuthChannel
from app.core.db import unit_of_work
from app.core.errors import BusinessError, ErrorCode
from app.core.security import (
    hash_password,
    validate_password_strength,
    verify_password,
    verify_password_and_check_rehash,
)
from app.modules.auth.models import AuthLoginLog, AuthRefreshToken, Role, User

from .common import LOCK_MINUTES, MAX_LOGIN_FAILURES, IssuedTokens, LoginOutcome, logger


class LoginMixin:
    """认证写：口令登录 / 登录日志 / 改口令。"""

    session: AsyncSession

    if TYPE_CHECKING:

        async def is_login_locked(self, employee_no: str) -> bool: ...

        async def _register_failure(self, employee_no: str) -> None: ...

        async def _clear_failures(self, employee_no: str) -> None: ...

        async def get_roles(self, user_id: UUID) -> list[Role]: ...

        async def _issue_tokens(
            self, *, user: User, channel: AuthChannel, device_id: str | None
        ) -> IssuedTokens: ...

    # ---------------------------------------------------------------- 登录

    async def authenticate(
        self,
        *,
        employee_no: str,
        password: str,
        channel: AuthChannel,
        ip: str | None = None,
        user_agent: str | None = None,
        device_id: str | None = None,
    ) -> LoginOutcome:
        """口令登录。

        :raises BusinessError: ``10004`` 已锁定、``11003`` 工号或口令不对、
            ``11004`` 账号停用
        """
        if await self.is_login_locked(employee_no):
            raise BusinessError(
                ErrorCode.TOO_MANY_REQUESTS,
                f"连续 {MAX_LOGIN_FAILURES} 次登录失败，账号已锁定，请 {LOCK_MINUTES} 分钟后再试",
                details={"lock_minutes": LOCK_MINUTES},
            )

        user = await self._find_user_by_employee_no(employee_no)
        # 用户不存在与口令错误走**完全相同**的分支与文案，防止枚举工号。
        # argon2 校验只跑一次：verify_and_update 同时返回"是否需要重算哈希"，
        # 分成两次调用会让登录多花一倍 CPU（argon2 默认就是故意慢的）。
        verified, updated_hash = (
            verify_password_and_check_rehash(password, user.password_hash)
            if user is not None
            else (False, None)
        )
        if user is None or not verified:
            reason = "工号或口令错误"
            await self._record_login(
                employee_no=employee_no,
                channel=channel,
                ip=ip,
                user_agent=user_agent,
                is_success=False,
                fail_reason=reason,
                user_id=user.id if user else None,
            )
            await self._register_failure(employee_no)
            raise BusinessError(ErrorCode.PASSWORD_INCORRECT, reason)

        if not user.is_active:
            # 停用账号**不计入失败次数**：员工离职后系统还要能查审计，
            # 不该让他反复尝试把自己锁死
            await self._record_login(
                employee_no=employee_no,
                channel=channel,
                ip=ip,
                user_agent=user_agent,
                is_success=False,
                fail_reason="账号已停用",
                user_id=user.id,
            )
            raise BusinessError(ErrorCode.ACCOUNT_DISABLED, "账号已停用，请联系管理员")

        async with unit_of_work(self.session):
            # argon2 参数升级后顺带重算哈希，省得每人都要等下次登录才更新
            if updated_hash:
                await self.session.execute(
                    update(User).where(User.id == user.id).values(password_hash=updated_hash)
                )
            tokens = await self._issue_tokens(user=user, channel=channel, device_id=device_id)
        await self._record_login(
            employee_no=employee_no,
            channel=channel,
            ip=ip,
            user_agent=user_agent,
            is_success=True,
            fail_reason=None,
            user_id=user.id,
        )
        await self._clear_failures(employee_no)
        roles = await self.get_roles(user.id)
        return LoginOutcome(user=user, tokens=tokens, roles=tuple(role.code for role in roles))

    async def _find_user_by_employee_no(self, employee_no: str) -> User | None:
        stmt = select(User).where(User.employee_no == employee_no, User.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def _record_login(
        self,
        *,
        employee_no: str | None,
        channel: AuthChannel,
        is_success: bool,
        fail_reason: str | None,
        ip: str | None,
        user_agent: str | None,
        user_id: UUID | None,
    ) -> None:
        """写登录日志（append-only，docs/04 §7.9 同类）。独立事务，不受调用方影响。"""
        async with unit_of_work(self.session):
            self.session.add(
                AuthLoginLog(
                    employee_no=employee_no,
                    channel=channel.value,
                    ip=ip,
                    user_agent=user_agent[:500] if user_agent else None,
                    is_success=is_success,
                    fail_reason=fail_reason,
                    user_id=user_id,
                )
            )

    # -------------------------------------------------------------- 改口令

    async def change_password(self, *, user_id: UUID, old_password: str, new_password: str) -> None:
        """改口令。

        :raises BusinessError: ``11003`` 原口令不对、``10001`` 新口令不满足强度
        """
        if old_password == new_password:
            raise BusinessError(ErrorCode.PARAM_INVALID, "新口令不能与原口令相同")

        # 先校验强度再查库：弱口令不该消耗一次数据库往返，
        # 也避免用"报错快慢"探测口令策略的细节
        validate_password_strength(new_password)

        user = await self.session.get(User, user_id)
        if user is None:
            raise BusinessError(ErrorCode.UNAUTHORIZED, "登录已失效，请重新登录")
        if not verify_password(old_password, user.password_hash):
            raise BusinessError(ErrorCode.PASSWORD_INCORRECT, "原口令错误")

        async with unit_of_work(self.session):
            await self.session.execute(
                update(User)
                .where(User.id == user_id)
                .values(
                    password_hash=hash_password(new_password),
                    must_change_password=False,
                    version=User.version + 1,
                )
            )
            # 改密后强制重登：旧 refresh token 若继续有效，改密就等于没改
            await self.session.execute(
                update(AuthRefreshToken)
                .where(
                    AuthRefreshToken.user_id == user_id,
                    AuthRefreshToken.revoked_at.is_(None),
                )
                .values(revoked_at=datetime.now(tz=UTC))
            )
        logger.info("用户修改口令成功", extra={"user_id": str(user_id)})
