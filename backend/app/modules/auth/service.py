"""认证业务逻辑（docs/07-认证与权限规范.md §1）。

⚠️ **事务边界只在 service 层**（AGENTS.md §2.1）：本模块的写方法自己
``commit()``，router 不得再 commit。

⚠️ 为什么用显式 ``commit()`` 而不是 ``async with session.begin()``：
    SQLAlchemy 的 autobegin 会在**任何读操作后**就打开事务，此时再
    ``session.begin()`` 会抛 "transaction already begun"；而在测试里 session
    绑在外层事务上（``join_transaction_mode="create_savepoint"``），``commit()``
    只是释放 savepoint，外层回滚照样清干净 —— 两种场景语义都正确。

本模块的 6 条安全约定（每条都对应一个可测的行为）：
    1. **不区分"工号不存在"与"口令错误"** —— 两者都返回 11003。若分开报错，
       任何人可以枚举出全厂工号（工号是递增的，猜中很容易）。
    2. 登录**成功与失败都写** ``auth_login_logs``（append-only）：失败日志是
       事后审计爆破的唯一依据。
    3. 连续 5 次失败锁 15 分钟（docs/07 §1.1）：先读 Redis 计数，不可用时降级为
       查 ``auth_login_logs`` 的时间窗口。
    4. refresh token **每次刷新都轮换**，旧的立即吊销；重放旧 token 直接 11002。
    5. refresh token 库里只存 sha256 摘要，明文只出现在 HttpOnly Cookie 里。
    6. 改密后吊销该用户**全部** refresh token（否则别人手上的旧 token 还能用）。
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from redis.exceptions import RedisError
from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import AuthChannel, DataScope
from app.core.cache import get_redis, redis_get_int, redis_incr_with_ttl
from app.core.errors import BusinessError, ErrorCode
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_token,
    refresh_token_expiry,
    validate_password_strength,
    verify_password,
    verify_password_and_check_rehash,
)
from app.modules.auth.models import (
    AuthLoginLog,
    AuthRefreshToken,
    Role,
    RoleWorkshop,
    User,
    UserRole,
)

logger = logging.getLogger("app.auth")

#: 连续失败多少次后锁定（docs/07 §1.1）
MAX_LOGIN_FAILURES = 5
#: 锁定时长（docs/07 §1.1）
LOCK_MINUTES = 15

#: 失败计数的 Redis key 前缀。**按工号分键**，员工端爆破不影响其他账号
_LOCK_KEY_PREFIX = "auth:login_fail:"


@asynccontextmanager
async def unit_of_work(session: AsyncSession) -> AsyncIterator[None]:
    """service 层唯一的事务入口（docs/03 §1.1 第 5 条）。

    退出时**提交**，这是"事务边界只在 service"的落地方式 —— ``get_db`` 只负责
    建连接与关连接，全项目没有第二处 commit。

    为什么用 ``begin_nested()``（savepoint）而不是 ``begin()``：
        - 生产：请求期间前面的 SELECT 已触发 autobegin，此时 ``begin()`` 会抛
          "transaction already begun"；savepoint 可以在既有事务里安全开
        - 测试：session 绑在外层事务上（``join_transaction_mode="create_savepoint"``），
          提交 savepoint 只释放 savepoint，外层回滚照样把数据清干净，测试零污染
    """
    async with session.begin_nested():
        yield
    await session.commit()


@dataclass(frozen=True, slots=True)
class IssuedTokens:
    """一次签发结果。``refresh_token`` 只用于写 Cookie，不进响应体。"""

    access_token: str
    expires_in: int
    refresh_token: str
    refresh_expires_at: datetime


@dataclass(frozen=True, slots=True)
class LoginOutcome:
    """登录成功后的完整结果。"""

    user: User
    tokens: IssuedTokens
    roles: tuple[str, ...]


class AuthService:
    """认证业务。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

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


def build_lock_key(employee_no: str) -> str:
    """失败计数的 Redis key（导出供测试清理，避免各处硬编码前缀）。"""
    return f"{_LOCK_KEY_PREFIX}{employee_no}"
