"""打菲**申请侧**的真并发用例（T-BUND-008）。

============================  ==================================================
TC-BC-01（TC-12）            **20 并发**同一 ``Idempotency-Key`` 审核同一单 → 只生成 1 批码
TC-BC-02（TC-13）            并发两张**不同**的单抢同一尺码余量 → 一成一败 ``30002``、INV-6 不破
============================  ==================================================

⚠️ **独立引擎真提交**（docs/10 §5.4）：并发任务用自己的连接，外层事务没提交的话它们
根本看不见那张单（症状是全部报「打菲单不存在」）。共用助手与**精确清理**在
:mod:`tests.factories.bundling_concurrency`；本文件只放用例。

⚠️ **TC-BC-01 的证据是三件具体的事**，不是「没抛异常」：① 20 个请求的 HTTP 恒为 200
且 ``data`` 逐字相同（**不是** 19 个 409 —— 见下方 L-108 说明）；② 恰好 1 条 ``APPROVE``
日志；③ ``bundles`` 行数 == 手数且手号 ``1..N`` 无缺无重 —— **没有** ``IntegrityError``
漏成 500，也没有 ``31005``（那说明唯一索引被本模块之外的东西撞了）。

⚠️ 末尾的 **L-111** 用例守的是「真提交不许往共享库留残渣」：并发用例真提交的行不在任何
回滚范围内，而 ``test_document_logs`` / ``test_stock_views_and_grants`` 有全库计数断言
—— 漏清理的表现是「下一个用例失败，而失败原因与本用例毫无关系」。

⚠️ **TC-BC-01 的断言在 T-BUND-008f 修完 L-108 后改过一次**（这是唯一一处改既有断言，
改动理由必须留在案）：原稿断「恰好 1 个 200 + 19 个 ``{10003, 30001}``」，而那正是
``load_idempotent``「读缓存 → 没有就继续」**没有原子占位**时才会出现的形态 —— 该文件原
docstring 自己就写明「这是实现缺陷不是用例问题，本卡不改实现、也不把断言改成『19 次 200』」。
``load_idempotent`` 改成 Redis ``SET NX`` 占位后（docs/05 §5 已补并发语义），19 个请求
会**等到首次结果并原样返回**（HTTP 200），所以「19 个 409」变成假绿，必须改成
「20 个 200 且 ``data`` 逐字相同 + 只执行一次」。**唯一执行**的证据仍是状态机的条件
UPDATE（``03 §7`` 第一层）与唯一约束，幂等键管的是「客户端看到的响应」。

⚠️ **TC-BC-02 守的是 INV-6「恒不超发」**：预占走条件 UPDATE
（``state_repository.reserve_output_qty``：``available >= :qty`` 才加），并发下**只有一张
单能拿到余量**，另一张拿到 ``False`` → ``30002``。少了那个条件，两张单会同时看到同一份
余量并双双预占成功 —— 结转少掉 120 件，而**没有任何单表能验出这个差**。
"""

import asyncio
from collections.abc import AsyncIterator
from decimal import Decimal
from uuid import UUID

import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.common.enums import DocumentStatus
from app.core.errors import BusinessError, ErrorCode
from app.modules.bundling.models import Bundle, BundlingOrder
from app.modules.bundling.service import BundlingOrderService
from app.modules.cutting.models import CuttingOutput
from tests.factories.bundling import ctx
from tests.factories.bundling_concurrency import (
    CONCURRENCY,
    DOC_TYPE,
    concurrency_engine,
    make_persisted_order,
    pooled_client,
    purge_side_effects,
    reviewer_headers,
)
from tests.factories.user import OPERATOR_ID

API = "/api/v1/bundling-orders"


async def _code_snapshot(
    factory: async_sessionmaker[AsyncSession], order_id: UUID
) -> tuple[int, list[int]]:
    """本单的 ``(码行数, 手号序列)`` —— 「只生成一次」「手号无缺无重」的直接证据。"""
    async with factory() as session:
        rows = list(
            await session.scalars(
                select(Bundle.hands)
                .where(Bundle.doc_id == order_id, Bundle.deleted_at.is_(None))
                .order_by(Bundle.hands)
            )
        )
    return len(rows), rows


async def _log_count(factory: async_sessionmaker[AsyncSession], order_id: UUID, action: str) -> int:
    async with factory() as session:
        return int(
            (
                await session.execute(
                    text(
                        "SELECT count(*) FROM document_logs "
                        "WHERE doc_type = :t AND doc_id = :i AND action = :a"
                    ),
                    {"t": DOC_TYPE, "i": str(order_id), "a": action},
                )
            ).scalar_one()
        )


# ====================================================================== TC-BC-01 / TC-12


async def test_concurrent_approve_with_same_idempotency_key_generates_once(
    app_database_url: str,
    bundling_world_persisted,
    migration_url: str,
    _clean_redis_namespace: None,
) -> None:
    """TC-BC-01（TC-12）：**20 并发**同 ``Idempotency-Key`` 审核 → 只生成 1 批码。

    ⚠️ 走**接口层**而不是 service：这条要验的是幂等键那一层，而它在 router 上
    （``action_router.load_idempotent``）；service 直调根本碰不到它。
    ⚠️ 用 :func:`pooled_client` 而不是 ``conftest.client``：后者所有请求共用一条连接，
    20 个「并发」请求会被 PostgreSQL 串行化，测出来的是顺序执行。
    """
    async with concurrency_engine(app_database_url) as setup:
        order_id = await make_persisted_order(
            setup, bundling_world_persisted, size_code="XL", hands=2, submit=True
        )
    async with concurrency_engine(app_database_url) as factory:
        headers, employee_no = await reviewer_headers(factory)
    keyed = {**headers, "Idempotency-Key": f"approve-conc-{order_id.hex}"}
    barrier = asyncio.Barrier(CONCURRENCY)
    try:
        # ⚠️ **断言阶段必须在同一个引擎上下文里**：session 关掉后连接归池，而池在
        #    ``concurrency_engine`` 退出时就 dispose 了 —— 在外面再开一条 session，
        #    它的 socket 归进一个再没人关的池（见工厂里 NullPool 那段注释）。
        async with (
            concurrency_engine(app_database_url) as factory,
            pooled_client(factory) as client,
        ):

            async def attempt() -> tuple[int, object]:
                await barrier.wait()
                response = await client.post(f"{API}/{order_id}/approvals", headers=keyed)
                return response.status_code, response.json().get("data")

            results = await asyncio.gather(*(attempt() for _ in range(CONCURRENCY)))

            # ⚠️ **全部 200，且 ``data`` 与首次逐字相同**：同键同 body 必须回首次结果
            #    （docs/05 §5），不是 19 个 409。真正证明「只执行一次」的是下面那三条。
            assert {status for status, _ in results} == {200}, sorted(results)
            payloads = [body for _, body in results]
            assert all(item == payloads[0] for item in payloads), "19 个必须是首次那一份响应"

            count, hands = await _code_snapshot(factory, order_id)
            assert count == 2, f"码只应生成一次（2 个），实际 {count}"
            assert hands == [1, 2], f"手号必须无缺无重，实际 {hands}"
            assert await _log_count(factory, order_id, "APPROVE") == 1, "只该有一条 APPROVE 日志"
    finally:
        await purge_side_effects(migration_url, doc_ids=(order_id,), employee_nos=(employee_no,))


# ====================================================================== TC-BC-02 / TC-13


async def test_two_orders_contending_same_size_yield_one_30002(
    app_database_url: str, bundling_world_persisted, migration_url: str
) -> None:
    """TC-BC-02（TC-13）：并发两张单抢同一尺码余量 → 一成功一 ``30002``，INV-6 不破。

    ⚠️ 场景是「两张**不同**的单」而不是「同一张单提交两次」：后者被状态机挡在 ``30001``
    （TC-ST-06 已覆盖），根本走不到预占；两张单才会真的去抢同一行 ``cutting_outputs``。
    ⚠️ 结转只给 **120** 件（= 2 手 × 60）而两张单**各要 120**：所以「都成功」就等于超发
    120 件。用**三个独立口径**同时钉住 —— 结转四列、两张单的状态、SUBMIT 日志条数。
    """
    async with concurrency_engine(app_database_url) as setup:
        order_ids = (
            await make_persisted_order(setup, bundling_world_persisted, hands=2, output_qty=120),
            await make_persisted_order(setup, bundling_world_persisted, hands=2, output_qty=120),
        )
    style_no = bundling_world_persisted["style_no"]
    barrier = asyncio.Barrier(2)

    async def attempt(order_id: UUID) -> ErrorCode | None:
        async with factory() as session:
            await barrier.wait()
            try:
                await BundlingOrderService(session).submit(order_id, OPERATOR_ID, ctx())
                await session.commit()
                return None
            except BusinessError as exc:
                await session.rollback()
                return exc.code

    async with concurrency_engine(app_database_url) as factory:
        codes = await asyncio.gather(*(attempt(item) for item in order_ids))
    try:
        assert codes.count(None) == 1, f"只该成功一次，实际 {codes}"
        assert codes.count(ErrorCode.CUTTING_QTY_CONFLICT) == 1, f"另一张必须是 30002：{codes}"

        async with concurrency_engine(app_database_url) as factory, factory() as session:
            statuses = [
                str(item)
                for item in (
                    await session.execute(
                        select(BundlingOrder.status).where(BundlingOrder.id.in_(order_ids))
                    )
                ).scalars()
            ]
            row = (
                await session.execute(
                    select(
                        CuttingOutput.output_qty,
                        CuttingOutput.bundled_qty,
                        CuttingOutput.reserved_qty,
                        CuttingOutput.cut_waste_qty,
                    ).where(CuttingOutput.style_no == style_no)
                )
            ).one()
            generated = int(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(Bundle)
                        .where(Bundle.doc_id.in_(order_ids), Bundle.deleted_at.is_(None))
                    )
                ).scalar_one()
            )
        assert sorted(statuses) == sorted(
            [DocumentStatus.SUBMITTED.value, DocumentStatus.DRAFT.value]
        ), statuses

        output_qty, bundled_qty, reserved_qty, waste_qty = (Decimal(str(value)) for value in row)
        assert reserved_qty == Decimal("120"), "只该预占一次（INV-6）"
        assert bundled_qty == Decimal("0"), "提交不结转，结转只随 approve 变"
        assert output_qty - waste_qty - bundled_qty - reserved_qty == Decimal("0"), (
            "余量必须刚好用尽而不是为负"
        )
        assert generated == 0, "提交不生成码（码是审核的事）"
        # ⚠️ 日志按**各自的结果**断：赢的那张 1 条，输的那张 0 条（校验没过不许留痕，
        #   08 R2 —— 「被拒的动作也留一行 SUBMIT」会让审计时间线出现没发生过的动作）。
        async with concurrency_engine(app_database_url) as logs:
            for order_id, code in zip(order_ids, codes, strict=True):
                expected = 1 if code is None else 0
                assert await _log_count(logs, order_id, "SUBMIT") == expected, (
                    f"单 {order_id} 的结果码是 {code}，SUBMIT 日志条数应为 {expected}"
                )
    finally:
        await purge_side_effects(migration_url, doc_ids=order_ids)


# ====================================================================== L-111（测试隔离）


@pytest_asyncio.fixture
async def bundling_log_residue(migration_url: str) -> AsyncIterator[None]:
    """在持久化夹具**拆掉之后**再查 ``document_logs`` 残留（L-111）。

    ⚠️ **必须声明成测试函数的第一个参数**：pytest 按参数顺序建夹具、按**逆序**拆，
    所以只有这样 ``bundling_world_persisted`` 的 ``_purge_persisted_world`` 才会跑在
    本探针的断言**之前** —— 否则量到的是「还没清」的中间态，断言恒绿。

    ⚠️ **断「回到基线」而不是「等于 0」**：共享库里别的持久化用例留下的行与本条无关，
    按全库绝对值断言会让「上一个用例没清干净」变成这条的失败（报错点离真因三层）。
    """
    engine = create_async_engine(migration_url, pool_pre_ping=True)

    async def _count() -> int:
        async with engine.connect() as conn:
            return int(
                (
                    await conn.execute(
                        text("SELECT count(*) FROM document_logs WHERE doc_type = :t"),
                        {"t": DOC_TYPE},
                    )
                ).scalar_one()
            )

    before = await _count()
    yield
    after = await _count()
    await engine.dispose()
    assert after == before, (
        f"持久化夹具拆掉后 document_logs 仍有残留（{before} → {after}）："
        "清理条件与日志实际记的列对不上（L-111 打菲日志记的是 doc_id，不是款号）"
    )


async def test_persisted_world_leaves_no_document_logs(
    bundling_log_residue: None,
    app_database_url: str,
    bundling_world_persisted,
) -> None:
    """L-111：真提交并发用例跑完后 ``document_logs`` **必须回到基线**（连跑两次也是）。

    ⚠️ **先断前置「日志确实写出来了」**：直接断言残留 0 行的话，清理条件写错
    （比如按一个永远匹配不上的列删）会让「本条压根没造日志」也变绿 —— 而那正是
    L-111 的形态：``DELETE ... WHERE doc_no = '{款号}'`` 对 ``doc_no='BD-…'`` 恒删 0 行。
    """
    async with concurrency_engine(app_database_url) as factory:
        order_id = await make_persisted_order(factory, bundling_world_persisted, submit=True)
        assert await _log_count(factory, order_id, "SUBMIT") == 1, (
            "前置：真提交必须留下一条 SUBMIT 日志，否则下面的残留断言是假绿"
        )
