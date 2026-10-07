"""打菲单**反审核**的测试（T-BUND-005b）。

TC-AP-07 副作用对称（码全 ``VOIDED``、**行保留**、结转完全还原）+ 原因必填 + 中间态不可跳级。

⚠️ 反审核单独成文件而不是并进 ``test_bundling_approve.py``：单文件 400 行硬线（ADR-0030），
而反审核要断言的**每一半**（码作废 / 结转减回 / 预占重建 / 日志）都是审核的镜像，
塞在一起谁也读不完。
⚠️ 「行保留」是 B12 的硬要求且**必须验**：作废的码仍要被扫码枪查到并回报 ``31002``，
软删掉就只剩「查无此码（31001）」，用户无法区分「码打错了」与「这手被作废了」。
"""

from decimal import Decimal

import pytest

from app.common.enums import DocumentStatus
from app.core.errors import BusinessError, ErrorCode
from app.modules.bundling.service import BundlingOrderService
from tests.factories.bundling import ctx, read_reserved
from tests.factories.bundling_approve import (
    REVIEWER_ID,
    approve_order,
    order_codes,
    order_logs,
    prepare_order,
    read_bundled,
    reviewer_ctx,
)
from tests.factories.user import OPERATOR_ID

ZERO = Decimal("0")


async def test_reverse_voids_codes_and_restores_carry_over(db_session, bundling_world) -> None:
    """TC-AP-07：反审核**副作用对称** —— 码全 ``VOIDED``、**行保留**、结转完全还原。

    ⚠️ 「行保留」是 B12 的硬要求且**必须验**：作废的码仍要被扫码枪查到并回报
    ``31002``，软删掉就只剩「查无此码（31001）」，用户无法区分「码打错了」与「这手被作废了」。

    ⚠️ 结转还原验的是 ``bundled_qty`` **减回**、``reserved_qty`` **重新预占** —— 这两半
    必须同时发生；只减 ``bundled_qty`` 而漏了重新预占，单据回不到可重打的状态，而界面上
    少掉的是「本单占着多少余量」这个谁都不会主动去对的数。
    """
    world = bundling_world
    style_no = world["style"].style_no
    order = await prepare_order(db_session, world, plan=(("XL", 2),))
    await approve_order(db_session, order)

    assert await read_bundled(db_session, style_no, size_code="XL") == Decimal("120.000")
    assert await read_reserved(db_session, style_no, size_code="XL") == ZERO

    fresh = await BundlingOrderService(db_session).reverse(
        order.id, "打错了，重新打", REVIEWER_ID, reviewer_ctx()
    )

    assert fresh.status is DocumentStatus.SUBMITTED, "反审核回到 SUBMITTED（08 §1.1）"
    codes = await order_codes(db_session, order.id)
    assert len(codes) == 2, "码行必须保留（B12）"
    assert all(c.status == "VOIDED" for c in codes)
    assert await read_bundled(db_session, style_no, size_code="XL") == ZERO, "结转必须减回"
    assert await read_reserved(db_session, style_no, size_code="XL") == Decimal("120.000"), (
        "预占必须重新建立，否则这张单回不到可重打的状态"
    )
    log = next(row for row in await order_logs(db_session, order.id) if row.action == "REVERSE")
    assert (log.from_status, log.to_status, log.reason) == (
        "APPROVED",
        "SUBMITTED",
        "打错了，重新打",
    )
    assert log.changed_fields is not None and log.changed_fields["voided_codes"] == 2


async def test_reverse_requires_reason_and_is_not_idempotent(db_session, bundling_world) -> None:
    """反审核必填原因（空白也算没填 → ``10002``），且第二次反审核被状态机拒绝。

    ⚠️ 两个断言放一起是因为它们共用同一次建单 + 审核：拆开要多付一次 2000 行的世界铺场，
    而「缺原因时预占有没有被动过」正是重点。
    """
    world = bundling_world
    order = await prepare_order(db_session, world, plan=(("XL", 1),))
    await approve_order(db_session, order)
    service = BundlingOrderService(db_session)

    with pytest.raises(BusinessError) as missing:
        await service.reverse(order.id, "   ", REVIEWER_ID, reviewer_ctx())
    assert missing.value.code is ErrorCode.MISSING_BUSINESS_PARAM
    assert all(c.status == "ACTIVE" for c in await order_codes(db_session, order.id)), (
        "缺原因不许作废半个码"
    )

    await service.reverse(order.id, "重来", REVIEWER_ID, reviewer_ctx())
    with pytest.raises(BusinessError) as again:  # 幂等保护：终态不可再迁移
        await service.reverse(order.id, "再来一次", REVIEWER_ID, reviewer_ctx())
    assert again.value.code is ErrorCode.CUTTING_STATUS_NOT_ALLOWED


async def test_reverse_rejects_states_without_approval(db_session, bundling_world) -> None:
    """未审核就反审核 → ``30001``；驳回后也不能反审核（中间态不可跳级）。"""
    world = bundling_world
    order = await prepare_order(db_session, world, plan=(("XL", 1),))
    service = BundlingOrderService(db_session)

    with pytest.raises(BusinessError) as caught:
        await service.reverse(order.id, "没必要", REVIEWER_ID, reviewer_ctx())
    assert caught.value.code is ErrorCode.CUTTING_STATUS_NOT_ALLOWED

    from app.modules.bundling.schemas import RejectIn

    await service.reject(order.id, RejectIn(reason="手数再核一下"), OPERATOR_ID, ctx())
    with pytest.raises(BusinessError) as after_reject:
        await service.reverse(order.id, "没必要", REVIEWER_ID, reviewer_ctx())
    assert after_reject.value.code is ErrorCode.CUTTING_STATUS_NOT_ALLOWED
