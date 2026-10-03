"""Redis 连接访问器。

用途仅两处（docs/05 §5 幂等、docs/07 §1.1 登录失败锁定）：
    - ``Idempotency-Key`` 的结果缓存（TTL 24h）
    - 登录失败计数（连续 5 次锁 15 分钟）

⚠️ **Redis 不是强依赖**。未配置 ``REDIS_URL`` 时：
    - 幂等降级为**进程内** TTL 字典（单进程有效，重启即失效；仅用于本地开发）
    - 登录锁定降级为查 ``auth_login_logs`` 最近窗口（docs/07 §1.1 明确要求这个降级）
两条降级路径都会打 WARN 日志，便于发现"生产忘配 Redis"。
"""

import logging
from typing import Any, cast

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import get_settings

logger = logging.getLogger("app.cache")

_client: Redis | None = None
_warned = False


def get_redis() -> Redis | None:
    """取 Redis 客户端；未配置返回 ``None``（调用方负责降级）。"""
    global _client, _warned

    url = get_settings().redis_url.get_secret_value()
    if not url:
        if not _warned:
            logger.warning("redis 未配置（REDIS_URL 为空），幂等与登录锁定将走降级路径")
            _warned = True
        return None
    if _client is None:
        _client = Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
    return _client


async def close_redis() -> None:
    """关闭连接（应用关闭时调用）。"""
    global _client
    if _client is not None:
        await _client.aclose()
    _client = None


async def redis_get_json(key: str) -> dict[str, Any] | None:
    """读 JSON。Redis 不可用或超时返回 ``None``（**不抛异常**，让调用方降级）。"""
    client = get_redis()
    if client is None:
        return None
    try:
        raw = await client.get(key)
    except (RedisError, OSError, TimeoutError):
        logger.warning("redis 读取失败，降级处理", extra={"key": key})
        return None
    if raw is None:
        return None
    import json

    try:
        # 显式 cast：json.loads 返回 Any，禁止用 `no-any-return` 豁免掩盖它
        return cast("dict[str, Any]", json.loads(raw))
    except ValueError:
        return None


async def redis_set_json(key: str, value: dict[str, Any], ttl_seconds: int) -> bool:
    """写 JSON 并设 TTL。成功返回 ``True``；不可用返回 ``False``。"""
    client = get_redis()
    if client is None:
        return False
    import json

    try:
        await client.set(key, json.dumps(value, ensure_ascii=False), ex=ttl_seconds)
    except (RedisError, OSError, TimeoutError):
        logger.warning("redis 写入失败，幂等保证降级为尽力而为", extra={"key": key})
        return False
    return True


async def redis_incr_with_ttl(key: str, ttl_seconds: int) -> int | None:
    """自增并在首次时设 TTL，返回当前值。不可用返回 ``None``。"""
    client = get_redis()
    if client is None:
        return None
    try:
        pipe = client.pipeline()
        pipe.incr(key)
        pipe.ttl(key)
        incremented, ttl = await pipe.execute()
        if int(ttl) < 0:
            await client.expire(key, ttl_seconds)
        return int(incremented)
    except (RedisError, OSError, TimeoutError):
        logger.warning("redis 自增失败，降级处理", extra={"key": key})
        return None


async def redis_get_int(key: str) -> int | None:
    """读整数值。

    :returns: key 不存在返回 ``0``；Redis 不可用返回 ``None``。
        **区分这两者很重要**：调用方据此决定"用缓存值"还是"降级查库"。
        例如登录失败计数读到 0 表示"没失败过"，读到 None 表示"Redis 挂了，
        改查 auth_login_logs" —— 把 None 当 0 会让锁定功能在 Redis 故障时
        静默失效。
    """
    client = get_redis()
    if client is None:
        return None
    try:
        raw = await client.get(key)
    except (RedisError, OSError, TimeoutError):
        logger.warning("redis 读取失败，降级处理", extra={"key": key})
        return None
    if raw is None:
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None
