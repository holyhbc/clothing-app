"""打菲单草稿态 service 的测试（T-BUND-003）。

============================  ===============================================
TC-B03-01                    ``doc_no`` 格式 ``BD-YYYYMMDD-6 位``
TC-B03-02                    同日并发取号不重复
TC-B03-03                    表头汇总：头 = Σ明细（hands_total / output_qty / balance_qty / planned_qty）
TC-B03-04                    表头汇总**不信任前端**（入参里根本没有那四列）
TC-B03-05                    来源裁剪单非 APPROVED → 拒绝
TC-B03-06                    明细行 ``cutting_size_line_id`` 不存在 / 尺码不匹配 → 拒绝
TC-B03-07                    ``PUT /lines`` 全量替换：软删旧行 + 插新行 + 重算汇总
============================  ===============================================

⚠️ **TC-B03-03 与 TC-B03-04 是这一卡最要紧的两条**：守的是「service 重算所有汇总，
不信任前端」。若哪天有人把汇总列加进入参模型，TC-B03-04 立刻红。
"""

import os
from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.common.enums import DataScope, DocumentStatus
from app.core.errors import BusinessError, ErrorCode
from app.core.numbering import format_doc_no
from app.core.permissions import AuthContext
from app.modules.bundling.models import BundlingOrder, BundlingOrderLine
from app.modules.bundling.schemas import BundlingOrderCreateIn
from app.modules.bundling.service import BundlingOrderService
from app.modules.bundling.service.numbering import next_doc_no
from tests.factories.bundling import DOC_DATE, ctx, payload
from tests.factories.user import OPERATOR_ID

# ------------------------------------------------------------------ 夹具


async def _create(db_session, bundling_world, **overrides) -> BundlingOrder:
    service = BundlingOrderService(db_session)
    return await service.create(payload(bundling_world, **overrides), OPERATOR_ID)


async def _reload(db_session, order_id: UUID) -> BundlingOrder:
    """重新从库里读一遍（表头 + 明细），不复用内存对象。"""
    order = await db_session.get(BundlingOrder, order_id)
    if order is None:
        return None
    await db_session.refresh(order)
    lines = list(
        (
            await db_session.execute(
                select(BundlingOrderLine)
                .where(BundlingOrderLine.doc_id == order_id)
                .where(BundlingOrderLine.deleted_at.is_(None))
                .order_by(BundlingOrderLine.line_no)
            )
        )
        .scalars()
        .all()
    )
    order.lines = lines
    return order


# ------------------------------------------------------------------ TC-B03-01 / 02


def test_doc_no_format() -> None:
    """TC-B03-01：``BD-YYYYMMDD-6 位``（09 §2.1）。"""
    assert format_doc_no("BD", date(2026, 10, 18), 1) == "BD-20261018-000001"
    assert format_doc_no("BD", date(2026, 10, 18), 150) == "BD-20261018-000150"
    assert format_doc_no("BD", date(2026, 1, 5), 999999) == "BD-20260105-999999"


async def test_concurrent_doc_no_is_unique(db_session, bundling_world) -> None:
    """TC-B03-02：**同日并发取号不重复**（C1「生成即占用、永不复用」）。

    必须用**独立引擎真提交**：同一个连接上的语句会被 PostgreSQL 串行化。
    """
    import asyncio

    engine = create_async_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:

        async def take_one() -> str:
            async with factory() as session, session.begin():
                return await next_doc_no(session, doc_date=DOC_DATE)

        numbers = await asyncio.gather(*(take_one() for _ in range(20)))
        assert len(set(numbers)) == 20, f"取号重复了：{sorted(numbers)}"
        seqs = sorted(int(no[-6:]) for no in numbers)
        assert seqs == list(range(seqs[0], seqs[0] + 20)), f"取到的号不连续：{numbers}"
        assert all(no.startswith("BD-20261018-") for no in numbers)
    finally:
        await engine.dispose()


# ------------------------------------------------------------------ TC-B03-03 / 04


async def test_header_totals_are_recalculated(db_session, bundling_world) -> None:
    """TC-B03-03：表头汇总 = Σ明细（hands_total / output_qty / balance_qty / planned_qty）。

    两行明细：
      - 行1: XL, hands=2, qty_per_hand=60 → planned_qty=120
      - 行2: L,  hands=1, qty_per_hand=60 → planned_qty=60
    期望：
      hands_total = 3
      output_qty = 180
      balance_qty = 0
      planned_qty (表头) = 180
    """
    size_lines = bundling_world["cutting_size_lines"]
    # 取 XL 和 L 两行
    xl_line = next(sl for sl in size_lines if sl.size_code == "XL")
    l_line = next(sl for sl in size_lines if sl.size_code == "L")

    order = await BundlingOrderService(db_session).create(
        payload(
            bundling_world,
            lines=[
                {
                    "line_no": 1,
                    "size_code": "XL",
                    "color_code": "WHT",
                    "hands": 2,
                    "cutting_size_line_id": xl_line.id,
                },
                {
                    "line_no": 2,
                    "size_code": "L",
                    "color_code": "WHT",
                    "hands": 1,
                    "cutting_size_line_id": l_line.id,
                },
            ],
        ),
        OPERATOR_ID,
    )

    reloaded = await _reload(db_session, order.id)
    assert reloaded is not None
    assert reloaded.hands_total == 3
    assert reloaded.output_qty == Decimal("180.000")
    assert reloaded.balance_qty == Decimal("0.000")

    # 明细层也要对
    assert len(reloaded.lines) == 2
    by_size = {line.size_code: line for line in reloaded.lines}
    assert by_size["XL"].hands == 2
    assert by_size["XL"].planned_qty == Decimal("120.000")
    assert by_size["L"].hands == 1
    assert by_size["L"].planned_qty == Decimal("60.000")


def test_header_totals_not_in_create_input() -> None:
    """TC-B03-04：表头汇总四列**不接受前端传入**（入参模型里根本没有那四列）。"""
    fields = set(BundlingOrderCreateIn.model_fields)
    for forbidden in ("hands_total", "output_qty", "balance_qty", "planned_qty"):
        assert forbidden not in fields, (
            f"{forbidden} 不该出现在建单入参里 —— service 重算并覆盖，不信任前端。"
            f"而**能被忽略的入参是最坏的一种**：前端以为设的值生效了"
        )


# ------------------------------------------------------------------ TC-B03-05


async def test_rejected_when_source_cutting_not_approved(db_session, bundling_world) -> None:
    """TC-B03-05：来源裁剪单非 APPROVED → 拒绝（B8）。"""

    # 把裁剪单改成 SUBMITTED
    cutting = bundling_world["cutting_order"]
    cutting.status = DocumentStatus.SUBMITTED
    await db_session.flush()

    with pytest.raises(BusinessError) as caught:
        await _create(db_session, bundling_world)
    assert caught.value.code is ErrorCode.BASE_DATA_REFERENCED
    assert "必须是 APPROVED" in str(caught.value)


# ------------------------------------------------------------------ TC-B03-06


async def test_rejected_when_cutting_size_line_missing_or_mismatch(
    db_session, bundling_world
) -> None:
    """TC-B03-06：明细行 cutting_size_line_id 不存在 / 尺码不匹配 → 拒绝。"""
    # 情况 A：不存在的 cutting_size_line_id
    fake_id = UUID("00000000-0000-0000-0000-000000000001")
    with pytest.raises(BusinessError) as caught:
        await _create(
            db_session,
            bundling_world,
            lines=[{"cutting_size_line_id": fake_id, "hands": 1, "size_code": "L"}],
        )
    assert caught.value.code is ErrorCode.BASE_DATA_NOT_FOUND

    # 情况 B：尺码不匹配（用 L 的 cutting_size_line_id 但传 size_code=XL）
    size_lines = bundling_world["cutting_size_lines"]
    l_line = next(sl for sl in size_lines if sl.size_code == "L")
    with pytest.raises(BusinessError) as caught:
        await _create(
            db_session,
            bundling_world,
            lines=[{"size_code": "XL", "cutting_size_line_id": l_line.id, "hands": 1}],
        )
    assert caught.value.code is ErrorCode.PARAM_INVALID
    assert "不匹配" in str(caught.value)


# ------------------------------------------------------------------ TC-B03-07


async def test_put_lines_full_replace_recalc(db_session, bundling_world) -> None:
    """TC-B03-07：``PUT /lines`` 全量替换 = 软删旧行 + 插新行 + 重算汇总。

    步骤：
    1. 建单：2 行（XL 2手 + L 1手）→ hands_total=3, output_qty=180
    2. PUT /lines：只留 1 行（M 3手）→ hands_total=3, output_qty=180（3×60）
    3. 旧行被软删（deleted_at 非空），不物理删除
    4. 表头汇总重算正确
    """
    size_lines = bundling_world["cutting_size_lines"]
    xl_line = next(sl for sl in size_lines if sl.size_code == "XL")
    l_line = next(sl for sl in size_lines if sl.size_code == "L")
    m_line = next(sl for sl in size_lines if sl.size_code == "M")

    # 1) 建单
    order = await _create(
        db_session,
        bundling_world,
        lines=[
            {
                "line_no": 1,
                "size_code": "XL",
                "color_code": "WHT",
                "hands": 2,
                "cutting_size_line_id": xl_line.id,
            },
            {
                "line_no": 2,
                "size_code": "L",
                "color_code": "WHT",
                "hands": 1,
                "cutting_size_line_id": l_line.id,
            },
        ],
    )
    assert order.hands_total == 3
    assert order.output_qty == Decimal("180.000")

    # 2) 全量替换为 1 行 M 3手
    service = BundlingOrderService(db_session)
    from app.modules.bundling.schemas import LineIn, PutLinesIn

    new_order = await service.put_lines(
        order.id,
        PutLinesIn(
            version=order.version,
            items=[
                LineIn(
                    line_no=3,  # Use different line_no since old line_no=1,2 still exist (soft-deleted)
                    color_code="WHT",
                    size_code="M",
                    operation_no="OP01",
                    cutting_size_line_id=m_line.id,
                    hands=3,
                )
            ],
        ),
        OPERATOR_ID,
        ctx(),
    )

    # 3) 重读验证
    reloaded = await _reload(db_session, new_order.id)
    assert reloaded is not None
    assert reloaded.hands_total == 3
    assert reloaded.output_qty == Decimal("180.000")
    assert reloaded.balance_qty == Decimal("0.000")
    assert len(reloaded.lines) == 1
    assert reloaded.lines[0].size_code == "M"
    assert reloaded.lines[0].hands == 3
    assert reloaded.lines[0].planned_qty == Decimal("180.000")

    # 4) 旧行被软删（在库里 deleted_at 非空）
    old_lines = list(
        (
            await db_session.execute(
                select(BundlingOrderLine)
                .where(BundlingOrderLine.doc_id == order.id)
                .order_by(BundlingOrderLine.line_no)
            )
        )
        .scalars()
        .all()
    )
    assert len(old_lines) == 3  # 旧两行 + 新一行都在
    assert sum(1 for line in old_lines if line.deleted_at is not None) == 2, "旧两行应被软删"


# ------------------------------------------------------------------ 额外：乐观锁冲突


async def test_optimistic_lock_conflict_on_patch(db_session, bundling_world) -> None:
    """乐观锁冲突：version 不符 → 10003。"""
    order = await _create(db_session, bundling_world)

    service = BundlingOrderService(db_session)
    from app.modules.bundling.schemas import BundlingOrderPatchIn

    # 使用一个不存在的版本号（比当前版本大）
    with pytest.raises(BusinessError) as caught:
        await service.patch(
            order.id,
            BundlingOrderPatchIn(version=order.version + 999, remark="old"),
            OPERATOR_ID,
            ctx(),
        )
    assert caught.value.code is ErrorCode.OPTIMISTIC_LOCK_CONFLICT


async def test_optimistic_lock_conflict_on_put_lines(db_session, bundling_world) -> None:
    """乐观锁冲突：PUT /lines version 不符 → 10003。"""
    order = await _create(db_session, bundling_world)
    size_lines = bundling_world["cutting_size_lines"]
    m_line = next(sl for sl in size_lines if sl.size_code == "M")

    service = BundlingOrderService(db_session)
    from app.modules.bundling.schemas import LineIn, PutLinesIn

    with pytest.raises(BusinessError) as caught:
        await service.put_lines(
            order.id,
            PutLinesIn(
                version=order.version + 999,
                items=[
                    LineIn(
                        line_no=1,
                        color_code="WHT",
                        size_code="M",
                        operation_no="OP01",
                        cutting_size_line_id=m_line.id,
                        hands=1,
                    )
                ],
            ),
            OPERATOR_ID,
            ctx(),
        )
    assert caught.value.code is ErrorCode.OPTIMISTIC_LOCK_CONFLICT


# ------------------------------------------------------------------ 额外：数据范围


async def test_detail_respects_data_scope(db_session, bundling_world) -> None:
    """详情：越权按 ID 直查 → 12002（docs/07 §3.2 铁律 2）。"""
    order = await _create(db_session, bundling_world)
    service = BundlingOrderService(db_session)
    outsider = AuthContext(
        user_id=OPERATOR_ID,
        name="别的车间主管",
        employee_no="A002",
        workshop_id=None,
        group_no=None,
        permissions=frozenset(),
        data_scope=DataScope.WORKSHOP,
        allowed_workshop_ids=frozenset(),  # 看不到任何车间
    )
    with pytest.raises(BusinessError) as caught:
        await service.get(order.id, outsider)
    assert caught.value.code is ErrorCode.DATA_SCOPE_DENIED


# ------------------------------------------------------------------ 额外：状态限制


async def test_patch_rejected_when_not_draft_or_rejected(db_session, bundling_world) -> None:
    """非 DRAFT/REJECTED 状态下 patch → 30001。"""
    order = await _create(db_session, bundling_world)
    # 手动改状态（模拟已提交）
    order.status = DocumentStatus.SUBMITTED
    await db_session.flush()

    service = BundlingOrderService(db_session)
    from app.modules.bundling.schemas import BundlingOrderPatchIn

    with pytest.raises(BusinessError) as caught:
        await service.patch(
            order.id, BundlingOrderPatchIn(version=order.version, remark="try"), OPERATOR_ID, ctx()
        )
    assert caught.value.code is ErrorCode.CUTTING_STATUS_NOT_ALLOWED
