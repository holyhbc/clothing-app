"""工资单**发放**（``pay``）+ 银行代发文件（T-PAY-008）。

设计权威：``docs/modules/05-工资.md`` §2（BR-PR-08/09）、§3.6、§4.2、§5.5、§6.3、
§6.4、§8（S9）、§11（TC-07/TC-09）；``docs/08 §2.4``；``docs/04 §7.6.5/§7.6.6``。

## 三件同一事务内完成的事（08 §1.2 R4）

```
pay  APPROVED → PAID
  ├── 写 bank_payment_files（幂等：uq_bank_payment_files_sheet_id）+ 渲染 CSV
  ├── VoucherPort.create_payment(sheet)  → 回写 payment_voucher_no
  └── 记 paid_at，写 document_logs（谁 / 何时 / APPROVED → PAID）
```

凭证走唯一收敛点 :mod:`~app.modules.payroll.voucher_port`（同 T-PAY-007 的
``create_accrual``）；SSE ``payroll_settled`` **在提交之后**旁路发布（best-effort，
见 :mod:`~app.modules.payroll.events`），不参与事务。

## 前置校验

① 权限 ``payroll:pay``（否则 ``12001``）；② 状态可发放（``PAID`` → ``33001``，
其余非法状态 → ``10007``）；③ **所有** ``payroll_details.bank_account`` 非空
（缺卡 → ``10002``，``details.missing_lines`` 逐行给出缺卡的 ``line_no`` /
``employee_id``）—— 缺卡时**不发、不写文件、不调凭证端口**。

## CSV 与幂等

CSV 渲染在 :mod:`~.bank_file`（与导出共用同一实现）：UTF-8 带 BOM、6 列固定、
金额字符串两位小数、账号加引号保留前导 0。``bank_payment_files`` 的
``payroll_sheet_id`` 唯一（S9），``pay`` 与 :meth:`PayrollPayMixin.bank_file_export`
共用 :meth:`~PayrollPayMixin._ensure_bank_file` —— 记录已存在则复用（同 ``id`` / 同内容）。
``pay`` 本身**不幂等**：已 ``PAID`` 再 ``pay`` → ``33001``（业务拒绝，``05 §6.3``）。
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import DocumentAction, DocumentStatus
from app.core.db import unit_of_work
from app.core.errors import BusinessError, ErrorCode
from app.core.permissions import AuthContext
from app.modules.auth.models import User
from app.modules.payroll.events import SettledEvent, publish_paid_event
from app.modules.payroll.models import BankPaymentFile, PayrollDetail, PayrollSheet
from app.modules.payroll.service.bank_file import (
    BankFileExport,
    BankRow,
    new_bank_file,
    pay_log_fields,
    render_bank_csv,
)

if TYPE_CHECKING:
    from app.modules.payroll.voucher_port import VoucherPort

logger = logging.getLogger("app.payroll")


class PayrollPayMixin:
    """``pay`` + 代发文件（由 :class:`~.sheet.PayrollSheetService` 组装）。

    ⚠️ 本 Mixin **不重新实现** ``_apply_status`` / ``_write_log`` / ``_assert_transition``
    / ``_assert_can``：它们唯一的一份在 :class:`~.sheet_state_mixin.SheetStateMixin` /
    :class:`~.sheet_state_support.SheetStoreMixin`，这里只在 ``TYPE_CHECKING`` 下声明形状
    （同 :class:`~.sheet_approve.SheetApproveMixin`）。
    """

    session: AsyncSession
    voucher_port: VoucherPort

    if TYPE_CHECKING:

        def _assert_can(self, action: str, ctx: AuthContext) -> None: ...
        def _assert_transition(
            self, action: str, from_status: DocumentStatus, to_status: DocumentStatus
        ) -> None: ...
        def _assert_workshop_in_scope(self, workshop_id: UUID, ctx: AuthContext) -> None: ...
        async def _get_sheet(self, sheet_id: UUID) -> PayrollSheet: ...
        async def _apply_status(
            self,
            sheet: PayrollSheet,
            *,
            to_status: DocumentStatus,
            expected_version: int,
            operator_id: UUID,
            extra: dict[str, Any] | None = None,
        ) -> None: ...
        async def _write_log(
            self,
            sheet: PayrollSheet,
            operator_id: UUID,
            action: str,
            from_status: str | None,
            to_status: str,
            reason: str | None = None,
            changed_fields: dict[str, Any] | None = None,
        ) -> None: ...
        async def _reloaded(self, sheet_id: UUID) -> PayrollSheet: ...

    # ================================================================== pay

    async def pay(
        self,
        sheet_id: UUID,
        *,
        expected_version: int,
        operator_id: UUID,
        ctx: AuthContext,
        pay_date: date | None = None,
    ) -> PayrollSheet:
        """发放（``APPROVED`` → ``PAID``）：生成代发文件 + 付款凭证 + 记 ``paid_at``。

        副作用（同一事务）：写 ``bank_payment_files``（幂等）+ 渲染 CSV →
        ``VoucherPort.create_payment(sheet)`` → 写 ``document_logs``。
        提交**之后**best-effort 发 ``payroll_settled``。

        :param pay_date: 可选实际发放日（不传则不改既有 ``pay_date``）。
        :raises BusinessError: ``12001`` 无权限；``20001`` 单据不存在；``33001``
            已发放；``10007`` 非法状态；``10002`` 缺银行账号；``10003`` 乐观锁。
        """
        self._assert_can("pay", ctx)
        settled_event: SettledEvent
        async with unit_of_work(self.session):
            sheet = await self._get_sheet(sheet_id)
            self._assert_not_already_paid(sheet)
            self._assert_transition("pay", sheet.status, DocumentStatus.PAID)
            details = await self._load_details(sheet.id)
            self._assert_bank_accounts(sheet, details)
            record, _content = await self._ensure_bank_file(sheet, details, operator_id)
            voucher_no = await self.voucher_port.create_payment(sheet)
            paid_at = datetime.now(tz=UTC)
            extra: dict[str, Any] = {
                "paid_at": paid_at,
                "payment_voucher_no": voucher_no,
                "bank_file_id": record.id,
            }
            if pay_date is not None:
                extra["pay_date"] = pay_date
            # ⚠️ 日志先写、状态后迁：Core UPDATE 会 expire ORM 行，之后再读 doc_no 会 MissingGreenlet
            await self._write_log(
                sheet,
                operator_id,
                DocumentAction.PAY.value,
                sheet.status.value,
                DocumentStatus.PAID.value,
                changed_fields=pay_log_fields(sheet, record, voucher_no, paid_at),
            )
            await self._apply_status(
                sheet,
                to_status=DocumentStatus.PAID,
                expected_version=expected_version,
                operator_id=operator_id,
                extra=extra,
            )
            fresh = await self._reloaded(sheet_id)
            # 事务内拷成不可变快照：提交后 ORM 行可能过期（见 events.SettledEvent）
            settled_event = SettledEvent(
                sheet_id=fresh.id,
                doc_no=fresh.doc_no,
                period_code=fresh.period_code,
                workshop_id=fresh.workshop_id,
                employee_count=fresh.employee_count,
                total_net=fresh.total_net,
                paid_at=paid_at,
            )
        # 提交之后才发：发布失败只 WARN，不改「已发放」这个已落库的事实（05 §8）
        await publish_paid_event(settled_event)
        logger.info(
            "工资单已发放（代发文件 + 付款凭证生成点）",
            extra={
                "doc_no": fresh.doc_no,
                "total_net": str(fresh.total_net),
                "bank_file_id": str(fresh.bank_file_id),
                "payment_voucher_no": fresh.payment_voucher_no,
            },
        )
        return fresh

    # ================================================================== 代发导出

    async def bank_file_export(self, sheet_id: UUID, *, ctx: AuthContext) -> BankFileExport:
        """代发文件导出（``GET /payroll/sheets/{id}/bank-file``，权限 ``payroll:export``）。

        ⚠️ **唯一实现**：T-PAY-009 的导出路由必须调它，**不得**另起一套查询（``07 §3.2``
        铁律 3）。它与 :meth:`pay` 共用 :meth:`_ensure_bank_file` 与
        :func:`~.bank_file.render_bank_csv`，故「发放时生成的文件」与「导出的文件」
        逐字节一致（同 ``content_hash``）。

        前置：状态 ``APPROVED`` / ``PAID``（草稿 / 未审核不能出代发文件，``10008``）；
        全员 ``bank_account`` 非空（``10002``）。记录不存在则幂等创建（同一工资单只一行）。
        """
        self._assert_can("export", ctx)
        async with unit_of_work(self.session):
            sheet = await self._get_sheet(sheet_id)
            self._assert_workshop_in_scope(sheet.workshop_id, ctx)
            self._assert_exportable(sheet)
            details = await self._load_details(sheet.id)
            self._assert_bank_accounts(sheet, details)
            record, content = await self._ensure_bank_file(sheet, details, ctx.user_id)
            export = BankFileExport(
                file_name=record.file_name,
                content=content,
                record=record,
                row_count=record.row_count,
                total_amount=record.total_amount,
                content_hash=record.content_hash,
            )
        logger.info(
            "代发文件已导出（复用/创建代发记录）",
            extra={"doc_no": sheet.doc_no, "row_count": record.row_count},
        )
        return export

    # ================================================================== 代发文件（幂等）

    async def _ensure_bank_file(
        self, sheet: PayrollSheet, details: list[PayrollDetail], operator_id: UUID
    ) -> tuple[BankPaymentFile, str]:
        """幂等取 / 建 ``bank_payment_files``（``uq_bank_payment_files_sheet_id`` 兜底）。

        返回 ``(记录, CSV 文本)``。已存在则复用既有记录、**不新建**（一次导出 = 一条
        不可变事实，05 §6.3 / 04 §7.6.6）；并发下撞唯一约束时重查返回既有行。
        """
        rows = await self._build_bank_rows(details)
        content = render_bank_csv(rows)
        existing = await self._find_bank_file(sheet.id)
        if existing is not None:
            return existing, content
        record = new_bank_file(sheet, row_count=len(rows), content=content, operator_id=operator_id)
        if await self._insert_bank_file(record) is not None:
            return record, content
        again = await self._find_bank_file(sheet.id)
        if again is None:  # pragma: no cover —— 唯一冲突后必然查得到既有行
            raise BusinessError(
                ErrorCode.ILLEGAL_OPERATION,
                "代发文件记录写入冲突，请刷新后重试",
                details={"sheet_id": str(sheet.id)},
            )
        return again, content

    async def _find_bank_file(self, sheet_id: UUID) -> BankPaymentFile | None:
        return (
            await self.session.execute(
                select(BankPaymentFile).where(BankPaymentFile.payroll_sheet_id == sheet_id)
            )
        ).scalar_one_or_none()

    async def _insert_bank_file(self, record: BankPaymentFile) -> BankPaymentFile | None:
        """写一条 append-only 记录；撞唯一约束（并发）返回 ``None``，由调用方转幂等。"""
        try:
            async with self.session.begin_nested():
                self.session.add(record)
                await self.session.flush()
        except IntegrityError:
            return None
        return record

    async def _build_bank_rows(self, details: list[PayrollDetail]) -> list[BankRow]:
        """按**员工**聚合（同人多条工资行合计实发），并补 ``users`` 的工号 / 姓名。"""
        users = await self._load_users({detail.employee_id for detail in details})
        amounts: dict[UUID, Decimal] = {}
        snapshot: dict[UUID, dict[str, str]] = {}
        for detail in details:
            amounts[detail.employee_id] = amounts.get(detail.employee_id, Decimal("0")) + (
                detail.net_amount
            )
            snapshot.setdefault(
                detail.employee_id,
                {
                    "id_card": detail.id_card or "",
                    "bank_name": detail.bank_name or "",
                    "bank_account": detail.bank_account or "",
                },
            )
        rows: list[BankRow] = []
        for employee_id, amount in amounts.items():
            user = users.get(employee_id)
            bank = snapshot[employee_id]
            rows.append(
                BankRow(
                    employee_no=user.employee_no if user is not None else "",
                    name=user.name if user is not None else "",
                    id_card=bank["id_card"],
                    bank_name=bank["bank_name"],
                    bank_account=bank["bank_account"],
                    amount=amount,
                )
            )
        rows.sort(key=lambda row: (row.employee_no, row.name))
        return rows

    async def _load_users(self, employee_ids: set[UUID]) -> dict[UUID, User]:
        if not employee_ids:
            return {}
        users = (
            (await self.session.execute(select(User).where(User.id.in_(employee_ids))))
            .scalars()
            .all()
        )
        return {user.id: user for user in users}

    async def _load_details(self, sheet_id: UUID) -> list[PayrollDetail]:
        """本单有效工资行，按 ``line_no``（CSV 与缺卡检查共用，只查一次）。"""
        rows = await self.session.execute(
            select(PayrollDetail)
            .where(PayrollDetail.doc_id == sheet_id, PayrollDetail.deleted_at.is_(None))
            .order_by(PayrollDetail.line_no)
        )
        return list(rows.scalars().all())

    # ================================================================== 校验

    @staticmethod
    def _assert_not_already_paid(sheet: PayrollSheet) -> None:
        """已发放不可再发放（``33001``）。⚠️ 须先于 :meth:`_assert_transition`：

        ``PAID`` 不在 ``pay`` 的起始集里，先走状态机会报 ``10007``，语义不对
        （同 ``reverse`` 对 ``PAID`` 的处理，``05 §6.3``：已 ``PAID`` 再 pay → ``33001``）。
        """
        if sheet.status is not DocumentStatus.PAID:
            return
        raise BusinessError(
            ErrorCode.PAYROLL_SHEET_ALREADY_PAID,
            "工资单已发放，不能重复发放",
            details={"sheet_id": str(sheet.id), "doc_no": sheet.doc_no},
        )

    @staticmethod
    def _assert_bank_accounts(sheet: PayrollSheet, details: list[PayrollDetail]) -> None:
        """所有工资行 ``bank_account`` 非空（``05 §4.2`` / §5.5）。

        缺失**逐行**报出（``line_no`` + ``employee_id``），便于前端直接跳员工资料；
        缺卡时调用方**不写代发文件、不调凭证端口**（本方法抛错即整体回滚）。
        """
        missing = [item for item in details if not (item.bank_account or "").strip()]
        if not missing:
            return
        raise BusinessError(
            ErrorCode.MISSING_BUSINESS_PARAM,
            "部分员工缺少银行账号，不能发放；请先在员工资料中补齐银行卡信息",
            details={
                "doc_no": sheet.doc_no,
                "missing_line_count": len(missing),
                "missing_lines": [
                    {"line_no": item.line_no, "employee_id": str(item.employee_id)}
                    for item in missing
                ],
            },
        )

    @staticmethod
    def _assert_exportable(sheet: PayrollSheet) -> None:
        """代发导出仅限已审核单据（``APPROVED`` / ``PAID``）—— 草稿金额未冻结，不应出代发文件。"""
        if sheet.status in (DocumentStatus.APPROVED, DocumentStatus.PAID):
            return
        raise BusinessError(
            ErrorCode.ILLEGAL_OPERATION,
            "工资单尚未审核通过，不能导出代发文件",
            details={"doc_no": sheet.doc_no, "status": sheet.status.value},
        )


__all__ = ["PayrollPayMixin"]
