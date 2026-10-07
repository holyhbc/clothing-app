"""打菲单**轻状态迁移**（``submit``/``reject``/``withdraw``/``cancel``）的测试（T-BUND-005a）。

TC-ST-01 ``submit`` 正常（``SUBMITTED`` + 预占 + 快照 + 日志）；TC-ST-02 超量
``30002`` 且**零副作用**；TC-ST-03 驳回缺原因 ``10002``；TC-ST-04 非制单人撤回
``12001``；TC-ST-05 作废 ``CANCELLED`` + 终态；TC-ST-06 **20 并发** submit 一成一败
；TC-ST-07 四动作都有日志（from/to/原因/操作人）。

⚠️ **TC-ST-02 与 TC-ST-06 是这一卡最要紧的两条**：前者守「预占失败必须零副作用」（漏一次
回滚就留下半张单占着裁剪余量）；后者守「状态字段只由条件 UPDATE 写」——少了
``WHERE ... AND status=?``，20 个并发请求会全部成功，把同一份余量预占 20 次（INV-6 破）。
⚠️ 并发用例必须**独立引擎真提交**（docs/10 §5.4），否则并发任务看不见那张单。
"""

import asyncio
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.common.enums import DocumentStatus
from app.common.models import DocumentLog
from app.core.errors import BusinessError, ErrorCode
from app.core.numbering import BUSINESS_TZ
from app.modules.bundling.models import Bundle, BundlingOrder
from app.modules.bundling.schemas import CancelIn, RejectIn
from app.modules.bundling.service import BundlingOrderService
from app.modules.cutting.models import CuttingOrderSizeLine, CuttingOutput
from tests.factories.bundling import attach_output, ctx, payload, read_reserved
from tests.factories.user import OPERATOR_ID

ZERO = Decimal("0")
SIZE = "S"  # factory.build_world 的第一条尺码明细行（hands=1, qty_per_hand=60）
COLOR = "WHT"


# ------------------------------------------------------------------ 助手
async def _status(db_session, order_id: UUID) -> DocumentStatus:
    row = await db_session.scalar(
        select(BundlingOrder.status)
        .where(BundlingOrder.id == order_id)
        .execution_options(populate_existing=True)
    )
    return DocumentStatus(row)


async def _logs(db_session, order_id: UUID) -> list[DocumentLog]:
    return list(
        (
            await db_session.scalars(
                select(DocumentLog).where(
                    DocumentLog.doc_type == "BundlingOrder", DocumentLog.doc_id == order_id
                )
            )
        ).all()
    )


async def _make_output(db_session, world, qty: Decimal) -> None:
    await attach_output(
        db_session,
        world["style"].id,
        world["style"].style_no,
        world["workshop"].id,
        size_code=SIZE,
        output_qty=qty,
    )


def _line(world, *, size_code: str = SIZE, **extra) -> dict:
    size_line = next(sl for sl in world["cutting_size_lines"] if sl.size_code == size_code)
    return {"cutting_size_line_id": size_line.id, "size_code": size_code, **extra}


async def _create(db_session, world, **overrides) -> BundlingOrder:
    return await BundlingOrderService(db_session).create(payload(world, **overrides), OPERATOR_ID)


async def _submitted(db_session, world) -> tuple[BundlingOrder, BundlingOrderService]:
    await _make_output(db_session, world, Decimal("60"))
    order = await _create(db_session, world)
    service = BundlingOrderService(db_session)
    await service.submit(order.id, OPERATOR_ID, ctx())
    return order, service


# ------------------------------------------------------------------ TC-ST-01
async def test_submit_reserves_and_snapshots(db_session, bundling_world) -> None:
    """TC-ST-01：``submit`` 正常 —— 状态、预占、快照、日志四件事一起验。"""
    world = bundling_world
    style_no = world["style"].style_no
    await _make_output(db_session, world, Decimal("60"))
    order = await _create(db_session, world)
    # ⚠️ 先取版本快照：submit 返回的对象与 order 是**同一个** identity-mapped 对象
    version_before = order.version

    fresh = await BundlingOrderService(db_session).submit(order.id, OPERATOR_ID, ctx())

    assert fresh.status is DocumentStatus.SUBMITTED
    assert fresh.version == version_before + 1, "状态迁移必须 +1 版本（08 R3）"
    assert await read_reserved(db_session, style_no, size_code=SIZE) == Decimal("60"), (
        "必须预占裁剪余量"
    )
    assert fresh.lines[0].available_qty_before == Decimal("60.000"), "要快照可用量"
    log = next(row for row in await _logs(db_session, order.id) if row.action == "SUBMIT")
    assert (log.from_status, log.to_status, log.operator_id) == ("DRAFT", "SUBMITTED", OPERATOR_ID)


# ------------------------------------------------------------------ TC-ST-02
async def test_submit_over_available_is_rejected_without_side_effects(
    db_session, bundling_world
) -> None:
    """TC-ST-02：超可用量 → ``30002``，且**零副作用**（裁剪 2 手 = 120 件，结转只给 60）。"""
    world = bundling_world
    style_no = world["style"].style_no
    size_line = next(sl for sl in world["cutting_size_lines"] if sl.size_code == SIZE)
    size_line.hands = 2
    size_line.output_qty = 120
    await db_session.flush()
    await _make_output(db_session, world, Decimal("60"))
    order = await _create(db_session, world, lines=[_line(world, hands=2)])

    with pytest.raises(BusinessError) as caught:
        await BundlingOrderService(db_session).submit(order.id, OPERATOR_ID, ctx())
    assert caught.value.code is ErrorCode.CUTTING_QTY_CONFLICT

    assert await read_reserved(db_session, style_no, size_code=SIZE) == ZERO, "失败不许留半个预占"
    assert await _status(db_session, order.id) is DocumentStatus.DRAFT
    assert [row for row in await _logs(db_session, order.id) if row.action == "SUBMIT"] == []
    bundles = await db_session.scalar(
        select(func.count()).select_from(Bundle).where(Bundle.doc_id == order.id)
    )
    assert bundles == 0, "submit 不生成码"


# ------------------------------------------------------------------ TC-ST-03 / 04 / 05
async def test_reject_requires_reason_then_releases(db_session, bundling_world) -> None:
    """TC-ST-03 + 驳回主流程：缺原因（全空白）→ ``10002`` 且**预占不动**；补上原因才释放。

    两条共用同一次提交：拆开要多付一次建单，而「缺原因时预占有没有被动过」正是重点。
    """
    world = bundling_world
    style_no = world["style"].style_no
    order, service = await _submitted(db_session, world)

    with pytest.raises(BusinessError) as missing:
        await service.reject(order.id, RejectIn(reason="   "), OPERATOR_ID, ctx())
    assert missing.value.code is ErrorCode.MISSING_BUSINESS_PARAM
    assert await _status(db_session, order.id) is DocumentStatus.SUBMITTED
    assert await read_reserved(db_session, style_no, size_code=SIZE) == Decimal("60"), (
        "不许顺手释放预占"
    )

    fresh = await service.reject(order.id, RejectIn(reason="XL 手数录错"), OPERATOR_ID, ctx())
    assert fresh.status is DocumentStatus.REJECTED
    assert fresh.rejected_reason == "XL 手数录错"
    assert await read_reserved(db_session, style_no, size_code=SIZE) == ZERO, (
        "驳回必须释放预占（与 submit 成对）"
    )
    log = next(row for row in await _logs(db_session, order.id) if row.action == "REJECT")
    assert (log.from_status, log.to_status, log.reason) == ("SUBMITTED", "REJECTED", "XL 手数录错")


async def test_withdraw_rejects_non_creator_then_releases(db_session, bundling_world) -> None:
    """TC-ST-04 + 撤回主流程：非制单人 → ``12001`` 且预占不动；制单人本人撤回才释放。"""
    world = bundling_world
    style_no = world["style"].style_no
    order, service = await _submitted(db_session, world)
    # 一个**不是制单人**的身份（范围仍全厂，好把「越权」与「不是本人」分开）
    other = replace(ctx(), user_id=uuid4(), name="别人", employee_no="A999")

    with pytest.raises(BusinessError) as denied:
        await service.withdraw(order.id, other.user_id, other)
    assert denied.value.code is ErrorCode.PERMISSION_DENIED
    assert await _status(db_session, order.id) is DocumentStatus.SUBMITTED
    assert await read_reserved(db_session, style_no, size_code=SIZE) == Decimal("60")

    fresh = await service.withdraw(order.id, OPERATOR_ID, ctx())
    assert fresh.status is DocumentStatus.DRAFT
    assert await read_reserved(db_session, style_no, size_code=SIZE) == ZERO


async def test_cancel_is_terminal_and_logged(db_session, bundling_world) -> None:
    """TC-ST-05：``cancel`` 缺原因 → ``10002``；补上原因 → ``CANCELLED`` + 日志 + 终态。

    ⚠️ 顺带断言**无库存副作用**（它只能从 DRAFT/REJECTED 进，手里没有预占可放）。
    """
    world = bundling_world
    style_no = world["style"].style_no
    await _make_output(db_session, world, Decimal("60"))
    order = await _create(db_session, world)
    service = BundlingOrderService(db_session)

    with pytest.raises(BusinessError) as missing:
        await service.cancel(order.id, CancelIn(cancelled_reason=" "), OPERATOR_ID, ctx())
    assert missing.value.code is ErrorCode.MISSING_BUSINESS_PARAM

    fresh = await service.cancel(
        order.id, CancelIn(cancelled_reason="客户取消了"), OPERATOR_ID, ctx()
    )
    assert fresh.status is DocumentStatus.CANCELLED
    assert fresh.cancelled_reason == "客户取消了"
    assert await read_reserved(db_session, style_no, size_code=SIZE) == ZERO, (
        "作废不做任何预占/释放"
    )
    log = next(row for row in await _logs(db_session, order.id) if row.action == "CANCEL")
    assert (log.from_status, log.to_status, log.reason) == ("DRAFT", "CANCELLED", "客户取消了")

    with pytest.raises(BusinessError) as caught:  # 终态不可再迁移（08 R6）
        await service.cancel(order.id, CancelIn(cancelled_reason="再来一次"), OPERATOR_ID, ctx())
    assert caught.value.code is ErrorCode.CUTTING_STATUS_NOT_ALLOWED


# ------------------------------------------------------------------ 非法迁移 + 提交预检
async def test_illegal_transitions(db_session, bundling_world) -> None:
    """非法迁移一律 ``30001``：重复提交 / 已审核再提交 / 已审核再驳回 / 已审核再撤回。"""
    order, service = await _submitted(db_session, bundling_world)
    with pytest.raises(BusinessError) as repeat:
        await service.submit(order.id, OPERATOR_ID, ctx())
    assert repeat.value.code is ErrorCode.CUTTING_STATUS_NOT_ALLOWED

    order.status = DocumentStatus.APPROVED  # 已审核：禁止再提交（03 §4.1）
    await db_session.flush()
    for call in (
        service.submit(order.id, OPERATOR_ID, ctx()),
        service.reject(order.id, RejectIn(reason="x"), OPERATOR_ID, ctx()),
        service.withdraw(order.id, OPERATOR_ID, ctx()),
    ):
        with pytest.raises(BusinessError) as caught:
            await call
        assert caught.value.code is ErrorCode.CUTTING_STATUS_NOT_ALLOWED


async def test_submit_prechecks_hands_and_conflicts(db_session, bundling_world) -> None:
    """§4 ⑤ 少打 / 多打 → ``31004``（``details`` 回传两侧手数，B21）；§4 ⑥ 手序号冲突 → ``31005``（B22）。"""
    world = bundling_world
    style_no = world["style"].style_no
    service = BundlingOrderService(db_session)
    await _make_output(db_session, world, Decimal("600"))

    mismatch = await _create(db_session, world, lines=[_line(world, hands=2)])
    with pytest.raises(BusinessError) as caught:
        await service.submit(mismatch.id, OPERATOR_ID, ctx())
    assert caught.value.code is ErrorCode.BUNDLE_HANDS_MISMATCH
    assert (caught.value.details["planned_hands"], caught.value.details["cutting_hands"]) == (2, 1)
    assert await read_reserved(db_session, style_no, size_code=SIZE) == ZERO

    order = await _create(db_session, world)
    size_line = next(sl for sl in world["cutting_size_lines"] if sl.size_code == SIZE)
    bundle_no = f"{order.doc_no}-{SIZE}01-0001"
    db_session.add(
        Bundle(
            doc_id=order.id,
            line_id=order.lines[0].id,
            bundle_no=bundle_no,
            hands=1,
            style_no=order.style_no,
            color_code=COLOR,
            size_code=SIZE,
            operation_no="OP01",
            cutting_size_line_id=size_line.id,
            bundle_qty=Decimal("60"),
            qr_content=bundle_no,
            created_by=OPERATOR_ID,
            updated_by=OPERATOR_ID,
        )
    )
    await db_session.flush()
    with pytest.raises(BusinessError) as caught:
        await service.submit(order.id, OPERATOR_ID, ctx())
    assert caught.value.code is ErrorCode.BUNDLE_HAND_DUPLICATED
    assert caught.value.details["existing_bundle_no"] == bundle_no
    assert await read_reserved(db_session, style_no, size_code=SIZE) == ZERO


# ------------------------------------------------------------------ TC-ST-07
async def test_every_action_writes_a_log(db_session, bundling_world) -> None:
    """TC-ST-07：四个动作各留一行日志；带原因的两个把 ``reason`` 落库（08 R2）。"""
    world = bundling_world
    await _make_output(db_session, world, Decimal("600"))
    service = BundlingOrderService(db_session)

    rejected = await _create(db_session, world)
    await service.submit(rejected.id, OPERATOR_ID, ctx())
    await service.reject(rejected.id, RejectIn(reason="手数要改"), OPERATOR_ID, ctx())
    withdrawn = await _create(db_session, world)
    await service.submit(withdrawn.id, OPERATOR_ID, ctx())
    await service.withdraw(withdrawn.id, OPERATOR_ID, ctx())
    cancelled = await _create(db_session, world)
    await service.cancel(cancelled.id, CancelIn(cancelled_reason="款号错了"), OPERATOR_ID, ctx())

    expected = {
        rejected.id: ({"CREATE", "SUBMIT", "REJECT"}, "手数要改"),
        withdrawn.id: ({"CREATE", "SUBMIT", "WITHDRAW"}, None),
        cancelled.id: ({"CREATE", "CANCEL"}, "款号错了"),
    }
    for order_id, (actions, reason) in expected.items():
        rows = await _logs(db_session, order_id)
        assert {row.action for row in rows} == actions, "每个动作都要留痕"
        assert all(row.operator_id == OPERATOR_ID for row in rows)
        if reason:
            assert reason in {row.reason for row in rows}


# ------------------------------------------------------------------ TC-ST-06
async def test_concurrent_submit_one_wins(app_database_url, bundling_world_persisted) -> None:
    """TC-ST-06：**20 并发** submit 同一单 → 1 成功 19 败，只预占一次、只留一条日志。

    ⚠️ 失败码断言是集合 ``{10003, 30001, 30002}``：``POST /submissions`` 按 03 §6 是
    **无 body** 的，后到的请求看到「已经 SUBMITTED」→ ``30001``（03 §9 非 DRAFT 再操作）。
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
            # ``payload()`` 只取这几个属性，用轻量替身把持久化夹具的**纯 id** 接进去
            ref = type("Ref", (), {"id": world["workshop"], "style_no": world["style_no"]})()
            order = await BundlingOrderService(session).create(
                payload(
                    {
                        "workshop": ref,
                        "style": ref,
                        "cutting_order": type("C", (), {"id": world["cutting_order"]})(),
                        "cutting_size_lines": [size_line],
                    },
                    lines=[_line({"cutting_size_lines": [size_line]}, hands=size_line.hands)],
                ),
                OPERATOR_ID,
            )
            await session.commit()
            order_id = order.id

        async def attempt() -> ErrorCode | None:
            async with factory() as session:
                try:
                    await BundlingOrderService(session).submit(order_id, OPERATOR_ID, ctx())
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
            ErrorCode.CUTTING_QTY_CONFLICT,
        }, f"失败码异常：{failed}"

        async with factory() as session:
            reserved = select(CuttingOutput.reserved_qty).where(
                CuttingOutput.style_no == world["style_no"]
            )
            got = Decimal((await session.execute(reserved)).scalar_one())
            assert got == Decimal("60"), "20 并发只该预占一次（INV-6）"
            submits = (
                select(func.count())
                .select_from(DocumentLog)
                .where(DocumentLog.doc_id == order_id, DocumentLog.action == "SUBMIT")
            )
            assert (await session.execute(submits)).scalar_one() == 1, "只该有一条 SUBMIT 日志"
            # 软删真提交的行（INV-7：没有物理删除，erp_app 也没有 DELETE 权限）
            now = datetime.now(tz=BUSINESS_TZ)
            for model, where in (
                (BundlingOrder, BundlingOrder.id == order_id),
                (CuttingOutput, CuttingOutput.style_no == world["style_no"]),
            ):
                await session.execute(
                    update(model)
                    .where(where)
                    .values(
                        deleted_at=now,
                        version=model.version + 1,  # type: ignore[attr-defined]
                    )
                )
            await session.commit()
    finally:
        await engine.dispose()
