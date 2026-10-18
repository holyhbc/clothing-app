"""用户代发资料（身份证 / 银行卡）用例（T-BASE-012；L-128 闭环）。

============  ==================================================================
TC-BANK-01    新建用户带三字段 → 落库且 ``UserOut`` 回显
TC-BANK-02    PATCH 只改 ``bank_account`` → 其余字段不动
TC-BANK-03    无 ``system:user:manage`` → ``12001``
TC-BANK-04    T-PAY 前置回归：``users`` 有卡后工资单行快照非空
============  ==================================================================

## 为什么有这张卡

``docs/12`` L-128：``05 §3.5`` / ``04 §7.6.5`` 要求工资单生成时把
``id_card`` / ``bank_name`` / ``bank_account`` 从 ``users`` **快照**到
``payroll_details``，而 ``users`` 此前根本没有这三列 —— 快照恒为 ``None``，
T-PAY-008 的发放（要求 ``bank_account`` 非空否则 ``10002``）必然失败。

## 权限敏感用例为什么单建角色

⚠️ T-PIECE-003 的教训：``auth_headers(role="custom")`` 会**复用**同一个 ``custom``
角色，而 ``grant_permissions`` 只增不减 —— 一旦某个用例给它授过
``system:user:manage``，后续用 ``custom`` 的用例即使没显式授权也会**假绿**。
所以 TC-BANK-03 单建唯一 code 的角色（见 :func:`_headers_without_manage`）。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import DataScope
from app.core.permissions import AuthContext
from app.modules.auth.models import User
from app.modules.payroll.models import PayrollDetail
from app.modules.payroll.service import PayrollSheetService
from tests.factories.payroll import seed_approved_settlement
from tests.factories.user import (
    OPERATOR_ID,
    RoleFactory,
    UserFactory,
    UserRoleFactory,
    grant_permissions,
)

PREFIX = "/api/v1/system"

#: 固定代发资料（非真实卡号，仅测试用）。
ID_CARD = "110101199001011234"
BANK_NAME = "中国工商银行北京分行"
BANK_ACCOUNT = "6222020200000000001"


def _user_payload(employee_no: str, **over: Any) -> dict[str, Any]:
    """新建用户的请求体（含代发三字段）。"""
    payload: dict[str, Any] = {
        "employee_no": employee_no,
        "name": "打卡工",
        "password": "Abcd1234",
        "data_scope": "SELF",
        "id_card": ID_CARD,
        "bank_name": BANK_NAME,
        "bank_account": BANK_ACCOUNT,
    }
    payload.update(over)
    return payload


async def _row(session: AsyncSession, user_id: str) -> User:
    user = await session.scalar(select(User).where(User.id == user_id))
    assert user is not None
    return user


# ============================================================== TC-BANK-01


async def test_tc_bank_01_create_user_with_bank_fields(client, auth_headers, db_session) -> None:
    """新建用户带三字段 → 落库 + ``UserOut`` 回显（工资单快照的输入就绪）。"""
    headers = await auth_headers()

    response = await client.post(f"{PREFIX}/users", headers=headers, json=_user_payload("BANK001"))

    assert response.status_code == 201, response.json()
    data = response.json()["data"]
    assert data["id_card"] == ID_CARD
    assert data["bank_name"] == BANK_NAME
    assert data["bank_account"] == BANK_ACCOUNT

    row = await _row(db_session, data["id"])
    assert (row.id_card, row.bank_name, row.bank_account) == (ID_CARD, BANK_NAME, BANK_ACCOUNT)


# ============================================================== TC-BANK-02


async def test_tc_bank_02_patch_only_bank_account(client, auth_headers, db_session) -> None:
    """PATCH 只改 ``bank_account`` → ``id_card`` / ``bank_name`` 原样不动。"""
    headers = await auth_headers()
    created = await client.post(f"{PREFIX}/users", headers=headers, json=_user_payload("BANK002"))
    user = created.json()["data"]

    response = await client.patch(
        f"{PREFIX}/users/{user['id']}",
        headers=headers,
        json={"bank_account": "6222020200000000009", "version": user["version"]},
    )

    assert response.status_code == 200, response.json()
    data = response.json()["data"]
    assert data["bank_account"] == "6222020200000000009"
    assert data["id_card"] == ID_CARD
    assert data["bank_name"] == BANK_NAME
    assert data["version"] == user["version"] + 1

    row = await _row(db_session, user["id"])
    assert row.bank_account == "6222020200000000009"
    assert (row.id_card, row.bank_name) == (ID_CARD, BANK_NAME)


# ============================================================== TC-BANK-03


async def _headers_without_manage(db_session: AsyncSession, auth_headers: Any) -> dict[str, str]:
    """单建唯一 code 角色（无 ``system:user:manage``）的请求头。

    ⚠️ 不复用 ``custom``：``grant_permissions`` 只增不减，复用会让本用例假绿
    （T-PIECE-003 的教训）。
    """
    role = await RoleFactory.create(
        db_session,
        code=f"bank_ro_{uuid4().hex[:8]}",
        name="无用户管理权",
        data_scope=DataScope.SELF,
    )
    user = await UserFactory.create(db_session, employee_no=f"NB{uuid4().hex[:7].upper()}")
    await UserRoleFactory.create(db_session, user_id=user.id, role_id=role.id)
    # 给一个正常登录用户该有的读权限，但**不给** system:user:manage
    await grant_permissions(db_session, role=role, codes=("base:read",))
    return await auth_headers(user_id=user.id)


async def test_tc_bank_03_without_manage_permission_12001(client, auth_headers, db_session) -> None:
    """⚠️ 三字段属敏感 PII：无 ``system:user:manage`` 一律 ``12001``，不可见也不可写。"""
    headers = await _headers_without_manage(db_session, auth_headers)

    read = await client.get(f"{PREFIX}/users", headers=headers)
    assert read.status_code == 403
    assert read.json()["code"] == 12001

    write = await client.post(f"{PREFIX}/users", headers=headers, json=_user_payload("BANK003"))
    assert write.status_code == 403
    assert write.json()["code"] == 12001


# ============================================================== TC-BANK-04


def _ctx() -> AuthContext:
    return AuthContext(
        user_id=OPERATOR_ID,
        name="计件员",
        employee_no="A001",
        workshop_id=None,
        group_no=None,
        permissions=frozenset({"payroll:settle", "payroll:approve"}),
        data_scope=DataScope.FACTORY,
    )


async def test_tc_bank_04_payroll_snapshot_not_null(db_session) -> None:
    """T-PAY 前置回归：``users`` 有卡后工资单行的三列快照**非空**（L-128 闭环）。

    T-PAY-006 时期 ``users`` 无这三列 → 快照恒为 ``None``；补列后必须取到值，
    否则 T-PAY-008 的发放与代发 CSV 仍会失败。
    """
    employee = await UserFactory.create(
        db_session,
        employee_no=f"B{uuid4().hex[:7].upper()}",
        id_card=ID_CARD,
        bank_name=BANK_NAME,
        bank_account=BANK_ACCOUNT,
    )
    settlement = await seed_approved_settlement(
        db_session,
        specs=[
            {
                "employee_id": employee.id,
                "style_no": "ST-BANK04",
                "operation_no": "01",
                "qty": Decimal("10"),
                "unit_price": Decimal("0.500000"),
            }
        ],
    )

    sheet = await PayrollSheetService(db_session).create(
        settlement_id=settlement.id, ctx=_ctx(), doc_date=date(2099, 12, 31)
    )

    detail = (
        await db_session.execute(select(PayrollDetail).where(PayrollDetail.doc_id == sheet.id))
    ).scalar_one()
    assert detail.bank_account == BANK_ACCOUNT
    assert detail.id_card == ID_CARD
    assert detail.bank_name == BANK_NAME
