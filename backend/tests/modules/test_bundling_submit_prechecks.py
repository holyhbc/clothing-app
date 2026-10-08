"""打菲单**提交前预检**的测试（T-BUND-005a 拆出）。

从 ``test_bundling_state.py`` 拆出来的第二组用例：非法迁移、**防超打 / 少打允许**、
单尺码 99 手上限、手序号冲突。

⚠️ 拆分的理由不是「文件太长不好看」，而是 ADR-0030 的 400 行是硬线 ——
``test_bundling_state.py`` 原本 395 行，2026-10-06 落定 A+A 口径时加了「少打允许 +
99 手上限」用例涨到 429 行。凑合的代价是：**再往后加一条用例就无路可走**，
于是下一个人只能写一个 500 行的文件，或者把新断言塞进不相干的用例里。

这四条用例各自独立、都只依赖 ``db_session`` + ``bundling_world``，拆出去不影响任何一方。
"""

from decimal import Decimal

import pytest

from app.common.enums import DocumentStatus
from app.core.errors import BusinessError, ErrorCode
from app.modules.bundling.models import Bundle
from app.modules.bundling.schemas import RejectIn
from app.modules.bundling.service import BundlingOrderService
from app.modules.bundling.service.state_guard import MAX_HANDS_PER_SIZE_LINE
from tests.factories.bundling import ctx, read_reserved
from tests.factories.bundling_state import (
    COLOR,
    SIZE,
    ZERO,
)
from tests.factories.bundling_state import (
    create as _create,
)
from tests.factories.bundling_state import (
    line as _line,
)
from tests.factories.bundling_state import (
    make_output as _make_output,
)
from tests.factories.bundling_state import (
    submitted as _submitted,
)
from tests.factories.user import OPERATOR_ID


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
    """§4 ⑤ **超打** → ``31004``（``details`` 回传两侧手数，2026-10-06 口径：防超打，少打允许）；
    §4 ⑥ 手序号冲突 → ``31005``（B22）。"""
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


async def test_submit_allows_under_production_and_caps_hands_at_99(
    db_session, bundling_world
) -> None:
    """2026-10-06 业务确认的两条口径：

    ① **少打允许**（防超打而非强制相等）——打菲行的 ``hands`` 是主管另填的，
       少打几手是常态、分批打（ADR-0017 一对多）也天然要求「≤」。
    ② **单尺码手数上限 99** —— ``bundle_no`` 的手序号只有 2 位且与 1~3 位尺码码之间
       没有分隔符，超过就会生成「能过 DB CHECK 却解析成别的尺码第 0 手」的静默损坏码。
    """
    world = bundling_world
    service = BundlingOrderService(db_session)
    await _make_output(db_session, world, Decimal("600"))

    # ① 少打（裁剪 1 手、打 0 手不合规因为 hands>0；这里用「裁剪多手、打少手」）
    #    world 的尺码明细行 hands=1，所以「少打」用一行 hands 缺省（=1）即打满；
    #    真正验证「≤ 允许」的是下一段：把裁剪侧 hands 抬到 3、打 1 → 少打但必须放行。
    cutting_line = next(sl for sl in world["cutting_size_lines"] if sl.size_code == SIZE)
    cutting_line.hands = 3
    await db_session.flush()
    under = await _create(db_session, world, lines=[_line(world, hands=1)])
    await service.submit(under.id, OPERATOR_ID, ctx())  # 少打（1 < 3）不再被拦

    # ② 超过 99 手 → 10001（手序号只有 2 位）
    too_many = await _create(db_session, world, lines=[_line(world, hands=100)])
    with pytest.raises(BusinessError) as caught:
        await service.submit(too_many.id, OPERATOR_ID, ctx())
    assert caught.value.code is ErrorCode.PARAM_INVALID
    assert caught.value.details["max"] == MAX_HANDS_PER_SIZE_LINE
    assert caught.value.details["hands"] == 100
