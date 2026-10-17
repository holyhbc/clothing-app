"""结算域三表（T-PAY-001 / 迁移 0020）。

DDL 权威来源：``docs/04 §7.6.1``（``payroll_periods``）、``§7.6.2``
（``piecework_settlements``）、``§7.6.3``（``piecework_settlement_lines``）、
``docs/04 §2``（公共字段）、``docs/04 §7.3``（单据公共列）。
逐列照抄，约束与索引写成 ORM。

## 三张表的关系

```
payroll_periods                      周期（用户自定义起止区间，非单据，有独立状态机）
└── piecework_settlements            结算单（PW-，按「周期 × 车间」唯一）
    └── piecework_settlement_lines   结算行（一条计件流水一行，唯一占用）
```

## 三条贯穿三表的建模口径

1. **周期不是单据**：无 ``doc_no`` / ``doc_date``，状态走独立枚举
   ``payroll_period_status``（``OPEN`` / ``CLOSED``），不是通用 ``document_status``
   （``05 §4.3``）。区间唯一性靠 ``uq_payroll_periods_range`` 从库层面兜底
   （BR-PR-16 / ADR-0010 §1）。
2. **结算单是单据、结算行是行表**：结算单用 ``document_status`` 与 ``04 §7.3`` 的
   4 个审核列；结算行按 ``04 §7.3`` 行表约定带 ``doc_id`` / ``line_no``。
3. **外键只建两处**：``settlements.period_id`` → ``payroll_periods.id``、
   ``lines.doc_id`` → ``piecework_settlements.id``（``ON DELETE RESTRICT``）、
   ``lines.piecework_log_id`` → ``piecework_logs.id``。``workshop_id`` / ``employee_id``
   **不建外键**：车间停用重组、人事离职都是合法的档案维护动作（同 §7.1 口径），
   数据范围过滤走 ``app/core/scope.py`` 的列名约定。

## 本文件**不包含**的东西

无 relationship、无 service / repository / router —— 那些随 T-PAY-003 起补。
现在建空壳只会让「工资模块已就位」看起来比实际完整（同 ``piecework/__init__.py``）。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
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
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811 —— 别名照抄 docs/04 §2
from sqlalchemy.orm import Mapped, mapped_column

from app.common.enums import DocumentStatus
from app.common.models import BaseModel


class PayrollPeriodStatus(StrEnum):
    """结算周期状态（``docs/04 §7.6.1`` 的 ``CREATE TYPE payroll_period_status``）。

    ⚠️ 周期**不是单据**，故**不**复用通用 :class:`DocumentStatus`（``05 §4.3``）：

    - ``OPEN``：可往里加结算单；
    - ``CLOSED``：冻结（加单报 ``33003``），重开需 ``payroll:period:manage`` + 必填原因。

    木已成舟的既有结算单不受重开影响 —— 结算单是快照（ADR-0010）。
    """

    OPEN = "OPEN"
    CLOSED = "CLOSED"


#: PG enum ``payroll_period_status``，由迁移 0020 建。
#: ⚠️ ``create_type=False``：类型由迁移手写 ``CREATE TYPE`` 建，不加这个
#: SQLAlchemy 会**自己再发一条**，撞车报 ``type already exists``（0004/0007/0008/
#: 0009/0012/0014/0019 各踩过一次）。
PAYROLL_PERIOD_STATUS = SAEnum(PayrollPeriodStatus, name="payroll_period_status", create_type=False)

#: ``document_status``（迁移 0009 建，含 ``PAID``）—— 结算单复用，不重建。
DOCUMENT_STATUS = SAEnum(DocumentStatus, name="document_status", create_type=False)

#: 结算行快照的「原始流水类型」（PG enum 由迁移 0019 建）。
#: ⚠️ 刻意**不** import ``PieceworkLogType``：``piecework_logs`` 有指向本表的外键，
#: 若本模块反向 import 计件模块会成环。列语义仍是「原始流水类型」，值域与 §7.1 一致。
SETTLEMENT_LOG_TYPE = SAEnum(
    "SCAN", "MANUAL", "REVERSAL", "SUPPLEMENT", name="piecework_log_type", create_type=False
)


class PayrollPeriod(BaseModel):
    """工资结算周期（``docs/04 §7.6.1``；用户自定义起止日期区间，ADR-0010）。

    ⚠️ ``uq_payroll_periods_range UNIQUE (start_date, end_date)`` 是**库级**兜底：
    「同一区间重复建周期」不靠服务端 if（ADR-0010 §1）。``period_code`` 只是标签，
    随便写（``'2026-08'`` / ``'7-8月'`` / ``'夏季款'``），SQL 取数**不解析**标签
    （BR-PR-20）。``end_date >= start_date`` 由 ``ck_payroll_periods_range`` 兜住。
    """

    __tablename__ = "payroll_periods"

    __table_args__ = (
        UniqueConstraint("period_code", name="uq_payroll_periods_code"),
        UniqueConstraint("start_date", "end_date", name="uq_payroll_periods_range"),
        CheckConstraint("version > 0", name="ck_payroll_periods_version_positive"),
        CheckConstraint("end_date >= start_date", name="ck_payroll_periods_range"),
        Index(
            "idx_payroll_periods_status_start",
            "status",
            "start_date",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        {"comment": "工资结算周期（04 §7.6.1；用户自定义起止区间，ADR-0010）"},
    )

    period_code: Mapped[str] = mapped_column(String(32), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[PayrollPeriodStatus] = mapped_column(
        PAYROLL_PERIOD_STATUS,
        nullable=False,
        default=PayrollPeriodStatus.OPEN,
        server_default=text("'OPEN'"),
    )
    source: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="MANUAL",
        server_default=text("'MANUAL'"),
    )
    closed_by: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    close_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)


class PieceworkSettlement(BaseModel):
    """计件结算单（``docs/04 §7.6.2``；单据号 ``PW-{YYYYMMDD}-{6位}``）。

    ⚠️ ``uq_piecework_settlements_period_workshop UNIQUE (period_id, workshop_id)``
    是 BR-PR-01 / Q-PL08 的权威口径：**组别是行级维度**（
    :attr:`PieceworkSettlementLine.group_no`），不进单头唯一键。
    ``period_code`` / ``period_start_date`` / ``period_end_date`` 是**冗余快照**，
    取数一律走 ``period_id`` → 周期表读区间（BR-PR-20）。
    """

    __tablename__ = "piecework_settlements"

    __table_args__ = (
        UniqueConstraint("doc_no", name="uq_piecework_settlements_doc_no"),
        UniqueConstraint(
            "period_id", "workshop_id", name="uq_piecework_settlements_period_workshop"
        ),
        CheckConstraint("version > 0", name="ck_piecework_settlements_version_positive"),
        CheckConstraint("total_amount >= 0", name="ck_piecework_settlements_amount"),
        Index(
            "idx_piecework_settlements_workshop_period",
            "workshop_id",
            "period_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        {"comment": "计件结算单（04 §7.6.2；PW-，审核后回写 piecework_logs.payroll_id）"},
    )

    doc_no: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[DocumentStatus] = mapped_column(
        DOCUMENT_STATUS,
        nullable=False,
        default=DocumentStatus.DRAFT,
        server_default=text("'DRAFT'"),
    )
    doc_date: Mapped[date] = mapped_column(Date, nullable=False)
    period_id: Mapped[UUID] = mapped_column(
        # ⚠️ 不加 ON DELETE：周期只软删/关闭，不物理删（INV-7）
        ForeignKey("payroll_periods.id", name="fk_piecework_settlements_period"),
        nullable=False,
    )
    period_code: Mapped[str] = mapped_column(String(32), nullable=False)
    period_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    period_end_date: Mapped[date] = mapped_column(Date, nullable=False)
    workshop_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    group_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_qty: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    employee_count: Mapped[int] = mapped_column(Integer, nullable=False)
    line_count: Mapped[int] = mapped_column(Integer, nullable=False)
    approved_by: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)
    cancelled_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)


class PieceworkSettlementLine(BaseModel):
    """结算单行（``docs/04 §7.6.3``）。

    ⚠️ ``uq_piecework_settlement_lines_piecework_log_id`` **唯一**：一条计件流水只能
    被一张结算单占用 —— 这是「不重复计入」的最后一道库级兜底（P3 / INV-2）。
    审核时回写 ``piecework_logs.payroll_id``，反审核清空，均以
    ``WHERE payroll_id IS NULL`` 为条件。
    """

    __tablename__ = "piecework_settlement_lines"

    __table_args__ = (
        UniqueConstraint("doc_id", "line_no", name="uq_piecework_settlement_lines_line"),
        UniqueConstraint("piecework_log_id", name="uq_piecework_settlement_lines_piecework_log_id"),
        CheckConstraint("version > 0", name="ck_piecework_settlement_lines_version_positive"),
        Index(
            "idx_piecework_settlement_lines_doc_id",
            "doc_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        {"comment": "结算单行（04 §7.6.3；一条流水一行，防重复计入）"},
    )

    doc_id: Mapped[UUID] = mapped_column(
        # ⚠️ ON DELETE RESTRICT（04 §7.3 行表约定）：单头被物理删会让行成为孤儿
        ForeignKey(
            "piecework_settlements.id",
            name="fk_piecework_settlement_lines_doc",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    piecework_log_id: Mapped[UUID] = mapped_column(
        # ⚠️ 唯一索引才是「不重复计入」的兜底，外键只保证引用存在
        ForeignKey("piecework_logs.id", name="fk_piecework_settlement_lines_log"),
        nullable=False,
    )
    employee_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    group_no: Mapped[str] = mapped_column(String(32), nullable=False)
    style_no: Mapped[str] = mapped_column(String(32), nullable=False)
    operation_no: Mapped[str] = mapped_column(String(16), nullable=False)
    work_date: Mapped[date] = mapped_column(Date, nullable=False)
    log_type: Mapped[str] = mapped_column(SETTLEMENT_LOG_TYPE, nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
