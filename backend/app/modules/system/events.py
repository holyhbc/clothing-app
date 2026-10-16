"""SSE 事件流端点（``docs/05`` §8、``docs/modules/04-计件.md`` §6.1）。

把 :mod:`app.core.events` 里的 ``erp:events`` 流转成浏览器可消费的
``text/event-stream``。设计稿见 ``docs/modules/04-计件-SSE事件流设计.md``。

## 三条边界

1. **实时提示，不是真值**（``05 §8``）：本端点**禁止**下发写操作结果；员工确认
   计件结果一律以 ``POST /piecework/count`` 的响应为准。
2. **鉴权一次、断开重连**（``05 §8``）：连接建立时校验 token；之后靠客户端
   ``Last-Event-ID`` 续传。EventSource 不能自定义请求头，故 token 可走 ``?token=``。
3. **Redis 是本端点的硬依赖**：没有它就没有事件来源，回 ``11012``（前端据此降级轮询）。
"""

import logging
from collections.abc import AsyncIterator
from time import monotonic

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import StreamingResponse
from redis.exceptions import RedisError

from app.core.cache import get_redis
from app.core.errors import BusinessError, ErrorCode
from app.core.events import read_events
from app.core.permissions import AuthContext, get_sse_auth_context

logger = logging.getLogger("app.events")

router = APIRouter(tags=["系统"])

#: 心跳间隔（``05 §8``：每 25s 发 ``:ping``，防 nginx 60s 超时）。
HEARTBEAT_SECONDS = 25.0

#: 单次 ``XREAD`` 的阻塞毫秒数。比心跳间隔小，才能在无事件时按时发心跳。
_BLOCK_MS = 5000


def format_sse(event_id: str, event_type: str, data: str) -> str:
    """拼一帧 SSE（``05 §8``：``id:`` / ``event:`` / ``data:`` 三段，空行结束）。"""
    return f"id: {event_id}\nevent: {event_type}\ndata: {data}\n\n"


def parse_event_filter(events: str | None) -> frozenset[str]:
    """``events=piecework_counted,foo`` → 事件名白名单；空表示不过滤。"""
    if not events:
        return frozenset()
    return frozenset(part.strip() for part in events.split(",") if part.strip())


async def redis_available() -> bool:
    """事件流依赖的 Redis 是否可用。未配置或 ``PING`` 失败都算不可用。"""
    client = get_redis()
    if client is None:
        return False
    try:
        await client.ping()
    except (RedisError, OSError, TimeoutError):
        logger.warning("事件流：Redis 不可达")
        return False
    return True


async def stream_events(
    *,
    last_id: str,
    event_filter: frozenset[str],
    heartbeat_seconds: float = HEARTBEAT_SECONDS,
    block_ms: int = _BLOCK_MS,
) -> AsyncIterator[str]:
    """SSE 生成器：阻塞读 ``erp:events``，无事件时按间隔发 ``:ping``。

    :param last_id: 起始游标。``"$"`` = 只收新事件；给 ID = 从它之后续传（断点重连）。
    :param event_filter: 事件名白名单；空集则全部转发。
    """
    cursor = last_id
    last_ping = monotonic()
    while True:
        batch = await read_events(cursor, block_ms=block_ms)
        if not batch:
            if monotonic() - last_ping >= heartbeat_seconds:
                last_ping = monotonic()
                yield ":ping\n\n"
            continue
        for entry_id, fields in batch:
            cursor = entry_id
            event_type = fields.get("event", "")
            if event_filter and event_type not in event_filter:
                continue
            last_ping = monotonic()
            yield format_sse(entry_id, event_type, fields.get("data", "{}"))


@router.get(
    "/events/stream",
    summary="SSE 事件流（实时提示；真值仍查接口）",
    description=(
        "订阅 `erp:events` 事件流。事件名见 docs/05 §8（当前：`piecework_counted`）。"
        "鉴权：`Authorization: Bearer` 头，或 `?token=`（EventSource 不能设请求头）。"
        "断线重连请带 `Last-Event-ID` 以续传。"
    ),
)
async def events_stream(
    _ctx: AuthContext = Depends(get_sse_auth_context),
    events: str | None = Query(
        default=None, description="逗号分隔的事件名白名单，如 piecework_counted"
    ),
    last_event_id: str | None = Query(
        default=None, description="断点续传游标（优先级低于 Last-Event-ID 头）"
    ),
    last_event_id_header: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    """建立 SSE 连接。"""
    if not await redis_available():
        raise BusinessError(
            ErrorCode.EVENT_STREAM_UNAVAILABLE,
            "事件流依赖 Redis，当前未配置或不可达；请前端降级为轮询",
        )
    cursor = last_event_id_header or last_event_id or "$"
    return StreamingResponse(
        stream_events(last_id=cursor, event_filter=parse_event_filter(events)),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # nginx：关掉对 SSE 的响应缓冲，否则事件会被攒着不下发
            "X-Accel-Buffering": "no",
        },
    )
