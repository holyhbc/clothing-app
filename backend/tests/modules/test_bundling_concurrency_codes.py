"""打菲**码侧**的真并发用例（T-BUND-008 第二组）。

============================  ==================================================
TC-BC-03（TC-25 + TC-29）    反审核后 **20 并发**审核同一单 → **0 成功、20 个 ``31005``**
TC-BC-04（TC-35 替身）       并发「计件回写 ``counted_at``」vs「反审核」→ 串行、绝不半单作废
TC-BC-05（03 §7 重复点击）   **20 并发**作废同一码 → 只 1 次生效、1 条 ``VOID_CODE`` 日志
============================  ==================================================

⚠️ 单开文件而不是并进 ``test_bundling_concurrency.py``：那两条已 230 行，
本卡再加三条就破 ADR-0030 的 400 行硬线。共用助手在
:mod:`tests.factories.bundling_concurrency`（含精确清理，L-094 / L-099）。

⚠️ **TC-BC-03 是「第三层防线」唯一的证据**（docs/10 §5.4「必须有兜底：通过 = 唯一约束
生效 + 业务层正确处理冲突；**只靠业务层检查 = 未通过**」）。反审核后码全 ``VOIDED``，
而 ④ 的预检**只看 ``status='ACTIVE'`` 的码**（``VOIDED`` 已退出业务）→ 预检必然放行；
此时拦住 20 个并发请求的**只有**库层唯一约束（``uq_bundles_hand`` / ``uq_bundles_no``，
同单重审时两者都会撞、PG 报哪一个不保证）。所以断言必须是**精确的 ``31005`` × 20**：
写成 ``{10003, 30001}`` 集合就变成「随便哪个错都行」，那条防线就白测了。

⚠️ **TC-BC-04 是 TC-35 的可测替身，不是 TC-35 本身**：卡面那条是「改手数 vs 正在计件」，
而 ``31006`` 在 ``ErrorCode`` 里已登记（T-BUND-007b）却**全仓无抛出点**，且
``patch`` 只改表头、``put_lines`` 只允许 ``DRAFT``/``REJECTED`` —— 已审核的单根本改不了
手数（详见 :mod:`tests.factories.bundling_concurrency` 与本卡 docstring 尾注）。
本组能测的是同一条锁序纪律的**另一半**：「本模块的反向动作（反审核）先
``FOR UPDATE`` 锁码、再判 ``counted_at``」。计件侧仍属 P2（``piecework_logs`` 未建表，
L-096），所以那一侧只以**裸 UPDATE** 出现，不伪造计件流水。

⚠️ **TC-BC-04 的不变量选的是「不会半单作废」而不是「VOIDED 的码一定没被计件」**：
后者**不成立** —— 计件侧那个事务若在反审核提交之后才提交，码就同时是 ``VOIDED`` 且
``counted_at`` 非空，而拦截它的锁在**计件侧**（P2 建表后才有）。断言一个当前为假的
性质会逼着人去改断言（AGENTS §2.1），所以这里只断打菲侧真正保证的那条。
"""

import asyncio
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.common.enums import DocumentStatus
from app.core.errors import BusinessError, ErrorCode
from app.modules.bundling.models import Bundle
from app.modules.bundling.service import BundlingOrderService
from app.modules.cutting.models import CuttingOutput
from tests.factories.bundling import ctx
from tests.factories.bundling_approve import REVIEWER_ID, reviewer_ctx
from tests.factories.bundling_concurrency import (
    CONCURRENCY,
    DOC_TYPE,
    approve_persisted_order,
    concurrency_engine,
    make_persisted_order,
    purge_side_effects,
    reverse_persisted_order,
)
from tests.factories.user import OPERATOR_ID

#: 卡面 TC-BC-04「改手数 vs 正在计件」当前**不可测**，原因见模块 docstring；
#: 用本组 TC-BC-04（反审核 vs 计件回写）覆盖同一条锁序纪律。
DEFERRED = "TC-35 原口径（改手数）待 31006 实现后单开"


async def _rows(factory: async_sessionmaker[AsyncSession], order_id: UUID) -> list[tuple]:
    """本单全部码的 ``(hands, status, counted_qty, counted_by)``，按手号排序。"""
    async with factory() as session:
        result = await session.execute(
            select(Bundle.hands, Bundle.status, Bundle.counted_qty, Bundle.counted_by)
            .where(Bundle.doc_id == order_id, Bundle.deleted_at.is_(None))
            .order_by(Bundle.hands)
        )
        return [(int(r[0]), str(r[1]), Decimal(str(r[2])), r[3]) for r in result.all()]


async def _log_count(factory: async_sessionmaker[AsyncSession], doc_id: UUID, action: str) -> int:
    async with factory() as session:
        return int(
            (
                await session.execute(
                    text(
                        "SELECT count(*) FROM document_logs "
                        "WHERE doc_type = :t AND doc_id = :i AND action = :a"
                    ),
                    {"t": DOC_TYPE, "i": str(doc_id), "a": action},
                )
            ).scalar_one()
        )


async def _bundled(factory: async_sessionmaker[AsyncSession], style_no: str) -> Decimal:
    async with factory() as session:
        value = (
            await session.execute(
                select(CuttingOutput.bundled_qty).where(CuttingOutput.style_no == style_no)
            )
        ).scalar_one()
    return Decimal(str(value))


async def _approved_two_hand_order(app_database_url: str, world: dict[str, object]) -> UUID:
    """真提交一张「XL 2 手、已审核（已出码）」的单。"""
    async with concurrency_engine(app_database_url) as factory:
        order_id = await make_persisted_order(factory, world, size_code="XL", hands=2, submit=True)
        await approve_persisted_order(factory, order_id)
    return order_id


# ====================================================================== TC-BC-03


async def test_concurrent_approve_after_reverse_all_hit_unique_index(
    app_database_url: str, bundling_world_persisted, migration_url: str
) -> None:
    """TC-BC-03（TC-25 + TC-29）：反审核后 **20 并发**审核 → 0 成功、**20 个 ``31005``**。

    ⚠️ **20 个失败码必须全是 ``31005``**：这一层没有状态机兜底（每个请求都看到
    ``SUBMITTED`` → ``SUBMITTED``，但没人 commit 过），预检也只看 ``ACTIVE`` 码而现在
    全是 ``VOIDED`` —— 于是**只有**库层唯一约束能拦。断言写成 ``{10003, 30001, 31005}``
    集合就把「唯一索引有没有生效」这件事测没了。
    ⚠️ 同时断**码行数不变**：唯一索引报错必须整事务回滚，不许出现「补插了第 3 手」。
    """
    order_id = await _approved_two_hand_order(app_database_url, bundling_world_persisted)
    async with concurrency_engine(app_database_url) as factory:
        await reverse_persisted_order(factory, order_id, "打错了，重新打")
    barrier = asyncio.Barrier(CONCURRENCY)

    async def attempt() -> ErrorCode | None:
        async with factory() as session:
            await barrier.wait()
            try:
                await BundlingOrderService(session).approve(order_id, REVIEWER_ID, reviewer_ctx())
                await session.commit()
                return None
            except BusinessError as exc:
                await session.rollback()
                return exc.code

    try:
        codes = await asyncio.gather(*(attempt() for _ in range(CONCURRENCY)))
        assert codes.count(None) == 0, f"手号已被占，任何一次都不该成功：{codes}"
        assert set(codes) == {ErrorCode.BUNDLE_HAND_DUPLICATED}, f"失败码必须是 31005：{codes}"

        rows = await _rows(factory, order_id)
        assert [row[0] for row in rows] == [1, 2], "手号仍被反审核产生的 VOIDED 码占着（B12）"
        assert {row[1] for row in rows} == {"VOIDED"}, "反审核后的码保持作废"
        assert await _log_count(factory, order_id, "REVERSE") == 1
        # ⚠️ 反审核留下的那 1 条 APPROVE 仍在（历史留痕不删），而这 20 次都没再写 APPROVE
        assert await _log_count(factory, order_id, "APPROVE") == 1, "20 次失败不许留下 APPROVE 日志"
        async with factory() as session:
            status = (
                await session.execute(
                    text("SELECT status FROM bundling_orders WHERE id = :i"), {"i": str(order_id)}
                )
            ).scalar_one()
        assert DocumentStatus(str(status)) is DocumentStatus.SUBMITTED, (
            "全部回滚后状态必须仍是 SUBMITTED（没有半单 APPROVED）"
        )
    finally:
        await purge_side_effects(migration_url, doc_ids=(order_id,))


# ====================================================================== TC-BC-04


async def test_reverse_after_counted_is_rejected_without_voiding(
    app_database_url: str, bundling_world_persisted, migration_url: str
) -> None:
    """TC-BC-04 确定性半边：``counted_at`` 已写 → 反审核报 **32003** 且**一码未作废**。

    ⚠️ 这半边必须**先做**：它给出确切的错误码，而并发那一半只能给出「串行后的结果」。
    「已计件的码被作废」是最贵的一类缺陷（码没了、计件流水还在，报表上这批件数
    既不在码里也不在结转里），所以先确定性地钉住它。
    """
    world = bundling_world_persisted
    order_id = await _approved_two_hand_order(app_database_url, world)
    async with concurrency_engine(app_database_url) as factory:
        await _write_counted(factory, order_id, hands=2, counted_qty=28)
        before = await _rows(factory, order_id)
        assert before[1][3] is not None and before[1][2] == Decimal("28"), "前置没写上计件痕迹"

        with pytest.raises(BusinessError) as caught:
            async with factory() as session:
                await BundlingOrderService(session).reverse(
                    order_id, "重来", REVIEWER_ID, reviewer_ctx()
                )
                await session.commit()
        assert caught.value.code is ErrorCode.PIECEWORK_SETTLED
        assert caught.value.details is not None and caught.value.details["bundle_no"], (
            "报错要指出是哪一手（用户才知道该去红冲哪一个）"
        )

        after = await _rows(factory, order_id)
        assert [row[1] for row in after] == ["ACTIVE", "ACTIVE"], "被拒的反审核一码都不许作废"
        assert after[1][2] == Decimal("28"), "已计件的那一手计数不变"
        assert await _bundled(factory, world["style_no"]) == Decimal("120"), "结转不变"
        assert await _log_count(factory, order_id, "REVERSE") == 0, "被拒的动作不许留痕"
    await purge_side_effects(migration_url, doc_ids=(order_id,))


async def _write_counted(
    factory: async_sessionmaker[AsyncSession],
    order_id: UUID,
    *,
    hands: int,
    counted_qty: int,
) -> None:
    """把某一手标成**已计件**（模拟计件模块回写，``piecework_logs`` 属 P2 / L-096）。

    ⚠️ 用裸 UPDATE 而不是造计件流水：本卡**不为 P2 建表、也不伪造流水**，
    而 ``32003`` 的判定只看 ``counted_at``（``approve_assert._assert_hands_not_counted``）。
    """
    async with factory() as session:
        await session.execute(
            text(
                "UPDATE bundles SET counted_qty = :q, counted_by = :u, counted_at = now() "
                "WHERE doc_id = :d AND hands = :h"
            ),
            {"q": counted_qty, "u": str(uuid4()), "d": str(order_id), "h": hands},
        )
        await session.commit()


async def test_concurrent_counted_write_and_reverse_never_half_void(
    app_database_url: str, bundling_world_persisted, migration_url: str
) -> None:
    """TC-BC-04 并发半边：计件回写与反审核**真并发** → 串行，**绝不出现半单作废**。

    ⚠️ 断的是**不变量**而不是某条具体错误码：两条并发事务谁先拿到码行锁是不确定的，
    所以结果必然落在「成功整单作废」或「被 ``32003`` 拒」之间。真正必须恒为真的那条是
    **同事务**：绝不允许「一半码 ``VOIDED``、一半 ``ACTIVE``」——
    那是 08 R4（副作用与状态变更同事务）被破坏的症状，比任何一个错误码都严重。
    另外恒断 ``ck_bundles_cnt`` 的业务侧不变量：每手 ``counted_qty <= bundle_qty``。
    """
    world = bundling_world_persisted
    style_no = world["style_no"]
    order_id = await _approved_two_hand_order(app_database_url, world)
    barrier = asyncio.Barrier(2)
    async with concurrency_engine(app_database_url) as factory:

        async def write() -> str:
            async with factory() as session:
                await barrier.wait()
                await session.execute(
                    text(
                        "UPDATE bundles SET counted_qty = 28, counted_by = :u, counted_at = now() "
                        "WHERE doc_id = :d"
                    ),
                    {"u": str(uuid4()), "d": str(order_id)},
                )
                await session.commit()
                return "counted"

        async def reverse() -> ErrorCode | None:
            async with factory() as session:
                await barrier.wait()
                try:
                    await BundlingOrderService(session).reverse(
                        order_id, "并发反审核", REVIEWER_ID, reviewer_ctx()
                    )
                    await session.commit()
                    return None
                except BusinessError as exc:
                    await session.rollback()
                    return exc.code

        try:
            counted_result, reverse_code = await asyncio.gather(write(), reverse())
            assert counted_result == "counted"
            assert reverse_code in (None, ErrorCode.PIECEWORK_SETTLED), f"异常结果：{reverse_code}"

            rows = await _rows(factory, order_id)
            statuses = {row[1] for row in rows}
            assert statuses in ({"ACTIVE"}, {"VOIDED"}), f"半单作废了：{statuses}"
            assert all(Decimal("0") <= row[2] <= Decimal("60") for row in rows), f"越界：{rows}"
            assert all((row[3] is not None) == (row[2] > 0) for row in rows), (
                f"counted_qty>0 必须有 counted_by（ck_bundles_one）：{rows}"
            )
            if reverse_code is None:
                assert statuses == {"VOIDED"}
                assert await _bundled(factory, style_no) == Decimal("0"), "反审核成功必须减回结转"
                assert await _log_count(factory, order_id, "REVERSE") == 1
            else:
                assert statuses == {"ACTIVE"}, "被 32003 拒的单不该有任何码被作废"
                assert await _bundled(factory, style_no) == Decimal("120"), "结转不变"
                assert await _log_count(factory, order_id, "REVERSE") == 0
        finally:
            await purge_side_effects(migration_url, doc_ids=(order_id,))


# ====================================================================== TC-BC-05


async def test_concurrent_void_same_code_voids_once(
    app_database_url: str, bundling_world_persisted, migration_url: str
) -> None:
    """TC-BC-05：**20 并发**作废同一码 → 只 1 次生效、1 条 ``VOID_CODE``、结转不动。

    ⚠️ 断言「19 个 ``31002``」而不是「19 个 200」：``03 §7`` 写的「第二次命中返回首次
    结果（200）」是 **Router 层** ``Idempotency-Key`` 短路的行为
    （``test_bundling_router2.test_void_code_rejects_counted_and_is_idempotent`` 守着）；
    service 层的设计就是「已作废 → ``31002``」。两条路都验，分开断才不会互相顶掉。
    ⚠️ 顺带钉住「单码作废**不动结转**」：动了就再也回不到原点（结转只随整单
    approve / reverse 变），而 10 张单各废 1 手之后没有任何表能验出那个差。
    """
    world = bundling_world_persisted
    order_id = await _approved_two_hand_order(app_database_url, world)
    async with concurrency_engine(app_database_url) as factory:
        async with factory() as probe:
            bundle_no = (
                await probe.execute(
                    text("SELECT bundle_no FROM bundles WHERE doc_id = :d AND hands = 1"),
                    {"d": str(order_id)},
                )
            ).scalar_one()
        barrier = asyncio.Barrier(CONCURRENCY)

        async def attempt() -> ErrorCode | None:
            async with factory() as session:
                await barrier.wait()
                try:
                    await BundlingOrderService(session).void_code(
                        str(bundle_no), "重复点两次", OPERATOR_ID, ctx()
                    )
                    await session.commit()
                    return None
                except BusinessError as exc:
                    await session.rollback()
                    return exc.code

        try:
            codes = await asyncio.gather(*(attempt() for _ in range(CONCURRENCY)))
            assert codes.count(None) == 1, f"只该生效一次，实际 {codes.count(None)}：{codes}"
            assert codes.count(ErrorCode.BUNDLE_ALREADY_VOIDED) == CONCURRENCY - 1, codes

            rows = await _rows(factory, order_id)
            assert [row[1] for row in rows] == ["VOIDED", "ACTIVE"], rows
            assert await _log_count(factory, order_id, "VOID_CODE") == 1, "只该有一条作废留痕"
            assert await _bundled(factory, world["style_no"]) == Decimal("120"), "单码作废不动结转"
            async with factory() as session:
                kept = int(
                    (
                        await session.execute(
                            select(func.count())
                            .select_from(Bundle)
                            .where(Bundle.doc_id == order_id, Bundle.deleted_at.is_(None))
                        )
                    ).scalar_one()
                )
            assert kept == 2, "作废不许删行（B12）"
        finally:
            await purge_side_effects(migration_url, doc_ids=(order_id,))
