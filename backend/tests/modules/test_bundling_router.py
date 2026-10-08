"""打菲单接口层的**读 / 草稿写 / 契约 / 权限**测试（T-BUND-007a）。

======================================  ==========================================
TC-BD-01                        建单 / 详情 / 列表走统一响应包装，数量是字符串
TC-BD-02                        ``PATCH`` / ``PUT /lines`` 返回**新的 ``version````
TC-BD-03                        13 个端点齐（tags + summary + 已登记的 ``x-permission``）
TC-BD-04                        未实现的路径不在契约里，HTTP 上**绝不 501**
TC-BD-05                        无权限点 → **403 ``12001``**（前端隐藏不是安全）
TC-BD-06                        越权数据范围 → **403 ``12002``**（详情与日志都拦）
TC-BD-07                        ``/logs`` 只回本单留痕；``available-outputs`` 只读
TC-BD-08                        静态路由段声明在 ``/{order_id}`` **之前**
======================================  ==========================================

⚠️ **六个状态动作在** ``test_bundling_router_actions.py``：单文件 400 行硬线（ADR-0030），
而动作那侧每个用例都要铺一次「建单 → 提交」的世界。合并 = 谁也读不完。

⚠️ **TC-BD-05 / TC-BD-06 是本文件最重要的两条**：漏了 ``_require`` 或 ``assert_in_scope``，
车间主管就能按 ID 打开**别的车间**的打菲单 —— 而列表页看起来一切正常（列表有过滤），
这类越权从日志里完全看不出来。

⚠️ **TC-BD-08 / TC-BD-04 是一对**：守卫把「静态段先于 ``/{order_id}``」立住，T-BUND-007b
才能安全地往这个前缀上加 ``/statistics`` 与 ``/exports``。
"""

from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient

from app.common.enums import DataScope
from app.common.permissions_registry import PERMISSIONS
from tests.factories.bundling import payload as bundling_payload
from tests.factories.bundling_http import API, WRITER_PERMISSIONS, create_order, resolve

ORDER_ID = "00000000-0000-0000-0000-000000000007"
BUNDLE_NO = "BD-20261018-000031-XL01-0001"

#: 六个状态动作的子资源（``08 §1.1`` 动作表 → 复数子路径，``05 §2``「单资源动作不用动词」）
ACTIONS: tuple[str, ...] = (
    "submissions",
    "approvals",
    "rejections",
    "withdrawals",
    "reversals",
    "cancellations",
)

#: 本卡实现的 13 个端点（与 :mod:`app.modules.bundling.router` 一一对应）
IMPLEMENTED: tuple[str, ...] = (
    f"POST {API}",
    f"GET {API}",
    f"GET {API}/{{order_id}}",
    f"PATCH {API}/{{order_id}}",
    f"PUT {API}/{{order_id}}/lines",
    f"GET {API}/{{order_id}}/available-outputs",
    f"GET {API}/{{order_id}}/logs",
    *(f"POST {API}/{{order_id}}/{action}" for action in ACTIONS),
)

#: 归属 T-BUND-007b 的路径：service 侧还没实现，**路由不许存在**。
NOT_IMPLEMENTED: tuple[tuple[str, str], ...] = (
    ("POST", f"{API}/{{order_id}}/split"),
    ("GET", f"{API}/{{order_id}}/hands"),
    ("GET", f"{API}/{{order_id}}/labels"),
    ("POST", f"{API}/{{order_id}}/label-prints"),
    ("GET", "/api/v1/bundles"),
    ("GET", "/api/v1/bundles/{bundle_no}"),
    ("POST", "/api/v1/bundles/{bundle_no}/voids"),
    ("GET", f"{API}/statistics"),
    ("GET", f"{API}/exports"),
)


def _openapi_paths() -> dict[str, dict[str, Any]]:
    from app.main import create_app

    return dict(create_app().openapi()["paths"])


def _route_paths(app: Any) -> list[str]:
    """按**匹配顺序**摊平全部路由路径（带前缀）。

    ⚠️ FastAPI 0.142 起 ``include_router`` 不再把子路由摊进 ``app.routes``，而是塞进
    ``_IncludedRouter`` 包装。直接 ``for r in app.routes`` 拿到的是「一个端点都没有」的
    假象 —— 那类「守卫在跑、其实遍历了空列表」的断言**永远是绿的**。
    """
    out: list[str] = []

    def walk(node: Any) -> None:
        if hasattr(node, "effective_candidates"):
            for child in node.effective_candidates():
                walk(child)
            return
        path = getattr(node, "path", None)
        if path:
            out.append(str(path))

    for route in app.router.routes:
        walk(route)
    return out


# ====================================================================== 读与草稿写


async def test_create_detail_list_are_wrapped(
    client: AsyncClient, auth_headers: Any, bundling_world: dict[str, Any]
) -> None:
    """TC-BD-01：201/200 + ``{code, message, data}``；数量在响应里是**字符串**（05 §3）。"""
    headers = await auth_headers(role="custom", permissions=WRITER_PERMISSIONS)
    created = await create_order(client, headers, bundling_world)
    assert created["status"] == "DRAFT"
    assert created["doc_no"].startswith("BD-20261018-"), created["doc_no"]
    assert isinstance(created["output_qty"], str), "数量在响应里一律字符串（05 §3）"
    assert Decimal(created["output_qty"]) == Decimal("60")

    detail = await client.get(f"{API}/{created['id']}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["code"] == 0
    assert len(detail.json()["data"]["lines"]) == 1

    listing = await client.get(f"{API}?status=DRAFT&page=1&size=20", headers=headers)
    assert listing.status_code == 200
    data = listing.json()["data"]
    assert set(data) == {"items", "total", "page", "page_size"}
    assert data["total"] == 1
    assert "lines" not in data["items"][0], "列表省掉明细"


async def test_patch_and_put_lines_return_new_version(
    client: AsyncClient, auth_headers: Any, bundling_world: dict[str, Any]
) -> None:
    """TC-BD-02：两个写端点的响应都带**写完之后**的 ``version``（前端要拿它继续传）。"""
    headers = await auth_headers(role="custom", permissions=WRITER_PERMISSIONS)
    created = await create_order(client, headers, bundling_world)
    line = created["lines"][0]

    patched = await client.patch(
        f"{API}/{created['id']}",
        json={"version": created["version"], "remark": "改备注"},
        headers=headers,
    )
    assert patched.status_code == 200, patched.text
    after_patch = patched.json()["data"]
    assert after_patch["version"] == created["version"] + 1
    assert after_patch["remark"] == "改备注"

    replaced = await client.put(
        f"{API}/{created['id']}/lines",
        json={
            "version": after_patch["version"],
            "items": [
                {
                    "line_no": 1,
                    "color_code": line["color_code"],
                    "size_code": line["size_code"],
                    "operation_no": line["operation_no"],
                    "cutting_size_line_id": line["cutting_size_line_id"],
                    "hands": 2,
                }
            ],
        },
        headers=headers,
    )
    assert replaced.status_code == 200, replaced.text
    after_put = replaced.json()["data"]
    assert after_put["version"] == after_patch["version"] + 1
    assert after_put["hands_total"] == 2
    assert len(after_put["lines"]) == 1, "响应里不能混进软删的旧行"

    stale = await client.patch(
        f"{API}/{created['id']}",
        json={"version": created["version"], "remark": "用旧版本号"},
        headers=headers,
    )
    assert stale.status_code == 409 and stale.json()["code"] == 10003, "旧版本号必须被拒"


async def test_logs_and_available_outputs(
    client: AsyncClient, auth_headers: Any, bundling_world: dict[str, Any]
) -> None:
    """TC-BD-07：``/logs`` 只回本单留痕（倒序）；``available-outputs`` 只读、余量 0 也返回。"""
    headers = await auth_headers(role="custom", permissions=WRITER_PERMISSIONS)
    created = await create_order(client, headers, bundling_world)

    logs = await client.get(f"{API}/{created['id']}/logs", headers=headers)
    assert logs.status_code == 200, logs.text
    assert [row["action"] for row in logs.json()["data"]["items"]] == ["CREATE"]

    outputs = await client.get(f"{API}/{created['id']}/available-outputs", headers=headers)
    assert outputs.status_code == 200, outputs.text
    rows = outputs.json()["data"]
    assert rows, "来源裁剪单的尺码明细行必须返回"
    assert all(row["available_qty"] == "0" for row in rows), "没补结转行时余量恒为 0"
    again = await client.get(f"{API}/{created['id']}", headers=headers)
    assert again.json()["data"]["version"] == created["version"], "只读端点不许改 version"


# ====================================================================== 契约


def test_contract_has_exactly_thirteen_endpoints() -> None:
    """TC-BD-03：13 个端点齐，且每个都带 ``tags=打菲`` + ``summary`` + 已登记 ``x-permission``。"""
    known = {item.code for item in PERMISSIONS}
    seen: list[str] = []
    for path, methods in _openapi_paths().items():
        if not path.startswith(API):
            continue
        for method, op in methods.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            assert "打菲" in op.get("tags", []), f"{method.upper()} {path} 缺 tags=打菲"
            assert op.get("summary"), f"{method.upper()} {path} 缺 summary（05 §6）"
            permission = op.get("x-permission")
            assert permission, f"{method.upper()} {path} 缺 x-permission"
            assert permission in known, f"{permission!r} 不在 07 §2.2 登记过"
            seen.append(f"{method.upper()} {path}")
    assert sorted(seen) == sorted(IMPLEMENTED), f"端点数 {len(seen)} ≠ {len(IMPLEMENTED)}：{seen}"


def test_unimplemented_paths_absent_from_contract() -> None:
    """TC-BD-04：T-BUND-007b 的 9 个路径**不在 OpenAPI 里**（路由不存在，不是占位）。"""
    paths = set(_openapi_paths())
    for _method, template in NOT_IMPLEMENTED:
        assert resolve(template, ORDER_ID, BUNDLE_NO) not in paths, (
            f"{template} 已注册 —— 本卡不该注册 007b 的端点"
        )


@pytest.mark.parametrize(("method", "template"), NOT_IMPLEMENTED)
async def test_unimplemented_endpoint_is_not_501(
    client: AsyncClient, auth_headers: Any, method: str, template: str
) -> None:
    """TC-BD-04：未实现的路径 HTTP 上是 404，**绝不 501**。

    ⚠️ 允许 422 的只有**顶层静态段**（``/statistics`` ``/exports``）：它们会被
    ``/{order_id}`` 抢先匹配上，UUID 解析失败正是「**没有专门的路由**」的证据。
    真要加这两个端点时必须声明在 ``/{order_id}`` **之前**（TC-BD-08）。
    """
    headers = await auth_headers(role="super_admin")
    response = await client.request(method, resolve(template, ORDER_ID, BUNDLE_NO), headers=headers)
    assert response.status_code in (404, 422), f"{template} 返回 {response.status_code}"
    assert response.status_code != 501, "不写「501 未实现」占位（占位会让前端以为它存在）"


def test_static_routes_precede_the_id_route() -> None:
    """TC-BD-08：顶层静态段必须排在 ``/{order_id}`` **之前**（T-CUT-001c-1 的教训）。

    ⚠️ FastAPI 按**注册顺序**匹配。``/statistics`` 一旦排在 ``/{order_id}`` 之后，
    永远被 UUID 解析挡掉（422），而 OpenAPI 里**照样列着它** —— 契约说有、实际调不到，
    是最难查的一类缺陷。本卡顶层只有空路径（集合根），先把规则立住给 007b 用。
    """
    from app.main import create_app

    # 集合根 `/api/v1/bundling-orders` 后面**没有**段，取 ``[len(API):]`` 的首段（空串）
    segments = [
        path[len(API) :].strip("/").split("/")[0] if path[len(API) :].strip("/") else ""
        for path in _route_paths(create_app())
        if path.startswith(API)
    ]
    assert "{order_id}" in segments, "守卫本身失效：没抓到 {order_id} 路由"
    first_param = segments.index("{order_id}")
    for i, segment in enumerate(segments):
        if not segment.startswith("{"):
            assert i < first_param, f"静态段 {segment} 注册在 {{order_id}} 之后，永远命中不了"


# ====================================================================== 权限与数据范围


@pytest.mark.parametrize(
    ("method", "tail", "body"),
    [
        ("POST", "", None),
        ("PATCH", "/{order_id}", {"version": 1}),
        ("POST", "/{order_id}/approvals", None),
        ("POST", "/{order_id}/cancellations", {"cancelled_reason": "x"}),
        ("POST", "/{order_id}/reversals", {"reason": "x"}),
    ],
)
async def test_write_endpoints_require_permission(
    client: AsyncClient,
    auth_headers: Any,
    bundling_world: dict[str, Any],
    method: str,
    tail: str,
    body: dict[str, Any] | None,
) -> None:
    """TC-BD-05：只有 ``bundling:read`` 时，五个写端点一律 **403 ``12001``**。

    ⚠️ ``POST`` 建单**必须给合法 body**：FastAPI 先校验请求体、再进函数体，所以缺字段时
    返回 422（入参不合法）而不是 403（没权限）。用空 body 去测「有没有权限」测的是校验顺序，
    而这条用例要测的是「权限校验兜不兜得住」（``03 §2.1`` 第 8 条）。
    """
    headers = await auth_headers(role="custom", permissions=("bundling:read",))
    path = resolve(API + tail, ORDER_ID, BUNDLE_NO)
    payload = body
    if tail == "":
        payload = bundling_payload(bundling_world).model_dump(mode="json")
    response = await client.request(method, path, json=payload, headers=headers)
    assert response.status_code == 403, response.text
    assert response.json()["code"] == 12001
    assert response.json()["details"]["required_permission"], "错误里要写明缺哪个权限点"


async def test_detail_and_logs_reject_out_of_scope(
    client: AsyncClient, auth_headers: Any, bundling_world: dict[str, Any]
) -> None:
    """TC-BD-06：范围外的单据 → **403 ``12002``**，详情与日志都拦。

    ⚠️ 两者都要验：日志是单据的一部分，只拦详情的话，界面看不到单据却能读到
    「这张单被谁驳回过」。
    """
    admin = await auth_headers(role="custom", permissions=WRITER_PERMISSIONS)
    created = await create_order(client, admin, bundling_world)
    outsider = await auth_headers(
        role="custom", permissions=("bundling:read",), data_scope=DataScope.WORKSHOP
    )
    for path in (f"{API}/{created['id']}", f"{API}/{created['id']}/logs"):
        response = await client.get(path, headers=outsider)
        assert response.status_code == 403, path
        assert response.json()["code"] == 12002, path
