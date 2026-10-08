"""打菲**申请侧**的真并发用例（T-BUND-008）。

============================  ==================================================
TC-BC-01（TC-12）            **20 并发**同一 ``Idempotency-Key`` 审核同一单 → 只生成 1 批码
TC-BC-02（TC-13）            并发两张**不同**的单抢同一尺码余量 → 一成一败 ``30002``、INV-6 不破
============================  ==================================================

⚠️ **独立引擎真提交**（docs/10 §5.4）：并发任务用自己的连接，外层事务没提交的话它们
根本看不见那张单（症状是全部报「打菲单不存在」）。共用助手与**精确清理**在
:mod:`tests.factories.bundling_concurrency`；本文件只放用例。

⚠️ **TC-BC-01 的证据是四件具体的事**，不是「没抛异常」：① 恰好 1 个 ``200``；② 恰好
1 条 ``APPROVE`` 日志；③ ``bundles`` 行数 == 手数且手号 ``1..N`` 无缺无重；
④ 失败码落在 ``{10003, 30001}`` 且 HTTP 恒为 409 —— **没有** ``IntegrityError``
漏成 500，也没有 ``31005``（那说明唯一索引被本模块之外的东西撞了）。

⚠️ **TC-BC-01 与卡面「19 次返回首次结果（HTTP 200）」对不上，这是实现缺陷不是用例问题**：
``core/idempotency.py`` 的 ``load_idempotent`` 是「读缓存 → 没有就继续」，**没有原子占位**
（SETNX 之类），20 个并发请求会同时读到空缓存、全部去跑业务。真正兜住「只生成一次」的
是状态机的条件 UPDATE（``03 §7`` 第一层），不是幂等键。**串行**重试确实命中缓存返回首次
结果（``test_bundling_router_actions.test_approve_idempotency_key_applies_once`` 守着），
并发窗口内不成立。已登记为缺陷，本卡**不改实现、也不把断言改成「19 次 200」**。

⚠️ **TC-BC-02 守的是 INV-6「恒不超发」**：预占走条件 UPDATE
（``state_repository.reserve_output_qty``：``available >= :qty`` 才加），并发下**只有一张
单能拿到余量**，另一张拿到 ``False`` → ``30002``。少了那个条件，两张单会同时看到同一份
余量并双双预占成功 —— 结转少掉 120 件，而**没有任何单表能验出这个差**。
"""

import asyncio
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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

            async def attempt() -> tuple[int, int]:
                await barrier.wait()
                response = await client.post(f"{API}/{order_id}/approvals", headers=keyed)
                return response.status_code, response.json().get("code", 0)

            results = await asyncio.gather(*(attempt() for _ in range(CONCURRENCY)))

            ok = [item for item in results if item[0] == 200]
            failed = [item for item in results if item[0] != 200]
            assert len(ok) == 1, f"只该成功一次，实际 {len(ok)}：{results}"
            assert len(failed) == CONCURRENCY - 1
            assert {code for _, code in failed} <= {
                ErrorCode.OPTIMISTIC_LOCK_CONFLICT,
                ErrorCode.CUTTING_STATUS_NOT_ALLOWED,
            }, f"失败码异常：{sorted({code for _, code in failed})}"
            assert {status for status, _ in failed} == {409}, sorted(failed)

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
