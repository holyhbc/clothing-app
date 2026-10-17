"""工资单域三表（T-PAY-002 / 迁移 0021）。

DDL 权威来源：``docs/04 §7.6.4``（``payroll_sheets``）、``§7.6.5``
（``payroll_details``）、``§7.6.6``（``bank_payment_files``）、
``docs/04 §2``（公共字段）、``docs/04 §7.3``（单据公共列 / 行表约定）。

## 三张表的关系

```
piecework_settlements            结算单（来源，1:1）
└── payroll_sheets               工资单（PR-，来源结算单 1:1）
    ├── payroll_details          工资单行（每人每工序一行）
    └── bank_payment_files       代发文件记录（append-only，一工资单一行）
```

## 建模口径

1. **工资单是单据**：用通用 ``document_status``（值域含 ``PAID``）+ ``04 §7.3``
   单据公共列（含 ``doc_date``，R11 补齐）；工资单行按行表约定带 ``doc_id`` / ``line_no``。
2. **``bank_payment_files`` 是 append-only 留痕**：只有 ``id`` + ``created_at`` +
   ``created_by``，**没有** ``version`` / ``deleted_at`` / ``updated_*`` / ``remark``
   （口径同 :class:`app.modules.bundling.models.bundle.BundleLabelPrint`，Q-B14）。
   重复导出幂等返回同一行（``uq_bank_payment_files_sheet_id``）。
3. **外键**：``sheets.period_id`` → ``payroll_periods.id``、``sheets.settlement_id``
   → ``piecework_settlements.id``、``details.doc_id`` / ``bank_files.payroll_sheet_id``
   → ``payroll_sheets.id``（``ON DELETE RESTRICT``）。``workshop_id`` / ``employee_id``
   **不建外键**（同结算域口径）。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811 —— 别名照抄 docs/04 §2
from sqlalchemy.orm import Mapped, mapped_column

from app.common.enums import DocumentStatus
from app.common.models import Base, BaseModel, IdMixin
from app.modules.payroll.models.settlement import DOCUMENT_STATUS


class PayrollSheet(BaseModel):
    """工资单（``docs/04 §7.6.4``；单据号 ``PR-{YYYYMMDD}-{6位}``）。

    ⚠️ ``uq_payroll_sheets_settlement_id`` **唯一** + :attr:`settlement_id` NOT NULL：
    阶段一「1 工资单 : 1 结算单」（Q-PL06）。``(period_id, workshop_id)`` 的唯一性
    由 :attr:`settlement_id` 唯一**间接**保证 —— 结算单本身已按 ``(period_id, workshop_id)``
    唯一（BR-PR-01），故不重复建约束。

    ``status`` 用通用 :class:`DocumentStatus`（**不新建枚举**），值域额外含 ``PAID``
    —— 发放（``pay``）后置 ``PAID``，由 service 状态机约束（``05 §4.2``）。
    """

    __tablename__ = "payroll_sheets"

    __table_args__ = (
        UniqueConstraint("doc_no", name="uq_payroll_sheets_doc_no"),
        UniqueConstraint("settlement_id", name="uq_payroll_sheets_settlement_id"),
        CheckConstraint("version > 0", name="ck_payroll_sheets_version_positive"),
        CheckConstraint("total_net = total_gross - total_deduction", name="ck_payroll_sheets_net"),
        CheckConstraint(
            "total_gross >= 0 AND total_deduction >= 0", name="ck_payroll_sheets_amount"
        ),
        Index(
            "idx_payroll_sheets_workshop_period",
            "workshop_id",
            "period_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        {"comment": "工资单（04 §7.6.4；PR-，1:1 来源结算单）"},
    )

    doc_no: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[DocumentStatus] = mapped_column(
        DOCUMENT_STATUS,
        nullable=False,
        default=DocumentStatus.DRAFT,
        server_default=text("'DRAFT'"),
    )
    #: ★ ``04 §7.3`` 单据公共列（``modules/05 §3.4`` 原字段表漏列，R11）
    doc_date: Mapped[date] = mapped_column(Date, nullable=False)
    period_id: Mapped[UUID] = mapped_column(
        # ⚠️ 不加 ON DELETE：周期只软删 / 关闭（INV-7）
        ForeignKey("payroll_periods.id", name="fk_payroll_sheets_period"),
        nullable=False,
    )
    period_code: Mapped[str] = mapped_column(String(32), nullable=False)
    workshop_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    pay_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    employee_count: Mapped[int] = mapped_column(Integer, nullable=False)
    detail_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_gross: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    total_deduction: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    total_net: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    settlement_id: Mapped[UUID] = mapped_column(
        # ⚠️ 不加 ON DELETE：结算单只软删（INV-7）
        ForeignKey("piecework_settlements.id", name="fk_payroll_sheets_settlement"),
        nullable=False,
    )
    accrual_voucher_no: Mapped[str | None] = mapped_column(String(32), nullable=True)
    payment_voucher_no: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: ⚠️ 逻辑引用 ``bank_payment_files.id``，**不建 FK**：反向外键会成建表环（§7.6.4）
    bank_file_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    approved_by: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)
    cancelled_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)


class PayrollDetail(BaseModel):
    """工资单行（``docs/04 §7.6.5``；每人每工序一行）。

    ⚠️ ``uq_payroll_details_sheet_employee_operation_style`` 是 BR-PR-05 的**汇总粒度**。
    ``deduction_amount`` 阶段一恒 0、``deduction_type`` 只留列不实现录入（Q-PL01）。
    ``id_card`` / ``bank_name`` / ``bank_account`` 从 ``users`` **快照**到行上 ——
    发放后改卡不影响历史凭证。
    """

    __tablename__ = "payroll_details"

    __table_args__ = (
        UniqueConstraint("doc_id", "line_no", name="uq_payroll_details_line"),
        UniqueConstraint(
            "doc_id",
            "employee_id",
            "style_no",
            "operation_no",
            name="uq_payroll_details_sheet_employee_operation_style",
        ),
        CheckConstraint("version > 0", name="ck_payroll_details_version_positive"),
        CheckConstraint(
            "net_amount = gross_amount - deduction_amount", name="ck_payroll_details_net"
        ),
        Index(
            "idx_payroll_details_doc_id",
            "doc_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        {"comment": "工资单行（04 §7.6.5；每人每工序，金额快照）"},
    )

    doc_id: Mapped[UUID] = mapped_column(
        # ⚠️ ON DELETE RESTRICT（04 §7.3 行表约定）
        ForeignKey("payroll_sheets.id", name="fk_payroll_details_doc", ondelete="RESTRICT"),
        nullable=False,
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    employee_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    style_no: Mapped[str] = mapped_column(String(32), nullable=False)
    operation_no: Mapped[str] = mapped_column(String(16), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    gross_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    deduction_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    net_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    deduction_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    id_card: Mapped[str | None] = mapped_column(String(32), nullable=True)
    bank_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    bank_account: Mapped[str | None] = mapped_column(String(64), nullable=True)


class BankPaymentFile(IdMixin, Base):
    """代发文件记录（**append-only**，``docs/04 §7.6.6``）。

    ⚠️ **既不用 :class:`BaseModel` 也不用 :class:`AuditMixin`**：只有
    ``id`` + ``created_at`` + ``created_by``，**没有** ``version`` / ``deleted_at`` /
    ``updated_*``，也**不设 ``remark``**（同 ``bundle_label_prints`` / Q-B14）。
    一次导出 = 一条不可变事实；**重复导出幂等返回同一行**
    （``uq_bank_payment_files_sheet_id`` 兜底，``05 §6.3``）。
    """

    __tablename__ = "bank_payment_files"

    __table_args__ = (
        UniqueConstraint("payroll_sheet_id", name="uq_bank_payment_files_sheet_id"),
        CheckConstraint("total_amount >= 0", name="ck_bank_payment_files_amount"),
        {"comment": "代发文件记录（append-only；04 §7.6.6）"},
    )

    payroll_sheet_id: Mapped[UUID] = mapped_column(
        # ⚠️ 工资单只软删（INV-7）；RESTRICT 同 §7.16 留痕表口径
        ForeignKey("payroll_sheets.id", name="fk_bank_payment_files_sheet", ondelete="RESTRICT"),
        nullable=False,
    )
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    content_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    exported_by: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    exported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # ⚠️ append-only 的公共字段：只有 created_at / created_by（§7.6.6）
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    created_by: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
