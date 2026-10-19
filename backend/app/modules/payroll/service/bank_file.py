"""银行代发文件的 CSV 渲染与记录构造（T-PAY-008；``05 §6.4`` / ``04 §7.6.6``）。

从 :mod:`~.pay` 拆出（ADR-0031）：这些是**纯函数 + 值对象**，被 ``pay``（发放时生成）
与 ``bank_file_export``（导出时复用）**共用同一份实现** —— 导出若另写一套渲染，
「发放生成的文件」与「导出的文件」就会逐字节不一致（``07 §3.2`` 铁律 3 要防的正是这个）。

## CSV 六条硬约束（``05 §6.4``）

1. **UTF-8 带 BOM**（``\ufeff``）：Excel 双击不乱码；
2. **6 列固定序**：``employee_no,name,id_card,bank_name,bank_account,amount``；
3. **金额是字符串两位小数**（``format_amount``），非 ``float``、不用科学计数法；
4. **账号前导 0 保留**：以 ``str`` 写出，整表 ``QUOTE_NONNUMERIC`` 加引号，
   Excel 当文本、不转数字（不 ``int`` / ``float``）；
5. **一个员工一行**（同人多条工资行按实发合计）：§6.4 要求工号/姓名「非空、唯一」，
   且银行转账按账户，故不按工资行逐行出；
6. 行分隔符 ``\r\n``（Excel / 银行软件通行）。
"""

from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Final
from uuid import UUID

from app.modules.payroll.models import BankPaymentFile, PayrollSheet

#: CSV 表头（``05 §6.4`` 的 6 列，列序固定，禁止改序）。
BANK_CSV_HEADER: Final[tuple[str, ...]] = (
    "employee_no",
    "name",
    "id_card",
    "bank_name",
    "bank_account",
    "amount",
)
#: UTF-8 BOM（``05 §6.4``：Excel 直接双击不乱码）。
BOM: Final[str] = "\ufeff"
#: CSV 行分隔符：``\r\n``。
LINE_TERMINATOR: Final[str] = "\r\n"


@dataclass(frozen=True, slots=True)
class BankRow:
    """代发文件的一行（**一个员工一行**，金额 = 该员工全部工资行实发合计）。"""

    employee_no: str
    name: str
    id_card: str
    bank_name: str
    bank_account: str
    amount: Decimal


@dataclass(frozen=True, slots=True)
class BankFileExport:
    """代发文件导出结果（service → router 的唯一载体，非裸 dict）。"""

    file_name: str
    #: 含 UTF-8 BOM 的完整 CSV 文本；``GET .../bank-file`` 直接编码为 utf-8 返回。
    content: str
    record: BankPaymentFile
    row_count: int
    total_amount: Decimal
    content_hash: str


def render_bank_csv(rows: list[BankRow]) -> str:
    """渲染代发 CSV（``05 §6.4``）：UTF-8 带 BOM、6 列固定、金额字符串两位小数。"""
    buffer = io.StringIO()
    buffer.write(BOM)
    writer = csv.writer(buffer, lineterminator=LINE_TERMINATOR, quoting=csv.QUOTE_NONNUMERIC)
    writer.writerow(BANK_CSV_HEADER)
    for row in rows:
        writer.writerow(
            [
                row.employee_no,
                row.name,
                row.id_card,
                row.bank_name,
                row.bank_account,
                format_amount(row.amount),
            ]
        )
    return buffer.getvalue()


def format_amount(amount: Decimal) -> str:
    """金额 → 两位小数字符串（``05 §6.4``：不用科学计数法、不用 float）。"""
    return str(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def new_bank_file(
    sheet: PayrollSheet, *, row_count: int, content: str, operator_id: UUID
) -> BankPaymentFile:
    """构造一条 append-only 代发记录（``content_hash`` = CSV 字节的 SHA-256）。"""
    return BankPaymentFile(
        payroll_sheet_id=sheet.id,
        file_name=f"{sheet.doc_no}-bank.csv",
        row_count=row_count,
        total_amount=sheet.total_net,
        content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        exported_by=operator_id,
        exported_at=datetime.now(tz=UTC),
        created_by=operator_id,
    )


def pay_log_fields(
    sheet: PayrollSheet,
    record: BankPaymentFile,
    voucher_no: str | None,
    paid_at: datetime,
) -> dict[str, Any]:
    """``pay`` 日志字段：实发合计 + 代发文件 + 发放时刻 +（若有的）付款凭证号。"""
    fields: dict[str, Any] = {
        "total_net": str(sheet.total_net),
        "bank_file_id": str(record.id),
        "bank_file_name": record.file_name,
        "row_count": record.row_count,
        "paid_at": paid_at.isoformat(),
    }
    if voucher_no is not None:
        fields["payment_voucher_no"] = voucher_no
    return fields


__all__ = [
    "BANK_CSV_HEADER",
    "BOM",
    "LINE_TERMINATOR",
    "BankFileExport",
    "BankRow",
    "format_amount",
    "new_bank_file",
    "pay_log_fields",
    "render_bank_csv",
]
