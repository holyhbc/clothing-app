"""口令哈希与令牌测试（docs/07-认证与权限规范.md §1.1）。

重点：
    - argon2id（不是 argon2i/argon2m，更不是 MD5/SHA1）
    - 同口令两次哈希不同（随机盐）
    - JWT 载荷**不含权限明细**（否则改角色要等 token 过期才生效）
    - ``JWT_SECRET`` 缺失时拒绝签发，不静默用弱密钥
"""

import time

import pytest

from app.common.enums import AuthChannel
from app.core.errors import BusinessError, ErrorCode
from app.core.security import (
    TOKEN_TYPE_ACCESS,
    TOKEN_TYPE_REFRESH,
    create_access_token,
    decode_token,
    generate_refresh_token,
    hash_password,
    hash_token,
    new_device_id,
    refresh_token_expiry,
    validate_password_strength,
    verify_password,
    verify_password_and_check_rehash,
)

#: argon2id 的标准前缀
ARGON2ID_PREFIX = "$argon2id$"


# ---------------------------------------------------------------------------
# 口令哈希
# ---------------------------------------------------------------------------


def test_hash_uses_argon2id() -> None:
    """必须 argon2id（docs/07 §1.1 明确 argon2i/m 不接受）。"""
    assert hash_password("Passw0rd").startswith(ARGON2ID_PREFIX)


def test_hash_is_salted_so_same_password_differs() -> None:
    """随机盐：同口令两次哈希结果必须不同。"""
    first = hash_password("Passw0rd")
    second = hash_password("Passw0rd")
    assert first != second
    assert verify_password("Passw0rd", first)
    assert verify_password("Passw0rd", second)


def test_verify_rejects_wrong_password() -> None:
    assert not verify_password("Wr0ngPass", hash_password("Passw0rd"))


def test_verify_with_empty_hash_returns_false_without_raising() -> None:
    """员工端短信登录的账号没有口令，返回 False 而不是抛异常（调用方据此分支）。"""
    assert verify_password("anything", None) is False
    assert verify_password("anything", "") is False


def test_verify_and_check_rehash_reports_no_update_for_fresh_hash() -> None:
    """刚生成的哈希不需要重算。"""
    verified, updated = verify_password_and_check_rehash("Passw0rd", hash_password("Passw0rd"))
    assert verified is True
    assert updated is None


def test_verify_and_check_rehash_with_empty_hash() -> None:
    assert verify_password_and_check_rehash("x", None) == (False, None)


# ---------------------------------------------------------------------------
# 口令策略（docs/07 §1.1：≥8 位，含字母与数字）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("password", ["Sh0rt", "abcdefgh", "12345678"])
def test_weak_password_rejected(password: str) -> None:
    with pytest.raises(BusinessError) as exc:
        validate_password_strength(password)
    assert exc.value.code == ErrorCode.PARAM_INVALID


@pytest.mark.parametrize("password", ["Passw0rd", "abc12345", "Aa1bcdefgh"])
def test_strong_password_accepted(password: str) -> None:
    validate_password_strength(password)


# ---------------------------------------------------------------------------
# 刷新令牌
# ---------------------------------------------------------------------------


def test_refresh_token_is_random_and_only_hash_is_stored() -> None:
    """令牌本身随机；库里只存 sha256（docs/07 §1.1「存库可吊销」）。"""
    first = generate_refresh_token()
    second = generate_refresh_token()
    assert first != second
    assert len(first) >= 32
    digest = hash_token(first)
    assert len(digest) == 64
    assert first not in digest, "摘要里不能出现明文"
    assert digest == hash_token(first), "同令牌摘要必须稳定（查库用）"
    assert digest != hash_token(second)


def test_device_id_is_random() -> None:
    assert new_device_id() != new_device_id()


def test_refresh_expiry_differs_by_channel() -> None:
    """PC 端 7 天、员工端 30 天（docs/07 §1.1 / §1.2）。

    断言用"距今多少天"而不是两个到期点相减：``timedelta.days`` 对负值是向下取整，
    两个时间点相减会差 1 天，那是断言写法的问题而非实现问题。
    """
    from datetime import UTC, datetime

    now = datetime.now(tz=UTC)
    pc = refresh_token_expiry(AuthChannel.PC)
    mobile = refresh_token_expiry(AuthChannel.H5_SMS)
    assert pc < mobile
    assert (pc - now).days == 7
    assert (mobile - now).days == 30


def test_refresh_expiry_accepts_plain_string() -> None:
    assert refresh_token_expiry("PC") < refresh_token_expiry("H5_SMS")


# ---------------------------------------------------------------------------
# access token
# ---------------------------------------------------------------------------


def test_access_token_payload_has_identity_but_no_permissions() -> None:
    """载荷只放身份；**不放权限明细**（docs/07 §1.1）。"""
    token, expires_in = create_access_token(
        user_id="11111111-1111-1111-1111-111111111111",
        role_ids=["22222222-2222-2222-2222-222222222222"],
        data_scope="WORKSHOP",
    )
    payload = decode_token(token)

    assert expires_in == 15 * 60, "access token 默认 15 分钟（docs/07 §1.1）"
    assert payload["sub"] == "11111111-1111-1111-1111-111111111111"
    assert payload["role_ids"] == ["22222222-2222-2222-2222-222222222222"]
    assert payload["data_scope"] == "WORKSHOP"
    assert payload["type"] == TOKEN_TYPE_ACCESS
    assert "jti" in payload
    assert "permissions" not in payload
    assert "scopes" not in payload, "权限清单绝不能进 token"


def test_access_tokens_have_unique_jti() -> None:
    """同用户同秒签发两次，jti 必须不同（否则无法单独吊销）。"""
    kwargs = {"user_id": "u1", "role_ids": [], "data_scope": "FACTORY"}
    first, _ = create_access_token(**kwargs)
    second, _ = create_access_token(**kwargs)
    assert decode_token(first)["jti"] != decode_token(second)["jti"]


def test_expired_token_raises_unauthorized() -> None:
    """过期令牌 → 11001（docs/05 §4）。"""
    import jwt

    from app.core.config import get_settings

    settings = get_settings()
    expired = jwt.encode(
        {
            "sub": "u1",
            "type": TOKEN_TYPE_ACCESS,
            "exp": int(time.time()) - 10,
        },
        settings.jwt_secret.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )
    with pytest.raises(BusinessError) as exc:
        decode_token(expired)
    assert exc.value.code == ErrorCode.UNAUTHORIZED
    assert "过期" in exc.value.message


def test_tampered_token_raises_token_invalid() -> None:
    """被篡改的令牌 → 11002（docs/05 §4）。"""
    token, _ = create_access_token(user_id="u1", role_ids=[], data_scope="FACTORY")
    head, payload, signature = token.split(".")
    tampered = f"{head}.{payload}.{signature[:-2]}xx"
    with pytest.raises(BusinessError) as exc:
        decode_token(tampered)
    assert exc.value.code == ErrorCode.TOKEN_INVALID


def test_garbage_token_raises_token_invalid() -> None:
    with pytest.raises(BusinessError) as exc:
        decode_token("not-a-jwt")
    assert exc.value.code == ErrorCode.TOKEN_INVALID


def test_type_mismatch_is_rejected() -> None:
    """access token 不能当 refresh 用，反之亦然（防令牌混用）。"""
    access, _ = create_access_token(user_id="u1", role_ids=[], data_scope="FACTORY")
    with pytest.raises(BusinessError) as exc:
        decode_token(access, expected_type=TOKEN_TYPE_REFRESH)
    assert exc.value.code == ErrorCode.TOKEN_INVALID
    assert exc.value.message == "登录凭证类型错误，请重新登录"


def test_extra_claims_are_merged() -> None:
    """附加声明（如未来的 device_id）可合并进载荷。"""
    token, _ = create_access_token(
        user_id="u1", role_ids=[], data_scope="SELF", extra_claims={"device_id": "dev-1"}
    )
    assert decode_token(token)["device_id"] == "dev-1"


def test_missing_jwt_secret_refuses_to_sign(monkeypatch: pytest.MonkeyPatch) -> None:
    """缺 JWT 密钥必须拒绝签发，不静默用弱密钥（docs/07 §1.1）。"""
    from app.core.config import get_settings

    monkeypatch.setenv("JWT_SECRET", "")
    get_settings.cache_clear()
    with pytest.raises(BusinessError) as exc:
        create_access_token(user_id="u1", role_ids=[], data_scope="FACTORY")
    assert exc.value.code == ErrorCode.INTERNAL
    assert "JWT_SECRET" in exc.value.message
