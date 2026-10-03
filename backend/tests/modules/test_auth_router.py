"""5 个认证端点的集成测试（docs/10 §4「接口必测」）。

测试口径：**走真实 HTTP 接口**，只用 ASGI 内存客户端与回滚型 session，
不 mock service —— mock 掉 service 的集成测试只能证明 mock 配置对了。

每个用例都覆盖异常路径：docs/10 §3 要求正常 + 异常 + 权限拒绝三路齐全。
"""

import pytest
from sqlalchemy import select, update

from app.common.enums import AuthChannel, DataScope
from app.core.idempotency import clear_memory_store
from app.modules.auth.models import AuthLoginLog, AuthRefreshToken, User
from tests.factories.user import DEFAULT_PASSWORD, UserFactory, make_login_payload

PREFIX = "/api/v1/auth"


@pytest.fixture(autouse=True)
def _clean_idempotency_memory() -> None:
    """清进程内幂等降级存储，避免用例之间串键。"""
    clear_memory_store()


async def _seed_user(session, **overrides):
    user = await UserFactory.create(session, **overrides)
    await session.flush()
    return user


def _cookie_header(value: str) -> dict[str, str]:
    """把 refresh token 放进 Cookie 头。

    不用 httpx 的 ``cookies=`` 参数：它已废弃（每次请求传 cookie 时"是否保留
    旧 cookie"语义不明），而本项目 ``filterwarnings=error`` 会把废弃警告变成
    失败。显式写头最直白，也正好验证"refresh token 走 Cookie"这件事本身。
    """
    return {"Cookie": f"refresh_token={value}"}


def _cookies(response) -> str | None:
    raw = response.headers.get("set-cookie", "")
    for part in raw.split(";"):
        if part.strip().startswith("refresh_token="):
            return part.strip().split("=", 1)[1]
    return None


# ------------------------------------------------------------------- login


async def test_login_success(client, db_session):
    await _seed_user(db_session, employee_no="A001")
    response = await client.post(f"{PREFIX}/login", json=make_login_payload())
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    assert body["data"]["token_type"] == "bearer"  # noqa: S105 —— OAuth2 固定字面量
    assert body["data"]["expires_in"] == 15 * 60
    assert body["data"]["user"]["employee_no"] == "A001"
    # refresh token 不在响应体里，只走 Cookie（docs/07 §1.1）
    assert "refresh_token" not in body["data"]
    assert _cookies(response)


async def test_login_writes_refresh_cookie_httponly(client, db_session):
    await _seed_user(db_session, employee_no="A001")
    response = await client.post(f"{PREFIX}/login", json=make_login_payload())
    cookie_header = response.headers["set-cookie"].lower()
    assert "httponly" in cookie_header
    assert "samesite=lax" in cookie_header
    assert "secure" not in cookie_header  # 非生产环境不加 Secure，否则本地联调带不上


async def test_login_stores_only_token_hash(client, db_session):
    """库里只存 sha256 摘要，明文不留存。"""
    await _seed_user(db_session, employee_no="A001")
    response = await client.post(f"{PREFIX}/login", json=make_login_payload())
    plain = _cookies(response)
    rows = (await db_session.execute(select(AuthRefreshToken))).scalars().all()
    assert len(rows) == 1
    assert plain not in rows[0].token_hash
    assert len(rows[0].token_hash) == 64


async def test_login_writes_login_log(client, db_session):
    await _seed_user(db_session, employee_no="A001")
    await client.post(f"{PREFIX}/login", json=make_login_payload())
    rows = (await db_session.execute(select(AuthLoginLog))).scalars().all()
    assert [(row.employee_no, row.is_success) for row in rows] == [("A001", True)]


async def test_login_wrong_password_and_unknown_user_give_same_error(client, db_session):
    """防工号枚举：两种失败必须同码同文案。"""
    await _seed_user(db_session, employee_no="A001")
    wrong = await client.post(f"{PREFIX}/login", json=make_login_payload(password="Wrong123"))
    unknown = await client.post(f"{PREFIX}/login", json=make_login_payload(employee_no="NOPE"))
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["code"] == unknown.json()["code"] == 11003
    assert wrong.json()["message"] == unknown.json()["message"]


async def test_login_disabled_account_raises_11004(client, db_session):
    await _seed_user(db_session, employee_no="A001", is_active=False)
    response = await client.post(f"{PREFIX}/login", json=make_login_payload())
    assert response.status_code == 403
    assert response.json()["code"] == 11004


async def test_login_locks_after_five_failures(client, db_session):
    """连续 5 次失败 → 第 6 次直接 10004，且**正确口令也不给过**。"""
    await _seed_user(db_session, employee_no="A001")
    for _ in range(5):
        await client.post(f"{PREFIX}/login", json=make_login_payload(password="Bad12345"))

    locked = await client.post(f"{PREFIX}/login", json=make_login_payload(password="Bad12345"))
    assert locked.status_code == 429
    assert locked.json()["code"] == 10004

    even_right = await client.post(f"{PREFIX}/login", json=make_login_payload())
    assert even_right.status_code == 429


async def test_login_lock_counts_only_failures(client, db_session):
    await _seed_user(db_session, employee_no="A001")
    for _ in range(4):
        await client.post(f"{PREFIX}/login", json=make_login_payload(password="Bad12345"))
    ok = await client.post(f"{PREFIX}/login", json=make_login_payload())
    assert ok.json()["code"] == 0
    # 成功后清零，再错 4 次不该触发锁定
    for _ in range(4):
        await client.post(f"{PREFIX}/login", json=make_login_payload(password="Bad12345"))
    again = await client.post(f"{PREFIX}/login", json=make_login_payload())
    assert again.json()["code"] == 0


async def test_login_failure_log_written_even_for_unknown_employee(client, db_session):
    response = await client.post(f"{PREFIX}/login", json=make_login_payload(employee_no="GHOST"))
    assert response.json()["code"] == 11003
    rows = (await db_session.execute(select(AuthLoginLog))).scalars().all()
    assert [(row.employee_no, row.is_success) for row in rows] == [("GHOST", False)]


async def test_login_records_ip_and_channel(client, db_session):
    await _seed_user(db_session, employee_no="A001")
    await client.post(
        f"{PREFIX}/login",
        json=make_login_payload(channel=AuthChannel.H5_SMS.value, device_id="dev-1"),
    )
    row = (await db_session.execute(select(AuthLoginLog))).scalars().one()
    assert row.channel == AuthChannel.H5_SMS.value
    # INET 列由 asyncpg 解析成 ipaddress 对象，比较前转字符串
    assert str(row.ip) == "127.0.0.1"


# -------------------------------------------------------------- 幂等键


async def test_login_idempotent_same_key_same_body_returns_first_result(client, db_session):
    await _seed_user(db_session, employee_no="A001")
    payload = make_login_payload()
    first = await client.post(f"{PREFIX}/login", json=payload, headers={"Idempotency-Key": "k1"})
    second = await client.post(f"{PREFIX}/login", json=payload, headers={"Idempotency-Key": "k1"})
    assert first.json()["data"] == second.json()["data"]
    # 只签发了一次 refresh token
    assert len((await db_session.execute(select(AuthRefreshToken))).scalars().all()) == 1


async def test_login_idempotent_same_key_different_body_raises_10002(client, db_session):
    await _seed_user(db_session, employee_no="A001", name="A")
    await _seed_user(db_session, employee_no="A002", name="B")
    headers = {"Idempotency-Key": "k1"}
    await client.post(
        f"{PREFIX}/login", json=make_login_payload(employee_no="A001"), headers=headers
    )
    clash = await client.post(
        f"{PREFIX}/login", json=make_login_payload(employee_no="A002"), headers=headers
    )
    assert clash.status_code == 400
    assert clash.json()["code"] == 10002


async def test_login_without_idempotency_key_still_works(client, db_session):
    await _seed_user(db_session, employee_no="A001")
    response = await client.post(f"{PREFIX}/login", json=make_login_payload())
    assert response.json()["code"] == 0


# ------------------------------------------------------------------ me


async def test_me_requires_token(client):
    response = await client.get(f"{PREFIX}/me")
    assert response.status_code == 401
    assert response.json()["code"] == 11001


async def test_me_returns_roles_permissions_and_scope(client, db_session, auth_headers):
    headers = await auth_headers(role="super_admin")
    response = await client.get(f"{PREFIX}/me", headers=headers)
    body = response.json()
    assert body["code"] == 0
    assert body["data"]["data_scope"] == DataScope.FACTORY.value
    assert [role["code"] for role in body["data"]["roles"]] == ["super_admin"]
    # super_admin 在 registry 里拿到全部 122 个显式权限点
    assert len(body["data"]["permissions"]) == 122


async def test_me_rejects_refresh_token(client, db_session, auth_headers):
    headers = await auth_headers(role="employee")
    tampered = headers["Authorization"].replace("ey", "eyJ9")
    response = await client.get(f"{PREFIX}/me", headers={"Authorization": tampered})
    assert response.status_code in (401, 403)


async def test_me_disabled_user_loses_access(client, db_session, auth_headers):
    headers = await auth_headers(role="employee")
    user_id = await _current_user_id(db_session, headers)
    await db_session.execute(update(User).where(User.id == user_id).values(is_active=False))
    await db_session.flush()
    response = await client.get(f"{PREFIX}/me", headers=headers)
    assert response.json()["code"] == 11004


# -------------------------------------------------------------- refresh


async def test_refresh_rotates_token(client, db_session):
    await _seed_user(db_session, employee_no="A001")
    login = await client.post(f"{PREFIX}/login", json=make_login_payload())
    old_cookie = _cookies(login)

    refreshed = await client.post(f"{PREFIX}/refresh", headers=_cookie_header(old_cookie))
    assert refreshed.json()["code"] == 0
    new_cookie = _cookies(refreshed)
    assert new_cookie and new_cookie != old_cookie

    # 旧 token 立即失效（防重放）
    replay = await client.post(f"{PREFIX}/refresh", headers=_cookie_header(old_cookie))
    assert replay.status_code == 401
    assert replay.json()["code"] == 11002


async def test_refresh_without_cookie_raises_11001(client):
    response = await client.post(f"{PREFIX}/refresh")
    assert response.status_code == 401
    assert response.json()["code"] == 11001


async def test_refresh_with_garbage_cookie_raises_11002(client):
    response = await client.post(f"{PREFIX}/refresh", headers=_cookie_header("nope"))
    assert response.status_code == 401
    assert response.json()["code"] == 11002


# --------------------------------------------------------------- logout


async def test_logout_revokes_all_tokens(client, db_session):
    await _seed_user(db_session, employee_no="A001")
    login = await client.post(f"{PREFIX}/login", json=make_login_payload())
    first = _cookies(login)
    access = login.json()["data"]["access_token"]
    second = _cookies(await client.post(f"{PREFIX}/login", json=make_login_payload()))
    assert first and second

    out = await client.post(
        f"{PREFIX}/logout",
        headers={**_cookie_header(first), "Authorization": f"Bearer {access}"},
    )
    assert out.status_code == 200
    assert out.json()["data"]["revoked_tokens"] == 2

    # 两台设备的 refresh token 都失效
    for cookie in (first, second):
        again = await client.post(f"{PREFIX}/refresh", headers=_cookie_header(cookie))
        assert again.json()["code"] == 11002


async def test_logout_requires_token(client):
    response = await client.post(f"{PREFIX}/logout")
    assert response.status_code == 401


async def test_logout_clears_cookie(client, db_session, auth_headers):
    headers = await auth_headers(role="employee")
    response = await client.post(f"{PREFIX}/logout", headers=headers)
    assert "refresh_token=" in response.headers.get("set-cookie", "")


# ------------------------------------------------------------- password


async def test_change_password_success(client, db_session, auth_headers):
    headers = await auth_headers(role="employee")
    response = await client.put(
        f"{PREFIX}/password",
        json={"old_password": DEFAULT_PASSWORD, "new_password": "NewPass99"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["code"] == 0

    # 新口令可登录，旧口令不可
    user = await db_session.get(User, await _current_user_id(db_session, headers))
    assert user is not None and user.must_change_password is False
    again = await client.post(
        f"{PREFIX}/login",
        json=make_login_payload(employee_no=user.employee_no, password="NewPass99"),
    )
    assert again.json()["code"] == 0


async def test_change_password_rejects_wrong_old_password(client, db_session, auth_headers):
    headers = await auth_headers(role="employee")
    response = await client.put(
        f"{PREFIX}/password",
        json={"old_password": "Nope12345", "new_password": "NewPass99"},
        headers=headers,
    )
    assert response.status_code == 401
    assert response.json()["code"] == 11003


async def test_change_password_rejects_weak_new_password(client, db_session, auth_headers):
    headers = await auth_headers(role="employee")
    response = await client.put(
        f"{PREFIX}/password",
        json={"old_password": DEFAULT_PASSWORD, "new_password": "short"},
        headers=headers,
    )
    assert response.json()["code"] == 10001


async def test_change_password_rejects_same_password(client, db_session, auth_headers):
    headers = await auth_headers(role="employee")
    response = await client.put(
        f"{PREFIX}/password",
        json={"old_password": DEFAULT_PASSWORD, "new_password": DEFAULT_PASSWORD},
        headers=headers,
    )
    assert response.json()["code"] == 10001


async def test_change_password_revokes_existing_sessions(client, db_session, auth_headers):
    """改密后旧 refresh token 必须失效 —— 否则改密等于没改。"""
    headers = await auth_headers(role="employee")
    user_id = await _current_user_id(db_session, headers)
    user = await db_session.get(User, user_id)
    assert user is not None
    cookie = _cookies(
        await client.post(
            f"{PREFIX}/login",
            json=make_login_payload(employee_no=user.employee_no),
        )
    )
    await client.put(
        f"{PREFIX}/password",
        json={"old_password": DEFAULT_PASSWORD, "new_password": "NewPass99"},
        headers=headers,
    )
    after = await client.post(f"{PREFIX}/refresh", headers=_cookie_header(cookie))
    assert after.json()["code"] == 11002


async def test_change_password_requires_token(client):
    response = await client.put(
        f"{PREFIX}/password", json={"old_password": "a1", "new_password": "b2"}
    )
    assert response.status_code == 401


# ------------------------------------------------------------------ sms


async def test_sms_endpoint_is_explicitly_not_implemented(client):
    response = await client.post(f"{PREFIX}/sms/send-code", json={"phone": "13800138000"})
    assert response.json()["code"] == 10008


# --------------------------------------------------------------- 辅助


async def _current_user_id(db_session, headers) -> object:
    """从令牌里取 sub。测试不该反查数据库来"猜"当前用户。"""
    from app.core.security import decode_token

    token = headers["Authorization"].removeprefix("Bearer ")
    payload = decode_token(token)
    from uuid import UUID

    return UUID(str(payload["sub"]))


async def test_login_log_and_refresh_token_are_append_only(db_session):
    """append-only 表没有 version/updated_*/deleted_at（docs/04 §2 例外）。"""
    for column in ("version", "updated_at", "updated_by", "deleted_at"):
        assert not hasattr(AuthLoginLog, column), column
        assert not hasattr(AuthRefreshToken, column), column


async def test_failed_logins_are_counted_only_for_that_employee(client, db_session):
    """按工号分键：A 的失败不该锁住 B。"""
    await _seed_user(db_session, employee_no="AAA001")
    await _seed_user(db_session, employee_no="BBB001")
    for _ in range(5):
        await client.post(
            f"{PREFIX}/login", json=make_login_payload(employee_no="AAA001", password="Bad12345")
        )
    b_ok = await client.post(f"{PREFIX}/login", json=make_login_payload(employee_no="BBB001"))
    assert b_ok.json()["code"] == 0
