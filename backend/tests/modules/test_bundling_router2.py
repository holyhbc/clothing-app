"""打菲**辅助能力**端点的接口层测试（T-BUND-007b）。

======================================  ==========================================
TC-BX-01                            八个端点齐（tags + summary + 已登记 x-permission）
TC-BX-02                            拆分预演**只读**（码零新增、结转零变动），可带 body 试算
TC-BX-03                            标签导出：单未审核 → 30001；``format=csv`` 出 csv_text
TC-BX-04                            打印登记留痕进码详情；缺 hands_seq → 10002
TC-BX-05                            单码详情带「手号/共 M 手/缸号匹号」；查无此码 → 31001
TC-BX-06                            越权查码 → 12002；码列表越权 → 空列表（经 doc_id 回查）
TC-BX-07                            单码作废：行保留、**结转不变**、原因必填、重复 → 31002
TC-BX-08                            已计件 → 32003；``Idempotency-Key`` 重复只生效一次
TC-BX-09                            统计维度 = 款号 × 尺码；顶层静态段声明在 ``/{order_id}`` 之前
TC-BX-10                            导出 CSV（含 BOM）与列表同源；缺 bundling:export → 12001
======================================  ==========================================

⚠️ **TC-BX-07 的「结转不变」是本卡最要紧的一条**：单码作废与整单 ``reverse`` 是两件事。
把作废写成也去减 ``cutting_outputs.bundled_qty``，10 张单各作废 1 手之后结转少 10 件，
而**没有任何表能验出这个差** —— 只看码数与结转都「像是对得上」。

⚠️ **TC-BX-09 后半是路由顺序守卫**（T-CUT-001c-1 教训）：``/statistics`` ``/exports``
一旦排在 ``/{order_id}`` 之后，永远被 UUID 解析挡掉（422），而 OpenAPI 里照样列着它。
"""

from decimal import Decimal
from typing import Any
from uuid import uuid4

from httpx import AsyncClient

from app.common.enums import DataScope
from app.common.permissions_registry import PERMISSIONS
from tests.factories.bundling_approve import (
    mark_code_counted,
    order_codes,
    order_logs,
    read_bundled,
)
from tests.factories.bundling_http import API, approved_order

BUNDLES = "/api/v1/bundles"
COLOR = "WHT"

#: 本卡实现的八个端点（路径 + 方法；与 :mod:`app.modules.bundling.router` 一一对应）
IMPLEMENTED: tuple[str, ...] = (
    f"POST {API}/{{order_id}}/split",
    f"GET {API}/{{order_id}}/labels",
    f"POST {API}/{{order_id}}/label-prints",
    "GET /api/v1/bundles",
    "GET /api/v1/bundles/{bundle_no}",
    "POST /api/v1/bundles/{bundle_no}/voids",
    f"GET {API}/statistics",
    f"GET {API}/exports",
)


def _openapi_paths() -> dict[str, dict[str, Any]]:
    from app.main import create_app

    return dict(create_app().openapi()["paths"])


async def _reader(
    auth_headers: Any,
    extra: tuple[str, ...] = (),
    scope: Any = DataScope.FACTORY,
    employee_no: str | None = None,
) -> dict[str, str]:
    return await auth_headers(
        role="custom",
        permissions=("bundling:read", *extra),
        data_scope=scope,
        employee_no=employee_no,
    )


# ====================================================================== 契约


def test_contract_has_eight_aux_endpoints() -> None:
    """TC-BX-01：八个端点都在契约里，且带 ``tags=打菲`` + ``summary`` + 已登记权限点。

    ⚠️ **只断言这八个**，不是「打菲路径全量」：单据那 13 个端点由
    ``test_bundling_router.py::test_contract_has_exactly_thirteen_endpoints`` 守着，
    两边各守一半，加端点时不会互相顶掉对方的断言。
    """
    known = {item.code for item in PERMISSIONS}
    paths = _openapi_paths()
    for entry in IMPLEMENTED:
        method, template = entry.split(" ", 1)
        # ⚠️ OpenAPI 的路径**保留模板**（``{order_id}``）：替换成具体值就永远查不到，
        # 而守卫会一直绿（这条断言最初就是这么假绿的）。
        path = template
        assert path in paths, f"{entry} 不在契约里（本卡不该留未实现的路径）"
        op = paths[path][method.lower()]
        assert "打菲" in op.get("tags", []), f"{entry} 缺 tags=打菲"
        assert op.get("summary"), f"{entry} 缺 summary（05 §6）"
        permission = op.get("x-permission")
        assert permission, f"{entry} 缺 x-permission"
        assert permission in known, f"{permission!r} 不在 07 §2.2 登记过"
    assert len(IMPLEMENTED) == 8


def test_static_routes_precede_the_id_route() -> None:
    """TC-BX-09 后半：顶层静态段必须排在 ``/{order_id}`` **之前**（T-CUT-001c-1 教训）。

    ⚠️ 只比**一段**的路径：``/bundling-orders/{order_id}/split`` 是两段，它与
    ``/bundling-orders/statistics``（一段）压根不会争同一个匹配 —— 会争的只有
    ``/bundling-orders/{order_id}`` 那一条。比「首段」的话，``{order_id}`` 会在
    ``statistics`` 之后的第一个两段路由上就被抓到，这条守卫就会假绿。
    """
    from app.main import create_app
    from tests.modules.test_bundling_router import _route_paths

    tails = [
        path[len(API) :].strip("/") for path in _route_paths(create_app()) if path.startswith(API)
    ]
    first_param = next((i for i, tail in enumerate(tails) if tail == "{order_id}"), None)
    assert first_param is not None, "守卫本身失效：没抓到 /bundling-orders/{order_id} 路由"
    for index, tail in enumerate(tails):
        if tail and "{" not in tail:
            assert index < first_param, (
                f"顶层静态段 /{tail} 注册在 /{{order_id}} 之后，永远命中不了（422）"
            )


# ====================================================================== 预演与标签


async def test_split_preview_is_read_only(
    client: AsyncClient, db_session: Any, auth_headers: Any, bundling_world: dict[str, Any]
) -> None:
    """TC-BX-02：不传 body 用本单行；带 body 试算。**码零新增、结转零变动**。"""
    order = await approved_order(client, db_session, auth_headers, bundling_world, hands=1)
    headers = await _reader(auth_headers)
    before_codes = len(await order_codes(db_session, order["id"]))
    bundled_before = await read_bundled(
        db_session, bundling_world["style"].style_no, size_code="XL"
    )

    plain = await client.post(f"{API}/{order['id']}/split", headers=headers)
    assert plain.status_code == 200, plain.text
    data = plain.json()["data"]
    assert data["hands_total"] == 1
    assert data["previews"][0]["bundles"][0]["bundle_no"].endswith("-XL01-0001")
    assert isinstance(data["planned_qty"], str), "数量在响应里一律字符串（05 §3）"

    size_line = bundling_world["cutting_size_lines"][0]
    trial = await client.post(
        f"{API}/{order['id']}/split",
        json={
            "lines": [{"size_code": "XL", "hands": 2, "cutting_size_line_id": str(size_line.id)}]
        },
        headers=headers,
    )
    assert trial.status_code == 200, trial.text
    assert trial.json()["data"]["hands_total"] == 2, "带 body 的试算按 body 的手数算"
    assert len(await order_codes(db_session, order["id"])) == before_codes, "预演不许生成码"
    assert (
        await read_bundled(db_session, bundling_world["style"].style_no, size_code="XL")
        == bundled_before
    ), "预演不许动结转"


async def test_labels_require_approved_and_emit_csv_text(
    client: AsyncClient, db_session: Any, auth_headers: Any, bundling_world: dict[str, Any]
) -> None:
    """TC-BX-03：未审核 → ``30001``（**不能返回空数组**，否则误判「这单没有手」）。"""
    # ⚠️ **先断言「无权限」**：``role="custom"`` 在同一个用例里是**同一个角色对象**，
    #    ``grant_permissions`` 是往上加的 —— 先造出带 ``bundling:print`` 的用户，
    #    后面那条「只给 read 也该 403」的用例就再也测不到权限了（它会 200 通过）。
    denied_headers = await _reader(auth_headers, employee_no="A020")
    order = await approved_order(client, db_session, auth_headers, bundling_world, hands=2)
    denied = await client.get(f"{API}/{order['id']}/labels", headers=denied_headers)
    assert denied.status_code == 403 and denied.json()["code"] == 12001, denied.text
    printer = await _reader(auth_headers, ("bundling:print",), employee_no="A021")
    data = await client.get(f"{API}/{order['id']}/labels", headers=printer)
    assert data.status_code == 200, data.text
    items = data.json()["data"]["items"]
    assert len(items) == 2
    assert items[0]["hands_text"] == "第 1 手 / 共 2 手"
    # ⚠️ ``"60.000"`` 而非 ``"60"``：列是 ``numeric(14,3)``，05 §3 要求出参保留完整精度
    assert items[0]["bundle_qty"] == "60.000"

    csv_out = await client.get(f"{API}/{order['id']}/labels?format=csv", headers=printer)
    assert csv_out.status_code == 200, csv_out.text
    text = csv_out.json()["data"]["csv_text"]
    assert text.splitlines()[0].startswith("款号,颜色,尺码")
    assert data.json()["data"]["csv_text"] is None, "format=data 时不该渲染 CSV（白干活）"


async def test_label_prints_are_traced_in_bundle_detail(
    client: AsyncClient, db_session: Any, auth_headers: Any, bundling_world: dict[str, Any]
) -> None:
    """TC-BX-04：登记后码详情里能看到打印留痕；缺 ``hands_seq`` → ``10002``。"""
    order = await approved_order(client, db_session, auth_headers, bundling_world, hands=2)
    printer = await _reader(auth_headers, ("bundling:print",))
    bundle_no = (await order_codes(db_session, order["id"]))[0].bundle_no

    missing = await client.post(
        f"{API}/{order['id']}/label-prints", json={"size_code": "XL"}, headers=printer
    )
    assert missing.status_code == 400 and missing.json()["code"] == 10002, missing.text

    ok = await client.post(
        f"{API}/{order['id']}/label-prints",
        json={"hands_seq": 1, "size_code": "XL", "printed_qty": 1},
        headers=printer,
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["data"]["printed_count"] == 1
    assert len(ok.json()["data"]["print_ids"]) == 1

    detail = await client.get(f"{BUNDLES}/{bundle_no}", headers=await _reader(auth_headers))
    records = detail.json()["data"]["print_records"]
    assert [row["hands_seq"] for row in records] == [1], "B15：一手一行留痕，可逐次追溯"


# ====================================================================== 码查询


async def test_bundle_detail_carries_hand_context(
    client: AsyncClient, db_session: Any, auth_headers: Any, bundling_world: dict[str, Any]
) -> None:
    """TC-BX-05：「XL 第 2 手 · 60 件」能被前端直接显示（ADR-0016 §6）。"""
    order = await approved_order(client, db_session, auth_headers, bundling_world, hands=2)
    reader = await _reader(auth_headers)
    rows = await order_codes(db_session, order["id"])
    second = next(row for row in rows if row.hands == 2)

    detail = await client.get(f"{BUNDLES}/{second.bundle_no}", headers=reader)
    assert detail.status_code == 200, detail.text
    data = detail.json()["data"]
    assert data["hands"] == 2
    assert data["hands_total_of_size"] == 2, "共 M 手 = 该尺码总手数（标签与列表同口径）"
    assert data["bundle_qty"] == "60.000"
    assert data["counted_qty"] == "0.000" and data["counted_at"] is None
    assert data["cutting_size_line_id"]
    assert data["dye_lot_no"], "缸号必须回查得到（车间最高频的追溯问句）"
    assert data["status"] == "ACTIVE"

    missing = await client.get(f"{BUNDLES}/BD-20261018-999999-XL01-0001", headers=reader)
    assert missing.status_code == 404 and missing.json()["code"] == 31001, missing.text

    listing = await client.get(
        f"{BUNDLES}?style_no={bundling_world['style'].style_no}", headers=reader
    )
    assert listing.status_code == 200
    assert listing.json()["data"]["total"] == 2
    assert {row["hands_total_of_size"] for row in listing.json()["data"]["items"]} == {2}


async def test_bundle_scope_is_checked_through_parent_order(
    client: AsyncClient, db_session: Any, auth_headers: Any, bundling_world: dict[str, Any]
) -> None:
    """TC-BX-06：``bundles`` 没有 ``workshop_id`` → **经父单回查**车间后拦截。"""
    order = await approved_order(client, db_session, auth_headers, bundling_world, hands=1)
    bundle_no = (await order_codes(db_session, order["id"]))[0].bundle_no
    outsider = await _reader(auth_headers, scope=DataScope.WORKSHOP, employee_no="A009")

    detail = await client.get(f"{BUNDLES}/{bundle_no}", headers=outsider)
    assert detail.status_code == 403 and detail.json()["code"] == 12002, detail.text
    listing = await client.get(f"{BUNDLES}", headers=outsider)
    assert listing.status_code == 200
    assert listing.json()["data"]["total"] == 0, "越权车间的码不出现（列表返回空而不是报错）"


# ====================================================================== 单码作废


async def test_void_code_keeps_row_and_untouched_carry_over(
    client: AsyncClient, db_session: Any, auth_headers: Any, bundling_world: dict[str, Any]
) -> None:
    """TC-BX-07：行保留 + ``status=VOIDED`` + **结转不变** + 原因必填 + 重复 → 31002。"""
    order = await approved_order(client, db_session, auth_headers, bundling_world, hands=2)
    voider = await _reader(auth_headers, ("bundling:code:void",), employee_no="A010")
    bundle_no = (await order_codes(db_session, order["id"]))[0].bundle_no
    style_no = bundling_world["style"].style_no

    blank = await client.post(
        f"{BUNDLES}/{bundle_no}/voids", json={"void_reason": "   "}, headers=voider
    )
    assert blank.status_code == 400 and blank.json()["code"] == 10002, blank.text

    response = await client.post(
        f"{BUNDLES}/{bundle_no}/voids", json={"void_reason": "标签印错"}, headers=voider
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "VOIDED" and data["void_reason"] == "标签印错"

    rows = {row.bundle_no: row for row in await order_codes(db_session, order["id"])}
    assert len(rows) == 2, "作废不许删行（B12）"
    assert rows[bundle_no].status == "VOIDED"
    assert await read_bundled(db_session, style_no, size_code="XL") == Decimal("120"), (
        "单码作废**不动**结转 —— 动了就再也回不到原点（结转只随整单 approve/reverse 变）"
    )
    actions = [row.action for row in await order_logs(db_session, order["id"])]
    assert actions.count("VOID_CODE") == 1, actions

    again = await client.post(
        f"{BUNDLES}/{bundle_no}/voids", json={"void_reason": "再作废一次"}, headers=voider
    )
    assert again.status_code == 409 and again.json()["code"] == 31002, again.text

    detail = await client.get(f"{BUNDLES}/{bundle_no}", headers=await _reader(auth_headers))
    assert detail.json()["data"]["void_reason"] == "标签印错", "作废原因要能被扫码枪显示"


async def test_void_code_rejects_counted_and_is_idempotent(
    client: AsyncClient, db_session: Any, auth_headers: Any, bundling_world: dict[str, Any]
) -> None:
    """TC-BX-08：同 ``Idempotency-Key`` 重复 voids **只生效一次**；已计件 → ``32003``。"""
    order = await approved_order(client, db_session, auth_headers, bundling_world, hands=2)
    voider = await _reader(auth_headers, ("bundling:code:void",), employee_no="A011")
    rows = await order_codes(db_session, order["id"])
    first_no, second_no = rows[0].bundle_no, rows[1].bundle_no

    key = {**voider, "Idempotency-Key": f"void-{uuid4().hex}"}
    first = await client.post(
        f"{BUNDLES}/{first_no}/voids", json={"void_reason": "重复点两次"}, headers=key
    )
    second = await client.post(
        f"{BUNDLES}/{first_no}/voids", json={"void_reason": "重复点两次"}, headers=key
    )
    assert first.status_code == second.status_code == 200, (first.text, second.text)
    assert second.json()["data"] == first.json()["data"], "幂等命中必须返回首次结果"
    actions = [row.action for row in await order_logs(db_session, order["id"])]
    assert actions.count("VOID_CODE") == 1, actions

    # 另一手手工置成已计件（模拟计件模块回写 counted_at）→ 作废必须被拒
    await mark_code_counted(db_session, order["id"], hands=2)
    denied = await client.post(
        f"{BUNDLES}/{second_no}/voids",
        json={"void_reason": "这手已经计件了"},
        headers=await _reader(auth_headers, ("bundling:code:void",), employee_no="A012"),
    )
    assert denied.status_code == 409 and denied.json()["code"] == 32003, denied.text
    still = {row.bundle_no: row.status for row in await order_codes(db_session, order["id"])}
    assert still[second_no] == "ACTIVE", "被拒的作废不许留下任何痕迹"


# ====================================================================== 统计与导出


async def test_statistics_groups_by_style_and_size(
    client: AsyncClient, db_session: Any, auth_headers: Any, bundling_world: dict[str, Any]
) -> None:
    """TC-BX-09：维度只有款号 × 尺码；三组量齐；**已计 + 未计 = 手数**。"""
    await approved_order(client, db_session, auth_headers, bundling_world, hands=2)
    reader = await _reader(auth_headers)
    response = await client.get(f"{API}/statistics", headers=reader)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert [row["size_code"] for row in data["items"]] == ["XL"]
    row = data["items"][0]
    assert row["hands"] == 2 and row["counted_hands"] == 0 and row["uncounted_hands"] == 2
    assert row["qty"] == "120.000"
    assert data["totals"]["hands"] == row["hands"], "合计行与明细同口径"

    outsider = await _reader(auth_headers, scope=DataScope.WORKSHOP, employee_no="A013")
    blocked = await client.get(f"{API}/statistics", headers=outsider)
    assert blocked.json()["data"]["items"] == [], "统计同样受数据范围约束"


async def test_export_orders_csv_matches_list(
    client: AsyncClient, db_session: Any, auth_headers: Any, bundling_world: dict[str, Any]
) -> None:
    """TC-BX-10：CSV 带 BOM、表头中文、行数与列表一致；缺 ``bundling:export`` → 12001。"""
    await approved_order(client, db_session, auth_headers, bundling_world, hands=1)
    exporter = await auth_headers(
        role="custom",
        permissions=("bundling:read", "bundling:export"),
        data_scope=DataScope.FACTORY,
    )
    listing = await client.get(f"{API}?status=APPROVED", headers=exporter)
    expected = listing.json()["data"]["total"]

    response = await client.get(f"{API}/exports?status=APPROVED", headers=exporter)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv; charset=utf-8-sig")
    assert response.content.startswith(b"\xef\xbb\xbf"), "缺 BOM 时 Excel 按 GBK 打开 → 中文乱码"
    lines = response.content.decode("utf-8-sig").splitlines()
    assert lines[0].startswith("单据号,单据日期,款号")
    assert len(lines) - 1 == expected, "导出必须与列表同源（07 §3.2 铁律 3）"


# ====================================================================== 小工具
