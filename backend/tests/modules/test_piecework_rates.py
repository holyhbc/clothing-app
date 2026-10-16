"""``GET /piecework/rates`` 计件单价查询（T-PIECE-009）。

======================================  ==========================================
TC-PW-RT01                            无 ``piecework:rate:manage`` → ``12001``
TC-PW-RT02                            未知款号 → ``20001``
TC-PW-RT03                            款号在、工序无生效价 → ``20004``
TC-PW-RT04                            款号档单行 + 字符串单价 + ``rate_source=STYLE``
TC-PW-RT05                            ★ 三档全生效 → 3 行 STYLE/CATEGORY/OPERATION
TC-PW-RT06                            ★ 区间边界：``effective_from == work_date`` 含
TC-PW-RT07                            ★ 区间边界：``effective_to == work_date`` 不含
TC-PW-RT08                            未来 ``effective_from`` 不含
TC-PW-RT09                            软删行不返回
TC-PW-RT10                            ``operation_no`` 缺省 → 跨工序
TC-PW-RT11                            ``work_date`` 缺省 = 今天
======================================  ==========================================

## 本文件在验什么

不是「端点能不能返回数据」，而是**「当天生效」这条语义**：正常路径用 world 里那条
「30 天前生效、无失效日」的价就能过，而把 ``work_date`` 当作筛选参数或漏掉区间谓词时
它**照样通过** —— 只有 TC-PW-RT06/07/08 这三条边界用例能区分「按生效区间取」与
「按生效起始日取」（后者会漏掉今天生效的旧价、误报下月生效的新价）。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.core.numbering import business_today
from app.modules.base.models import Operation, OperationRate, Style
from tests.factories.user import OPERATOR_ID
from tests.modules.pw_query_helpers import hdr

RATES = "/api/v1/piecework/rates"
RATE_PERMS = ("piecework:rate:manage",)


async def _category_id(session: Any, style_no: str) -> Any:
    return (
        await session.execute(select(Style.category_id).where(Style.style_no == style_no))
    ).scalar_one()


async def _ensure_operation(session: Any, operation_no: str) -> None:
    exists = (
        await session.execute(select(Operation).where(Operation.operation_no == operation_no))
    ).scalar_one_or_none()
    if exists is None:
        session.add(
            Operation(
                operation_no=operation_no,
                name="车缝",
                default_bundle_qty=Decimal(1),
                is_piecework=True,
                created_by=OPERATOR_ID,
                updated_by=OPERATOR_ID,
            )
        )
        await session.flush()


async def _add_rate(
    session: Any,
    *,
    operation_no: str,
    effective_from: date,
    effective_to: date | None = None,
    unit_price: str = "0.500000",
    style_no: str | None = None,
    style_id: Any = None,
    category_id: Any = None,
    deleted: bool = False,
) -> OperationRate:
    await _ensure_operation(session, operation_no)
    rate = OperationRate(
        operation_no=operation_no,
        style_no=style_no,
        style_id=style_id,
        product_category_id=category_id,
        effective_from=effective_from,
        effective_to=effective_to,
        unit_price=Decimal(unit_price),
        reason="用例单价",
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    if deleted:
        rate.deleted_at = datetime.now(tz=UTC)
    session.add(rate)
    await session.flush()
    return rate


async def _get(client: Any, headers: dict[str, str], **params: Any) -> Any:
    return await client.get(RATES, params=params, headers=headers)


async def test_tc_pw_rt01_requires_manage_permission(
    client, db_session, auth_headers, piecework_world
) -> None:
    headers = await hdr(db_session, auth_headers, piecework_world, ())
    response = await _get(client, headers, style_no=piecework_world["style_no"])
    assert response.status_code == 403
    assert response.json()["code"] == 12001


async def test_tc_pw_rt02_unknown_style_20001(
    client, db_session, auth_headers, piecework_world
) -> None:
    headers = await hdr(db_session, auth_headers, piecework_world, RATE_PERMS)
    response = await _get(client, headers, style_no="NO-SUCH-STYLE-9Z")
    assert response.status_code == 404
    assert response.json()["code"] == 20001


async def test_tc_pw_rt03_no_effective_rate_20004(
    client, db_session, auth_headers, piecework_world
) -> None:
    headers = await hdr(db_session, auth_headers, piecework_world, RATE_PERMS)
    response = await _get(
        client, headers, style_no=piecework_world["style_no"], operation_no="OP99"
    )
    assert response.status_code == 404
    assert response.json()["code"] == 20004
    assert response.json()["details"]["operation_no"] == "OP99"


async def test_tc_pw_rt04_style_tier_single_row(
    client, db_session, auth_headers, piecework_world
) -> None:
    headers = await hdr(db_session, auth_headers, piecework_world, RATE_PERMS)
    response = await _get(
        client,
        headers,
        style_no=piecework_world["style_no"],
        operation_no=piecework_world["operation_no"],
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert len(data) == 1, data
    row = data[0]
    assert row["operation_no"] == piecework_world["operation_no"]
    assert row["rate_source"] == "STYLE"
    assert row["is_current"] is True
    # ⚠️ 金额是字符串且按列标度补零（05 §3）：0.42 → "0.420000"
    assert row["unit_price"] == "0.420000"


async def test_tc_pw_rt05_three_tiers(client, db_session, auth_headers, piecework_world) -> None:
    style_no = piecework_world["style_no"]
    op = piecework_world["operation_no"]
    category_id = await _category_id(db_session, style_no)
    await _add_rate(
        db_session,
        operation_no=op,
        style_no=None,
        category_id=category_id,
        effective_from=business_today() - timedelta(days=10),
        unit_price="0.500000",
    )
    await _add_rate(
        db_session,
        operation_no=op,
        effective_from=business_today() - timedelta(days=5),
        unit_price="0.300000",
    )
    headers = await hdr(db_session, auth_headers, piecework_world, RATE_PERMS)
    response = await _get(client, headers, style_no=style_no, operation_no=op)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert [r["rate_source"] for r in data] == ["STYLE", "CATEGORY", "OPERATION"]
    assert [r["unit_price"] for r in data] == ["0.420000", "0.500000", "0.300000"]


async def test_tc_pw_rt06_effective_from_boundary_inclusive(
    client, db_session, auth_headers, piecework_world
) -> None:
    today = business_today()
    await _add_rate(db_session, operation_no="OP06", effective_from=today)
    headers = await hdr(db_session, auth_headers, piecework_world, RATE_PERMS)
    response = await _get(
        client, headers, style_no=piecework_world["style_no"], operation_no="OP06"
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"][0]["effective_from"] == today.isoformat()


async def test_tc_pw_rt07_effective_to_boundary_exclusive(
    client, db_session, auth_headers, piecework_world
) -> None:
    today = business_today()
    await _add_rate(
        db_session,
        operation_no="OP07",
        effective_from=today - timedelta(days=5),
        effective_to=today,
    )
    headers = await hdr(db_session, auth_headers, piecework_world, RATE_PERMS)
    response = await _get(
        client, headers, style_no=piecework_world["style_no"], operation_no="OP07"
    )
    # effective_to == work_date 不含当日 → 无生效价
    assert response.status_code == 404
    assert response.json()["code"] == 20004


async def test_tc_pw_rt08_future_effective_from_excluded(
    client, db_session, auth_headers, piecework_world
) -> None:
    await _add_rate(
        db_session, operation_no="OP08", effective_from=business_today() + timedelta(days=1)
    )
    headers = await hdr(db_session, auth_headers, piecework_world, RATE_PERMS)
    response = await _get(
        client, headers, style_no=piecework_world["style_no"], operation_no="OP08"
    )
    assert response.status_code == 404
    assert response.json()["code"] == 20004


async def test_tc_pw_rt09_deleted_excluded(
    client, db_session, auth_headers, piecework_world
) -> None:
    await _add_rate(
        db_session,
        operation_no="OP09",
        effective_from=business_today() - timedelta(days=5),
        deleted=True,
    )
    headers = await hdr(db_session, auth_headers, piecework_world, RATE_PERMS)
    response = await _get(
        client, headers, style_no=piecework_world["style_no"], operation_no="OP09"
    )
    assert response.status_code == 404
    assert response.json()["code"] == 20004


async def test_tc_pw_rt10_cross_operation_when_no_operation_filter(
    client, db_session, auth_headers, piecework_world
) -> None:
    style_no = piecework_world["style_no"]
    await _add_rate(
        db_session,
        operation_no="OP20",
        style_no=style_no,
        effective_from=business_today() - timedelta(days=3),
    )
    headers = await hdr(db_session, auth_headers, piecework_world, RATE_PERMS)
    response = await _get(client, headers, style_no=style_no)
    assert response.status_code == 200, response.text
    ops = {r["operation_no"] for r in response.json()["data"]}
    assert {piecework_world["operation_no"], "OP20"} <= ops, ops


async def test_tc_pw_rt11_default_work_date_is_today(
    client, db_session, auth_headers, piecework_world
) -> None:
    headers = await hdr(db_session, auth_headers, piecework_world, RATE_PERMS)
    # 不传 work_date：world 那条价 30 天前生效、无失效日 → 今天仍生效
    response = await _get(
        client, headers, style_no=piecework_world["style_no"], operation_no="OP01"
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"][0]["rate_source"] == "STYLE"
