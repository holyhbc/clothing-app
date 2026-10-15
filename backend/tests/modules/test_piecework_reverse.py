"""红冲 / 补录 / 手工补录的用例（``04-计件 §5.3`` 流程 C + §5.4 流程 D）。

======================================  ==========================================
TC-PW-R01                             红冲：REVERSAL 落库（负值）+ counted_* 清空
TC-PW-R02                             红冲已结算流水 → 32003
TC-PW-R03                             红冲自审（创建人操作）→ 10005
TC-PW-R04                             重复红冲 → 幂等返回既有 REVERSAL
TC-PW-R05                             对 REVERSAL 流水再红冲 → 拒绝
TC-PW-R06                             红冲越权 → 12002
TC-PW-R07                             补录 SUPPLEMENT：reversal_of_id 必填 + 重写 counted_*
TC-PW-R08                             补录 SUPPLEMENT 超产 → 32006
TC-PW-R09                             手工补录 MANUAL：remark 必填 + 已计码 → 32001
TC-PW-R10                             红冲后他人可重扫（TC-45 闭环）
======================================  ==========================================

## 本卡最该守的三条不变量

1. **append-only**：红冲是**新增**一条负值流水，绝不 UPDATE 原流水。
2. **``counted_*`` 清空是判定「回到未计件」的唯一真相**（``counted_at IS NULL`）——
   红冲后他人能重扫，靠的是它，不是"删了原流水"。
3. **补录不得绕过超产**：`qty ≤ bundle_qty` 在 SUPPLEMENT / MANUAL 同样强制。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select, update

from app.common.enums import DataScope
from app.modules.bundling.models import Bundle
from app.modules.piecework.models import PieceworkLog, PieceworkLogType
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


# ============================================================== 红冲


async def test_tc_pw_r01_reverse_creates_reversal_and_clears_counted(
    client, db_session, auth_headers, piecework_world
) -> None:
    """TC-09 / TC-45：红冲 → REVERSAL（负值）+ counted_* 清空（回到未计件）。"""
    original = await _count_one(db_session, piecework_world)
    headers = await hdr(db_session, auth_headers, piecework_world, REVERSE_PERMS)

    # ⚠️ 红冲人 ≠ 创建人：count_piecework 的 operator 是 ctx_for()（OPERATOR_ID），
    #    这里 hdr 造的新用户不是 OPERATOR_ID → 自审校验通过。
    response = await client.post(
        f"{REV}/{original.id}/reverse", json={"reason": "员工报错了数量，红冲重录"}, headers=headers
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]

    assert data["reversed_qty"] == "-60.000" or data["reversed_qty"] == "60.000"
    assert data["bundle_counted_reset"] is True

    rows = await _reversal_rows(db_session, original.bundle_no)
    reversal = rows[-1]
    assert reversal.log_type == PieceworkLogType.REVERSAL
    assert reversal.qty == -original.qty, "REVERSAL 必须是负值"
    assert reversal.reversal_of_id == original.id
    assert Decimal(reversal.amount) == -Decimal(original.amount)

    bundle = await _bundle(db_session, original.bundle_id)
    assert bundle.counted_qty == Decimal(0), "counted_* 必须清空"
    assert bundle.counted_at is None, "判定「未计件」靠 counted_at IS NULL（不是 0）"
    assert bundle.counted_by is None

    # ⚠️ append-only：原流水一字未改
    fresh = await db_session.get(PieceworkLog, original.id)
    assert fresh is not None and fresh.qty == original.qty and fresh.amount == original.amount


async def test_tc_pw_r02_reverse_settled_raises_32003(
    client, db_session, auth_headers, piecework_world
) -> None:
    """已结算（payroll_id 非空）不可红冲 → 32003。"""
    original = await _count_one(db_session, piecework_world)
    await db_session.execute(
        update(PieceworkLog).where(PieceworkLog.id == original.id).values(payroll_id=uuid4())
    )
    headers = await hdr(db_session, auth_headers, piecework_world, REVERSE_PERMS)
    response = await client.post(
        f"{REV}/{original.id}/reverse", json={"reason": "试试已结算能不能红冲"}, headers=headers
    )
    assert response.status_code == 409
    assert response.json()["code"] == 32003


async def test_tc_pw_r03_reverse_self_raises_10005(
    client, db_session, auth_headers, piecework_world
) -> None:
    """红冲人 = 原流水创建人 → 10005（08 §4 不自审）。

    ⚠️ 走 **service 直调**：`_count_one` 的操作人是 ``ctx_for().user_id``（OPERATOR_ID），
    所以原流水 ``created_by = OPERATOR_ID``。要触发「自审」，红冲的 ``operator_id``
    必须也是 OPERATOR_ID —— 而 HTTP 的 token 永远是新用户，构造不出来。
    """
    from app.modules.piecework.schemas import PieceworkReverseIn
    from tests.factories.piecework_world import ctx_for

    original = await _count_one(db_session, piecework_world)
    with pytest.raises(Exception) as exc:
        await PieceworkService(db_session).reverse_log(
            original.id, PieceworkReverseIn(reason="自审"), ctx_for().user_id, ctx_for()
        )
    # BusinessError 从异常链里取
    err = exc.value
    assert getattr(err, "code", None) == 10005 or "10005" in str(err)


async def test_tc_pw_r04_reverse_is_idempotent(
    client, db_session, auth_headers, piecework_world
) -> None:
    """同 reversal_of_id 重复红冲 → 返回既有 REVERSAL（§6.3），只落 1 条。"""
    original = await _count_one(db_session, piecework_world)
    headers = await hdr(db_session, auth_headers, piecework_world, REVERSE_PERMS)
    body = {"reason": "重复提交测试"}
    first = await client.post(f"{REV}/{original.id}/reverse", json=body, headers=headers)
    second = await client.post(f"{REV}/{original.id}/reverse", json=body, headers=headers)
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["data"]["reversal_log_id"] == second.json()["data"]["reversal_log_id"]
    rows = await _reversal_rows(db_session, original.bundle_no)
    assert sum(1 for r in rows if r.log_type == PieceworkLogType.REVERSAL) == 1


async def test_tc_pw_r05_reverse_reversal_raises_illegal(
    client, db_session, auth_headers, piecework_world
) -> None:
    """对 REVERSAL 流水再红冲 → 拒绝（红冲链不能套娃）。"""
    original = await _count_one(db_session, piecework_world)
    headers = await hdr(db_session, auth_headers, piecework_world, REVERSE_PERMS)
    await client.post(
        f"{REV}/{original.id}/reverse", json={"reason": "第一次红冲"}, headers=headers
    )
    rows = await _reversal_rows(db_session, original.bundle_no)
    reversal = next(r for r in rows if r.log_type == PieceworkLogType.REVERSAL)
    response = await client.post(
        f"{REV}/{reversal.id}/reverse", json={"reason": "对红冲流水再红冲"}, headers=headers
    )
    assert response.status_code == 409
    assert response.json()["code"] == 10008  # ILLEGAL_OPERATION


async def test_tc_pw_r06_reverse_scope_denial(
    client, db_session, auth_headers, piecework_world, other_piecework_world
) -> None:
    """越权红冲别人的流水 → 12002。拿别人车间的真实 id。"""
    other_original = await _count_one(db_session, other_piecework_world)
    headers = await hdr(
        db_session,
        auth_headers,
        piecework_world,
        REVERSE_PERMS,
        data_scope=DataScope.WORKSHOP,
        workshop_id=piecework_world["workshop_id"],
    )
    response = await client.post(
        f"{REV}/{other_original.id}/reverse", json={"reason": "越权红冲"}, headers=headers
    )
    assert response.status_code == 403
    assert response.json()["code"] == 12002
