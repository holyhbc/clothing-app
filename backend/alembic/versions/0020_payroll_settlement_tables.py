"""0020 结算域三表 + `payroll_period_status` 枚举 + `piecework_logs.payroll_id` 补 FK（T-PAY-001）。

建表权威来源：``docs/04 §7.6.1``（``payroll_periods``）、``§7.6.2``
（``piecework_settlements``）、``§7.6.3``（``piecework_settlement_lines``）、
``docs/04 §2``（公共字段）、``docs/04 §7.3``（单据公共列）。
索引照 ``docs/modules/05-工资 §8``。

只留迁移特有的四件事（其余一律指 04，不复述）：

1. 顺带建 PG 枚举 ``payroll_period_status``（``OPEN`` / ``CLOSED`` 一次建全；
   后续新增走 ``ALTER TYPE ... ADD VALUE``，AGENTS §5 禁止改 enum 值本身）。
   ⚠️ **不** ``ALTER TYPE document_status ADD VALUE 'PAID'`` —— 迁移 0009 的
   ``DOCUMENT_STATUS_VALUES`` 已含 ``PAID``（实施说明 §2.2 已更正）。
2. 三张表：``payroll_periods``（周期，非单据但有极简状态机）→ ``piecework_settlements``
   （结算单，单据号 ``PW-``）→ ``piecework_settlement_lines``（结算行）。
3. 给 ``piecework_logs.payroll_id`` 补上 ``REFERENCES piecework_settlements(id)``。
   ⚠️ **不加 ``ON DELETE``**：结算单只软删（INV-7），PG 默认 ``NO ACTION`` 即可；
   迁移 0019 当时刻意只建列不建 FK（那时结算表还不存在），本卡补上。
4. 授权边界：三张都是软删表 → ``REVOKE DELETE``（``04 §6.2.1`` 双保险）。

⚠️ ``payroll_sheets`` / ``payroll_details`` / ``bank_payment_files`` 属 **T-PAY-002**（迁移 0021）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811 —— 照抄 docs/04 §2

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: ``docs/04 §7.6.1`` 的 ``CREATE TYPE payroll_period_status``（周期两个值一次建全）。
PAYROLL_PERIOD_STATUS_VALUES: tuple[str, ...] = ("OPEN", "CLOSED")
_CREATED_ENUMS: dict[str, tuple[str, ...]] = {"payroll_period_status": PAYROLL_PERIOD_STATUS_VALUES}

#: ⚠️ ``create_type=False`` 必需：否则 create_table 会**自己再发一条** ``CREATE TYPE``，
#: 与本迁移手写的那条撞车（``type already exists``）。抄 0014 / 0019 同款。
_PAYROLL_PERIOD_STATUS = postgresql.ENUM(
    *PAYROLL_PERIOD_STATUS_VALUES, name="payroll_period_status", create_type=False
)
#: ``document_status`` 由迁移 0009 建，这里只引用（含 ``PAID``）。
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
#: ``piecework_log_type`` 由迁移 0019 建，这里只引用（结算行快照原始流水类型）。
_PIECEWORK_LOG_TYPE = postgresql.ENUM(
    "SCAN",
    "MANUAL",
    "REVERSAL",
    "SUPPLEMENT",
    name="piecework_log_type",
    create_type=False,
)

#: ``04 §2`` 公共字段。⚠️ 三张表**都**是软删 + 乐观锁（周期重开、结算单反审核
#: 都走条件 UPDATE，INV-7）。
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

#: 先父后子（``settlements`` 引用 ``payroll_periods``；``lines`` 引用 ``settlements``）。
#: downgrade 反向。
_PAYROLL_TABLES: tuple[str, ...] = (
    "payroll_periods",
    "piecework_settlements",
    "piecework_settlement_lines",
)


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
    _create_enums()
    _create_payroll_periods()
    _create_piecework_settlements()
    _create_piecework_settlement_lines()
    _add_payroll_id_foreign_key()
    _create_soft_delete_indexes()
    _revoke_mutations()


def downgrade() -> None:
    # ⚠️ 先摘 ``payroll_id`` 外键，再删表：结算表被删时若外键还在，PG 找不到目标表。
    op.drop_constraint("fk_piecework_logs_payroll", "piecework_logs", type_="foreignkey")
    for table in reversed(_PAYROLL_TABLES):
        op.drop_table(table)
    # ⚠️ 用 ``IF EXISTS`` 无条件删，而**不是**靠 ``config.attributes`` 里 upgrade 时
    # 记下的「我建过」标记：CLI 的 ``upgrade`` / ``downgrade`` 是两个进程，那份标记
    # 在 downgrade 时是空的（0014 的 TC-B07 / 0019 实测撞到 —— 枚举没被删）。
    for name in _CREATED_ENUMS:
        op.execute(f"DROP TYPE IF EXISTS {name}")


def _create_enums() -> None:
    """建 ``payroll_period_status``（PG 无 ``CREATE TYPE IF NOT EXISTS``，先查 ``pg_type``）。"""
    for name, values in _CREATED_ENUMS.items():
        exists = (
            op.get_bind()
            .execute(sa.text("SELECT 1 FROM pg_type WHERE typname = :name"), {"name": name})
            .scalar()
        )
        if exists:
            continue
        literals = ", ".join(f"'{value}'" for value in values)  # 常量元组，非外部输入
        op.execute(f"CREATE TYPE {name} AS ENUM ({literals})")


def _create_soft_delete_indexes() -> None:
    """``SoftDeleteMixin`` 的 ``deleted_at`` 声明了 ``index=True``，每张软删表都要有。

    列表查询恒带 ``WHERE deleted_at IS NULL``，没索引就是全表扫（抄 0014 / 0019 同款）。
    """
    for table in _PAYROLL_TABLES:
        op.create_index(f"ix_{table}_deleted_at", table, ["deleted_at"], unique=False)


def _revoke_mutations() -> None:
    """软删表 ``REVOKE DELETE``（``04 §6.2.1``；纠错手段只有红冲/反审核，INV-7）。"""
    if not _app_role_exists():
        return
    for table in _PAYROLL_TABLES:
        op.execute(f"REVOKE DELETE ON {table} FROM erp_app")


def _app_role_exists() -> bool:
    query = sa.text("SELECT 1 FROM pg_roles WHERE rolname = :name")
    return bool(op.get_bind().execute(query, {"name": "erp_app"}).scalar())


def _create_payroll_periods() -> None:
    """工资结算周期（``04 §7.6.1``，逐字照抄）。

    ⚠️ 周期**不是单据**（无 ``doc_no`` / ``doc_date``），但有独立的状态枚举
    ``payroll_period_status``（``OPEN`` / ``CLOSED``）与极简状态机（``05 §4.3``）。
    ``uq_payroll_periods_range`` 让「同一区间重复建周期」在库层面就被拦住
    （BR-PR-16 / ADR-0010 §1：区间唯一性不靠服务端 if）。
    """
    op.create_table(
        "payroll_periods",
        *_audit_cols(),
        sa.Column("period_code", sa.String(32), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column(
            "status",
            _PAYROLL_PERIOD_STATUS,
            server_default=sa.text("'OPEN'"),
            nullable=False,
        ),
        sa.Column("source", sa.String(16), server_default=sa.text("'MANUAL'"), nullable=False),
        sa.Column("closed_by", PgUUID(as_uuid=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("close_reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_payroll_periods"),
        sa.UniqueConstraint("period_code", name="uq_payroll_periods_code"),
        sa.UniqueConstraint("start_date", "end_date", name="uq_payroll_periods_range"),
        _ver("payroll_periods"),
        sa.CheckConstraint("end_date >= start_date", name="ck_payroll_periods_range"),
        comment="工资结算周期（04 §7.6.1；用户自定义起止区间，ADR-0010）",
    )
    op.create_index(
        "idx_payroll_periods_status_start",
        "payroll_periods",
        ["status", "start_date"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def _create_piecework_settlements() -> None:
    """计件结算单（``04 §7.6.2``，逐字照抄；单据号 ``PW-``）。

    ⚠️ ``uq_piecework_settlements_period_workshop UNIQUE (period_id, workshop_id)``
    是 BR-PR-01 / Q-PL08 的权威口径：组别降为**行级**维度（``lines.group_no``），
    不在单头唯一键里。
    """
    op.create_table(
        "piecework_settlements",
        *_audit_cols(),
        sa.Column("doc_no", sa.String(32), nullable=False),
        sa.Column("status", _DOCUMENT_STATUS, server_default=sa.text("'DRAFT'"), nullable=False),
        sa.Column("doc_date", sa.Date(), nullable=False),
        sa.Column(
            "period_id",
            PgUUID(as_uuid=True),
            # ⚠️ 不加 ON DELETE：周期只软删/关闭，不物理删（INV-7）
            sa.ForeignKey("payroll_periods.id", name="fk_piecework_settlements_period"),
            nullable=False,
        ),
        sa.Column("period_code", sa.String(32), nullable=False),
        sa.Column("period_start_date", sa.Date(), nullable=False),
        sa.Column("period_end_date", sa.Date(), nullable=False),
        sa.Column("workshop_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("group_count", sa.Integer(), nullable=False),
        sa.Column("total_qty", sa.Numeric(14, 3), nullable=False),
        sa.Column("total_amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("employee_count", sa.Integer(), nullable=False),
        sa.Column("line_count", sa.Integer(), nullable=False),
        sa.Column("approved_by", PgUUID(as_uuid=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_reason", sa.Text(), nullable=True),
        sa.Column("cancelled_reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_piecework_settlements"),
        sa.UniqueConstraint("doc_no", name="uq_piecework_settlements_doc_no"),
        sa.UniqueConstraint(
            "period_id", "workshop_id", name="uq_piecework_settlements_period_workshop"
        ),
        _ver("piecework_settlements"),
        sa.CheckConstraint("total_amount >= 0", name="ck_piecework_settlements_amount"),
        comment="计件结算单（04 §7.6.2；PW-，审核后回写 piecework_logs.payroll_id）",
    )
    op.create_index(
        "idx_piecework_settlements_workshop_period",
        "piecework_settlements",
        ["workshop_id", "period_id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def _create_piecework_settlement_lines() -> None:
    """结算单行（``04 §7.6.3``，逐字照抄）。

    ⚠️ ``uq_piecework_settlement_lines_piecework_log_id`` **唯一**：一条流水只能
    被一张结算单占用 —— 这是「不重复计入」的最后一道库级兜底（P3 / INV-2）。
    """
    op.create_table(
        "piecework_settlement_lines",
        *_audit_cols(),
        sa.Column(
            "doc_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey(
                "piecework_settlements.id",
                name="fk_piecework_settlement_lines_doc",
                ondelete="RESTRICT",
            ),
            nullable=False,
        ),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column(
            "piecework_log_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("piecework_logs.id", name="fk_piecework_settlement_lines_log"),
            nullable=False,
        ),
        sa.Column("employee_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("group_no", sa.String(32), nullable=False),
        sa.Column("style_no", sa.String(32), nullable=False),
        sa.Column("operation_no", sa.String(16), nullable=False),
        sa.Column("work_date", sa.Date(), nullable=False),
        sa.Column("log_type", _PIECEWORK_LOG_TYPE, nullable=False),
        sa.Column("qty", sa.Numeric(14, 3), nullable=False),
        sa.Column("unit_price", sa.Numeric(12, 6), nullable=False),
        sa.Column("amount", sa.Numeric(18, 4), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_piecework_settlement_lines"),
        sa.UniqueConstraint("doc_id", "line_no", name="uq_piecework_settlement_lines_line"),
        sa.UniqueConstraint(
            "piecework_log_id", name="uq_piecework_settlement_lines_piecework_log_id"
        ),
        _ver("piecework_settlement_lines"),
        comment="结算单行（04 §7.6.3；一条流水一行，防重复计入）",
    )
    op.create_index(
        "idx_piecework_settlement_lines_doc_id",
        "piecework_settlement_lines",
        ["doc_id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def _add_payroll_id_foreign_key() -> None:
    """给 ``piecework_logs.payroll_id`` 补 FK（迁移 0019 刻意只建列）。

    ⚠️ **不加 ``ON DELETE``**：结算单只软删（INV-7），PG 默认 ``NO ACTION`` 即可；
    照实施说明 §4「只建 REFERENCES」。
    """
    op.create_foreign_key(
        "fk_piecework_logs_payroll",
        "piecework_logs",
        "piecework_settlements",
        ["payroll_id"],
        ["id"],
    )
