"""口令哈希与令牌（docs/07-认证与权限规范.md §1.1）。

三条硬规则：
    1. 密码哈希**只用 argon2id**，禁止 MD5/SHA1/裸存
    2. JWT 载荷**只放身份**，不放权限明细 —— 权限每次请求查库，
       否则改了角色要等 token 过期才生效
    3. ``JWT_SECRET`` 缺失时**启动即失败**，绝不回退到弱默认值

refresh token 走 HttpOnly Cookie，因此这里只提供摘要计算，实际令牌字符串由
service 层生成后交给 cookie。
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import jwt
from pwdlib import PasswordHash

from app.common.enums import AuthChannel
from app.core.config import get_settings
from app.core.errors import BusinessError, ErrorCode

#: argon2id。argon2i/m 不接受 —— m 是有已知弱点的变体
_ALGORITHM: Final[str] = "argon2id"

_password_hash = PasswordHash.recommended()

#: JWT 的 type 声明值（docs/07 §1.1：载荷含 type，用于区分 access/refresh）
#: noqa 说明：S105 把名字含 TOKEN 的字符串当密钥，这是 OAuth2 固定字面量，不是密钥
TOKEN_TYPE_ACCESS: Final[str] = "access"  # noqa: S105
TOKEN_TYPE_REFRESH: Final[str] = "refresh"  # noqa: S105


def hash_password(plain_password: str) -> str:
    """算口令哈希。同一口令两次调用结果不同（随机盐）。"""
    return _password_hash.hash(plain_password)


def verify_password(plain_password: str, password_hash: str | None) -> bool:
    """校验口令。

    ``password_hash`` 为空（员工端短信登录的账号没有口令）时返回 False，
    **不抛异常** —— 调用方据此走"该账号不支持口令登录"的分支。
    """
    verified, _updated_hash = verify_password_and_check_rehash(plain_password, password_hash)
    return verified


def verify_password_and_check_rehash(
    plain_password: str, password_hash: str | None
) -> tuple[bool, str | None]:
    """校验口令并返回是否需要重算哈希。

    argon2 参数升级后旧哈希仍可用但强度落后，登录成功时应重算（docs/07 §1.1 密码策略）。
    :return: (是否通过, 需要更新的新哈希；无需更新时为 None)
    """
    if not password_hash:
        return False, None
    return _password_hash.verify_and_update(plain_password, password_hash)


def validate_password_strength(password: str) -> None:
    """口令策略：长度 ≥ 8 且含字母与数字（docs/07 §1.1）。不满足抛 10001。"""
    if len(password) < 8:
        raise BusinessError(ErrorCode.PARAM_INVALID, "密码长度至少 8 位")
    has_letter = any(char.isalpha() for char in password)
    has_digit = any(char.isdigit() for char in password)
    if not (has_letter and has_digit):
        raise BusinessError(ErrorCode.PARAM_INVALID, "密码必须同时包含字母与数字")


def generate_refresh_token() -> str:
    """生成 refresh token 随机串（256 位熵）。

    只在服务端保存其 sha256；明文仅此一次通过 HttpOnly Cookie 下发。
    """
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """令牌摘要。refresh token 查库用这个，不用明文（docs/07 §1.1「存库可吊销」）。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class TokenPair:
    """一次签发结果。refresh token 明文只用于写 Cookie。"""

    access_token: str
    refresh_token: str
    expires_in_seconds: int
    token_type: str = "bearer"  # noqa: S105 —— OAuth2 固定字面量


def _secret() -> str:
    """取 JWT 密钥；缺失时抛业务异常而不是用空串签发。"""
    secret = get_settings().jwt_secret.get_secret_value()
    if not secret:
        raise BusinessError(
            ErrorCode.INTERNAL,
            "JWT 密钥未配置，无法签发令牌；请设置 JWT_SECRET（openssl rand -hex 32）",
        )
    return secret


def create_access_token(
    *,
    user_id: str,
    role_ids: list[str],
    data_scope: str,
    extra_claims: dict[str, Any] | None = None,
) -> tuple[str, int]:
    """签发 access token。

    载荷只含身份：``sub``（user_id）、``role_ids``、``data_scope``、``type``、``jti``、
    ``iat``、``exp``。**刻意不含** permissions —— 见模块 docstring 第 2 条。

    :return: (token, 有效期秒数)
    """
    settings = get_settings()
    expires_in = settings.jwt_expire_minutes * 60
    now = datetime.now(tz=UTC)
    payload: dict[str, Any] = {
        "sub": user_id,
        "role_ids": role_ids,
        "data_scope": data_scope,
        "type": TOKEN_TYPE_ACCESS,
        "jti": secrets.token_urlsafe(16),
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
    }
    if extra_claims:
        payload.update(extra_claims)
    token = jwt.encode(payload, _secret(), algorithm=settings.jwt_algorithm)
    return token, expires_in


def decode_token(token: str, *, expected_type: str = TOKEN_TYPE_ACCESS) -> dict[str, Any]:
    """校验并解码令牌。

    :raises BusinessError: ``11001`` 未登录/过期、``11002`` 令牌非法
    """
    settings = get_settings()
    try:
        payload = jwt.decode(token, _secret(), algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise BusinessError(ErrorCode.UNAUTHORIZED, "登录已过期，请重新登录") from exc
    except jwt.InvalidTokenError as exc:
        raise BusinessError(ErrorCode.TOKEN_INVALID, "登录凭证无效，请重新登录") from exc

    actual_type = payload.get("type")
    if actual_type != expected_type:
        raise BusinessError(ErrorCode.TOKEN_INVALID, "登录凭证类型错误，请重新登录")
    return payload


def refresh_token_expiry(channel: AuthChannel | str) -> datetime:
    """refresh token 过期时间。

    PC 端 7 天、员工端 30 天（docs/07 §1.1 / §1.2）。
    """
    settings = get_settings()
    days = 30 if channel == AuthChannel.H5_SMS else settings.jwt_refresh_expire_days
    return datetime.now(tz=UTC) + timedelta(days=days)


def new_device_id() -> str:
    """设备标识（员工端首次登录生成后存 localStorage，docs/07 §1.2）。"""
    return secrets.token_urlsafe(16)
