"""事件流（T-PIECE-010）：``erp:events`` 发布/读取 + SSE ``GET /events/stream``。

======================================  ==========================================
TC-EVT-01                             ``format_sse`` 帧格式（``05 §8``）
TC-EVT-02                             ``parse_event_filter`` 解析/去空
TC-EVT-03                             ``publish_event`` → ``read_events`` 往返
TC-EVT-04                             ★ 扫码计件成功 → 流里恰好一条 ``piecework_counted``
TC-EVT-05                             ★ ``Idempotency-Key`` 幂等重试 **不**重复发布
TC-EVT-06                             ``stream_events`` 投递已发布事件
TC-EVT-07                             ``stream_events`` 按 ``events`` 白名单过滤
TC-EVT-08                             端点：无 token → ``11001``
TC-EVT-09                             端点：Redis 不可用 → ``11012``（503）
TC-EVT-10                             ★ 端点：``Authorization`` 头 + ``?token=`` 均可用，回 ``text/event-stream``
======================================  ==========================================

⚠️ **SSE 端点的「真正流式」路径不在本文件里用 HTTP 跑**：``httpx.ASGITransport``
会把响应体整个读完才返回，而事件流是**无限**的 —— 那样调用会永远挂住。
所以：端点「组装/鉴权/响应头」用假生成器逐帧验（TC-EVT-10），
「阻塞读 + 投递 + 过滤」直接验生成器（TC-EVT-06/07），两者合起来覆盖真实路径。
"""

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import pytest

from app.common.enums import DataScope
from app.core.cache import get_redis
from app.core.events import publish_event, read_events
from app.modules.system.events import format_sse, parse_event_filter, stream_events

COUNT_API = "/api/v1/piecework/count"
STREAM_API = "/api/v1/events/stream"


@pytest.fixture
def require_redis(_clean_redis_namespace: None) -> None:
    """需要真实 Redis 的用例：未配置时跳过（CI 提供 Redis）。

    ⚠️ 依赖 ``_clean_redis_namespace``：``cache.get_redis`` 是**模块级单例**，
    而 pytest-asyncio 每个用例开一个新 loop。不复用它，用例之间会跨 loop 复用同一个
    Redis 客户端，报出「Future attached to a different loop」—— 报错点离真因很远。
    """
    if get_redis() is None:
        pytest.skip("本用例需要 Redis（CI 已提供；本地需配 REDIS_URL）")


async def _admin_headers(auth_headers: Any) -> dict[str, str]:
    return await auth_headers(role="super_admin")


async def _count_headers(auth_headers: Any, world: dict[str, Any]) -> dict[str, str]:
    return await auth_headers(
        role="custom",
        permissions=("piecework:count",),
        data_scope=DataScope.WORKSHOP,
        workshop_id=world["workshop_id"],
    )


def _count_body(world: dict[str, Any]) -> dict[str, Any]:
    return {
        "bundle_no": world["bundle_no"],
        "employee_id": str(world["employee"].id),
        "scanned_at": datetime.now(tz=UTC).isoformat(),
    }


async def _counted_events() -> list[dict[str, Any]]:
    events = await read_events("0", block_ms=1)
    return [fields for _id, fields in events if fields.get("event") == "piecework_counted"]


# ---------------------------------------------------------------- 单元


def test_tc_evt_01_format_sse() -> None:
    """``05 §8``：``id:`` / ``event:`` / ``data:`` 三段 + 结尾空行。"""
    frame = format_sse("1-0", "piecework_counted", '{"qty":1}')
    assert frame == 'id: 1-0\nevent: piecework_counted\ndata: {"qty":1}\n\n'


def test_tc_evt_02_parse_event_filter() -> None:
    assert parse_event_filter(None) == frozenset()
    assert parse_event_filter("") == frozenset()
    assert parse_event_filter("a, b ,") == frozenset({"a", "b"})


# ---------------------------------------------------------------- 事件总线


async def test_tc_evt_03_publish_read_roundtrip(require_redis: None) -> None:
    event_id = await publish_event("piecework_counted", {"bundle_no": "BD-RT"})
    assert event_id, "publish_event 应返回 stream id"
    events = await read_events("0", block_ms=1)
    assert any(
        fields.get("event") == "piecework_counted" and "BD-RT" in fields.get("data", "")
        for _id, fields in events
    )


# ---------------------------------------------------------------- 计件生产端


async def test_tc_evt_04_count_publishes_event(
    client: Any, auth_headers: Any, piecework_world: dict[str, Any], require_redis: None
) -> None:
    """扫码计件成功 → 流里恰好一条事件，字段与 ``05 §8`` 示例一致。"""
    headers = await _count_headers(auth_headers, piecework_world)
    response = await client.post(COUNT_API, json=_count_body(piecework_world), headers=headers)
    assert response.status_code == 200, response.text

    counted = await _counted_events()
    assert len(counted) == 1, f"应恰好一条事件，实际 {len(counted)}"
    data = json.loads(counted[0]["data"])
    assert data["bundle_no"] == piecework_world["bundle_no"]
    assert data["style_no"] == piecework_world["style_no"]
    assert data["operation_no"] == piecework_world["operation_no"]
    assert data["employee_name"] == piecework_world["employee"].name
    assert data["qty"] == 60, "全件扫码 = 该手 60 件（BUNDLE_QTY）"


async def test_tc_evt_05_idempotent_retry_does_not_republish(
    client: Any, auth_headers: Any, piecework_world: dict[str, Any], require_redis: None
) -> None:
    """同一 ``Idempotency-Key`` 发两次 → 只发布一次（幂等命中不再广播）。"""
    headers = {**(await _count_headers(auth_headers, piecework_world)), "Idempotency-Key": "EVT-05"}
    body = _count_body(piecework_world)
    first = await client.post(COUNT_API, json=body, headers=headers)
    second = await client.post(COUNT_API, json=body, headers=headers)
    assert first.status_code == 200 and second.status_code == 200
    assert second.json()["data"]["log_id"] == first.json()["data"]["log_id"]

    counted = await _counted_events()
    assert len(counted) == 1, "幂等重试不得重复广播"


# ---------------------------------------------------------------- 生成器


async def test_tc_evt_06_stream_events_delivers(require_redis: None) -> None:
    await publish_event("piecework_counted", {"bundle_no": "BD-STREAM"})
    agen = stream_events(last_id="0", event_filter=frozenset())
    try:
        frame = await asyncio.wait_for(agen.__anext__(), timeout=5)
    finally:
        await agen.aclose()
    assert "event: piecework_counted" in frame
    assert "BD-STREAM" in frame


async def test_tc_evt_07_stream_events_filters(require_redis: None) -> None:
    await publish_event("unrelated_event", {"x": 1})
    await publish_event("piecework_counted", {"bundle_no": "BD-KEEP"})
    agen = stream_events(last_id="0", event_filter=frozenset({"piecework_counted"}))
    try:
        frame = await asyncio.wait_for(agen.__anext__(), timeout=5)
    finally:
        await agen.aclose()
    assert "piecework_counted" in frame
    assert "unrelated_event" not in frame


# ---------------------------------------------------------------- 端点


async def test_tc_evt_08_endpoint_requires_login(client: Any) -> None:
    response = await client.get(STREAM_API)
    assert response.status_code == 401
    assert response.json()["code"] == 11001


async def test_tc_evt_09_endpoint_redis_unavailable(
    client: Any, auth_headers: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Redis 未配置/不可达 → ``11012``（前端据此降级轮询，``05 §8``）。"""
    monkeypatch.setattr("app.modules.system.events.get_redis", lambda: None)
    response = await client.get(STREAM_API, headers=await _admin_headers(auth_headers))
    assert response.status_code == 503
    assert response.json()["code"] == 11012


@pytest.mark.parametrize("via_query_token", [False, True])
async def test_tc_evt_10_endpoint_wires_stream(
    client: Any, auth_headers: Any, monkeypatch: pytest.MonkeyPatch, *, via_query_token: bool
) -> None:
    """鉴权两种入口都能过，且回 ``text/event-stream`` + 把生成器的帧原样下发。

    ⚠️ 用假生成器（一帧即止）而不是真流：``ASGITransport`` 会把无限流读挂住。
    """

    async def _fake_stream(**_: Any) -> Any:
        yield format_sse("1-0", "piecework_counted", '{"bundle_no":"BD-WIRE"}')

    async def _available() -> bool:
        return True

    monkeypatch.setattr("app.modules.system.events.redis_available", _available)
    monkeypatch.setattr("app.modules.system.events.stream_events", _fake_stream)

    headers = await _admin_headers(auth_headers)
    if via_query_token:
        token = headers["Authorization"].removeprefix("Bearer ")
        response = await client.get(STREAM_API, params={"token": token})
    else:
        response = await client.get(STREAM_API, headers=headers)

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: piecework_counted" in response.text
    assert "BD-WIRE" in response.text
