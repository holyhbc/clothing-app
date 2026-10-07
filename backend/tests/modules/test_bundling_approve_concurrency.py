"""打菲单审核的**并发**用例（T-BUND-005b TC-AP-08）：20 并发 approve 同一单。

⚠️ 必须**独立引擎真提交**（docs/10 §5.4）：并发任务用自己的连接，外层事务没提交的话
它们根本看不见这张单（症状是全部报「打菲单不存在」）。
⚠️ 单开文件是因为它用**另一套夹具**（``app_database_url`` + ``bundling_world_persisted``），
而其余审核用例走 ``db_session``；混在一起读的人会以为并发也共享事务。
"""

import asyncio
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.common.models import DocumentLog
from app.core.errors import BusinessError, ErrorCode
from app.core.numbering import BUSINESS_TZ
from app.modules.bundling.models import Bundle, BundlingOrder
from app.modules.bundling.service import BundlingOrderService
from app.modules.cutting.models import CuttingOrderSizeLine, CuttingOutput
from tests.factories.bundling import attach_output, ctx, payload
from tests.factories.bundling_approve import REVIEWER_ID, reviewer_ctx
from tests.factories.user import OPERATOR_ID

# ------------------------------------------------------------------ 并发


async def test_concurrent_approve_one_wins(app_database_url, bundling_world_persisted) -> None:
    """TC-AP-08：**20 并发** approve 同一单 → 1 成功 19 败，无重复手号、无缺号。

    ⚠️ 失败码断言是**集合** ``{10003, 30001, 31005}``：条件 UPDATE 的 ``rowcount==0`` 报
    ``10003``，而先看到「已 APPROVED」的请求在进入迁移校验时就报 ``30001``（03 §9「非 SUBMITTED
    审核」），两者都是「只有一个成功」的合法表现。真正要守的是**码只生成一次** ——
    少一次 ``WHERE status='SUBMITTED'``，20 个请求会全部通过并生成 20 批码。

    ⚠️ 必须**独立引擎真提交**（docs/10 §5.4）：并发任务用自己的连接，外层事务没提交的话
    它们根本看不见这张单（症状是全部报「打菲单不存在」）。
    """
    engine = create_async_engine(app_database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    world = bundling_world_persisted
    try:
        async with factory() as session:
            size_line = await session.get(CuttingOrderSizeLine, world["cutting_size_lines"][0])
            assert size_line is not None
            await attach_output(
                session,
                world["style"],
                world["style_no"],
                world["workshop"],
                size_code=size_line.size_code,
                output_qty=Decimal("60"),
            )
            # 持久化夹具给的是**纯 id**，而 payload() 只取这几个属性 → 用轻量替身接进去
            ref = type("Ref", (), {"id": world["workshop"], "style_no": world["style_no"]})()
            order = await BundlingOrderService(session).create(
                payload(
                    {
                        "workshop": ref,
                        "style": ref,
                        "cutting_order": type("C", (), {"id": world["cutting_order"]})(),
                        "cutting_size_lines": [size_line],
                    },
                    lines=[
                        {
                            "cutting_size_line_id": size_line.id,
                            "size_code": size_line.size_code,
                            "hands": size_line.hands,
                        }
                    ],
                ),
                OPERATOR_ID,
            )
            await session.commit()
            await BundlingOrderService(session).submit(order.id, OPERATOR_ID, ctx())
            await session.commit()
            order_id = order.id

        async def attempt() -> ErrorCode | None:
            async with factory() as session:
                try:
                    await BundlingOrderService(session).approve(
                        order_id, REVIEWER_ID, reviewer_ctx()
                    )
                    await session.commit()
                    return None
                except BusinessError as exc:
                    await session.rollback()
                    return exc.code

        codes = await asyncio.gather(*(attempt() for _ in range(20)))
        assert codes.count(None) == 1, f"只该成功一次，实际 {codes.count(None)} 次：{codes}"
        failed = [code for code in codes if code is not None]
        assert len(failed) == 19
        assert set(failed) <= {
            ErrorCode.OPTIMISTIC_LOCK_CONFLICT,
            ErrorCode.CUTTING_STATUS_NOT_ALLOWED,
            ErrorCode.BUNDLE_HAND_DUPLICATED,
        }, f"失败码异常：{failed}"

        async with factory() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(Bundle)
                .where(Bundle.doc_id == order_id, Bundle.deleted_at.is_(None))
            )
            assert count == size_line.hands, f"码只应生成一次（{size_line.hands} 个），实际 {count}"
            hands = list(
                await session.scalars(
                    select(Bundle.hands)
                    .where(Bundle.doc_id == order_id, Bundle.deleted_at.is_(None))
                    .order_by(Bundle.hands)
                )
            )
            assert hands == list(range(1, size_line.hands + 1)), "手号必须无缺无重"
            approves = (
                select(func.count())
                .select_from(DocumentLog)
                .where(DocumentLog.doc_id == order_id, DocumentLog.action == "APPROVE")
            )
            assert (await session.execute(approves)).scalar_one() == 1, "只该有一条 APPROVE 日志"
            # 软删真提交的行（INV-7：没有物理删除，erp_app 也没有 DELETE 权限）
            now = datetime.now(tz=BUSINESS_TZ)
            for model, where in (
                (BundlingOrder, BundlingOrder.id == order_id),
                (CuttingOutput, CuttingOutput.style_no == world["style_no"]),
            ):
                await session.execute(
                    update(model).where(where).values(deleted_at=now, version=model.version + 1)  # type: ignore[attr-defined]
                )
            await session.commit()
    finally:
        await engine.dispose()
