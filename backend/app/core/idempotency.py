"""``Idempotency-Key`` 依赖（docs/05-接口设计规范.md §5）。

两件事**禁止混淆**（docs/05 §5 原文）：

======================================  ==========================================
情形                                     结果
======================================  ==========================================
同幂等键 + 同 body（扫码枪连扫、网络重试） HTTP 200 + ``code=0``，返回首次记录（**不是错误**）
同幂等键 + **不同** body                   HTTP 400 + ``10002``（业务冲突，人要介入）
======================================  ==========================================

## 并发语义（docs/05 §5，2026-10-08 补，T-BUND-008f 闭环 L-108）

**串行重试命中缓存还不够** —— 20 个请求同时进来时，「读缓存 → 没有就继续」会让 19 个都
去执行（只有第一个真正执行，后 19 个拿到 409）。所以这里是 ``SET NX`` **原子占位**：

    请求 A：SET NX 占位成功 → 执行业务 → 写结果（DONE，TTL 24h）
    请求 B~T：SET NX 失败 → 按占位里的 body_hash 分流：
              同 body 且已 DONE → 原样返回首次结果（HTTP 200）
              同 body 仍在执行 → 轮询等结果，超 ``WAIT_TIMEOUT_SECONDS`` 报 ``10003``
              不同 body        → 立即 ``10002``（不等）

存储：Redis（占位 TTL :data:`PLACEHOLDER_TTL_SECONDS`、结果 TTL 24h）+ 请求体指纹。
Redis 不可用时降级为**进程内**字典（单进程有效，仅本地开发；降级会打 WARN 日志），
降级路径**同样有等待上限**（否则 Redis 一次抖动就放大成「整个接口不可用」）。

⚠️ **本模块是跨模块核心**：``load_idempotent`` / ``store_idempotent`` 的**签名与返回形状**
被 auth / base / bundling 三个模块的 router 直接调用，只允许在**占位与等待**这层加东西，
不许顺手改接口形状。
"""

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from fastapi import Request

from app.core.cache import redis_claim_json, redis_delete, redis_get_json, redis_set_json
from app.core.errors import BusinessError, ErrorCode

logger = logging.getLogger("app.idempotency")

#: 幂等**结果**保留时长（docs/05 §5：24 小时）
TTL_SECONDS = 24 * 3600

#: 「我正在执行」这个占位的存活时长。
#:
#: ⚠️ **必须有 TTL**：结果只在**成功**时写入，进程崩在执行中途时没人来清占位 ——
#: 没有 TTL 的话这个幂等键**永久不可用**，而用户看不出原因（只看到每次都超时）。
#: ⚠️ **取值理由**：必须**明显长于** :data:`WAIT_TIMEOUT_SECONDS`（否则等一次就把占位
#: 等过期，然后自己重新执行 —— 等于幂等失效），也要明显长于最慢的写操作
#: （2000 手审核是秒级）。取 30s：崩在执行中途时这个键最多被堵 30 秒。
PLACEHOLDER_TTL_SECONDS = 30

#: 等首次结果的上限（秒）。超时报 ``10003``（HTTP 409），**绝不无限等**。
#:
#: ⚠️ 取值理由：① 必须远小于 nginx ``proxy_read_timeout 30s``
#: （``docker/nginx/nginx.conf``），否则请求先被网关掐断，客户端拿到 502 而不是错误码；
#: ② 必须远大于最慢的写操作（秒级），否则正常重试会被误判超时；
#: ③ 等满 5s 基本等价于「首次请求已崩在执行中途」（占位 TTL 30s 内不会有结果），
#: 继续等只是白占一个 worker 与一条数据库连接。
WAIT_TIMEOUT_SECONDS = 5.0

#: 轮询间隔（秒）。20 个并发重试 × 每 50ms 一次 GET 是 Redis 能轻松吃下的量；
#: 再密只会把「等结果」变成给 Redis 加压。
POLL_INTERVAL_SECONDS = 0.05

#: 进程内降级存储：key -> (过期时间戳, 载荷)
#:
#: ⚠️ 载荷形状与 Redis 里**完全一致**（``PENDING`` / ``DONE``），否则降级路径会少一套
#: 分支，而那套分支平时跑不到（只在 Redis 挂了时暴露）。
_MEMORY_STORE: dict[str, tuple[float, dict[str, Any]]] = {}

#: 占位 / 结果的状态标记。⚠️ 判定 ``DONE`` 用「有没有 ``response`` 键」而不是这个标记，
#: 这样即使缓存里有旧版本写的无标记结果也照样当命中（不认标记 = 不被格式变更反噬）。
_PENDING = "PENDING"
_DONE = "DONE"


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
    """**原子占位**并返回「该不该由本请求执行」。

    :returns: ``cached is None`` 表示"首次请求，应继续执行业务"；否则返回缓存记录，
        调用方需把 ``cached["response"]`` 原样返回给客户端。
    :raises BusinessError: 同键不同 body → ``10002``（**立即**，不进等待循环）；
        等首次结果超过 :data:`WAIT_TIMEOUT_SECONDS` → ``10003``
    """
    key = request.headers.get("Idempotency-Key")
    if not key:
        return None
    if len(key) > 255:
        raise BusinessError(ErrorCode.PARAM_INVALID, "Idempotency-Key 过长（上限 255 字符）")

    body_hash = _fingerprint(await request.body())
    cache_key = f"idem:{key}"
    deadline = time.monotonic() + WAIT_TIMEOUT_SECONDS

    while True:
        if await _claim(cache_key, body_hash):
            return IdempotentRequest(key=key, body_hash=body_hash, cached=None)
        payload = await _read(cache_key)
        if payload is None:
            # 占位在「占位失败」与「读」之间过期消失了 → 立刻回循环重新占位。
            # 这里**不放宽 deadline**：那会让一次请求无限重试。
            if _over(deadline):
                raise _wait_timeout(key)
            continue
        if payload.get("body_hash") != body_hash:
            raise BusinessError(
                ErrorCode.MISSING_BUSINESS_PARAM,
                "Idempotency-Key 已被另一个不同的请求体使用，请换一个键",
                details={"idempotency_key": key},
            )
        if isinstance(payload.get("response"), dict):
            return IdempotentRequest(key=key, body_hash=body_hash, cached=payload)
        if _over(deadline):
            raise _wait_timeout(key)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def store_idempotent(request: IdempotentRequest, response: dict[str, Any]) -> None:
    """登记首次响应，供后续重试原样返回（**覆盖**占位）。

    ⚠️ 用普通 ``SET`` 而不是 ``SET NX``：占位本来就是自己占的，必须能覆盖掉，
    否则占位会一直停在 ``PENDING`` 直到 TTL 过期，同键重试全在空等。
    """
    payload = {"body_hash": request.body_hash, "state": _DONE, "response": response}
    cache_key = f"idem:{request.key}"
    if not await redis_set_json(cache_key, payload, TTL_SECONDS):
        _memory_put(cache_key, payload)


async def mark_idempotent_failed(request: IdempotentRequest) -> None:
    """**释放**本请求占的位（业务失败时由 router 调用，闭环 L-113）。

    ## 为什么需要它

    :func:`load_idempotent` 占位之后，**只有成功路径**才会写结果。而业务失败
    （``31004`` 超打 / ``12001`` 无权限 / 参数校验不过）会直接抛异常，
    ``store_idempotent`` 永远不执行 —— 占位就停在 ``PENDING`` 直到 TTL（30s）过期。

    后果：**用户在同一个键上重试会一直等到等满 :data:`WAIT_TIMEOUT_SECONDS` 才拿到
    ``10003``**，而首次请求其实早就失败了、根本不会有人写结果。最典型的场景是
    **登录输错口令**：同键重试每次都要等 5 秒才知道「又错了」，且提示是
    「请求正在处理中」而不是「口令错误」。

    ## 为什么是「删键」而不是「写失败标记」

    失败**不是**一种可缓存的结果 —— 它通常只反映当时的入参（口令错了、这单已被别人审了），
    用户改完入参就该能用**同一个键**重试。写标记会让「改完再试」继续拿到旧失败，
    于是用户被迫去点「换个键」—— 而键是客户端生成的，用户没有理由知道要换。

    代价：删键之后**首次请求与重试之间**的那段窗口不再有保护。但那正是我们要的：
    业务失败说明**没有副作用发生**，重试是安全的。
    """
    if request is None or not request.key:
        return
    cache_key = f"idem:{request.key}"
    _memory_drop(cache_key)
    await redis_delete(cache_key)


def _memory_drop(cache_key: str) -> None:
    """从进程内降级存储删键（与 :func:`_memory_put` / :func:`_memory_get` 成对）。"""
    _MEMORY_STORE.pop(cache_key, None)


def _wait_timeout(key: str) -> BusinessError:
    """等首次结果超上限：明确错误码 + 可观察的日志。

    ⚠️ 用 ``10003``（乐观锁冲突 / HTTP 409，客户端动作「刷新后重试」）而不是 ``99999``：
    这不是服务端故障，是「上一次同键请求还在处理中」，让客户端自己消化的冲突。
    """
    logger.info("idempotency_wait_timeout", extra={"idempotency_key": key})
    return BusinessError(
        ErrorCode.OPTIMISTIC_LOCK_CONFLICT,
        "同一请求正在处理中，请稍后重试",
        details={"idempotency_key": key},
    )


def _over(deadline: float) -> bool:
    return time.monotonic() >= deadline


async def _claim(cache_key: str, body_hash: str) -> bool:
    """原子占位。``True`` = 本请求占到了，可以去执行。"""
    claimed = await redis_claim_json(
        cache_key, {"body_hash": body_hash, "state": _PENDING}, PLACEHOLDER_TTL_SECONDS
    )
    if claimed is None:
        return _memory_claim(cache_key, body_hash)
    return claimed


async def _read(cache_key: str) -> dict[str, Any] | None:
    payload = await redis_get_json(cache_key)
    if payload is None:
        payload = _memory_get(cache_key)
    return payload


def _memory_claim(cache_key: str, body_hash: str) -> bool:
    """进程内降级下的占位。

    ⚠️ **整个函数没有 ``await``**：asyncio 单线程里「读 → 判 → 写」不被打断，
    所以它在降级路径上也是原子的（跨进程不成立 —— 这正是降级只保证单进程的原因）。
    """
    now = time.time()
    entry = _MEMORY_STORE.get(cache_key)
    if entry is not None and entry[0] >= now:
        return False
    _MEMORY_STORE[cache_key] = (
        now + PLACEHOLDER_TTL_SECONDS,
        {"body_hash": body_hash, "state": _PENDING},
    )
    return True


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
