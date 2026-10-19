"""工资单**发放 `pay` + 代发文件**用例（T-PAY-008）。

设计权威：``docs/modules/05-工资.md`` §4.2、§5.5、§6.3、§6.4、§11（TC-07/TC-09）；
``docs/08 §2.4``；``docs/04 §7.6.6``。

===============  ====================================================================
TC-07            ``pay`` → ``PAID`` + ``paid_at`` + 代发 CSV 6 列 + 金额合计 = ``total_net``
TC-09            缺银行账号 → ``10002``，不写 ``bank_payment_files``、不调凭证端口
TC-PAY-PAY01     重复导出幂等：同一记录（同 id）+ 同内容；``pay`` 复用该记录
TC-PAY-PAY02     已 ``PAID`` 再 ``pay`` → ``33001``（业务拒绝，非幂等）
===============  ====================================================================

权限 / 数据范围 / 草稿导出 / 唯一冲突回退等边界分支见 ``test_payroll_bank_file.py``
（ADR-0030 单文件 400 行硬线，拆两份）；共用造数见 ``tests/factories/payroll_pay.py``。

## 本文件刻意不走 HTTP

本卡**没有 router**（导出路由归 T-PAY-009），故直接调 service；鉴权用构造 ``AuthContext``。
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

import pytest

from app.common.enums import DocumentStatus
from app.core.errors import BusinessError, ErrorCode
from app.modules.payroll.models import PayrollSheet
from app.modules.payroll.service import PayrollSheetService
from app.modules.payroll.service.bank_file import BOM
from tests.factories.payroll_pay import (
    PAYER_ID,
    FakeVoucherPort,
    approved_sheet,
    bank_file,
    bank_file_count,
    data_rows,
    export_ctx,
    logs,
    pay_ctx,
    spec,
    user_with_bank,
)
from tests.factories.user import UserFactory

# ============================================================== TC-07


async def test_tc07_pay_produces_bank_file(db_session) -> None:
    emp1 = await user_with_bank(db_session, employee_no="A900", name="张三")
    emp2 = await user_with_bank(
        db_session, employee_no="A901", name="李四", bank_account="0987654321098765"
    )
    # 员工1 两条工资行（10×0.5 + 20×0.25 = 10.00），员工2 一条（20×0.25 = 5.00）
    sheet = await approved_sheet(
        db_session,
        specs=[
            spec(emp1.id, "ST-PAY-1", "01", "10", "0.500000"),
            spec(emp1.id, "ST-PAY-2", "02", "20", "0.250000"),
            spec(emp2.id, "ST-PAY-1", "01", "20", "0.250000"),
        ],
    )
    assert sheet.total_net == Decimal("15.0000")
    fake = FakeVoucherPort(payment_no="VO-PAY-0001")

    paid = await PayrollSheetService(db_session, voucher_port=fake).pay(
        sheet.id,
        expected_version=sheet.version,
        operator_id=PAYER_ID,
        ctx=pay_ctx(),
        pay_date=date(2099, 12, 25),
    )

    assert paid.status is DocumentStatus.PAID
    assert paid.paid_at is not None, "发放必须记 paid_at"
    assert paid.pay_date == date(2099, 12, 25)
    assert paid.payment_voucher_no == "VO-PAY-0001"
    assert fake.payment_calls == [sheet.id], "create_payment 必须恰好调用一次"

    record = await bank_file(db_session, sheet.id)
    assert record is not None
    assert paid.bank_file_id == record.id
    assert record.row_count == 2, "一个员工一行"
    assert record.total_amount == paid.total_net
    assert len(record.content_hash) == 64

    export = await PayrollSheetService(db_session).bank_file_export(sheet.id, ctx=export_ctx())
    assert export.content.startswith(BOM), "UTF-8 必须带 BOM（05 §6.4）"
    assert export.content_hash == record.content_hash, "导出内容与发放时逐字节一致"
    data = data_rows(export.content)
    assert len(data) == 2
    assert all(len(row) == 6 for row in data), "CSV 必须恰好 6 列"
    amounts = [Decimal(row[5]) for row in data]
    assert sum(amounts) == paid.total_net, "金额合计必须等于 total_net"
    for row in data:
        assert re.fullmatch(r"\d+\.\d{2}", row[5]), f"金额必须是两位小数字符串：{row[5]!r}"
    accounts = {row[4] for row in data}
    assert accounts == {"0012345678901234", "0987654321098765"}, "账号作为文本、前导 0 保留"
    assert '"0012345678901234"' in export.content, "账号字段必须加引号（Excel 当文本）"
    assert [row[0] for row in data] == ["A900", "A901"], "工号唯一、按工号排序"

    pay_logs = [item for item in await logs(db_session, sheet.id) if item.action == "PAY"]
    assert len(pay_logs) == 1
    assert (pay_logs[0].from_status, pay_logs[0].to_status) == ("APPROVED", "PAID")
    assert (pay_logs[0].changed_fields or {})["total_net"] == str(paid.total_net)


# ============================================================== TC-09


async def test_tc09_missing_bank_account_10002(db_session) -> None:
    employee = await UserFactory.create(db_session, employee_no="A902", name="王五")
    assert employee.bank_account is None
    sheet = await approved_sheet(
        db_session, specs=[spec(employee.id, "ST-PAY-1", "01", "10", "0.500000")]
    )
    fake = FakeVoucherPort(payment_no="VO-SHOULD-NOT")

    with pytest.raises(BusinessError) as exc:
        await PayrollSheetService(db_session, voucher_port=fake).pay(
            sheet.id, expected_version=sheet.version, operator_id=PAYER_ID, ctx=pay_ctx()
        )

    assert exc.value.code == ErrorCode.MISSING_BUSINESS_PARAM
    assert exc.value.details["missing_line_count"] == 1
    assert exc.value.details["missing_lines"][0]["employee_id"] == str(employee.id)
    assert fake.payment_calls == [], "缺卡被拒时不得调用付款凭证端口"
    assert await bank_file_count(db_session, sheet.id) == 0, "缺卡被拒时不得生成代发文件"
    refreshed = await db_session.get(PayrollSheet, sheet.id, populate_existing=True)
    assert refreshed is not None and refreshed.status is DocumentStatus.APPROVED


# ============================================================== TC-PAY-PAY01


async def test_tc_pay_pay01_repeat_export_idempotent(db_session) -> None:
    employee = await user_with_bank(db_session, employee_no="A903", name="赵六")
    sheet = await approved_sheet(
        db_session, specs=[spec(employee.id, "ST-PAY-1", "01", "10", "0.500000")]
    )
    service = PayrollSheetService(db_session)

    first = await service.bank_file_export(sheet.id, ctx=export_ctx())
    second = await service.bank_file_export(sheet.id, ctx=export_ctx())

    assert first.record.id == second.record.id, "重复导出必须返回同一条记录"
    assert first.content == second.content, "重复导出必须返回同内容"
    assert await bank_file_count(db_session, sheet.id) == 1

    # 发放复用「导出时已建」的记录，不新建第二条
    paid = await PayrollSheetService(
        db_session, voucher_port=FakeVoucherPort(payment_no="VO-PAY-2")
    ).pay(sheet.id, expected_version=sheet.version, operator_id=PAYER_ID, ctx=pay_ctx())
    assert paid.bank_file_id == first.record.id
    assert await bank_file_count(db_session, sheet.id) == 1


# ============================================================== TC-PAY-PAY02


async def test_tc_pay_pay02_paid_again_33001(db_session) -> None:
    employee = await user_with_bank(db_session, employee_no="A904", name="钱七")
    sheet = await approved_sheet(
        db_session, specs=[spec(employee.id, "ST-PAY-1", "01", "10", "0.500000")]
    )
    fake = FakeVoucherPort(payment_no="VO-PAY-3")
    service = PayrollSheetService(db_session, voucher_port=fake)

    paid = await service.pay(
        sheet.id, expected_version=sheet.version, operator_id=PAYER_ID, ctx=pay_ctx()
    )
    with pytest.raises(BusinessError) as exc:
        await service.pay(
            paid.id, expected_version=paid.version, operator_id=PAYER_ID, ctx=pay_ctx()
        )

    assert exc.value.code == ErrorCode.PAYROLL_SHEET_ALREADY_PAID
    assert len(fake.payment_calls) == 1, "第二次 pay 不得再次调用凭证端口"
    assert await bank_file_count(db_session, paid.id) == 1
    assert len(await logs(db_session, paid.id)) == 4, "CREATE/SUBMIT/APPROVE/PAY 各一条"
