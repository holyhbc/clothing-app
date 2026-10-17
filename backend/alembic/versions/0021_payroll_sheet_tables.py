"""0021 工资单域三表（T-PAY-002）。

建表权威来源：``docs/04 §7.6.4``（``payroll_sheets``）、``§7.6.5``
（``payroll_details``）、``§7.6.6``（``bank_payment_files``）、
``docs/04 §2``（公共字段）、``docs/04 §7.3``（单据公共列）。
索引照 ``docs/modules/05-工资 §8``。

只留迁移特有的四件事（其余一律指 04，不复述）：

1. 三张表：``payroll_sheets``（工资单，单据号 ``PR-``）→ ``payroll_details``
   （工资单行）→ ``bank_payment_files``（代发文件记录，append-only）。
   ⚠️ ``payroll_sheets.settlement_id`` FK → ``piecework_settlements(id)``
   （迁移 0020 建），故 ``down_revision = "0020"``。
2. **不加** ``ALTER TYPE document_status ADD VALUE 'PAID'`` —— ``PAID`` 已在
   迁移 0009 的 ``DOCUMENT_STATUS_VALUES`` 建出（实施说明 §2.2 已更正）。
3. 授权边界：两张软删表（``payroll_sheets`` / ``payroll_details``）``REVOKE DELETE``；
   ``bank_payment_files`` 是 append-only 留痕 → ``REVOKE UPDATE, DELETE``
   （``04 §6.2.1`` 双保险，抄 0014 / 0020）。
4. ``bank_payment_files`` 按 ``bundle_label_prints``（Q-B14）**append-only** 口径：
   公共字段仅 ``id`` + ``created_at`` + ``created_by``，**无** ``version`` /
   ``deleted_at`` / ``updated_*`` / ``remark``；重复导出幂等返回同一行。

⚠️ ``payroll_periods`` / ``piecework_settlements`` / ``piecework_settlement_lines``
属 **T-PAY-001**（迁移 0020）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811 —— 照抄 docs/04 §2

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: ``document_status`` 由迁移 0009 建（**含 ``PAID``**），这里只引用，不重建。
_DOCUMENT_STATUS = postgresql.ENUM(
    "DRAFT",
    "SUBMITTED",
    "APPROVED",
    "REJECTED",
    "CANCELLED",
    "PAID",
    name="document_status",
    create_type=False,
)

#: ``04 §2`` 公共字段（软删 + 乐观锁）。
_AUDIT: tuple[sa.Column, ...] = (
    sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.Column("created_by", PgUUID(as_uuid=True), nullable=False),
    sa.Column(
        "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.Column("updated_by", PgUUID(as_uuid=True), nullable=False),
    sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
    sa.Column("remark", sa.Text(), nullable=True),
)

#: append-only（``04 §7.6.6``）：无 version / deleted_at / updated_*，也不设 remark。
_APPEND_ONLY_COLS: tuple[sa.Column, ...] = (
    sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.Column("created_by", PgUUID(as_uuid=True), nullable=False),
)

#: 先父后子（``details`` 引用 ``sheets``；``bank_payment_files`` 引用 ``sheets``）。
_ALL_TABLES: tuple[str, ...] = ("payroll_sheets", "payroll_details", "bank_payment_files")
_SOFT_DELETE_TABLES: tuple[str, ...] = ("payroll_sheets", "payroll_details")
_APPEND_ONLY_TABLES: tuple[str, ...] = ("bank_payment_files",)


def _pk() -> sa.Column:
    return sa.Column(
        "id", PgUUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False
    )


def _audit_cols() -> list[sa.Column]:
    """``04 §2`` 公共字段 + ``id``。"""
    return [_pk(), *_AUDIT]


def _ver(table: str) -> sa.CheckConstraint:
    return sa.CheckConstraint("version > 0", name=f"ck_{table}_version_positive")


def upgrade() -> None:
    _create_payroll_sheets()
    _create_payroll_details()
    _create_bank_payment_files()
    _create_soft_delete_indexes()
    _revoke_mutations()


def downgrade() -> None:
    # ⚠️ 反向删表（先子后父）：details / bank_payment_files 都引用 sheets。
    for table in reversed(_ALL_TABLES):
        op.drop_table(table)
    # ⚠️ 不 DROP ``document_status``：它由迁移 0009 建，本迁移只引用（含 PAID）。


def _create_soft_delete_indexes() -> None:
    """``SoftDeleteMixin`` 的 ``deleted_at`` 声明了 ``index=True``，每张软删表都要有。

    列表查询恒带 ``WHERE deleted_at IS NULL``，没索引就是全表扫（抄 0014 / 0020 同款）。
    """
    for table in _SOFT_DELETE_TABLES:
        op.create_index(f"ix_{table}_deleted_at", table, ["deleted_at"], unique=False)


def _revoke_mutations() -> None:
    """软删表 ``REVOKE DELETE``；append-only 留痕表 ``REVOKE UPDATE, DELETE``（04 §6.2.1）。"""
    if not _app_role_exists():
        return
    for table in _SOFT_DELETE_TABLES:
        op.execute(f"REVOKE DELETE ON {table} FROM erp_app")
    for table in _APPEND_ONLY_TABLES:
        op.execute(f"REVOKE UPDATE, DELETE ON {table} FROM erp_app")


def _app_role_exists() -> bool:
    query = sa.text("SELECT 1 FROM pg_roles WHERE rolname = :name")
    return bool(op.get_bind().execute(query, {"name": "erp_app"}).scalar())


def _create_payroll_sheets() -> None:
    """工资单（``04 §7.6.4``，逐字照抄；单据号 ``PR-``）。

    ⚠️ ``uq_payroll_sheets_settlement_id`` **唯一** + ``settlement_id`` NOT NULL：
    阶段一「1 工资单 : 1 结算单」（Q-PL06）。``(period_id, workshop_id)`` 的唯一性
    由结算单唯一**间接**保证（BR-PR-01），不重复建约束。
    ⚠️ ``doc_date`` 是 ``04 §7.3`` 单据公共列（``modules/05 §3.4`` 原字段表漏列，R11）。
    """
    op.create_table(
        "payroll_sheets",
        *_audit_cols(),
        sa.Column("doc_no", sa.String(32), nullable=False),
        sa.Column("status", _DOCUMENT_STATUS, server_default=sa.text("'DRAFT'"), nullable=False),
        sa.Column("doc_date", sa.Date(), nullable=False),
        sa.Column(
            "period_id",
            PgUUID(as_uuid=True),
            # ⚠️ 不加 ON DELETE：周期只软删 / 关闭（INV-7）
            sa.ForeignKey("payroll_periods.id", name="fk_payroll_sheets_period"),
            nullable=False,
        ),
        sa.Column("period_code", sa.String(32), nullable=False),
        sa.Column("workshop_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("pay_date", sa.Date(), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("employee_count", sa.Integer(), nullable=False),
        sa.Column("detail_count", sa.Integer(), nullable=False),
        sa.Column("total_gross", sa.Numeric(18, 4), nullable=False),
        sa.Column("total_deduction", sa.Numeric(18, 4), nullable=False),
        sa.Column("total_net", sa.Numeric(18, 4), nullable=False),
        sa.Column(
            "settlement_id",
            PgUUID(as_uuid=True),
            # ⚠️ 不加 ON DELETE：结算单只软删（INV-7）
            sa.ForeignKey("piecework_settlements.id", name="fk_payroll_sheets_settlement"),
            nullable=False,
        ),
        sa.Column("accrual_voucher_no", sa.String(32), nullable=True),
        sa.Column("payment_voucher_no", sa.String(32), nullable=True),
        sa.Column("bank_file_id", PgUUID(as_uuid=True), nullable=True),
        sa.Column("approved_by", PgUUID(as_uuid=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_reason", sa.Text(), nullable=True),
        sa.Column("cancelled_reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_payroll_sheets"),
        sa.UniqueConstraint("doc_no", name="uq_payroll_sheets_doc_no"),
        sa.UniqueConstraint("settlement_id", name="uq_payroll_sheets_settlement_id"),
        _ver("payroll_sheets"),
        sa.CheckConstraint(
            "total_net = total_gross - total_deduction", name="ck_payroll_sheets_net"
        ),
        sa.CheckConstraint(
            "total_gross >= 0 AND total_deduction >= 0", name="ck_payroll_sheets_amount"
        ),
        comment="工资单（04 §7.6.4；PR-，1:1 来源结算单）",
    )
    op.create_index(
        "idx_payroll_sheets_workshop_period",
        "payroll_sheets",
        ["workshop_id", "period_id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def _create_payroll_details() -> None:
    """工资单行（``04 §7.6.5``，逐字照抄）。

    ⚠️ ``uq_payroll_details_sheet_employee_operation_style`` 是 BR-PR-05 的汇总粒度。
    """
    op.create_table(
        "payroll_details",
        *_audit_cols(),
        sa.Column(
            "doc_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey(
                "payroll_sheets.id",
                name="fk_payroll_details_doc",
                ondelete="RESTRICT",
            ),
            nullable=False,
        ),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("employee_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("style_no", sa.String(32), nullable=False),
        sa.Column("operation_no", sa.String(16), nullable=False),
        sa.Column("qty", sa.Numeric(14, 3), nullable=False),
        sa.Column("unit_price", sa.Numeric(12, 6), nullable=False),
        sa.Column("gross_amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("deduction_amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("net_amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("deduction_type", sa.String(32), nullable=True),
        sa.Column("id_card", sa.String(32), nullable=True),
        sa.Column("bank_name", sa.String(128), nullable=True),
        sa.Column("bank_account", sa.String(64), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_payroll_details"),
        sa.UniqueConstraint("doc_id", "line_no", name="uq_payroll_details_line"),
        sa.UniqueConstraint(
            "doc_id",
            "employee_id",
            "style_no",
            "operation_no",
            name="uq_payroll_details_sheet_employee_operation_style",
        ),
        _ver("payroll_details"),
        sa.CheckConstraint(
            "net_amount = gross_amount - deduction_amount", name="ck_payroll_details_net"
        ),
        comment="工资单行（04 §7.6.5；每人每工序，金额快照）",
    )
    op.create_index(
        "idx_payroll_details_doc_id",
        "payroll_details",
        ["doc_id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def _create_bank_payment_files() -> None:
    """代发文件记录（**append-only**，``04 §7.6.6``）。

    ⚠️ 公共字段只有 ``id`` + ``created_at`` + ``created_by``（同 §7.16
    ``bundle_label_prints`` / Q-B14）；``uq_bank_payment_files_sheet_id`` 唯一，
    重复导出幂等返回同一行。
    """
    op.create_table(
        "bank_payment_files",
        _pk(),
        sa.Column(
            "payroll_sheet_id",
            PgUUID(as_uuid=True),
            # ⚠️ 工资单只软删（INV-7）；RESTRICT 同 §7.16 留痕表口径
            sa.ForeignKey(
                "payroll_sheets.id",
                name="fk_bank_payment_files_sheet",
                ondelete="RESTRICT",
            ),
            nullable=False,
        ),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("total_amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("content_hash", sa.CHAR(64), nullable=False),
        sa.Column("exported_by", PgUUID(as_uuid=True), nullable=False),
        sa.Column("exported_at", sa.DateTime(timezone=True), nullable=False),
        *_APPEND_ONLY_COLS,
        sa.PrimaryKeyConstraint("id", name="pk_bank_payment_files"),
        sa.UniqueConstraint("payroll_sheet_id", name="uq_bank_payment_files_sheet_id"),
        sa.CheckConstraint("total_amount >= 0", name="ck_bank_payment_files_amount"),
        comment="代发文件记录（append-only；04 §7.6.6）",
    )
