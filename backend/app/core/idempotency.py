"""``Idempotency-Key`` 依赖（docs/05-接口设计规范.md §5）。

两件事**禁止混淆**（docs/05 §5 原文）：

======================================  ==========================================
情形                                     结果
======================================  ==========================================
同幂等键 + 同 body（扫码枪连扫、网络重试） HTTP 200 + ``code=0``，返回首次记录（**不是错误**）
同幂等键 + **不同** body                   HTTP 400 + ``10002``（业务冲突，人要介入）
======================================  ==========================================

存储：Redis（TTL 24h）+ 请求体指纹。Redis 不可用时降级为**进程内**字典
（单进程有效，仅本地开发；降级会打 WARN 日志）。
"""

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

from fastapi import Request

from app.core.cache import redis_get_json, redis_set_json
from app.core.errors import BusinessError, ErrorCode

#: 幂等结果保留时长（docs/05 §5：24 小时）
TTL_SECONDS = 24 * 3600

#: 进程内降级存储：key -> (过期时间戳, 首次响应体)
_MEMORY_STORE: dict[str, tuple[float, dict[str, Any]]] = {}


@dataclass(frozen=True, slots=True)
class IdempotentRequest:
    """一个幂等请求的解析结果。"""

    key: str
    body_hash: str
    cached: dict[str, Any] | None


def _fingerprint(body: bytes) -> str:
    """请求体指纹。用于判定"同键不同 body"。"""
    if not body:
        return "empty"
    try:
        canonical = json.dumps(json.loads(body), sort_keys=True, ensure_ascii=False)
    except ValueError:
        canonical = body.decode("utf-8", errors="replace")
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def load_idempotent(request: Request) -> IdempotentRequest | None:
    """解析并查缓存。

    :returns: ``None`` 表示"首次请求，应继续执行业务"；否则返回缓存记录，
        调用方需把 ``cached`` 原样返回给客户端。
    :raises BusinessError: 同键不同 body → ``10002``
    """
    key = request.headers.get("Idempotency-Key")
    if not key:
        return None
    if len(key) > 255:
        raise BusinessError(ErrorCode.PARAM_INVALID, "Idempotency-Key 过长（上限 255 字符）")

    body = await request.body()
    body_hash = _fingerprint(body)
    cache_key = f"idem:{key}"

    cached = await redis_get_json(cache_key)
    if cached is None:
        cached = _memory_get(cache_key)
    if cached is None:
        return IdempotentRequest(key=key, body_hash=body_hash, cached=None)

    if cached.get("body_hash") != body_hash:
        raise BusinessError(
            ErrorCode.MISSING_BUSINESS_PARAM,
            "Idempotency-Key 已被另一个不同的请求体使用，请换一个键",
            details={"idempotency_key": key},
        )
    return IdempotentRequest(key=key, body_hash=body_hash, cached=cached)


async def store_idempotent(request: IdempotentRequest, response: dict[str, Any]) -> None:
    """登记首次响应，供后续重试原样返回。"""
    if request is None:
        return
    payload = {"body_hash": request.body_hash, "response": response}
    cache_key = f"idem:{request.key}"
    if not await redis_set_json(cache_key, payload, TTL_SECONDS):
        _memory_put(cache_key, payload)


def _memory_get(cache_key: str) -> dict[str, Any] | None:
    entry = _MEMORY_STORE.get(cache_key)
    if entry is None:
        return None
    expires_at, payload = entry
    if expires_at < time.time():
        del _MEMORY_STORE[cache_key]
        return None
    return payload


def _memory_put(cache_key: str, payload: dict[str, Any]) -> None:
    _MEMORY_STORE[cache_key] = (time.time() + TTL_SECONDS, payload)


def clear_memory_store() -> None:
    """清空进程内降级存储（测试隔离用）。"""
    _MEMORY_STORE.clear()
