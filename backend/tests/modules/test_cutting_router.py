"""裁剪单接口层测试（T-CUT-001c-1）。

============================  ===============================================
TC-C01c-01                   建单 201 + 响应包装 ``code=0``（``05 §3``）
TC-C01c-02                   详情返回**三层结构**
TC-C01c-03                   列表分页 ``data.items/total/page/page_size``
TC-C01c-04                   无 ``cutting:create`` → **403**
TC-C01c-05                   越权按 ID 取单 → **403** ``12002``（``07 §3.2`` 铁律 2）
TC-C01c-06                   ``version`` 缺失 → 422 ``10001``
TC-C01c-07                   三条 PUT 都要求 ``version``
TC-C01c-08                   路径参数非法 UUID → 422（不是 500）
TC-C01c-09                   每个端点都带 ``tags=裁剪`` 与 ``summary``
TC-C01c-10                   每个端点都带 ``x-permission`` 且在 ``07 §2.2`` 登记过
TC-C01c-11                   **未实现的端点不占位**（404 而不是 500/501）
============================  ===============================================

⚠️ **TC-C01c-05 是本模块最重要的一条**：漏了 ``assert_in_scope`` 的话，车间主管能按
ID 打开**别的车间**的裁剪单 —— 而列表页看起来一切正常（列表有过滤）。这类越权
从日志里完全看不出来。

⚠️ **TC-C01c-11 守的是一个刻意的决定**：本卡**不**为状态机端点
（``/submissions`` ``/approvals`` …）加「501 未实现」占位。占位的危害是前端会以为
那个接口存在、OpenAPI 里列了它、E2E 里看到 501 而不是 404 —— 而「这个功能还没做」
的正确表达是**路由不存在**。
"""

from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient

from app.common.enums import DataScope
from app.common.permissions_registry import PERMISSIONS
from tests.factories.cutting import payload as cutting_payload

API = "/api/v1/cutting-orders"

#: 本卡实现的端点（方法 + 路径模板）。**刻意不含**状态机那些 —— 见 TC-C01c-11。
IMPLEMENTED: tuple[tuple[str, str], ...] = (
    ("POST", API),
    ("GET", API),
    ("GET", API + "/{order_id}"),
    ("PATCH", API + "/{order_id}"),
    ("DELETE", API + "/{order_id}"),
    ("PUT", API + "/{order_id}/lines"),
    ("PUT", API + "/{order_id}/lines/{line_id}/colors"),
    ("PUT", API + "/{order_id}/size-lines"),
    ("GET", API + "/{order_id}/suggest-lines"),
    ("POST", API + "/{order_id}/entry-mode"),
)

#: 状态机端点：service 里还没有（T-CUT-001b-3），**路由也不该存在**
NOT_IMPLEMENTED: tuple[str, ...] = (
    f"{API}/{{order_id}}/submissions",
    f"{API}/{{order_id}}/approvals",
    f"{API}/{{order_id}}/rejections",
    f"{API}/{{order_id}}/withdrawals",
    f"{API}/{{order_id}}/reversals",
    f"{API}/{{order_id}}/cancellations",
    f"{API}/{{order_id}}/logs",
    f"{API}/imports/cutting-orders",
    f"{API}/exports",
    f"{API}/statistics",
)


async def _create(
    client: AsyncClient, headers: dict[str, str], w: dict[str, Any]
) -> dict[str, Any]:
    body = cutting_payload(w).model_dump(mode="json")
    response = await client.post(API, json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["data"]


# ====================================================================== 正常路径


async def test_create_returns_wrapped_response(
    client: AsyncClient, auth_headers: Any, cutting_world: dict[str, Any]
) -> None:
    """TC-C01c-01：201 + ``{code, message, data}`` 包装。"""
    headers = await auth_headers(role="custom", permissions=("cutting:create",))
    response = await client.post(
        API, json=cutting_payload(cutting_world).model_dump(mode="json"), headers=headers
    )
    assert response.status_code == 201
    body = response.json()
    assert body["code"] == 0
    data = body["data"]
    assert data["doc_no"].startswith("CT-20261018-"), data["doc_no"]
    assert data["status"] == "DRAFT"
    # ⚠️ 数量在响应里一律**字符串**（docs/05 §3）：JS 的 number 表示不了 numeric
    assert isinstance(data["fabric_qty"], str)
    assert Decimal(data["balance_qty"]) == Decimal("60")


async def test_detail_returns_three_levels(
    client: AsyncClient, auth_headers: Any, cutting_world: dict[str, Any]
) -> None:
    """TC-C01c-02：详情含 ``lines[].colors[].size_lines[]``。"""
    headers = await auth_headers(role="custom", permissions=("cutting:create", "cutting:read"))
    created = await _create(client, headers, cutting_world)
    response = await client.get(f"{API}/{created['id']}", headers=headers)
    assert response.status_code == 200
    lines = response.json()["data"]["lines"]
    assert len(lines) == 1
    assert len(lines[0]["colors"]) == 1
    size_lines = lines[0]["colors"][0]["size_lines"]
    assert size_lines[0]["output_qty"] == "60", "件数是 integer，但响应仍是字符串（05 §3）"
    assert size_lines[0]["balance_qty"] == "0"


async def test_list_is_paged(
    client: AsyncClient, auth_headers: Any, cutting_world: dict[str, Any]
) -> None:
    """TC-C01c-03：分页响应结构 ``items/total/page/page_size``。"""
    headers = await auth_headers(role="custom", permissions=("cutting:create", "cutting:read"))
    await _create(client, headers, cutting_world)
    response = await client.get(API, headers=headers)
    assert response.status_code == 200
    data = response.json()["data"]
    assert set(data) >= {"items", "total", "page", "page_size"}
    assert data["total"] == 1
    assert data["page"] == 1
    # ⚠️ 列表**省掉三层明细** —— 一次加载三层会让「取 20 张单」变成上万行
    assert "lines" not in data["items"][0]


# ====================================================================== 权限与数据范围


async def test_create_requires_permission(
    client: AsyncClient, auth_headers: Any, cutting_world: dict[str, Any]
) -> None:
    """TC-C01c-04：没有 ``cutting:create`` → **403**。

    ⚠️ 「前端隐藏按钮」不是安全（``docs/03 §2.1`` 第 8 条）。这条断言确保后端真的兜住了。
    """
    headers = await auth_headers(role="custom", permissions=("cutting:read",))
    response = await client.post(
        API, json=cutting_payload(cutting_world).model_dump(mode="json"), headers=headers
    )
    assert response.status_code == 403
    assert response.json()["code"] == 12001


async def test_detail_rejects_out_of_scope(
    client: AsyncClient, auth_headers: Any, cutting_world: dict[str, Any]
) -> None:
    """TC-C01c-05：数据范围外的单据 → **403** ``12002``。"""
    admin = await auth_headers(role="custom", permissions=("cutting:create", "cutting:read"))
    created = await _create(client, admin, cutting_world)

    # 一个**看不到任何车间**的车间主管
    outsider = await auth_headers(
        role="custom", permissions=("cutting:read",), data_scope=DataScope.WORKSHOP
    )
    response = await client.get(f"{API}/{created['id']}", headers=outsider)
    assert response.status_code == 403
    assert response.json()["code"] == 12002


async def test_list_cannot_widen_scope_by_workshop_id(
    client: AsyncClient, auth_headers: Any, cutting_world: dict[str, Any]
) -> None:
    """传 ``workshop_id`` **不能放大范围**（``07 §3.2`` 铁律 1）。

    ⚠️ 车间主管传**别的车间**的 id，依然必须查不到 —— 否则「参数可控的范围」
    就是一个越权入口。
    """
    admin = await auth_headers(role="custom", permissions=("cutting:create", "cutting:read"))
    await _create(client, admin, cutting_world)

    from uuid import uuid4

    outsider = await auth_headers(
        role="custom", permissions=("cutting:read",), data_scope=DataScope.WORKSHOP
    )
    response = await client.get(f"{API}?workshop_id={uuid4()}", headers=outsider)
    assert response.status_code == 200
    assert response.json()["data"]["total"] == 0


# ====================================================================== 参数校验


async def test_patch_requires_version(
    client: AsyncClient, auth_headers: Any, cutting_world: dict[str, Any]
) -> None:
    """TC-C01c-06：``version`` 缺失 → 422 ``10001``（``docs/05 §4``）。"""
    headers = await auth_headers(role="custom", permissions=("cutting:create", "cutting:update"))
    created = await _create(client, headers, cutting_world)
    response = await client.patch(
        f"{API}/{created['id']}", json={"remark": "改备注"}, headers=headers
    )
    assert response.status_code == 422
    assert response.json()["code"] == 10001


async def test_patch_version_mismatch_is_conflict(
    client: AsyncClient, auth_headers: Any, cutting_world: dict[str, Any]
) -> None:
    """``version`` 不匹配 → **409** ``10003``（不是 422）。"""
    headers = await auth_headers(role="custom", permissions=("cutting:create", "cutting:update"))
    created = await _create(client, headers, cutting_world)
    response = await client.patch(
        f"{API}/{created['id']}",
        json={"version": created["version"] + 9, "remark": "x"},
        headers=headers,
    )
    assert response.status_code == 409
    assert response.json()["code"] == 10003


@pytest.mark.parametrize(
    ("path", "method"),
    [
        (f"{API}/{{order_id}}/lines", "put"),
        (f"{API}/{{order_id}}/lines/{{line_id}}/colors", "put"),
        (f"{API}/{{order_id}}/size-lines", "put"),
        (f"{API}/{{order_id}}/entry-mode", "post"),
        (f"{API}/{{order_id}}", "patch"),
    ],
)
def test_write_endpoints_require_version(path: str, method: str) -> None:
    """TC-C01c-07：五个写端点的请求体里 ``version`` 是**必填**。

    ⚠️ 验的是 **OpenAPI 契约**而不是发请求发来的 422：前端类型就是从
    ``openapi.json`` 生成的（``docs/03 §2.1`` 第 3 条），所以「``version``
    在契约里是必填」才是前端**知道要带**它的依据。只有一个 422 的话，
    前端拿不到这个信号。
    """
    from app.main import create_app

    spec = create_app().openapi()
    operation = spec["paths"][path][method]
    schema_name = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    schema = spec["components"]["schemas"][schema_name.rsplit("/", 1)[-1]]
    assert "version" in schema.get("required", []), (
        f"{method.upper()} {path} 的请求体里 version 不是必填 —— "
        f"required={schema.get('required')}。前端会不知道要带乐观锁版本号"
    )


async def test_invalid_uuid_path_is_422(client: AsyncClient, auth_headers: Any) -> None:
    """TC-C01c-08：路径参数非法 UUID → **422**，不是 500。

    ⚠️ 自己解析 UUID 的话会抛 ``ValueError`` → 500，而 ``docs/05 §2`` 要求格式错误是 422。
    """
    headers = await auth_headers(role="custom", permissions=("cutting:read",))
    response = await client.get(f"{API}/not-a-uuid", headers=headers)
    assert response.status_code == 422


# ====================================================================== OpenAPI 契约


def test_every_endpoint_has_tag_summary_and_permission() -> None:
    """TC-C01c-09 / 10：``tags=裁剪`` + ``summary`` + ``x-permission`` 且已登记。

    ⚠️ ``x-permission`` 必须在 ``docs/07 §2.2`` 登记过 —— 前端按它生成按钮，
    而「三处不一致视为闸门 1 失败」（``docs/12 §9``）。这里直接查
    :data:`app.common.permissions_registry.PERMISSIONS`，与那个守卫同源。
    """
    import re

    from app.main import create_app

    known = {item.code for item in PERMISSIONS}
    spec = create_app().openapi()
    seen: list[str] = []
    for path, methods in spec["paths"].items():
        if not path.startswith("/api/v1/cutting-orders"):
            continue
        for method, op in methods.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            assert "裁剪" in op.get("tags", []), f"{method.upper()} {path} 缺 tags=裁剪"
            assert op.get("summary"), f"{method.upper()} {path} 缺 summary（docs/05 §6）"
            # ⚠️ `openapi_extra={"x-permission": ...}` 在 OpenAPI 里是**平铺**的一个键
            #    （不是 `x-permission: {x-permission: ...}`）—— 写成两层会拿到 str
            #    再 `.get()` 直接炸，而报错与「权限点没声明」毫无关系
            permission = op.get("x-permission")
            assert permission, f"{method.upper()} {path} 缺 x-permission"
            assert permission in known, (
                f"{method.upper()} {path} 的权限点 {permission!r} 不在 07 §2.2 登记过"
            )
            seen.append(f"{method.upper()} {re.sub(r'\\{[^}]+\\}', '{…}', path)}")
    assert len(seen) == len(IMPLEMENTED), f"实际端点数 {len(seen)}，期望 {len(IMPLEMENTED)}：{seen}"


@pytest.mark.parametrize("path", NOT_IMPLEMENTED)
async def test_unimplemented_endpoint_is_404_not_501(
    client: AsyncClient, auth_headers: Any, path: str
) -> None:
    """TC-C01c-11：未实现的端点**路由不存在** → 404。

    ⚠️ 刻意不写「501 未实现」占位：占位会让前端以为那个接口存在、OpenAPI 里列了它、
    E2E 里看到 501 而不是 404 —— 而「还没做」的正确表达就是**路由不存在**。
    """
    headers = await auth_headers(role="super_admin")
    resolved = path.format(order_id="00000000-0000-0000-0000-000000000009")
    response = await client.get(resolved, headers=headers)
    # ⚠️ 422 也要放过：`/exports`、`/statistics` 会被 `/{order_id}` 匹配上，
    #    然后 UUID 解析失败 —— 那说明**没有专门的路由**，正是本卡要的。
    #    ⚠️ 真要加这两个端点时必须**声明在 `/{order_id}` 之前**，否则永远 422
    #    （FastAPI 的路由匹配顺序问题，不是本卡的缺陷）
    assert response.status_code in (404, 405, 422), (
        f"{resolved} 返回 {response.status_code}，说明有专门的路由存在 —— "
        f"未实现的端点不该有占位路由"
    )


# ====================================================================== 入参形状


def test_order_line_input_rejects_lot_columns() -> None:
    """缸号 / 匹号**不得由前端传**（ADR-0022 级联选料，``extra="forbid"``）。

    ⚠️ 传了会得到 ``10001``（422）而不是「悄悄被忽略」—— 能被忽略的入参是最坏的一种。
    """
    import pydantic

    from app.modules.cutting.schemas import OrderLineIn

    with pytest.raises(pydantic.ValidationError):
        OrderLineIn(
            line_no=1,
            stock_id="00000000-0000-0000-0000-000000000001",
            fabric_qty=Decimal("96"),
            dye_lot_no="DY-1",  # ← 不该由前端传
        )


def test_create_input_has_no_header_totals() -> None:
    """表头汇总五列**不在入参里**（C6 不信任前端）。

    ⚠️ 这条守卫的是「为了让前端能显示点什么」而把汇总加进入参 —— 那会让
    service 的重算被绕过，或者让前端以为它设的值生效了。
    """
    from app.modules.cutting.schemas import CuttingOrderCreateIn

    fields = set(CuttingOrderCreateIn.model_fields)
    for forbidden in ("fabric_qty", "output_qty", "cut_waste_qty", "balance_qty", "hands_total"):
        assert forbidden not in fields


async def test_entry_mode_switch_requires_confirm(
    client: AsyncClient, auth_headers: Any, cutting_world: dict[str, Any]
) -> None:
    """切模式未确认 → **422**，且错误文案要说清「二次确认」。

    ⚠️ 这条经由 HTTP 验，因为「服务端不接受 confirm=false」是接口契约的一部分，
    单元测试只能证明 service 的行为，证明不了契约。
    """
    headers = await auth_headers(role="custom", permissions=("cutting:create", "cutting:update"))
    created = await _create(client, headers, cutting_world)
    color_id = created["lines"][0]["colors"][0]["id"]

    response = await client.post(
        f"{API}/{created['id']}/entry-mode",
        json={
            "version": created["version"],
            "line_color_id": color_id,
            "mode": "MANUAL",
            "confirm": False,
        },
        headers=headers,
    )
    assert response.status_code == 422
    assert "二次确认" in response.json()["message"]


async def test_size_line_input_rejects_fractional_hands(
    client: AsyncClient, auth_headers: Any, cutting_world: dict[str, Any]
) -> None:
    """``hands = 1.5`` → 422（ADR-0020 取消 1.5 手，``30006``）。

    ⚠️ 经 HTTP 验是为了确认 **OpenAPI 契约**里 ``hands`` 是 integer ——
    前端据此知道不能发小数，而不只是服务端会拒。
    """
    headers = await auth_headers(role="custom", permissions=("cutting:create", "cutting:update"))
    created = await _create(client, headers, cutting_world)
    color_id = created["lines"][0]["colors"][0]["id"]
    response = await client.put(
        f"{API}/{created['id']}/size-lines",
        json={
            "version": created["version"],
            "line_color_id": color_id,
            "items": [{"size_code": "L", "hands": 1.5, "qty_per_hand": 60}],
        },
        headers=headers,
    )
    assert response.status_code == 422
