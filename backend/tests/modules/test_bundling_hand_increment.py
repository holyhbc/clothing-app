"""打菲单**审核后增手**的接口层用例（T-BUND-011，ADR-0033，闭环 L-109）。

=========================================  =========================================
TC-HI-01                                   3 手 → +2 = 5 手 / 2 个新码 / 结转对账
TC-HI-02                                   入参校验：``delta ≤ 0`` / 缺字段 / 多余字段 → ``10001``
TC-HI-03                                   状态门：非 ``APPROVED`` → ``30001``（**减手不实现**）
TC-HI-04                                   增手后超打 → ``31004`` 且**零副作用**
TC-HI-05                                   手号接续：跨布批接着编、跨尺码各编各的（Q-B13）
TC-HI-07                                   ``version`` 过期 → ``10003``
TC-HI-08                                   权限与范围：``12001`` / ``12002``
TC-HI-09                                   已计件码**完全不受影响**（``counted_at`` / ``bundle_qty``）
TC-HI-10                                   日志记改前改后 + **新增手号区间**
TC-HI-11                                   契约：入参结构只容得下「某尺码 + 加几手」+ 路由不被抢
=========================================  =========================================

⚠️ **TC-HI-06（20 并发）不在本文件**：并发必须**独立引擎真提交**（docs/10 §5.4），而这里
全部走回滚型 ``db_session``；混在一起读的人会以为并发也共享事务（见并发文件）。

⚠️ **「零副作用」断言不能省**（TC-HI-04）：被拒的请求若已写下表头 / 结转 / 码，症状是
「报错对了、数据错了」，而这种错要等下次审核对账才看得出来。
"""

from decimal import Decimal
from typing import Any
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import select

from app.common.enums import DataScope
from app.modules.bundling.models import Bundle
from tests.factories.bundling_approve import (
    mark_code_counted,
    order_codes,
    order_logs,
    read_bundled,
)
from tests.factories.bundling_hand_increment import (
    approved_with_headroom,
    increment,
    updater,
)
from tests.factories.bundling_http import API, WRITER_PERMISSIONS, create_order, submitted_order


async def _codes_by_line(db_session: Any, order_id: Any) -> list[tuple[str, int, str]]:
    """``(尺码, 手序号, 明细行 id)`` 升序 —— 验「跨布批接着编」而不是「按 delta 从 1 重编」。"""
    rows = (
        await db_session.execute(
            select(Bundle.size_code, Bundle.hands, Bundle.line_id)
            .where(Bundle.doc_id == order_id, Bundle.deleted_at.is_(None))
            .order_by(Bundle.size_code, Bundle.hands)
            .execution_options(populate_existing=True)
        )
    ).all()
    return [(row[0], row[1], str(row[2])) for row in rows]


# ====================================================================== TC-HI-01


async def test_increment_appends_codes_and_carries_over(
    client: AsyncClient, db_session: Any, auth_headers: Any, bundling_world: dict[str, Any]
) -> None:
    """TC-HI-01：3 手 → +2 = 5 手、**新增 ``XL04`` / ``XL05`` 两个码**、结转对账。

    ⚠️ 裁剪侧刻意铺 5 手（``hands`` 决定可打手数）：两侧相等时 ``+2`` 必然撞 ``31004``，
    而这条用例要验的是「正常增手」的完整副作用。
    """
    order = await approved_with_headroom(
        client, db_session, auth_headers, bundling_world, lines=(("XL", 3, 5),)
    )
    response = await increment(client, order, await updater(auth_headers), delta_hands=2)
    assert response.status_code == 200, response.text
    data = response.json()["data"]

    assert data["status"] == "APPROVED", "增手**不改状态**（03 §4.1：目标同起始）"
    assert data["hands_total"] == 5, "表头汇总只加 delta，5 = 3 + 2"
    assert data["version"] == order["version"] + 1, "响应必须带写完之后的新版本号"
    assert data["output_qty"] == "300.000", "件数随每手件数同步（05 §3：数量一律字符串）"
    assert data["lines"][0]["hands"] == 5, "明细行的 hands 也要落库，否则下次改单全乱"

    codes = await order_codes(db_session, order["id"])
    assert [row.hands for row in codes] == [1, 2, 3, 4, 5], "手号必须接着原最大号往下编"
    assert [row.bundle_no for row in codes[3:]] == [
        f"{order['doc_no']}-XL04-0001",
        f"{order['doc_no']}-XL05-0001",
    ], "新增的码沿用 005b 的 bundle_no 格式（尺码码与手序号之间无分隔符）"
    assert all(row.bundle_qty == Decimal("60.000") for row in codes), "每手件数取 qty_per_hand"
    assert await read_bundled(db_session, bundling_world["style"].style_no, size_code="XL") == (
        Decimal("300")
    ), "结转 += 2 × qty_per_hand（180 + 120）"


# ====================================================================== TC-HI-02


async def test_payload_validation_rejects_bad_bodies(
    client: AsyncClient, db_session: Any, auth_headers: Any, bundling_world: dict[str, Any]
) -> None:
    """TC-HI-02：``delta ≤ 0`` / 缺字段 / 多余字段 → ``10001``，且**一个码都不多**。

    ⚠️ 「减手」在**入参层**就被挡下（``delta_hands`` 的 ``ge=1``），不是 service 里的
    二次判断 —— ``10001`` 才带字段级明细，前端能做行内红字（05 §3）。
    """
    order = await approved_with_headroom(
        client, db_session, auth_headers, bundling_world, lines=(("XL", 3, 5),)
    )
    headers = await updater(auth_headers)
    version = order["version"]
    line_id = str(order["lines"][0]["id"])
    bad: tuple[dict[str, Any], ...] = (
        {"version": version, "size_code": "XL", "delta_hands": 0},  # 零手
        {"version": version, "size_code": "XL", "delta_hands": -2},  # 减手（不实现）
        {"version": version, "size_code": "XL"},  # 缺 delta_hands
        {"version": version, "delta_hands": 1},  # 定位字段一个都没给
        {"version": version, "size_code": "XL", "line_id": line_id, "delta_hands": 1},  # 两个都给
        {"version": version, "delta_hands": 1, "hands": 4},  # 全量替换 hands：结构上不许有
        {"version": version, "delta_hands": 1, "color_code": "WHT"},  # 不接受色码
        {
            "version": version,
            "line_id": line_id,
            "delta_hands": 1,
            "cutting_size_line_id": line_id,  # 不接受换来源行
        },
        {"version": version, "size_code": "XXL", "delta_hands": 1},  # 本单没有的尺码 → service 拦
    )
    for body in bad:
        response = await increment(client, order, headers, body=body)
        assert response.status_code == 422, (body, response.text)
        assert response.json()["code"] == 10001, (body, response.text)

    zero_side_effect = await increment(client, order, headers, delta_hands=2)
    assert zero_side_effect.status_code == 200, zero_side_effect.text
    assert [row.hands for row in await order_codes(db_session, order["id"])] == [1, 2, 3, 4, 5]


# ====================================================================== TC-HI-03


async def test_only_approved_orders_accept_increments(
    client: AsyncClient, db_session: Any, auth_headers: Any, bundling_world: dict[str, Any]
) -> None:
    """TC-HI-03：``DRAFT`` / ``SUBMITTED`` → ``30001``；路径拼错也会命中本端点（不是 422）。

    ⚠️ 后半条是**路由顺序守卫**：``/hand-increments`` 挂在 ``{order_id}`` 之下，一旦被
    ``/bundling-orders/{order_id}`` 抢走，报错就变成「UUID 解析失败 422」而契约里照样
    列着它 —— 契约说有、实际调不到，是最难查的一类缺陷（T-CUT-001c-1 教训）。
    """
    maker = await auth_headers(role="custom", permissions=WRITER_PERMISSIONS)
    draft = await create_order(
        client,
        maker,
        bundling_world,
        lines=[{"cutting_size_line_id": str(bundling_world["cutting_size_lines"][0].id)}],
    )
    submitted = await submitted_order(client, db_session, maker, bundling_world)

    headers = await updater(auth_headers)
    for order in (draft, submitted):
        response = await increment(client, order, headers, delta_hands=1)
        assert response.status_code == 409, (order["status"], response.text)
        assert response.json()["code"] == 30001, (order["status"], response.text)

    ghost = await client.post(
        f"{API}/{uuid4()}/hand-increments",
        json={"version": 1, "size_code": "XL", "delta_hands": 1},
        headers=headers,
    )
    assert ghost.status_code == 409 and ghost.json()["code"] == 30001, ghost.text
    assert await order_codes(db_session, draft["id"]) == [], "被拒的单不许留下任何码"


# ====================================================================== TC-HI-04


async def test_over_bundle_is_rejected_without_side_effects(
    client: AsyncClient, db_session: Any, auth_headers: Any, bundling_world: dict[str, Any]
) -> None:
    """TC-HI-04：增手后超打 → ``31004``，**表头 / 结转 / 码三者都不动**。

    ⚠️ 这条验的是「防超打不变式**在增手时必须重跑**」：``hands ≤ 裁剪可打手数`` 是在
    ``approve`` 时验的，裁剪侧这中间没增产量，所以 3 + 2 = 5 > 4 必须被拦。裁剪侧刻意留
    **1 手**余量（打 3 / 裁 4）：既能撞超打，又能用「紧接着 +1 成功」证明被拒的那次没留下
    脏数据（裁剪侧恰好 3 手时后续 +1 也撞超打，这条断言就无从谈起）。
    """
    order = await approved_with_headroom(
        client, db_session, auth_headers, bundling_world, lines=(("XL", 3, 4),)
    )
    bundled_before = await read_bundled(
        db_session, bundling_world["style"].style_no, size_code="XL"
    )

    headers = await updater(auth_headers)
    response = await increment(client, order, headers, delta_hands=2)
    assert response.status_code == 409, response.text
    assert response.json()["code"] == 31004, response.text
    assert response.json()["details"]["planned_hands"] == 5, "报错要回「应有手数」供用户核对"
    assert response.json()["details"]["cutting_hands"] == 4

    after = await increment(client, order, headers, delta_hands=1)
    assert after.status_code == 200, after.text
    assert after.json()["data"]["version"] == order["version"] + 1, "版本号只在成功时前进"
    assert await read_bundled(db_session, bundling_world["style"].style_no, size_code="XL") == (
        bundled_before + Decimal("60")
    ), "被拒的那次不许动结转；随后成功的一次只结转 1 手（与基线差 60）"
    assert [row.hands for row in await order_codes(db_session, order["id"])] == [1, 2, 3, 4]


# ====================================================================== TC-HI-05


async def test_hand_numbers_continue_across_batches_and_sizes(
    client: AsyncClient, db_session: Any, auth_headers: Any, bundling_world: dict[str, Any]
) -> None:
    """TC-HI-05：同尺码**跨布批接着编**、跨尺码**各编各的**（Q-B13 / B22）。

    ⚠️ 三行明细：``L`` 2 手、``XL`` 2 手（第 1 布批）、``XL`` 1 手（第 2 布批）。
    给 ``size_code="XL"`` 增手必须**被拒**（同尺码两行，定位不唯一 —— 不猜），
    给 ``line_id``（第 2 布批那行）增 1 手才合法，且新码是 **XL04 而不是 XL01**。
    """
    order = await approved_with_headroom(
        client,
        db_session,
        auth_headers,
        bundling_world,
        lines=(("L", 2, 4), ("XL", 2, 2), ("XL", 1, 3)),
    )
    headers = await updater(auth_headers)
    xl_second_batch = str(order["lines"][2]["id"])

    ambiguous = await increment(client, order, headers, delta_hands=1)
    assert ambiguous.status_code == 422 and ambiguous.json()["code"] == 10001, ambiguous.text
    ambiguous_l = await increment(client, order, headers, size_code="L", delta_hands=2)
    assert ambiguous_l.status_code == 200, ambiguous_l.text
    order = dict(ambiguous_l.json()["data"])

    one_more = await increment(client, order, headers, line_id=xl_second_batch, delta_hands=1)
    assert one_more.status_code == 200, one_more.text
    order = dict(one_more.json()["data"])

    rows = await _codes_by_line(db_session, order["id"])
    assert [(size, hands) for size, hands, _ in rows] == [("L", hands) for hands in range(1, 5)] + [
        ("XL", hands) for hands in range(1, 5)
    ], "两个尺码各自从 1 编到底、无缺号"
    assert rows[2][2] == rows[3][2], "L 的新增码挂在同一行"
    assert rows[6][2] == xl_second_batch and rows[7][2] == xl_second_batch, (
        "XL 的新增码挂在目标行，且第 2 布批接在第 1 布批之后（不是从 1 重编）"
    )
    assert order["hands_total"] == 8 and order["lines"][0]["hands"] == 4
    assert order["lines"][2]["hands"] == 2, "被增的那一行 hands 也 +1"


# ====================================================================== TC-HI-07 / 08


async def test_stale_version_is_rejected_then_succeeds(
    client: AsyncClient, db_session: Any, auth_headers: Any, bundling_world: dict[str, Any]
) -> None:
    """TC-HI-07：``version`` 过期 → ``10003``，且**重试前不许有任何副作用**。"""
    order = await approved_with_headroom(
        client, db_session, auth_headers, bundling_world, lines=(("XL", 3, 5),)
    )
    headers = await updater(auth_headers)
    stale = await increment(client, order, headers, version=order["version"] - 1, delta_hands=1)
    assert stale.status_code == 409 and stale.json()["code"] == 10003, stale.text

    retried = await increment(client, order, headers, delta_hands=1)
    assert retried.status_code == 200, retried.text
    assert retried.json()["data"]["version"] == order["version"] + 1, "被拒的那次不涨版本号"
    assert len(await order_codes(db_session, order["id"])) == 4


async def test_permission_and_scope_are_enforced(
    client: AsyncClient, db_session: Any, auth_headers: Any, bundling_world: dict[str, Any]
) -> None:
    """TC-HI-08：缺 ``bundling:update`` → ``12001``；越权车间 → ``12002``。

    ⚠️ ``12001`` 用**内置角色** ``line_leader``（03 §10：能查看、**不能修改**）而不是
    ``role="custom"``：custom 角色在同一个 pytest session 里是**同一个角色对象**，
    ``grant_permissions`` 往上累加 —— 本文件的其它用例刚给它授过 ``bundling:update``，
    于是「没有 update 的人」会拿到 200，断言假绿（TC-BX-03 踩过同一类）。
    """
    order = await approved_with_headroom(
        client, db_session, auth_headers, bundling_world, lines=(("XL", 3, 5),)
    )
    denied = await increment(
        client, order, await auth_headers(role="line_leader", employee_no="A030"), delta_hands=1
    )
    assert denied.status_code == 403 and denied.json()["code"] == 12001, denied.text

    outsider = await increment(
        client,
        order,
        await updater(auth_headers, data_scope=DataScope.WORKSHOP, employee_no="A031"),
        delta_hands=1,
    )
    assert outsider.status_code == 403 and outsider.json()["code"] == 12002, outsider.text
    assert len(await order_codes(db_session, order["id"])) == 3, "被拒的两次一个码都不生成"


# ====================================================================== TC-HI-09 / 10


async def test_counted_codes_are_untouched(
    client: AsyncClient, db_session: Any, auth_headers: Any, bundling_world: dict[str, Any]
) -> None:
    """TC-HI-09：**已计件码完全不受影响**（``counted_at`` / ``counted_qty`` / ``bundle_qty``）。

    ⚠️ 这是 ADR-0033 判「增手安全」的全部依据：只补生成新码、不碰任何已存在的码，所以
    **连 ``32003`` 都不需要判**（计件流水属 P2，L-096）。
    """
    order = await approved_with_headroom(
        client, db_session, auth_headers, bundling_world, lines=(("XL", 3, 5),)
    )
    await mark_code_counted(db_session, order["id"], hands=2)
    before = (
        await db_session.execute(
            select(Bundle.hands, Bundle.counted_at, Bundle.counted_qty, Bundle.bundle_qty)
            .where(Bundle.doc_id == order["id"], Bundle.hands == 2)
            .execution_options(populate_existing=True)
        )
    ).one()

    response = await increment(client, order, await updater(auth_headers), delta_hands=2)
    assert response.status_code == 200, response.text
    after = (
        await db_session.execute(
            select(Bundle.hands, Bundle.counted_at, Bundle.counted_qty, Bundle.bundle_qty)
            .where(Bundle.doc_id == order["id"], Bundle.hands == 2)
            .execution_options(populate_existing=True)
        )
    ).one()
    assert tuple(after) == tuple(before), "已计件的那一手四列都不许动"
    fresh = (
        await db_session.execute(
            select(Bundle.hands, Bundle.counted_at)
            .where(Bundle.doc_id == order["id"], Bundle.hands.in_([4, 5]))
            .order_by(Bundle.hands)
            .execution_options(populate_existing=True)
        )
    ).all()
    assert [row[0] for row in fresh] == [4, 5] and all(row[1] is None for row in fresh)


async def test_log_records_before_after_and_new_hand_range(
    client: AsyncClient, db_session: Any, auth_headers: Any, bundling_world: dict[str, Any]
) -> None:
    """TC-HI-10：日志记 ``改前 → 改后`` **加新增手号区间**（`document_logs.changed_fields`）。

    ⚠️ 「新增手号区间」不可省：手号是**标签上印的**（「第 N 手 / 共 M 手」，B25），
    而车间里的旧标签已经贴上去了 —— 只记「加了 2 手」的话，事后没人说得清新码是哪两号。
    """
    order = await approved_with_headroom(
        client, db_session, auth_headers, bundling_world, lines=(("XL", 3, 5),)
    )
    await increment(client, order, await updater(auth_headers), delta_hands=2)

    logs = [
        row for row in await order_logs(db_session, order["id"]) if row.action == "HAND_INCREMENT"
    ]
    assert len(logs) == 1, [row.action for row in await order_logs(db_session, order["id"])]
    log = logs[0]
    assert log.from_status == log.to_status == "APPROVED", "不改状态也要留痕"
    changed = dict(log.changed_fields or {})
    assert changed["size_code"] == "XL" and changed["delta_hands"] == 2
    assert (changed["hands_before"], changed["hands_after"]) == (3, 5)
    assert (changed["hands_total_before"], changed["hands_total_after"]) == (3, 5)
    assert changed["new_hands_range"] == "4..5" and changed["codes"] == 2
    assert changed["bundled_delta"] == "120", "日志里是 str(Decimal)，与 approve 同款"


# ====================================================================== TC-HI-11 契约


def test_contract_only_accepts_size_plus_delta() -> None:
    """TC-HI-11：入参**结构上**只容得下「某尺码 + 加几手」，且路径不被 ``{order_id}`` 抢走。

    ⚠️ 断言的是 **OpenAPI 组件本身**而不是「实现里没读这个字段」：ADR-0033 的核心论据是
    「借增手之名改别的，在类型上就表达不出来」，所以契约层必须与实现同款约束。
    """
    from app.common.permissions_registry import PERMISSIONS
    from app.main import create_app

    spec = create_app().openapi()
    op = spec["paths"][f"{API}/{{order_id}}/hand-increments"]["post"]
    assert "打菲" in op["tags"] and op["summary"], "05 §6：tags + summary 不可少"
    assert op["x-permission"] == "bundling:update"
    assert "bundling:update" in {item.code for item in PERMISSIONS}, "权限点必须在 07 §2.2 登记过"

    ref = op["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    schema = spec["components"]["schemas"][ref.rsplit("/", 1)[-1]]
    assert set(schema["properties"]) == {"version", "size_code", "line_id", "delta_hands"}, (
        "入参只允许这四个字段：多一个就是给「借增手之名改别的」留了门"
    )
    assert schema["additionalProperties"] is False, "extra=forbid 必须进契约"
    assert schema["properties"]["delta_hands"]["minimum"] == 1, "delta_hands 必须 > 0（减手不实现）"
