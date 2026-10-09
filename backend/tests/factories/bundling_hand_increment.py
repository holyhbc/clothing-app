"""打菲**审核后增手**（``POST .../hand-increments``）用例的测试世界（T-BUND-011 / ADR-0033）。

⚠️ **为什么不能直接用 :func:`~tests.factories.bundling_approve.approved_order`**：
审核用例的世界让「打菲手数 == 裁剪可打手数」（两侧相等才不撞 ``31004``），
而增手用例的世界必须让**裁剪侧比打菲侧多几手** —— 那正是「分批打」的形状
（先审 3 手开工，后面再补 2 手，ADR-0033）。两侧相等时每条增手用例都会撞 ``31004``，
而报错指向打菲、根因却在测试世界，排查要跨三层。

⚠️ :func:`approved_with_headroom` 的 ``lines`` 一律是**逐明细行**的
``(size_code, 本行 hands, 该行引用的裁剪布批可打 hands)``：同尺码可以有多行
（跨布批，ADR-0017 §4），而「同尺码第二布批」的裁剪明细行要在这里现造 ——
:func:`~tests.factories.bundling_approve.ensure_sizes` 按尺码去重，补不出第二行。
"""

from typing import Any

from httpx import AsyncClient, Response

from app.common.enums import DataScope
from app.modules.cutting.models import CuttingOrderSizeLine
from tests.factories.bundling_approve import ensure_sizes
from tests.factories.bundling_http import API, WRITER_PERMISSIONS, create_order
from tests.factories.user import OPERATOR_ID

#: 增手人的权限点：``bundling:update``（03 §4.1 该行的权限点列，ADR-0033 不新增权限点）。
#: 带 ``bundling:read`` 是为了顺带能读单据确认结果，不是端点要求。
INCREMENTER: tuple[str, ...] = ("bundling:read", "bundling:update")

#: 审核人权限点（与制单人 / 增手人刻意分开：08 §1.1「制单人 ≠ 审核人」）。
CHECKER: tuple[str, ...] = ("bundling:read", "bundling:approve")

#: 一条明细行的 ``(尺码码, 本行 hands, 引用的裁剪布批可打 hands)``。
Line = tuple[str, int, int]


async def updater(
    auth_headers: Any,
    *,
    extra: tuple[str, ...] = (),
    data_scope: DataScope | None = DataScope.FACTORY,
    employee_no: str = "A005",
) -> dict[str, str]:
    """增手人的请求头（``bundling:update``）。

    ⚠️ 默认 **FACTORY**：``role="custom"`` 的默认范围是 SELF，而这些用例的单据是
    「别人建的」（制单人 / 审核人各自独立的 token）—— 不给全厂范围的话每条用例都会撞
    ``12002``，报错指向数据范围、与本卡要验的东西毫无关系。
    """
    return await auth_headers(
        role="custom",
        permissions=(*INCREMENTER, *extra),
        data_scope=data_scope,
        employee_no=employee_no,
    )


async def increment(
    client: AsyncClient,
    order: dict[str, Any],
    headers: dict[str, str],
    *,
    body: dict[str, Any] | None = None,
    **overrides: Any,
) -> Response:
    """POST 一次 ``/hand-increments``，**默认带上该单当前的 ``version``**。

    ⚠️ 默认体刻意从 ``order["version"]`` 取而不是写死：乐观锁就是拿它挡「用旧版本号
    覆盖别人刚做的改动」，而 TC-HI-01~10 全部只关心业务副作用，只有 TC-HI-07 显式传
    过期版本 —— 把「传当前版本」做成默认，那条用例才真的是在测过期版本。

    :param body: 整个替换请求体（用来测**缺字段**）；``overrides`` 是往默认体上打补丁。
    """
    payload: dict[str, Any] = {
        "version": order["version"],
        "size_code": "XL",
        "delta_hands": 1,
        **overrides,
    }
    if payload.get("line_id") is not None:
        # ⚠️ ``size_code`` 与 ``line_id`` 是**二选一**（同尺码跨布批多行时用 line_id 定位），
        # 给了一个就不能再带另一个 —— 否则报 ``10001``，而用例想验的其实是「按行增手」。
        payload["size_code"] = None
    if body is not None:
        payload = body
    return await client.post(f"{API}/{order['id']}/hand-increments", json=payload, headers=headers)


async def new_batch_line(
    session: Any,
    world: dict[str, Any],
    size_code: str,
    *,
    hands: int,
    qty_per_hand: int = 60,
) -> CuttingOrderSizeLine:
    """给 ``size_code`` **再补一条**裁剪尺码明细行（同尺码的第二布批，ADR-0017 §4）。

    ⚠️ **必须复用同一条 ``line_id`` / ``line_color_id``**（即同一个布批行的同一颜色行）：
    打菲明细行引的是尺码明细行，而「跨布批」在本测试世界里表达为「同尺码多条尺码明细行」
    —— 手号连续（Q-B13）验的正是这一种。
    """
    origin = next(row for row in world["cutting_size_lines"] if row.size_code == size_code)
    row = CuttingOrderSizeLine(
        line_color_id=origin.line_color_id,
        line_id=origin.line_id,
        size_line_no=max(item.size_line_no for item in world["cutting_size_lines"]) + 1,
        size_code=size_code,
        hands=hands,
        qty_per_hand=qty_per_hand,
        output_qty=hands * qty_per_hand,
        output_qty_manual=False,
        balance_qty=0,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    session.add(row)
    await session.flush()
    world["cutting_size_lines"].append(row)
    return row


async def approved_with_headroom(
    client: AsyncClient,
    db_session: Any,
    auth_headers: Any,
    world: dict[str, Any],
    *,
    lines: tuple[Line, ...],
    qty_per_hand: int = 60,
    employee_no: str = "A002",
) -> dict[str, Any]:
    """建一张**已审核、且裁剪侧还留着手数余量**的单，返回审核后的响应体。

    :param lines: 逐明细行的 ``(尺码码, 本行 hands, 该布批可打 hands)``；同尺码重复出现
        即「跨布批」，第二个起现造裁剪尺码明细行（见 :func:`new_batch_line`）。
    :returns: ``POST /approvals`` 的响应体（带 ``version`` / ``hands_total`` / ``lines``）。
    """
    cutting_totals: dict[str, int] = {}
    for size_code, _hands, cutting_hands in lines:
        cutting_totals[size_code] = cutting_totals.get(size_code, 0) + cutting_hands
    # ⚠️ 结转行的可用量按「该尺码全部布批合计」铺：``ensure_sizes`` 只会为每个尺码建
    # 一行结转，而增手结转走 ``bundled_qty += delta × qty_per_hand`` —— 余量不够就是 30002。
    await ensure_sizes(db_session, world, tuple(cutting_totals.items()), qty_per_hand=qty_per_hand)

    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for index, (size_code, hands, cutting_hands) in enumerate(lines, start=1):
        size_line = next(row for row in world["cutting_size_lines"] if row.size_code == size_code)
        if size_code in seen:
            size_line = await new_batch_line(
                db_session, world, size_code, hands=cutting_hands, qty_per_hand=qty_per_hand
            )
        seen.add(size_code)
        items.append(
            {
                "cutting_size_line_id": str(size_line.id),
                "size_code": size_code,
                "line_no": index,
                "hands": hands,
            }
        )

    maker = await auth_headers(role="custom", permissions=WRITER_PERMISSIONS)
    created = await create_order(client, maker, world, lines=items)
    submitted = await client.post(f"{API}/{created['id']}/submissions", headers=maker)
    assert submitted.status_code == 200, submitted.text
    checker = await auth_headers(
        role="custom", permissions=CHECKER, data_scope=DataScope.FACTORY, employee_no=employee_no
    )
    approved = await client.post(f"{API}/{created['id']}/approvals", headers=checker)
    assert approved.status_code == 200, approved.text
    return dict(approved.json()["data"])


__all__ = [
    "CHECKER",
    "INCREMENTER",
    "Line",
    "approved_with_headroom",
    "increment",
    "new_batch_line",
    "updater",
]
