"""``GET /bundles`` 的 ``counted`` 三态过滤 + ``/hands`` 端点归并（T-BUND-007c）。

======================================  ==========================================
TC-30-01                             ``counted=false`` 只回未计件手（``counted_at IS NULL``）
TC-30-02                             ``counted=true`` 只回已计件手
TC-30-03                             非法值（``counted=abc``）→ ``10001``
TC-30-04                             ★ 部分生产的手**不算**未计件（``counted_at`` 非空）
TC-30-05                             默认排除 ``VOIDED``；``status=VOIDED`` 单独取
TC-30-06                             越权：详情 → ``12002``；列表 → 空（经父单回查）
TC-30-07                             ★ ``counted=false`` 的计划走 ``idx_bundles_counted_pending``
======================================  ==========================================

⚠️ **TC-30-04 是最容易搞错的一条**：判「未计件」看 ``counted_at IS NULL``，**不看**
``counted_qty``。只做了 28 件的那一手（``counted_qty=28 < bundle_qty=60``）是**已计件**
的手 —— 它已经进过计件流水、已经算过工钱，再把它列进「还没开始做的手」会让主管
重复安排同一批活。写成 ``counted_qty < bundle_qty`` 的症状是「未计件手清单越查越长，
而那批手其实早就在流水里了」。

⚠️ **TC-30-05 的默认排除**：``VOIDED`` 既不是「已计件」也不是「未计件」—— 算进任何
一边都会让「本单手数 = 已计 + 未计」对不上。要取作废码得显式 ``status=VOIDED``。

⚠️ **TC-30-07 只验「索引用得上」**：测试库里表很小，规划器**本来就会**选顺序扫描，
所以这里 ``SET LOCAL enable_seqscan = off`` 后再看计划里有没有那个索引名。
它验的是**谓词可证**（``deleted_at IS NULL AND status='ACTIVE' AND counted_at IS NULL``
能被规划器认出来与部分索引的 ``indexpred`` 一致），而不是小表上的代价估算 ——
后者由任务卡里那份 ``EXPLAIN`` 记录作证。
"""

from decimal import Decimal
from typing import Any

from httpx import AsyncClient
from sqlalchemy import text

from app.common.enums import DataScope
from tests.factories.bundling_approve import mark_code_counted, order_codes
from tests.factories.bundling_http import approved_order

BUNDLES = "/api/v1/bundles"

#: 审核出 4 手 → 前 2 手模拟计件（**部分生产**：1 < 60）、第 3 手留未计件、第 4 手作废。
#: 这样四种形态一次铺齐，四条过滤用例各自只需断言自己关心的那一半。
HANDS = 4
COUNTED_HANDS = (1, 2)
PENDING_HAND = 3
VOIDED_HAND = 4


async def _reader(auth_headers: Any, extra: tuple[str, ...] = ()) -> dict[str, str]:
    return await auth_headers(
        role="custom",
        permissions=("bundling:read", *extra),
        data_scope=DataScope.FACTORY,
    )


async def _mixed_world(
    client: AsyncClient, db_session: Any, auth_headers: Any, bundling_world: dict[str, Any]
) -> dict[str, Any]:
    """铺一张「4 手 = 2 手已计件（部分）+ 1 手未计件 + 1 手作废」的单。"""
    order = await approved_order(client, db_session, auth_headers, bundling_world, hands=HANDS)
    for hands in COUNTED_HANDS:
        await mark_code_counted(db_session, order["id"], hands)
    codes = {row.hands: row.bundle_no for row in await order_codes(db_session, order["id"])}
    voider = await _reader(auth_headers, ("bundling:code:void",))
    response = await client.post(
        f"{BUNDLES}/{codes[VOIDED_HAND]}/voids",
        json={"void_reason": "TC-30-05：铺一张作废手"},
        headers=voider,
    )
    assert response.status_code == 200, response.text
    return order


async def _hands_of(client: AsyncClient, headers: dict[str, str], **params: Any) -> set[int]:
    """按 ``**params`` 过滤码列表，返回命中的**手号集合**。"""
    response = await client.get(BUNDLES, params=params, headers=headers)
    assert response.status_code == 200, response.text
    page = response.json()["data"]
    return {row["hands"] for row in page["items"]}


# ====================================================================== 契约：三态过滤


async def test_counted_false_returns_only_pending_hands(
    client: AsyncClient, db_session: Any, auth_headers: Any, bundling_world: dict[str, Any]
) -> None:
    """TC-30-01：``counted=false`` 只回未计件手，且每行带「第 N 手 / 共 M 手 / 件数」。"""
    order = await _mixed_world(client, db_session, auth_headers, bundling_world)
    headers = await _reader(auth_headers)

    response = await client.get(
        BUNDLES, params={"doc_id": order["id"], "counted": "false"}, headers=headers
    )
    assert response.status_code == 200, response.text
    page = response.json()["data"]
    assert {row["hands"] for row in page["items"]} == {PENDING_HAND}, page
    assert page["total"] == 1, page
    row = page["items"][0]
    assert row["counted_at"] is None and Decimal(row["counted_qty"]) == 0, row
    # TC-30 明写「每行含 hands、hands_total_of_size、bundle_qty」
    assert row["hands_total_of_size"] == HANDS, row
    assert Decimal(row["bundle_qty"]) == 60, row


async def test_counted_true_returns_only_counted_hands(
    client: AsyncClient, db_session: Any, auth_headers: Any, bundling_world: dict[str, Any]
) -> None:
    """TC-30-02：``counted=true`` 只回已计件手；不传 = 两者之外还有未计件手。"""
    order = await _mixed_world(client, db_session, auth_headers, bundling_world)
    headers = await _reader(auth_headers)

    assert await _hands_of(client, headers, doc_id=order["id"], counted="true") == set(
        COUNTED_HANDS
    )
    assert await _hands_of(client, headers, doc_id=order["id"]) == {
        *COUNTED_HANDS,
        PENDING_HAND,
    }


async def test_counted_rejects_non_boolean(
    client: AsyncClient, db_session: Any, auth_headers: Any, bundling_world: dict[str, Any]
) -> None:
    """TC-30-03：``counted=abc`` → ``10001``（不是 422、不是「当作没传」）。"""
    await _mixed_world(client, db_session, auth_headers, bundling_world)
    headers = await _reader(auth_headers)

    response = await client.get(BUNDLES, params={"counted": "abc"}, headers=headers)
    assert response.status_code == 422, response.text
    assert response.json()["code"] == 10001, response.text


# ====================================================================== 口径：部分生产 / 作废


async def test_partially_produced_hand_is_not_pending(
    client: AsyncClient, db_session: Any, auth_headers: Any, bundling_world: dict[str, Any]
) -> None:
    """TC-30-04：``counted_at`` 非空但 ``counted_qty < bundle_qty`` 的手算**已计件**。"""
    order = await _mixed_world(client, db_session, auth_headers, bundling_world)
    headers = await _reader(auth_headers)

    pending = await _hands_of(client, headers, doc_id=order["id"], counted="false")
    assert pending == {PENDING_HAND}, "部分生产的手被列进未计件清单 → 主管会重复安排同一批活"
    counted = await _hands_of(client, headers, doc_id=order["id"], counted="true")
    assert counted == set(COUNTED_HANDS)


async def test_voided_excluded_by_default_and_fetchable_by_status(
    client: AsyncClient, db_session: Any, auth_headers: Any, bundling_world: dict[str, Any]
) -> None:
    """TC-30-05：默认排除 ``VOIDED``；要取它得显式 ``status=VOIDED``。"""
    order = await _mixed_world(client, db_session, auth_headers, bundling_world)
    headers = await _reader(auth_headers)

    assert VOIDED_HAND not in await _hands_of(client, headers, doc_id=order["id"])
    assert VOIDED_HAND not in await _hands_of(client, headers, doc_id=order["id"], counted="false")
    assert await _hands_of(client, headers, doc_id=order["id"], status="VOIDED") == {VOIDED_HAND}


# ====================================================================== 数据范围


async def test_counted_filter_still_enforces_data_scope(
    client: AsyncClient, db_session: Any, auth_headers: Any, bundling_world: dict[str, Any]
) -> None:
    """TC-30-06：``counted`` 与 ``via`` 范围条件**并列 AND** —— 加了它也不能放大可见范围。"""
    order = await _mixed_world(client, db_session, auth_headers, bundling_world)
    outsider = await auth_headers(
        role="custom",
        permissions=("bundling:read",),
        data_scope=DataScope.WORKSHOP,
        employee_no="A009",
    )
    bundle_no = (await order_codes(db_session, order["id"]))[0].bundle_no

    listing = await client.get(
        BUNDLES, params={"doc_id": order["id"], "counted": "false"}, headers=outsider
    )
    assert listing.status_code == 200
    assert listing.json()["data"]["total"] == 0, "越权车间的未计件码也不出现"
    detail = await client.get(f"{BUNDLES}/{bundle_no}", headers=outsider)
    assert detail.status_code == 403 and detail.json()["code"] == 12002, detail.text


# ====================================================================== 索引（TC-30 明写「命中」）


async def test_pending_query_plan_uses_counted_pending_index(
    db_session: Any, bundling_world: dict[str, Any]
) -> None:
    """TC-30-07：``counted=false`` 的计划能走 ``idx_bundles_counted_pending``。

    ⚠️ ``enable_seqscan=off``：测试库里 ``bundles`` 只有个位数行，规划器必然选顺序扫描，
    而那与「部分索引的谓词能否被证明」无关。这里关掉它，让规划器**只**能选索引；
    若索引因谓词不可证而落选，计划里就不会出现那个名字，用例随即红。
    ⚠️ 用 ``WORKSHOP`` 范围而不是 ``FACTORY``：车间主管才是「未计件手清单」的主要用户，
    而 ``FACTORY`` 不加范围条件、计划里少一次 ``via`` join —— 那不是生产里的形状。
    """
    from sqlalchemy.dialects import postgresql

    from app.modules.bundling.code_repository import BundleListQuery, _scoped_stmt
    from tests.factories.bundling import ctx as bundling_ctx

    stmt = _scoped_stmt(
        bundling_ctx(workshop_id=bundling_world["workshop"].id, scope=DataScope.WORKSHOP)
    ).where(*BundleListQuery(counted=False, size_code="XL").filters())
    sql = str(
        stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    ).replace("\n", " ")
    await db_session.execute(text("SET LOCAL enable_seqscan = off"))
    plan = "\n".join(row[0] for row in (await db_session.execute(text(f"EXPLAIN {sql}"))).all())
    assert "idx_bundles_counted_pending" in plan, f"{sql}\n{plan}"
