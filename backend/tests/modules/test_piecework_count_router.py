"""``POST /piecework/count`` 的 HTTP 层（权限、幂等键、响应包装）。

⚠️ **单开文件**：这些用例走真实登录 token（``auth_headers``），而规则用例
（``test_piecework_count.py``）直调 service。混在一起时，service 层的一个断言失败
和「token 没带对权限」会长得一模一样。

======================================  ==========================================
TC-PW-R01                             无 ``piecework:count`` → ``12001``
TC-PW-R02                             正常 → ``code=0`` + ``data.qty`` 是字符串
TC-PW-R03                             ★ ``Idempotency-Key``：同键同 body → 回首次结果
TC-PW-R04                             ★ 同键**不同 body** → ``10002``
TC-PW-R05                             请求体多传字段 → ``10001``（``extra="forbid"``）
======================================  ==========================================
"""

from datetime import UTC, datetime
from typing import Any

from app.common.enums import DataScope
from app.core.numbering import BUSINESS_TZ
from tests.factories.piecework_world import ctx_for

API = "/api/v1/piecework/count"


async def _headers(auth_headers: Any, world: dict[str, Any], **kw: Any) -> dict[str, str]:
    """⚠️ **必须给 WORKSHOP 范围 + 对应车间**：本接口的数据范围是 ``WORKSHOP``
    （04-计件 §6.1：工位机代员工计件，看的是本车间）。用默认的 SELF 会撞 ``12002``，
    而那条报错说的是「超出数据范围」—— 与「你到底能不能计件」毫无关系，
    报错指向的方向会把人带到数据范围那一章去查半天。"""
    return await auth_headers(
        role="custom",
        permissions=("piecework:count",),
        data_scope=DataScope.WORKSHOP,
        workshop_id=world["workshop_id"],
        **kw,
    )


def _body(world: dict[str, Any], **over: Any) -> dict[str, Any]:
    return {
        "bundle_no": world["bundle_no"],
        "employee_id": str(world["employee"].id),
        "scanned_at": datetime.now(tz=UTC).isoformat(),
        **over,
    }


async def test_tc_pw_r01_permission_denied(client, auth_headers, piecework_world) -> None:
    """「前端隐藏不是安全」（03 §2.1 第 8 条）：没权限点就 401/403，与有没有传 body 无关。"""
    headers = await auth_headers(
        role="custom",
        permissions=("piecework:read",),
        data_scope=DataScope.WORKSHOP,
        workshop_id=piecework_world["workshop_id"],
    )
    response = await client.post(API, json=_body(piecework_world), headers=headers)
    assert response.status_code == 403
    assert response.json()["code"] == 12001


async def test_tc_pw_r02_success_shape(client, auth_headers, piecework_world) -> None:
    headers = await _headers(auth_headers, piecework_world)
    response = await client.post(API, json=_body(piecework_world), headers=headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["code"] == 0
    data = payload["data"]
    # ⚠️ 05 §3：数量与金额在 JSON 里是**字符串**。这条若变成数字，
    # 前端的金额累加就会开始出现 25.199999999999996。
    for field in ("qty", "bundle_qty", "unit_price", "amount", "today_qty", "today_amount"):
        assert isinstance(data[field], str), f"{field} 必须是字符串，实际 {type(data[field])}"
    assert data["hands"] == 1 and data["hands_total_of_size"] == 2
    assert data["today_hands"] == 1
    assert data["work_date"] == datetime.now(tz=BUSINESS_TZ).date().isoformat()
    assert data["idempotent"] is False


async def test_tc_pw_r03_idempotency_key_returns_first_result(
    client, auth_headers, piecework_world
) -> None:
    """05 §5：同键同 body → HTTP 200 且返回**首次结果**（不是 409、也不是重新算一遍）。

    ⚠️ 第二次必须连 ``today_amount`` 都一样：若 service 又跑了一遍，金额会翻倍，
    而工位机上「今日本站」显示翻倍是工人最敏感的问题。
    """
    headers = {**(await _headers(auth_headers, piecework_world)), "Idempotency-Key": "PW-R03-KEY-1"}
    # ⚠️ **同一个 body 对象**发两次：``_body`` 每次都取新的 ``now()``，
    # 两次调用出来的 JSON 不一样 → 那是「同键不同 body」，正确结果是 10002
    # （由 TC-PW-R04 验）。这里要验的是「同键同 body 回首次结果」。
    body = _body(piecework_world)
    first = await client.post(API, json=body, headers=headers)
    second = await client.post(API, json=body, headers=headers)
    assert first.status_code == 200 and second.status_code == 200
    assert second.json()["data"]["log_id"] == first.json()["data"]["log_id"]
    assert second.json()["data"]["today_amount"] == first.json()["data"]["today_amount"]


async def test_tc_pw_r04_same_key_different_body_rejected(
    client, auth_headers, piecework_world
) -> None:
    """同键不同 body → ``10002``。⚠️ 这条拦的是「复用了一个还在缓存里的键，
    却想改成另一个码/另一个人」—— 不拦的话幂等缓存会返回**别人的**扫码结果。"""
    headers = {**(await _headers(auth_headers, piecework_world)), "Idempotency-Key": "PW-R04-KEY-1"}
    first = await client.post(API, json=_body(piecework_world), headers=headers)
    assert first.status_code == 200
    other = _body(piecework_world, qty="28")
    second = await client.post(API, json=other, headers=headers)
    assert second.status_code == 400
    assert second.json()["code"] == 10002


async def test_tc_pw_r05_extra_field_rejected(client, auth_headers, piecework_world) -> None:
    """``extra="forbid"``：多传 ``employee_no`` 这类字段直接 10001。

    ⚠️ 这不只是洁癖：``employee_no`` 与 ``employee_id`` 长得像，一旦被当成同一个字段
    接下，工位机上就能传 A 的号记 B 的工钱 —— 而流水里 ``employee_id`` 是服务端写的，
    两边对不上时**只有数据能发现**。"""
    headers = await _headers(auth_headers, piecework_world)
    response = await client.post(
        API, json=_body(piecework_world, employee_no="A002"), headers=headers
    )
    assert response.status_code == 422
    assert response.json()["code"] == 10001


async def test_tc_pw_r06_operator_is_not_employee(client, auth_headers, piecework_world) -> None:
    """工位机代员工：``ctx.user_id``（组长）≠ ``employee_id``（员工）。

    ⚠️ 守的是「谁的 token 决定了权限、谁被记成干活的人」这条分界 ——
    两者写成同一个的话，员工端按 token 取 ``employee_id`` 的规则就与工位机端
    长得一样，而那正是 Q-PW09 要在阶段二关掉的入口。"""
    headers = await _headers(auth_headers, piecework_world)
    response = await client.post(API, json=_body(piecework_world), headers=headers)
    assert response.status_code == 200, response.text
    ctx = ctx_for()
    assert ctx.user_id != piecework_world["employee"].id
