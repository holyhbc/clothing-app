"""``AuthService`` 的数据库路径测试（docs/10 §4）。

端点级行为在 :mod:`tests.modules.test_auth_router`，这里只测**不经 HTTP**
也能覆盖到的分支：锁定计数、强制改密标记、重降级路径。

⚠️ 重点是"Redis 不可用"的降级：docs/07 §1.1 要求登录锁定在 Redis 故障时
退化为查 ``auth_login_logs``。这条路径平时测不到（Redis 一直可用），
所以这里用 monkeypatch 强制打掉 Redis。
"""

import pytest
from sqlalchemy import select

from app.common.enums import AuthChannel, DataScope
from app.core.errors import BusinessError, ErrorCode
from app.modules.auth.models import AuthLoginLog, AuthRefreshToken, User
from app.modules.auth.service import (
    LOCK_MINUTES,
    MAX_LOGIN_FAILURES,
    AuthService,
    build_lock_key,
)
from tests.factories.user import DEFAULT_PASSWORD, UserFactory


async def _seed(session, **overrides) -> User:
    user = await UserFactory.create(session, **overrides)
    await session.flush()
    return user


# --------------------------------------------------------------- 锁定计数


async def test_lock_threshold_is_five_and_window_is_fifteen_minutes() -> None:
    """docs/07 §1.1 的具体数字锁死，防止被"顺手调优"改掉。"""
    assert MAX_LOGIN_FAILURES == 5
    assert LOCK_MINUTES == 15


async def test_is_login_locked_reads_redis_counter(db_session, monkeypatch) -> None:
    from app.core.cache import get_redis

    service = AuthService(db_session)
    key = build_lock_key("LOCK01")
    client = get_redis()
    assert client is not None
    await client.set(key, MAX_LOGIN_FAILURES - 1, ex=60)
    assert await service.is_login_locked("LOCK01") is False

    await client.set(key, MAX_LOGIN_FAILURES, ex=60)
    assert await service.is_login_locked("LOCK01") is True
    await client.delete(key)


async def test_is_login_locked_does_not_consume_the_counter(db_session) -> None:
    """纯查询：**不能**把失败计数 +1。

    如果 ``is_login_locked`` 自增，一次探测就等于一次失败，探测 5 次账号就被锁了。
    """
    from app.core.cache import get_redis

    service = AuthService(db_session)
    key = build_lock_key("LOCK02")
    client = get_redis()
    assert client is not None
    await client.set(key, 0, ex=60)
    for _ in range(3):
        assert await service.is_login_locked("LOCK02") is False
    assert await client.get(key) == b"0"
    await client.delete(key)


async def test_lock_falls_back_to_log_window_when_redis_down(db_session, monkeypatch) -> None:
    """Redis 挂掉时降级为查 ``auth_login_logs``（docs/07 §1.1）。"""
    monkeypatch.setattr("app.modules.auth.service.redis_get_int", _always_none)
    monkeypatch.setattr("app.modules.auth.service.redis_incr_with_ttl", _always_none)
    service = AuthService(db_session)
    employee_no = "FALL01"

    for _index in range(MAX_LOGIN_FAILURES - 1):
        db_session.add(
            AuthLoginLog(
                employee_no=employee_no,
                channel=AuthChannel.PC.value,
                is_success=False,
                fail_reason="工号或口令错误",
            )
        )
    await db_session.flush()
    assert await service.is_login_locked(employee_no) is False

    db_session.add(
        AuthLoginLog(
            employee_no=employee_no,
            channel=AuthChannel.PC.value,
            is_success=False,
            fail_reason="工号或口令错误",
        )
    )
    await db_session.flush()
    assert await service.is_login_locked(employee_no) is True


async def test_log_window_ignores_old_failures(db_session, monkeypatch) -> None:
    """窗口外的失败不计入 —— 否则锁 15 分钟就变成了永久锁。"""
    from datetime import UTC, datetime, timedelta

    monkeypatch.setattr("app.modules.auth.service.redis_get_int", _always_none)
    old = datetime.now(tz=UTC) - timedelta(minutes=LOCK_MINUTES + 5)
    for _index in range(MAX_LOGIN_FAILURES + 3):
        db_session.add(
            AuthLoginLog(
                employee_no="OLD01",
                channel=AuthChannel.PC.value,
                is_success=False,
                fail_reason="工号或口令错误",
                created_at=old,
            )
        )
    await db_session.flush()
    assert await AuthService(db_session).is_login_locked("OLD01") is False


async def test_successful_login_clears_failures(db_session) -> None:
    from app.core.cache import get_redis

    user = await _seed(db_session, employee_no="CLR01")
    client = get_redis()
    assert client is not None
    key = build_lock_key("CLR01")
    await client.set(key, 3, ex=60)

    await AuthService(db_session).authenticate(
        employee_no="CLR01", password=DEFAULT_PASSWORD, channel=AuthChannel.PC
    )
    assert await client.get(key) is None
    assert user.employee_no == "CLR01"


# ------------------------------------------------------------ 角色与车间


async def test_get_roles_is_sorted_and_skips_deleted(db_session) -> None:
    from tests.factories.user import RoleFactory, UserRoleFactory

    user = await _seed(db_session, employee_no="ROLE01")
    zeta = await RoleFactory.create(db_session, code="zeta", name="Z")
    alpha = await RoleFactory.create(db_session, code="alpha", name="A")
    gone = await RoleFactory.create(db_session, code="gone", name="G")
    from datetime import UTC, datetime

    gone.deleted_at = datetime.now(tz=UTC)
    for role in (zeta, alpha, gone):
        await UserRoleFactory.create(db_session, user_id=user.id, role_id=role.id)
    await db_session.flush()

    codes = [role.code for role in await AuthService(db_session).get_roles(user.id)]
    assert codes == ["alpha", "zeta"]


async def test_get_role_ids_are_sorted_strings(db_session) -> None:
    user = await _seed(db_session, employee_no="ROLE02")
    service = AuthService(db_session)
    assert await service.get_role_ids(user.id) == []
    from tests.factories.user import RoleFactory, UserRoleFactory

    role = await RoleFactory.create(db_session, code="r1", name="R")
    await UserRoleFactory.create(db_session, user_id=user.id, role_id=role.id)
    await db_session.flush()
    ids = await service.get_role_ids(user.id)
    assert ids == [str(role.id)]


async def test_allowed_workshops_empty_without_role_ids(db_session) -> None:
    assert await AuthService(db_session).get_allowed_workshop_ids(None) == frozenset()
    assert await AuthService(db_session).get_allowed_workshop_ids([]) == frozenset()


# ---------------------------------------------------------------- 改口令


async def test_change_password_clears_must_change_flag(db_session) -> None:
    user = await _seed(db_session, employee_no="PWD01", must_change_password=True)
    await AuthService(db_session).change_password(
        user_id=user.id, old_password=DEFAULT_PASSWORD, new_password="BrandNew9"
    )
    refreshed = await db_session.get(User, user.id)
    assert refreshed is not None
    assert refreshed.must_change_password is False
    assert refreshed.version == 2


async def test_change_password_rehashes_on_success(db_session) -> None:
    user = await _seed(db_session, employee_no="PWD02")
    before = user.password_hash
    await AuthService(db_session).change_password(
        user_id=user.id, old_password=DEFAULT_PASSWORD, new_password="BrandNew9"
    )
    refreshed = await db_session.get(User, user.id)
    assert refreshed is not None
    assert refreshed.password_hash != before
    assert refreshed.password_hash.startswith("$argon2id$")


async def test_revoke_all_tokens_is_alias_of_logout(db_session) -> None:
    user = await _seed(db_session, employee_no="TOK01")
    service = AuthService(db_session)
    for _ in range(2):
        db_session.add(
            AuthRefreshToken(
                user_id=user.id,
                token_hash=f"hash-{_}",
                channel=AuthChannel.PC.value,
                expires_at=_future(),
            )
        )
    await db_session.flush()
    assert await service.revoke_all_tokens(user.id) == 2


async def test_get_active_user_rejects_unknown(db_session) -> None:
    from uuid import uuid4

    with pytest.raises(BusinessError) as excinfo:
        await AuthService(db_session).get_active_user(uuid4())
    assert excinfo.value.code is ErrorCode.UNAUTHORIZED


async def test_token_is_signed_when_data_scope_comes_back_as_str(db_session) -> None:
    """``users.data_scope`` 映射的是 String 列，所以**重新查出来的**值是 ``str``。

    这不是"DataScope 能吃 str"这种语言特性（那是废话），而是一条真实回归：
    登录时若直接写 ``user.data_scope.value``，第二次登录（对象从库里重载）
    就会抛 ``AttributeError: 'str' object has no attribute 'value'``。
    第一条用例恰好用的是刚 ``create`` 出来、属性还是枚举的对象，测不出来。
    """
    user = await _seed(db_session, employee_no="STR001", data_scope=DataScope.WORKSHOP)
    await db_session.flush()
    await db_session.refresh(user)  # 强制从库里重载，data_scope 变成 str
    assert isinstance(user.data_scope, str)
    assert not isinstance(user.data_scope, DataScope)

    outcome = await AuthService(db_session).authenticate(
        employee_no="STR001", password=DEFAULT_PASSWORD, channel=AuthChannel.PC
    )
    assert outcome.tokens.access_token

    from app.core.security import decode_token

    assert decode_token(outcome.tokens.access_token)["data_scope"] == "WORKSHOP"


async def test_refresh_rejects_expired_token(db_session) -> None:
    from datetime import UTC, datetime, timedelta

    user = await _seed(db_session, employee_no="EXP01")
    db_session.add(
        AuthRefreshToken(
            user_id=user.id,
            token_hash="x",
            channel=AuthChannel.PC.value,
            expires_at=datetime.now(tz=UTC) - timedelta(seconds=1),
        )
    )
    await db_session.flush()
    with pytest.raises(BusinessError) as excinfo:
        await AuthService(db_session).refresh("whatever")
    assert excinfo.value.code is ErrorCode.TOKEN_INVALID


async def test_refresh_rejects_token_of_disabled_user(db_session) -> None:
    from app.core.security import hash_token

    user = await _seed(db_session, employee_no="DIS01", is_active=False)
    token = "plain-refresh"  # noqa: S105 —— 测试夹具，非真实凭据
    db_session.add(
        AuthRefreshToken(
            user_id=user.id,
            token_hash=hash_token(token),
            channel=AuthChannel.PC.value,
            expires_at=_future(),
        )
    )
    await db_session.flush()
    with pytest.raises(BusinessError) as excinfo:
        await AuthService(db_session).refresh(token)
    assert excinfo.value.code is ErrorCode.ACCOUNT_DISABLED


async def test_change_password_unknown_user_raises(db_session) -> None:
    from uuid import uuid4

    with pytest.raises(BusinessError):
        await AuthService(db_session).change_password(
            user_id=uuid4(), old_password=DEFAULT_PASSWORD, new_password="BrandNew9"
        )


async def test_login_log_rows_are_queryable(db_session) -> None:
    """append-only 表没有软删位，用普通 select 即可。"""
    rows = await db_session.execute(select(AuthLoginLog).limit(1))
    assert rows.scalars().all() == []


# ---------------------------------------------------------------- 辅助


def _future():
    from datetime import UTC, datetime, timedelta

    return datetime.now(tz=UTC) + timedelta(days=7)


async def _always_none(*_args: object, **_kwargs: object) -> None:
    return None
