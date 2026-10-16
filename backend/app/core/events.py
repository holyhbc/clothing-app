"""Redis Stream 领域事件总线（``docs/01`` §5 / §121、``docs/05`` §8）。

用途：把**已经落库的领域事实**best-effort 广播给 SSE 订阅端（如 ``piecework_counted``）。

两条硬约束：

1. **Redis 不是唯一数据源**（``01 §5``）：事件丢了不影响业务真值，真值一律查接口
   （``05 §8`` 禁止用 SSE 下发写操作结果）。所以本模块**永不落库**、**永不回滚业务**。
2. **发布是 best-effort**：``publish_event`` 失败只打 WARN 返回 ``None``，**不抛异常**
   —— 否则 Redis 抖动会把一次已经成功的计件变成 500，客户端据此重试又会撞唯一索引。
   这与 :mod:`app.core.cache` 的降级哲学一致（那里也一律「失败只降级」）。

⚠️ 连接复用 ``app.core.cache.get_redis`` 的**单例**（同一个 event loop 内共享）。
   未配置 ``REDIS_URL`` 时 ``get_redis()`` 返回 ``None``：发布静默跳过、读取返回空，
   SSE 端点据此回 ``11012``。
"""

import json
import logging
from typing import Any, cast

from redis.exceptions import RedisError

from app.core.cache import get_redis

logger = logging.getLogger("app.events")

#: 全局单流。事件名放 payload 而不是「按事件名建流」：订阅端只需读一条流、
#: 用 ``event`` 字段过滤，不必先列举「现在有哪些流」。
EVENTS_STREAM = "erp:events"

#: 流的近似长度上限（``XADD MAXLEN ~``）。事件是易失的实时提示，不需要保留全量；
#: 上限存在的意义是防止 Redis 内存被无限增长的事件撑爆。
_MAX_LEN = 1000


def _text(value: object) -> str:
    """把 Redis 返回的 bytes 归一成 str（``Redis.from_url`` 未开 ``decode_responses``）。"""
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return str(value)


async def publish_event(event_type: str, data: dict[str, Any]) -> str | None:
    """把一条事件写进 ``erp:events``。

    :returns: 写入后的 stream ID（字符串）；Redis 未配置 / 发布失败返回 ``None``。
    :param data: 事件载荷。调用方负责**只放业务白名单字段**（INV-3：无 token / 无口令）。
    """
    client = get_redis()
    if client is None:
        return None
    try:
        raw = await client.xadd(
            EVENTS_STREAM,
            {"event": event_type, "data": json.dumps(data, ensure_ascii=False)},
            maxlen=_MAX_LEN,
            approximate=True,
        )
    except (RedisError, OSError, TimeoutError):
        logger.warning("事件发布失败，降级为尽力而为", extra={"event": event_type})
        return None
    return _text(raw)


async def read_events(
    last_id: str, *, block_ms: int, count: int = 100
) -> list[tuple[str, dict[str, str]]]:
    """阻塞读一批事件（``XREAD BLOCK``）。

    :param last_id: 游标。``"$"`` = 只读**新**事件；给具体 ID = 从该 ID **之后**续读。
    :param block_ms: 阻塞毫秒数；到点无事件返回空列表（调用方据此发心跳）。
    :returns: ``[(stream_id, fields)]``；Redis 不可用 / 超时返回 ``[]``（**不抛**）。
    """
    client = get_redis()
    if client is None:
        return []
    try:
        response = await client.xread({EVENTS_STREAM: last_id}, count=count, block=block_ms)
    except (RedisError, OSError, TimeoutError):
        logger.warning("事件读取失败，降级为等待", extra={"stream": EVENTS_STREAM})
        return []
    if not response:
        return []
    # ``xread`` 的 stub 类型太松（既不是 Any 也不够具体）；这里显式声明期望形状：
    # ``[[stream_key, [(entry_id, {field: value})]]]``。字段值运行时是 bytes，交给 ``_text``。
    rows = cast("list[tuple[str, list[tuple[str, dict[str, str]]]]]", response)
    return [
        (_text(entry_id), {_text(key): _text(value) for key, value in fields.items()})
        for _stream, entries in rows
        for entry_id, fields in entries
    ]
