"""工资单**发放 / 代发文件**用例共用的测试世界（T-PAY-008）。

单开一个工厂而不是塞进 :mod:`tests.factories.payroll`（已 400 行、顶到 ADR-0030 硬线），
也不让两个测试文件互相 import（测试互相 import 会让导入顺序不可预测，同
:mod:`tests.factories.base_style` 的口径）。

## 造数口径

``approved_sheet`` 走真实 service（``create`` → ``submit`` → ``approve``），
**不手搓六列**：这样 ``pay`` 拿到的就是生产同款对象，银行资料来自 ``users`` 的真实快照。

⚠️ 区间用固定 ``2099-12``（``seed_approved_settlement`` 默认），与真提交的并发用例隔离。
"""

from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select

from app.common.enums import DataScope
from app.common.models import DocumentLog
from app.core.permissions import AuthContext
from app.modules.auth.models import User
from app.modules.payroll.models import BankPaymentFile, PayrollSheet
from app.modules.payroll.service import PayrollSheetService
from app.modules.payroll.service.bank_file import BANK_CSV_HEADER, BOM
from tests.factories.payroll import seed_approved_settlement
from tests.factories.payroll_approve import REVIEWER_ID, reviewer_ctx
from tests.factories.user import OPERATOR_ID, UserFactory

SETTLE = "payroll:settle"
PAY = "payroll:pay"
EXPORT = "payroll:export"
DOC_DATE = date(2099, 12, 31)
DOC_TYPE = "PayrollSheet"
PAYER_ID = UUID("00000000-0000-0000-0000-000000000003")
EXPORTER_ID = UUID("00000000-0000-0000-0000-000000000004")


class FakeVoucherPort:
    """计数用 fake：断言付款凭证端口「被调用 / 未被调用」，并可控返回凭证号。"""

    def __init__(self, *, payment_no: str | None = None) -> None:
        self.payment_no = payment_no
        self.payment_calls: list[UUID] = []
        self.accrual_calls: list[UUID] = []
        self.reverse_calls: list[UUID] = []

    async def create_accrual(self, sheet: PayrollSheet) -> str | None:
        self.accrual_calls.append(sheet.id)
        return None

    async def reverse_accrual(self, sheet: PayrollSheet) -> str | None:
        self.reverse_calls.append(sheet.id)
        return None

    async def create_payment(self, sheet: PayrollSheet) -> str | None:
        self.payment_calls.append(sheet.id)
        return self.payment_no


def ctx(
    user_id: UUID,
    permissions: frozenset[str],
    *,
    scope: DataScope = DataScope.FACTORY,
    workshop_id: UUID | None = None,
) -> AuthContext:
    return AuthContext(
        user_id=user_id,
        name="操作人",
        employee_no="A000",
        workshop_id=workshop_id,
        group_no=None,
        permissions=permissions,
        data_scope=scope,
    )


def settle_ctx() -> AuthContext:
    return ctx(OPERATOR_ID, frozenset({SETTLE}))


def pay_ctx(permissions: frozenset[str] = frozenset({PAY})) -> AuthContext:
    return ctx(PAYER_ID, permissions)


def export_ctx(*, permissions: frozenset[str] = frozenset({EXPORT}), **kwargs: Any) -> AuthContext:
    return ctx(EXPORTER_ID, permissions, **kwargs)


def spec(
    employee_id: UUID, style_no: str, operation_no: str, qty: str, price: str
) -> dict[str, Any]:
    return {
        "employee_id": employee_id,
        "style_no": style_no,
        "operation_no": operation_no,
        "qty": Decimal(qty),
        "unit_price": Decimal(price),
    }


async def user_with_bank(
    session: Any,
    *,
    employee_no: str,
    name: str,
    bank_account: str = "0012345678901234",
    id_card: str = "110101199001011234",
    bank_name: str = "中国工商银行",
) -> User:
    return await UserFactory.create(
        session,
        employee_no=employee_no,
        name=name,
        bank_account=bank_account,
        id_card=id_card,
        bank_name=bank_name,
    )


async def approved_sheet(session: Any, *, specs: list[dict[str, Any]]) -> PayrollSheet:
    """造一张 ``APPROVED`` 工资单（``create`` → ``submit`` → ``approve``），制单人 = 计件员。"""
    settlement = await seed_approved_settlement(session, specs=specs, created_by=uuid4())
    service = PayrollSheetService(session)
    sheet = await service.create(settlement_id=settlement.id, ctx=settle_ctx(), doc_date=DOC_DATE)
    submitted = await service.submit(
        sheet.id, expected_version=sheet.version, operator_id=OPERATOR_ID, ctx=settle_ctx()
    )
    return await service.approve(
        submitted.id,
        expected_version=submitted.version,
        operator_id=REVIEWER_ID,
        ctx=reviewer_ctx(),
    )


async def bank_file(session: Any, sheet_id: UUID) -> BankPaymentFile | None:
    return (
        await session.execute(
            select(BankPaymentFile).where(BankPaymentFile.payroll_sheet_id == sheet_id)
        )
    ).scalar_one_or_none()


async def bank_file_count(session: Any, sheet_id: UUID) -> int:
    return (
        await session.execute(
            select(func.count(BankPaymentFile.id)).where(
                BankPaymentFile.payroll_sheet_id == sheet_id
            )
        )
    ).scalar_one()


async def logs(session: Any, sheet_id: UUID) -> list[DocumentLog]:
    rows = await session.execute(
        select(DocumentLog)
        .where(DocumentLog.doc_type == DOC_TYPE, DocumentLog.doc_id == sheet_id)
        .order_by(DocumentLog.created_at)
    )
    return list(rows.scalars().all())


def data_rows(content: str) -> list[list[str]]:
    """去掉 BOM 后解析 CSV；断言表头与 ``05 §6.4`` 的 6 列逐字一致，返回数据行。"""
    rows = list(csv.reader(io.StringIO(content.removeprefix(BOM))))
    assert rows[0] == list(BANK_CSV_HEADER), "CSV 表头必须与 05 §6.4 的 6 列逐字一致"
    return rows[1:]
