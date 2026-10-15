"""补录 / 手工补录 / 红冲后重扫的用例（``04-计件 §5.3`` 步骤 4 + §5.4 流程 D）。

======================================  ==========================================
TC-PW-R07                             补录 SUPPLEMENT：reversal_of_id 必填
TC-PW-R07b                            SUPPLEMENT 重写 counted_*（净额 = 补录值）
TC-PW-R08                             SUPPLEMENT 超产 → 32006
TC-PW-R09                             手工补录 MANUAL：remark 必填
TC-PW-R09b                            MANUAL 补录已计件的码 → 32001
TC-PW-R09c                            MANUAL 正常补录漏扫码：落库 + 回写
TC-PW-R10                             红冲后他人可重扫（TC-45 闭环）
======================================  ==========================================

⚠️ 红冲本身的用例在 ``test_piecework_reverse.py``。拆开是因为两个文件合计超
400 行硬线（ADR-0030）：红冲（R01~R06）与补录（R07~R10）是**两组独立入口**。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.modules.bundling.models import Bundle
from app.modules.piecework.models import PieceworkLog
from app.modules.piecework.service import PieceworkService
from tests.modules.pw_query_helpers import hdr

REV = "/api/v1/piecework-logs"
WRITE = "/api/v1/piecework-logs"

REVERSE_PERMS = ("piecework:reverse", "piecework:read")
MANUAL_PERMS = ("piecework:manual", "piecework:read")


async def _count_one(db_session: Any, world: dict[str, Any]) -> PieceworkLog:
    """扫一手（默认整手），返回落库的流水。"""
    from tests.factories.piecework_world import ctx_for, scanned_at
    from tests.modules.pw_query_helpers import bundle_nos

    no = (await bundle_nos(db_session, world, 1))[0]
    out = await PieceworkService(db_session).count_piecework(
        bundle_no=no,
        employee_id=world["employee"].id,
        scanned_at=scanned_at(),
        qty=None,
        operator_id=ctx_for().user_id,
        ctx=ctx_for(),
    )
    row = (
        await db_session.execute(select(PieceworkLog).where(PieceworkLog.id == out.log_id))
    ).scalar_one()
    return row


async def _reversal_rows(db_session: Any, bundle_no: str) -> list[PieceworkLog]:
    return list(
        (
            await db_session.execute(
                select(PieceworkLog)
                .where(PieceworkLog.bundle_no == bundle_no, PieceworkLog.deleted_at.is_(None))
                .order_by(PieceworkLog.created_at)
            )
        )
        .scalars()
        .all()
    )


async def _bundle(db_session: Any, bundle_id: Any) -> Bundle:
    row = await db_session.get(Bundle, bundle_id)
    assert row is not None
    return row


# ============================================================== 补录 SUPPLEMENT


async def test_tc_pw_r07_supplement_requires_reversal_of(
    client, db_session, auth_headers, piecework_world
) -> None:
    """SUPPLEMENT 不传 reversal_of_id → 10002。"""
    headers = await hdr(db_session, auth_headers, piecework_world, MANUAL_PERMS)
    response = await client.post(
        WRITE,
        json={
            "log_type": "SUPPLEMENT",
            "bundle_no": piecework_world["bundle_no"],
            "remark": "补录缺 reversal_of_id",
        },
        headers=headers,
    )
    assert response.status_code == 400
    assert response.json()["code"] == 10002


async def test_tc_pw_r07b_supplement_rewrites_counted(
    client, db_session, auth_headers, piecework_world
) -> None:
    """红冲后补录：SUPPLEMENT 重写 counted_*（净额 = 补录值）。"""
    original = await _count_one(db_session, piecework_world)
    headers = await hdr(db_session, auth_headers, piecework_world, (*MANUAL_PERMS, *REVERSE_PERMS))
    await client.post(f"{REV}/{original.id}/reverse", json={"reason": "数量报错"}, headers=headers)
    bundle = await _bundle(db_session, original.bundle_id)
    assert bundle.counted_at is None, "红冲后回到未计件"

    # 补录正确的量（比如 30 件）
    response = await client.post(
        WRITE,
        json={
            "log_type": "SUPPLEMENT",
            "bundle_no": original.bundle_no,
            "reversal_of_id": str(original.id),
            "remark": "补录正确数量 30",
            "qty": "30",
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["qty"] == "30.000"

    bundle2 = await _bundle(db_session, original.bundle_id)
    assert bundle2.counted_qty == Decimal(30), "补录重写 counted_qty"
    assert bundle2.counted_at is not None

    rows = await _reversal_rows(db_session, original.bundle_no)
    types = sorted(r.log_type.value for r in rows)
    assert types == ["REVERSAL", "SCAN", "SUPPLEMENT"]
    assert rows[-1].reversal_of_id == original.id


async def test_tc_pw_r08_supplement_over_production_raises_32006(
    client, db_session, auth_headers, piecework_world
) -> None:
    """补录 qty > bundle_qty → 32006（不得用补录绕过超产，TC-46）。

    ⚠️ **必须先红冲清码**：SUPPLEMENT 走到 qty 校验前要过 ``guard_bundle``，
    而码一旦被计过（``counted_at`` 非空）就撞 32001 而不是 32006。
    红冲清空后再补录，qty 校验才会真正执行。
    """
    original = await _count_one(db_session, piecework_world)
    headers = await hdr(db_session, auth_headers, piecework_world, (*MANUAL_PERMS, *REVERSE_PERMS))
    await client.post(
        f"{REV}/{original.id}/reverse", json={"reason": "先红冲清码"}, headers=headers
    )
    response = await client.post(
        WRITE,
        json={
            "log_type": "SUPPLEMENT",
            "bundle_no": original.bundle_no,
            "reversal_of_id": str(original.id),
            "remark": "试图超产补录",
            "qty": "999",
        },
        headers=headers,
    )
    assert response.status_code == 409
    assert response.json()["code"] == 32006


# ============================================================== 手工补录 MANUAL


async def test_tc_pw_r09_manual_requires_remark(
    client, db_session, auth_headers, piecework_world
) -> None:
    """MANUAL 缺 remark → 10002。"""
    headers = await hdr(db_session, auth_headers, piecework_world, MANUAL_PERMS)
    response = await client.post(
        WRITE,
        json={"log_type": "MANUAL", "bundle_no": piecework_world["bundle_no"], "remark": ""},
        headers=headers,
    )
    assert response.status_code == 400
    assert response.json()["code"] == 10002


async def test_tc_pw_r09b_manual_on_counted_code_raises_32001(
    client, db_session, auth_headers, piecework_world
) -> None:
    """MANUAL 补录已计件的码 → 32001（他人已做，guard_bundle 拦）。"""
    original = await _count_one(db_session, piecework_world)
    headers = await hdr(db_session, auth_headers, piecework_world, MANUAL_PERMS)
    response = await client.post(
        WRITE,
        json={
            "log_type": "MANUAL",
            "bundle_no": original.bundle_no,
            "remark": "试试补录已计件的码",
        },
        headers=headers,
    )
    assert response.status_code == 409
    assert response.json()["code"] == 32001


async def test_tc_pw_r09c_manual_happy_path(
    client, db_session, auth_headers, piecework_world
) -> None:
    """MANUAL 正常补录漏扫码：落库 + 回写 counted_*。"""
    headers = await hdr(db_session, auth_headers, piecework_world, MANUAL_PERMS)
    response = await client.post(
        WRITE,
        json={
            "log_type": "MANUAL",
            "bundle_no": piecework_world["bundle_no"],
            "remark": "纸质单 X-12 补录",
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["log_type"] == "MANUAL"
    assert data["qty"] == "60.000", "缺省 = 该手全件（bundle_qty），不是 1"
    bundle = await _bundle(db_session, piecework_world["bundle"].id)
    assert bundle.counted_qty == Decimal(60)


# ============================================================== 红冲后他人可重扫（TC-45）


async def test_tc_pw_r10_after_reverse_other_employee_can_scan(
    client, db_session, auth_headers, piecework_world
) -> None:
    """TC-45 闭环：A 记 28 → 红冲 → B 重扫整手 60 成功。"""
    from tests.factories.piecework_world import ctx_for, scanned_at
    from tests.modules.pw_query_helpers import bundle_nos

    no = (await bundle_nos(db_session, piecework_world, 1))[0]
    out = await PieceworkService(db_session).count_piecework(
        bundle_no=no,
        employee_id=piecework_world["employee"].id,
        scanned_at=scanned_at(),
        qty=Decimal(28),  # 部分生产
        operator_id=ctx_for().user_id,
        ctx=ctx_for(),
    )
    original = await db_session.get(PieceworkLog, out.log_id)
    assert original is not None

    headers = await hdr(db_session, auth_headers, piecework_world, REVERSE_PERMS)
    await client.post(
        f"{REV}/{original.id}/reverse", json={"reason": "部分生产报多了"}, headers=headers
    )

    # B（另一员工）重扫整手。⚠️ B 也要有**当天绑定**（BR-PW-04），否则撞 32002。
    from tests.factories.piecework_world import bind, make_employee

    bundle = await _bundle(db_session, original.bundle_id)
    assert bundle.counted_at is None
    other = await make_employee(db_session)
    await bind(
        db_session,
        user=other,
        bundle=bundle,
        operation_no=original.operation_no,
        workshop_id=piecework_world["workshop_id"],
    )
    second = await PieceworkService(db_session).count_piecework(
        bundle_no=no,
        employee_id=other.id,
        scanned_at=scanned_at(),
        qty=None,  # 整手
        operator_id=ctx_for().user_id,
        ctx=ctx_for(),
    )
    assert second.qty == "60.000", "红冲后 B 整手重扫"
    bundle2 = await _bundle(db_session, original.bundle_id)
    assert bundle2.counted_qty == Decimal(60)
