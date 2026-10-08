"""``Idempotency-Key`` 的**并发语义**回归（T-BUND-008f，闭环 L-108）。

⚠️ **这组用例验的是 ``docs/05 §5`` 的并发承诺，不是「没抛异常」**。三条硬断言：
① **20 并发**同一键 → **恰好 1 次真正执行**（1 条 ``APPROVE`` 日志 + 恰好 1 批码）；
② 其余 19 个拿到的是**首次那一份响应**（HTTP 200 且 ``data`` 逐字相同），**不是** 19 个 409；
③ 同键**不同** body 在「首次仍在执行」时也分流成 ``10002``，不与等待混在一起。

⚠️ **为什么必须独立引擎真提交**（docs/10 §5.4）：共用一条 session 的 20 个「并发」请求
会被 PostgreSQL 串行化，测出来的是顺序执行 —— 而顺序执行下 ``load_idempotent`` 的
「读缓存 → 没有就继续」缺陷**永远不会暴露**（每个请求都读到了前一个写好的结果）。
所以这里照 :mod:`tests.factories.bundling_concurrency` 用 ``NullPool`` + 每请求一条
独立物理连接，并用 ``asyncio.Barrier`` 把 20 个任务对齐到同一起跑线。

⚠️ **等待必须有上限**：同键同 body 的请求在「首次仍在执行」时会轮询等结果，等满上限
就报**明确错误码**（``10003`` / HTTP 409）。无限等会把一个 worker 和一条数据库连接
占住，而且首次请求若已崩在执行中途（占位 TTL 未到），等下去也等不到任何结果。
"""

import asyncio
import json
import time
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import Request
from sqlalchemy import func, select, text

from app.core import idempotency as idem
from app.core.cache import get_redis
from app.core.errors import BusinessError, ErrorCode
from app.modules.bundling.models import Bundle
from tests.factories.bundling_concurrency import (
    CONCURRENCY,
    DOC_TYPE,
    concurrency_engine,
    make_persisted_order,
    pooled_client,
    purge_side_effects,
    reviewer_headers,
)

API = "/api/v1/bundling-orders"

#: nginx ``proxy_read_timeout``（``docker/nginx/nginx.conf``）—— 等待上限必须远小于它
NGINX_READ_TIMEOUT_SECONDS = 30


def _idem_request(key: str, raw: bytes) -> Request:
    """造一个只带 ``Idempotency-Key`` 与 body 的 ``Request``。

    ⚠️ 直接构造 ``Request`` 而不走 ASGI 客户端：这三条要验的是 ``core/idempotency.py``
    那一层本身（占位 / TTL / 等待上限 / 降级），不需要起引擎、不需要造单据；
    起引擎反而让「红」的原因变得看不出是缺陷还是环境。
    """

    scope: dict[str, Any] = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/_idempotency_probe",
        "headers": [
            (b"idempotency-key", key.encode()),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(raw)).encode()),
        ],
    }

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": raw, "more_body": False}

    return Request(scope, receive)


def _body(hands: int) -> bytes:
    return json.dumps({"hands": hands}, ensure_ascii=False).encode()


async def _one_execution(factory: Any, order_id: UUID) -> tuple[int, int, list[int]]:
    """本单的 ``(APPROVE 日志条数, 码行数, 手号序列)`` —— 「只执行一次」的直接证据。"""
    async with factory() as session:
        logs = int(
            (
                await session.execute(
                    text(
                        "SELECT count(*) FROM document_logs "
                        "WHERE doc_type = :t AND doc_id = :i AND action = 'APPROVE'"
                    ),
                    {"t": DOC_TYPE, "i": str(order_id)},
                )
            ).scalar_one()
        )
        hands = list(
            await session.scalars(
                select(Bundle.hands)
                .where(Bundle.doc_id == order_id, Bundle.deleted_at.is_(None))
                .order_by(Bundle.hands)
            )
        )
        codes = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(Bundle)
                    .where(Bundle.doc_id == order_id, Bundle.deleted_at.is_(None))
                )
            ).scalar_one()
        )
    return logs, codes, [int(item) for item in hands]


# ============================================================ ① 20 并发同一键


async def test_20_concurrent_same_key_execute_once_and_rest_get_first_result(
    app_database_url: str,
    bundling_world_persisted,
    migration_url: str,
    _clean_redis_namespace: None,
) -> None:
    """**20 并发**同 ``Idempotency-Key`` 审核 → 恰好 1 次执行，其余 19 次**回首次结果**。

    ⚠️ 断「19 次 200 且 ``data`` 与首次逐字相同」而不是「19 次 409」：``docs/05 §5``
    承诺的是「同键同 body 回首次结果、不返回错误」，409 是「客户端该自己消化的冲突」。
    用户视角是「连点两次审核，第二次说失败」，而真相是两次都跑了、第二次撞状态机。
    """
    async with concurrency_engine(app_database_url) as setup:
        order_id = await make_persisted_order(setup, bundling_world_persisted, hands=2, submit=True)
    async with concurrency_engine(app_database_url) as factory:
        headers, employee_no = await reviewer_headers(factory)
    keyed = {**headers, "Idempotency-Key": f"idem-conc-{order_id.hex}"}
    barrier = asyncio.Barrier(CONCURRENCY)
    try:
        async with (
            concurrency_engine(app_database_url) as factory,
            pooled_client(factory) as client,
        ):

            async def attempt() -> tuple[int, Any]:
                await barrier.wait()
                response = await client.post(f"{API}/{order_id}/approvals", headers=keyed)
                return response.status_code, response.json()

            results = await asyncio.gather(*(attempt() for _ in range(CONCURRENCY)))

            statuses = [status for status, _ in results]
            assert set(statuses) == {200}, f"命中幂等的请求必须是 200（首次结果），实际 {statuses}"
            payloads = [body.get("data") for _, body in results]
            assert all(item == payloads[0] for item in payloads), (
                "19 个必须是首次那一份响应，逐字相同（不是各自的 409）"
            )
            logs, codes, hands = await _one_execution(factory, order_id)
            assert logs == 1, f"恰好一次执行：只该有一条 APPROVE 日志，实际 {logs}"
            assert codes == 2, f"码只应生成一次（2 手），实际 {codes}"
            assert hands == [1, 2], f"手号必须无缺无重，实际 {hands}"
    finally:
        await purge_side_effects(migration_url, doc_ids=(order_id,), employee_nos=(employee_no,))


# ============================================================ ② 占位的 TTL


async def test_placeholder_carries_ttl_and_key_is_reusable_after_it_expires(
    _clean_redis_namespace: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """占位**必须带 TTL**；TTL 过后同键能重新占位执行（崩溃在执行中途不会废掉这个键）。

    ⚠️ TTL 是硬约束不是优化：占位是「我正在执行」的独占声明，而写结果只在**成功**时
    发生 —— 进程崩在执行中途时没人来清它，没有 TTL 的话这个幂等键**永久不可用**。
    所以这里直接读 Redis 的 ``TTL``（不是「行为上看起来会过期」）：``-1`` 表示没设过期。
    """
    monkeypatch.setattr(idem, "PLACEHOLDER_TTL_SECONDS", 1)
    idem.clear_memory_store()
    key = f"ttl-{uuid4().hex}"
    raw = _body(2)

    owner = await idem.load_idempotent(_idem_request(key, raw))
    assert owner is not None and owner.cached is None, "首个请求应当拿到占位去执行"

    client = get_redis()
    assert client is not None
    remaining = await client.ttl(f"idem:{key}")
    assert 0 < remaining <= 1, f"占位必须带 TTL（-1 = 没设过期，-2 = 键不存在），实际 {remaining}"

    await asyncio.sleep(1.2)
    again = await idem.load_idempotent(_idem_request(key, raw))
    assert again is not None and again.cached is None, (
        "TTL 过后同一个键必须能重新占位执行，否则崩溃的请求永久废掉了这个键"
    )


# ============================================================ ③ 同键不同 body


async def test_different_body_while_first_is_running_raises_10002_immediately(
    _clean_redis_namespace: None,
) -> None:
    """占位期间同键**不同** body → ``10002``，且**不进等待循环**（不等满上限才报错）。

    ⚠️ 「立即」用时间断而不是只看错误码：把异 body 当成「也在等首次结果」的话，
    每次误用都要白等满 5 秒才拿到一个错码，用户会以为接口卡住了。
    """
    idem.clear_memory_store()
    key = f"clash-{uuid4().hex}"
    owner = await idem.load_idempotent(_idem_request(key, _body(2)))
    assert owner is not None and owner.cached is None

    started = time.monotonic()
    with pytest.raises(BusinessError) as caught:
        await idem.load_idempotent(_idem_request(key, _body(3)))
    elapsed = time.monotonic() - started
    assert caught.value.code is ErrorCode.MISSING_BUSINESS_PARAM
    assert caught.value.details["idempotency_key"] == key
    assert elapsed < 1.0, f"异 body 必须立刻分流，不许进等待循环（实测等了 {elapsed:.2f}s）"


# ============================================================ ④ 等待上限


async def test_waiting_for_first_result_is_bounded_and_reports_explicit_code(
    _clean_redis_namespace: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """等首次结果**有上限**：超时报 ``10003``（HTTP 409），随后结果写好仍能正常命中。

    ⚠️ 断两半：① 超时必须是**明确错误码**而不是超时挂起 —— 无限等会占住一个 worker
    与一条数据库连接，且首次请求若已崩在中途，等下去也等不到任何结果；
    ② 超时**不销毁占位**，首次请求把结果写上之后同键必须立刻拿到它（不能被毒化）。
    """
    monkeypatch.setattr(idem, "WAIT_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(idem, "POLL_INTERVAL_SECONDS", 0.01)
    idem.clear_memory_store()
    key = f"wait-{uuid4().hex}"
    raw = _body(2)
    owner = await idem.load_idempotent(_idem_request(key, raw))
    assert owner is not None and owner.cached is None

    started = time.monotonic()
    with pytest.raises(BusinessError) as caught:
        await idem.load_idempotent(_idem_request(key, raw))
    elapsed = time.monotonic() - started
    assert caught.value.code is ErrorCode.OPTIMISTIC_LOCK_CONFLICT
    assert caught.value.http_status == 409, "等待超时是客户端可消化的冲突，不是 500"
    assert caught.value.details["idempotency_key"] == key
    assert 0.2 <= elapsed < 5.0, f"必须等到上限才放弃（不该立刻报错），实测 {elapsed:.2f}s"

    await idem.store_idempotent(owner, {"data": {"id": "first-result"}})
    hit = await idem.load_idempotent(_idem_request(key, raw))
    assert hit is not None and hit.cached is not None, "结果写上后同键必须命中"
    assert hit.cached["response"] == {"data": {"id": "first-result"}}


async def test_production_bounds_are_ordered() -> None:
    """**生产取值**本身也要有界（否则「有上限」只是一句空话）。"""
    assert 0 < idem.WAIT_TIMEOUT_SECONDS <= NGINX_READ_TIMEOUT_SECONDS, (
        "等待上限必须远小于 nginx proxy_read_timeout，否则先被网关掐断而不是拿到错误码"
    )
    assert idem.WAIT_TIMEOUT_SECONDS < idem.PLACEHOLDER_TTL_SECONDS, (
        "占位 TTL 必须长于等待上限：否则等一次就等于把占位等过期，然后自己重新执行"
    )
    assert 0 < idem.POLL_INTERVAL_SECONDS <= 1
    assert idem.TTL_SECONDS == 24 * 3600, "docs/05 §5 写死 24h"


# ============================================================ ⑤ Redis 不可用时的降级


async def test_redis_unavailable_degrades_to_process_memory(
    _clean_redis_namespace: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Redis 不可用 → 降级为**进程内**占位（docs/05 §5），且降级后**仍有等待上限**。

    ⚠️ 降级路径同样要断「有上限」：把「Redis 挂了」当成「别人占着这个键」的话，
    所有请求都会去等一个永远不会出现的首次结果 —— 故障从「幂等降级为尽力而为」
    放大成「整个接口不可用」。
    """
    monkeypatch.setattr("app.core.cache.get_redis", lambda: None)
    monkeypatch.setattr(idem, "WAIT_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(idem, "POLL_INTERVAL_SECONDS", 0.01)
    idem.clear_memory_store()
    key = f"degraded-{uuid4().hex}"
    raw = _body(2)

    owner = await idem.load_idempotent(_idem_request(key, raw))
    assert owner is not None and owner.cached is None, "降级路径也要有占位"
    with pytest.raises(BusinessError) as caught:
        await idem.load_idempotent(_idem_request(key, raw))
    assert caught.value.code is ErrorCode.OPTIMISTIC_LOCK_CONFLICT, "降级后也必须有等待上限"

    await idem.store_idempotent(owner, {"data": {"ok": True}})
    hit = await idem.load_idempotent(_idem_request(key, raw))
    assert hit is not None and hit.cached is not None
    assert hit.cached["response"] == {"data": {"ok": True}}
    idem.clear_memory_store()
