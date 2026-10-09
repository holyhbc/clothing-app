"""打菲**审核后增手**的并发用例（T-BUND-011 TC-HI-06）：20 并发各增 1 手。

⚠️ 必须**独立引擎真提交**（docs/10 §5.4）：并发任务用自己的连接，外层事务没提交的话
它们根本看不见这张单（症状是全部报「打菲单不存在」）。所以本文件不碰 ``db_session``，
而是自己建 ``NullPool`` 引擎 —— 理由见 :func:`tests.factories.bundling_concurrency.
concurrency_engine` 的 docstring（QueuePool 会把「20 并发」悄悄退化成「分批串行」）。

⚠️ 单开文件而不是并进 ``test_bundling_hand_increment.py``：那边全部走回滚型
``db_session``，混在一起读的人会以为并发也共享事务（ADR-0030 的 400 行硬线之外的
第二条理由：夹具不同）。

## 这条用例真正在守什么

分两轮，因为**「乐观锁」与「手号不重」在本端点上是两件事**：

**第一轮（20 个请求带同一个版本号）**：20 个人同时读到 ``version=v`` 然后同时提交，
所以只有 1 个能落地，其余 19 个必须是 **``10003``**（乐观锁）而**不是** ``31005``
（手序号重复）。这一轮守的是「版本号真被当乐观锁用」，它同时是**手号是否算重**的判据：
若 20 个事务都去写第 ``N+1`` 手，撞 ``uq_bundles_hand`` 的那 19 个会报 ``31005``
（整单回滚，补不上第二手）—— 症状正是「19 次 409 全败、永远凑不齐 20 手」。
算手号在**锁住表头之后**才读，20 个事务因此拿到互不相同的起点。

**第二轮（刷新后重试）**：``10003`` 的处理就是「刷新后重试」（05 §4），重试前重读版本号。
增手是**纯增量**，重试不会多打一手，所以 20 个请求**最终必须全部落地**：
``N+20`` 手、20 个新码、手号连续无缺、20 条 ``HAND_INCREMENT`` 日志。
"""

import asyncio
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.common.models import DocumentLog
from app.modules.bundling.models import Bundle, BundlingOrder
from app.modules.bundling.service import BundlingOrderService
from tests.factories.bundling import attach_output, ctx, payload
from tests.factories.bundling_concurrency import (
    CONCURRENCY,
    approve_persisted_order,
    concurrency_engine,
    persisted_size_line,
    pooled_client,
    purge_side_effects,
    reviewer_headers,
    set_persisted_size_hands,
    world_refs,
)
from tests.factories.user import OPERATOR_ID

API = "/api/v1/bundling-orders"

#: 本单先审的手数（分批打的「先开工」那一批）。
APPROVED_HANDS = 3

#: 裁剪侧给足的可打手数：**必须 > APPROVED_HANDS + CONCURRENCY**，否则 ③ 防超打
#: （``31004``）会把并发请求拦掉一部分 —— 那条不变式在增手时是要**重跑**的。
CUTTING_HANDS = APPROVED_HANDS + CONCURRENCY + 2

QTY_PER_HAND = 60


async def _analyze_after_purge(migration_url: str) -> None:
    """真提交用例清完行之后 **ANALYZE** 受影响的表（用迁移账号）。

    ⚠️ **为什么这条不是洁癖**：并发用例是真提交 + ``DELETE`` 清理，清理完 ``bundles`` /
    ``users`` 的统计信息就**过期**了（PG 要等 autovacuum 才更新），而
    ``test_bundling_counted.py::test_pending_query_plan_uses_counted_pending_index``
    断言的是 **EXPLAIN 计划里出现哪个索引** —— 规划器的选择依赖这些估计行数，于是
    「上一个真提交用例跑过什么」会决定它的红绿（实测踩到：同一套用例偶发红，而重跑就绿）。
    在本用例清理完立刻 ANALYZE，把计划断言的输入固定下来。

    ⚠️ 刻意**不碰**那个用例的断言（AGENTS §2.1）：让它在别人留下的统计漂移上飘才是问题。
    """
    engine = create_async_engine(migration_url, pool_pre_ping=True)
    try:
        async with engine.begin() as conn:
            for table in ("bundles", "users", "bundling_orders"):
                await conn.execute(text(f"ANALYZE {table}"))
    finally:
        await engine.dispose()


async def _seed(factory: async_sessionmaker[AsyncSession], world: dict[str, Any]) -> UUID:
    """真提交一张「已审核 3 手、裁剪侧备了 35 手」的打菲单。"""
    async with factory() as session:
        size_line = await persisted_size_line(session, world, "XL")
        await set_persisted_size_hands(
            session, size_line, hands=CUTTING_HANDS, qty_per_hand=QTY_PER_HAND
        )
        await attach_output(
            session,
            world["style"],
            world["style_no"],
            world["workshop"],
            size_code="XL",
            output_qty=Decimal(CUTTING_HANDS * QTY_PER_HAND),
        )
        refs = world_refs(world)
        refs["cutting_size_lines"] = [size_line]
        order = await BundlingOrderService(session).create(
            payload(
                refs,
                lines=[
                    {
                        "cutting_size_line_id": size_line.id,
                        "size_code": "XL",
                        "hands": APPROVED_HANDS,
                    }
                ],
            ),
            OPERATOR_ID,
        )
        await session.commit()
        await BundlingOrderService(session).submit(order.id, OPERATOR_ID, ctx())
        await session.commit()
        order_id = order.id
    await approve_persisted_order(factory, order_id)
    return order_id


async def _version(factory: async_sessionmaker[AsyncSession], order_id: UUID) -> int:
    async with factory() as session:
        return int(
            await session.scalar(select(BundlingOrder.version).where(BundlingOrder.id == order_id))
        )


async def _snapshot(factory: async_sessionmaker[AsyncSession], order_id: UUID) -> dict[str, Any]:
    async with factory() as session:
        hands = list(
            await session.scalars(
                select(Bundle.hands)
                .where(Bundle.doc_id == order_id, Bundle.deleted_at.is_(None))
                .order_by(Bundle.hands)
            )
        )
        logs = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(DocumentLog)
                    .where(DocumentLog.doc_id == order_id, DocumentLog.action == "HAND_INCREMENT")
                )
            ).scalar_one()
        )
        totals = (
            await session.execute(
                select(BundlingOrder.hands_total, BundlingOrder.output_qty).where(
                    BundlingOrder.id == order_id
                )
            )
        ).one()
    return {
        "hands": hands,
        "logs": logs,
        "hands_total": totals[0],
        "output_qty": Decimal(totals[1]),
    }


async def test_twenty_concurrent_increments_land_without_gaps(
    app_database_url: str, bundling_world_persisted, migration_url: str
) -> None:
    """TC-HI-06：20 并发各增 1 手 → ``N+20`` 手、20 个新码、**手号连续无缺**。

    ⚠️ 分两轮（见模块 docstring）：第一轮 20 个请求**带同一个版本号**，因此
    ``1 成功 + 19 个 10003`` 才是**正确**行为（乐观锁）—— 断言它正是为了把这个语义钉死，
    否则哪天有人「为了让 20 个都成功」把版本号检查去掉，这条用例就是下一个报错的发现处。
    """
    world = bundling_world_persisted
    async with concurrency_engine(app_database_url) as factory:
        order_id = await _seed(factory, world)
        headers, employee_no = await reviewer_headers(factory)
        try:
            async with pooled_client(factory) as client:
                url = f"{API}/{order_id}/hand-increments"

                async def add_one() -> tuple[int, int]:
                    """一次尝试：(HTTP 状态, 业务码)。"""
                    version = await _version(factory, order_id)
                    response = await client.post(
                        url,
                        json={"version": version, "size_code": "XL", "delta_hands": 1},
                        headers=headers,
                    )
                    code = int(response.json()["code"]) if response.status_code != 200 else 0
                    return response.status_code, code

                async def add_one_until_done() -> tuple[int, list[int]]:
                    """刷新后重试到成功为止（增手是纯增量，重试不会多打一手）。"""
                    codes: list[int] = []
                    for _ in range(CONCURRENCY):
                        status, code = await add_one()
                        if status == 200:
                            return status, codes
                        assert status == 409, status
                        codes.append(code)
                    raise AssertionError("重试若干轮仍未成功")

                # ---- 第一轮：**先并发读完版本号再并发提交**（确定性场景，不靠调度运气）
                stale = await asyncio.gather(
                    *(_version(factory, order_id) for _ in range(CONCURRENCY))
                )
                assert len(set(stale)) == 1, f"20 个请求应读到同一个版本号，实际 {set(stale)}"
                body = {"version": stale[0], "size_code": "XL", "delta_hands": 1}
                first = await asyncio.gather(
                    *(client.post(url, json=body, headers=headers) for _ in range(CONCURRENCY))
                )
                statuses = [response.status_code for response in first]
                assert statuses.count(200) == 1, f"带同一个版本号只该成功一次：{statuses}"
                failed = [
                    response.json()["code"] for response in first if response.status_code != 200
                ]
                assert set(failed) == {10003}, (
                    f"失败只能是乐观锁 10003（31005 = 手号算重了）：{failed}"
                )
                after_first = await _snapshot(factory, order_id)
                assert after_first["hands"] == [1, 2, 3, 4], after_first
                assert after_first["logs"] == 1, "被回滚的 19 次不许留下日志"

                # ---- 第二轮：**被拒的那 19 个**刷新后重试 → 20 个请求最终全部落地。
                # ⚠️ 这里只跑 19 个：第一轮已成功的那 1 个**没有待重试的东西**，再跑一次
                # 就成了「多打一手」（症状是手号多出一个，本条的连续性断言直接抓得到）。
                results = await asyncio.gather(
                    *(add_one_until_done() for _ in range(CONCURRENCY - 1))
                )
                seen = {code for _status, codes in results for code in codes}
                assert seen <= {10003}, f"重试阶段出现了非乐观锁的失败码：{seen}"
                assert all(status == 200 for status, _ in results), (
                    f"20 个请求必须全部落地（这正是「19 次 409 全败」与「都成功」的分界）：{results}"
                )

            snapshot = await _snapshot(factory, order_id)
            assert snapshot["hands"] == list(range(1, APPROVED_HANDS + CONCURRENCY + 1)), (
                f"手号必须连续无缺，实际 {snapshot['hands']}"
            )
            assert snapshot["hands_total"] == APPROVED_HANDS + CONCURRENCY
            assert snapshot["output_qty"] == Decimal(
                (APPROVED_HANDS + CONCURRENCY) * QTY_PER_HAND
            ), "表头件数随每手同步"
            assert snapshot["logs"] == CONCURRENCY, "每次成功增手一条日志"
        finally:
            await purge_side_effects(
                migration_url, doc_ids=(order_id,), employee_nos=(employee_no,)
            )
            await _analyze_after_purge(migration_url)
