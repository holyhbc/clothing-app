"""打菲单接口层的**六个状态动作**测试（T-BUND-007a）。

======================================  ==========================================
TC-BD-09                        提交 → 审核成功（制单人 ≠ 审核人），响应带新 ``version``
TC-BD-10                        制单人自审 → ``10005``，一个码都不生成
TC-BD-11                        审核重复 ``Idempotency-Key`` **只生效一次**（05 §5）
TC-BD-12                        驳回 / 反审核 / 作废必填原因：缺失 ``10001``、空白 ``10002``
TC-BD-13                        撤回仅制单人；作废进终态，再改报 ``30001``
======================================  ==========================================

⚠️ 单独成文件而不是并进 ``test_bundling_router.py``：单文件 400 行硬线（ADR-0030），
而这里每个用例都要铺一次「建单 → 提交」的世界。

⚠️ **TC-BD-12 刻意验两个不同的码**：「字段没传」由 schema 层拦（``10001``，与本仓所有
写端点一致，见 ``base/router/dict_handlers`` 的停用端点），「传了但是空白」由 service 的
``require_reason`` 报 ``10002``（03 §9）。两层不是重复：一个管「有没有有没有」，一个管
「有没有认真填」。

⚠️ **TC-BD-11 的三个断言缺一不可**：只断言「两次响应一样」，在「第二次也真跑了、只是被
状态机挡掉」的实现下**也会绿** —— 所以必须同时验**只写了一条 APPROVE 日志**与**只生成
一手码**。
"""

from typing import Any
from uuid import uuid4

import pytest
from httpx import AsyncClient

from app.common.enums import DataScope
from tests.factories.bundling_approve import order_codes, order_logs
from tests.factories.bundling_http import API, WRITER_PERMISSIONS, submitted_order

#: 审核人权限点（与制单人刻意分开，见工厂的注释）。
#:
#: ⚠️ 第二个用户必须显式给 ``DataScope.FACTORY``：``role="custom"`` 的默认范围是
#: **SELF**（只看得见自己建的），而审核 / 作废是「别人建的单」—— 不给全厂范围的话，
#: 这些用例全部撞 ``12002``，报错指向数据范围、与本条要验的东西毫无关系。
CHECKER = ("bundling:read", "bundling:approve")


async def _maker(auth_headers: Any, extra: tuple[str, ...] = ()) -> dict[str, str]:
    """制单人的请求头。

    ⚠️ **必须整个用例复用同一个 token**：``withdraw`` 判的是「撤回人 = 制单人」
    （08 §1.1），而 ``role="custom"`` 的默认数据范围是 **SELF**（只看得见自己建的）。
    每次新建一个用户会同时踩这两条 —— 前者报 ``12001``、后者报 ``12002``，
    两个都与「这条用例想验的东西」无关。
    """
    return await auth_headers(role="custom", permissions=(*WRITER_PERMISSIONS, *extra))


# ====================================================================== 审核


async def test_submit_then_approve(
    client: AsyncClient, db_session: Any, auth_headers: Any, bundling_world: dict[str, Any]
) -> None:
    """TC-BD-09：提交 → 审核（**制单人 ≠ 审核人**），响应带新 ``version`` 与码数。"""
    submitted = await submitted_order(
        client, db_session, await _maker(auth_headers), bundling_world
    )
    assert submitted["status"] == "SUBMITTED"
    assert submitted["version"] > 1, "提交是一次迁移，版本号必须涨"

    checker = await auth_headers(
        role="custom", permissions=CHECKER, data_scope=DataScope.FACTORY, employee_no="A002"
    )
    approved = await client.post(f"{API}/{submitted['id']}/approvals", headers=checker)
    assert approved.status_code == 200, approved.text
    data = approved.json()["data"]
    assert data["status"] == "APPROVED"
    assert data["version"] == submitted["version"] + 1, "响应必须带写完之后的新版本号"
    assert data["hands_total"] == 1, "码数 = 手数（03 §5.3 断言）"
    assert len(await order_codes(db_session, submitted["id"])) == 1


async def test_self_approval_is_forbidden(
    client: AsyncClient, db_session: Any, auth_headers: Any, bundling_world: dict[str, Any]
) -> None:
    """TC-BD-10：制单人自审 → **409 ``10005``**，一个码都不生成。"""
    both = await auth_headers(role="custom", permissions=(*WRITER_PERMISSIONS, "bundling:approve"))
    submitted = await submitted_order(client, db_session, both, bundling_world)
    response = await client.post(f"{API}/{submitted['id']}/approvals", headers=both)
    assert response.status_code == 409, response.text
    assert response.json()["code"] == 10005
    assert await order_codes(db_session, submitted["id"]) == []


async def test_approve_idempotency_key_applies_once(
    client: AsyncClient, db_session: Any, auth_headers: Any, bundling_world: dict[str, Any]
) -> None:
    """TC-BD-11：同 ``Idempotency-Key`` 重复审核**只生效一次**（05 §5）。

    ⚠️ 同键**不同 body** → ``10002``（业务冲突，人要介入）；而「同键同 body」返回的是
    首次结果、**不是错误** —— 两件事禁止混淆（05 §5 的原表）。
    """
    maker = await _maker(auth_headers)
    submitted = await submitted_order(client, db_session, maker, bundling_world)
    checker = await auth_headers(
        role="custom", permissions=CHECKER, data_scope=DataScope.FACTORY, employee_no="A003"
    )
    key = f"approve-{uuid4().hex}"
    keyed = {**checker, "Idempotency-Key": key}

    first = await client.post(f"{API}/{submitted['id']}/approvals", headers=keyed)
    assert first.status_code == 200, first.text
    second = await client.post(f"{API}/{submitted['id']}/approvals", headers=keyed)
    assert second.status_code == 200, second.text
    assert second.json()["data"] == first.json()["data"], "幂等命中必须返回首次结果"

    logs = [row.action for row in await order_logs(db_session, submitted["id"])]
    assert logs.count("APPROVE") == 1, logs
    assert len(await order_codes(db_session, submitted["id"])) == 1, "只生成一手码"

    conflict = await client.post(
        f"{API}/{submitted['id']}/approvals", json={"remark": "同键不同 body"}, headers=keyed
    )
    assert conflict.status_code == 400 and conflict.json()["code"] == 10002


# ====================================================================== 驳回 / 撤回 / 作废


@pytest.mark.parametrize(
    ("suffix", "field", "prepare"),
    [
        ("rejections", "reason", "submitted"),
        ("cancellations", "cancelled_reason", "withdrawn"),
        ("reversals", "reason", "approved"),
    ],
)
async def test_reason_required_at_schema_layer(
    client: AsyncClient,
    db_session: Any,
    auth_headers: Any,
    bundling_world: dict[str, Any],
    suffix: str,
    field: str,
    prepare: str,
) -> None:
    """TC-BD-12：必填原因缺失 → **422 ``10001``**（schema 层，L-098）；空白 → ``10002``。

    ⚠️ 三个动作都验，且都断言**不落库**：它们各自带释放预占 / 改终态 / 作废码的副作用，
    「校验没过却已经改了数据」是这类接口最贵的缺陷。
    """
    order = await _prepare(client, db_session, auth_headers, bundling_world, prepare)
    headers = await auth_headers(
        role="custom",
        permissions=(
            *WRITER_PERMISSIONS,
            "bundling:reverse",
            "bundling:cancel",
            "bundling:approve",
        ),
    )
    url = f"{API}/{order['id']}/{suffix}"

    missing = await client.post(url, json={}, headers=headers)
    assert missing.status_code == 422, missing.text
    assert missing.json()["code"] == 10001
    assert missing.json()["details"]["fields"][0]["field"] == field

    blank = await client.post(url, json={field: "   "}, headers=headers)
    assert blank.status_code == 400, blank.text
    assert blank.json()["code"] == 10002, "空白原因由 service 的 require_reason 拦（03 §9）"

    before = await order_logs(db_session, order["id"])
    assert not [row for row in before if row.action in ("REJECT", "CANCEL", "REVERSE")], (
        "校验没过不许留下动作日志"
    )


async def test_withdraw_requires_creator_and_cancel_is_terminal(
    client: AsyncClient, db_session: Any, auth_headers: Any, bundling_world: dict[str, Any]
) -> None:
    """TC-BD-13：撤回仅制单人（他人 → ``12002``）；作废进终态，再改报 ``30001``。"""
    maker = await _maker(auth_headers, ("bundling:withdraw",))
    submitted = await submitted_order(client, db_session, maker, bundling_world)

    outsider = await auth_headers(
        role="custom", permissions=("bundling:withdraw",), data_scope=DataScope.FACTORY
    )
    denied = await client.post(f"{API}/{submitted['id']}/withdrawals", headers=outsider)
    assert denied.status_code == 403 and denied.json()["code"] == 12001, denied.text
    assert "制单人" in denied.json()["message"]

    withdrawn = await client.post(f"{API}/{submitted['id']}/withdrawals", headers=maker)
    assert withdrawn.status_code == 200, withdrawn.text
    body = withdrawn.json()["data"]
    assert body["status"] == "DRAFT"
    assert body["version"] == submitted["version"] + 1

    canceller = await auth_headers(
        role="custom",
        permissions=("bundling:read", "bundling:cancel"),
        data_scope=DataScope.FACTORY,
    )
    cancelled = await client.post(
        f"{API}/{body['id']}/cancellations",
        json={"cancelled_reason": "重复建单"},
        headers=canceller,
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["data"]["status"] == "CANCELLED"
    again = await client.patch(
        f"{API}/{body['id']}",
        json={"version": cancelled.json()["data"]["version"], "remark": "还想改"},
        headers=maker,
    )
    assert again.status_code == 409 and again.json()["code"] == 30001, "终态不许再改"


async def _prepare(
    client: AsyncClient,
    db_session: Any,
    auth_headers: Any,
    world: dict[str, Any],
    how: str,
) -> dict[str, Any]:
    """把单据推到 ``how`` 指定的状态（``submitted`` / ``withdrawn`` / ``approved``）。"""
    extra = ("bundling:withdraw",) if how == "withdrawn" else ()
    maker = await _maker(auth_headers, extra)
    order = await submitted_order(client, db_session, maker, world)
    if how == "submitted":
        return order
    url = (
        f"{API}/{order['id']}/withdrawals"
        if how == "withdrawn"
        else f"{API}/{order['id']}/approvals"
    )
    headers = (
        maker
        if how == "withdrawn"
        # 审核要「制单人 ≠ 审核人」：自审会撞 10005，这条用例就变成测自审了
        else await auth_headers(
            role="custom", permissions=CHECKER, data_scope=DataScope.FACTORY, employee_no="A004"
        )
    )
    response = await client.post(url, headers=headers)
    assert response.status_code == 200, response.text
    return dict(response.json()["data"])
