"""打菲 **03 §11 TC-20~TC-23 的规则缺口**：整件口径 + 入参边界（T-BUND-008）。

============================  ==============================================
TC-20                        审核**落库层**的「余数不出码」：5 手 × 2 件 = 5 个码
TC-22                        裁剪侧有尾数时，那些件**一个码都不出**
TC-21 / TC-23                ``hands = 0`` / 负数 / 小数 → ``10001`` + **字段级定位**
============================  ==============================================

⚠️ **先盘点后补，不重写已有覆盖**（T-BUND-008 的第一原则）：1:2:2:1 → 6 码（TC-24 /
TC-AP-01）、不 floor（TC-AP-02）、``31004`` 超打（TC-AP-03）、手序号重复 ``31005``
（TC-AP-04）、``30002`` 超量（TC-ST-02）、``/split`` 零写库（TC-SP-09 / TC-BX-02）、
未计件手清单与索引（TC-30-01~07）、标签留痕（TC-LB-02/03）都已有用例，**一条都没动**
—— 重写等于把已验证的断言换成新的、更弱的断言。码侧那几条在
``test_bundling_rules_codes.py``。

⚠️ **TC-20 与 TC-03 的区别是「审核落库层」**：``test_bundling_split`` 的 TC-SP-02 已在
**纯函数**层验过「余数不出码」，但它没经过事务、``generate_series`` 批量 INSERT 与表头
回写；而 TC-20 真正要防的是「有人把 ``floor(output_qty / hands)`` 加回 ``approve``」
—— 那只在**这条链路上**露出来，纯函数层根本到不了。

⚠️ **TC-21 / TC-23 只断接口层**：``LineIn.hands`` 是 ``Field(ge=1)``，所以「零手」在
**请求边界**就被 pydantic 拦掉（422 / ``10001``，带 ``lines.0.hands`` 字段定位），
压根到不了 service 的第二道 ``hands <= 0``。断接口层才是真实用户会撞到的那一层。
"""

from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient

from app.core.errors import ErrorCode
from app.modules.bundling.service import BundlingOrderService
from tests.factories.bundling import attach_output, ctx, payload
from tests.factories.bundling_approve import (
    approve_order,
    order_codes,
    set_size_hands,
)
from tests.factories.bundling_http import WRITER_PERMISSIONS
from tests.factories.user import OPERATOR_ID

# ------------------------------------------------------------------ TC-20 / TC-22


async def test_approve_makes_five_codes_of_two_pieces_each(db_session, bundling_world) -> None:
    """TC-20：裁剪 ``hands=5, qty_per_hand=2``（``output_qty=10``）→ **恰好 5 个码各 2 件**。

    ⚠️ **小手数是这里的关键**：``5 × 2`` 整除，所以「用 ``floor(output/hands)`` 也能算对」；
    真正把这条钉住的是**审核事务里生成的是 5 行、每行 ``bundle_qty = 2``** —— 有人把
    ``floor()`` 加回 ``approve`` 时，纯函数层的 TC-SP-02 会仍然绿，只有这条会红。
    """
    world = bundling_world
    await set_size_hands(db_session, world, "XL", hands=5, qty_per_hand=2)
    await attach_output(
        db_session,
        world["style"].id,
        world["style"].style_no,
        world["workshop"].id,
        size_code="XL",
        output_qty=Decimal("10"),
    )
    order = await BundlingOrderService(db_session).create(
        payload(
            world,
            lines=[
                {
                    "cutting_size_line_id": next(
                        row.id for row in world["cutting_size_lines"] if row.size_code == "XL"
                    ),
                    "size_code": "XL",
                    "hands": 5,
                }
            ],
        ),
        OPERATOR_ID,
    )
    await BundlingOrderService(db_session).submit(order.id, OPERATOR_ID, ctx())
    fresh = await approve_order(db_session, order)

    codes = await order_codes(db_session, order.id)
    assert [row.hands for row in codes] == [1, 2, 3, 4, 5], "5 手 = 5 个码，手号连续"
    assert {row.bundle_qty for row in codes} == {Decimal("2")}, "每手件数直接取 qty_per_hand"
    assert sum(row.bundle_qty for row in codes) == Decimal("10"), "总件数 = 5 × 2"
    assert fresh.hands_total == 5
    assert fresh.output_qty == Decimal("10.000")
    assert fresh.balance_qty == Decimal("0.000"), "本单不出余数码（09 §4.2）"
    assert {row.bundle_no.rsplit("-", 2)[-2] for row in codes} == {
        f"XL{index:02d}" for index in range(1, 6)
    }


async def test_cutting_balance_never_becomes_a_code(db_session, bundling_world) -> None:
    """TC-22：裁剪侧有**尾数**（人工指定出数留下的差额）→ 那些件**一个码都不出**。

    ⚠️ 口径修正（2026-10-08 实测）：``cutting_order_size_lines.balance_qty`` 是
    ``integer``（ADR-0020 把 ``hands``/``qty_per_hand``/``output_qty`` 全改成整数），
    所以 ``03 §11`` TC-22 写的 ``balance_qty=0.6`` **存不进去**。本用例按 ADR-0020
    之后的真实形态验同一件事：``hands=5 × qty_per_hand=2`` 人工指定 ``output_qty=12`` →
    尾 2 件留在裁剪侧的 ``balance_qty``，打菲只出 5 个码各 2 件。
    """
    world = bundling_world
    size_line = await _set_manual_output(db_session, world, hands=5, qty_per_hand=2, output=12)
    order = await BundlingOrderService(db_session).create(
        payload(
            world,
            lines=[
                {"cutting_size_line_id": size_line.id, "size_code": "XL", "hands": 5},
            ],
        ),
        OPERATOR_ID,
    )
    await BundlingOrderService(db_session).submit(order.id, OPERATOR_ID, ctx())
    fresh = await approve_order(db_session, order)

    assert size_line.balance_qty == 2, "尾 2 件留在裁剪侧"
    codes = await order_codes(db_session, order.id)
    assert len(codes) == 5, f"尾数不出码，只有 5 个码，实际 {len(codes)}"
    assert all(row.bundle_qty == Decimal("2") for row in codes)
    assert sum(row.bundle_qty for row in codes) == Decimal("10")
    assert fresh.balance_qty == Decimal("0.000"), "打菲侧不承接裁剪余额"
    # ⚠️ 「全库检索无尾数码」的正向表述：**每一行都是整件**，DB CHECK 与业务口径一致
    assert all(row.bundle_qty == row.bundle_qty.to_integral_value() for row in codes)


async def _set_manual_output(db_session, world, *, hands: int, qty_per_hand: int, output: int):
    """把裁剪尺码明细改成「人工指定出数」：``output_qty`` 与 ``hands × qty_per_hand`` 不等。"""
    row = next(item for item in world["cutting_size_lines"] if item.size_code == "XL")
    await attach_output(
        db_session,
        world["style"].id,
        world["style"].style_no,
        world["workshop"].id,
        size_code="XL",
        output_qty=Decimal(output),
    )
    row.hands = hands
    row.qty_per_hand = qty_per_hand
    row.output_qty = output
    row.output_qty_manual = True
    row.balance_qty = output - hands * qty_per_hand
    await db_session.flush()
    return row


# ------------------------------------------------------------------ TC-21 / TC-23


@pytest.mark.parametrize(
    ("hands", "field"),
    [
        (0, "lines.0.hands"),
        (-3, "lines.0.hands"),
    ],
    ids=["zero", "negative"],
)
async def test_non_positive_hands_is_rejected_at_api_boundary(
    client: AsyncClient, auth_headers: Any, bundling_world, hands: int, field: str
) -> None:
    """TC-21 / TC-23：零手 / 负手 → **422 ``10001``** + 字段级定位，且**一单都不建**。

    ⚠️ 绕开 ``payload()`` 直接改 dict：``payload`` 构造的是 pydantic 模型，
    ``hands=0`` 在**工厂里**就被 ``Field(ge=1)`` 拒了 —— 那测的是 pydantic 不是接口。
    真实用户发的是 JSON，校验发生在请求边界，所以断言落在 HTTP 响应上。
    """
    headers = await auth_headers(role="custom", permissions=WRITER_PERMISSIONS)
    body = payload(bundling_world).model_dump(mode="json")
    body["lines"][0]["hands"] = hands

    response = await client.post("/api/v1/bundling-orders", json=body, headers=headers)
    assert response.status_code == 422, response.text
    detail = response.json()
    assert detail["code"] == ErrorCode.PARAM_INVALID
    reported = detail["details"]["fields"][0]
    assert reported["field"] == field, f"要给行内红字定位（06 §5）：{detail['details']}"
    listed = await client.get("/api/v1/bundling-orders", headers=headers)
    assert listed.status_code == 200 and listed.json()["data"]["total"] == 0, "零单都不许建"


async def test_fractional_hands_is_rejected_at_api_boundary(
    client: AsyncClient, auth_headers: Any, bundling_world
) -> None:
    """TC-21 另一半：``hands`` 传小数 → ``10001``，**不静默取整**（B18 / 09 §4.2）。"""
    headers = await auth_headers(role="custom", permissions=WRITER_PERMISSIONS)
    body = payload(bundling_world).model_dump(mode="json")
    body["lines"][0]["hands"] = 1.5

    response = await client.post("/api/v1/bundling-orders", json=body, headers=headers)
    assert response.status_code == 422, response.text
    assert response.json()["code"] == ErrorCode.PARAM_INVALID
    assert response.json()["details"]["fields"][0]["field"] == "lines.0.hands"
