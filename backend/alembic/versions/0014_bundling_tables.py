"""0014 打菲四表 + `bundle_status` 枚举（T-BUND-001）。

建表权威来源：``docs/04 §7.16``（单表 / 明细 / 标签打印）、``docs/04 §7.0``
（``bundles``，ADR-0016，``modules/03 §3.3`` 明令不得改写）、``docs/04 §7.3``
（单据公共列）、``docs/04 §2``（公共字段）。索引照 ``docs/modules/03 §8`` 场景。

只留迁移特有的三件事（其余一律指 04，不复述）：

1. 顺带建 PG 枚举 ``bundle_status``；``document_status``（迁移 0009 建）只引用不重建。
2. 前三张表是软删 + 乐观锁表（``_AUDIT``）；``bundle_label_prints`` 是 **append-only**
   （只有 ``created_at`` / ``created_by``，Q-B14 闭环、变更 0111）。
3. 权限边界：软删表 ``REVOKE DELETE``；留痕表 ``REVOKE UPDATE, DELETE``。

本迁移**零依赖** ``cutting_outputs``（``available_qty_before`` 只建列不建 FK），
可与 T-BASE-009-2 并行落地，谁先谁取当时的 ``head+1``。
``bundle_no`` 格式正则 CHECK 不落（Q-B18 未闭环，``3XL`` 会撞），由 T-BUND-002 保证。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811 —— 照抄 docs/04 §2

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: ``04 §7.0`` 末尾的 ``CREATE TYPE bundle_status``。
BUNDLE_STATUS_VALUES: tuple[str, ...] = ("ACTIVE", "VOIDED")
_CREATED_ENUMS: dict[str, tuple[str, ...]] = {"bundle_status": BUNDLE_STATUS_VALUES}

#: ``document_status`` 由迁移 0009 建，这里只引用。
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
#: ⚠️ ``create_type=False`` 必需：否则 create_table 会**自己再发一条** ``CREATE TYPE``，
#: 与本迁移手写的那条撞车（``type already exists``）。类型由 ``_create_enums`` 建。
_BUNDLE_STATUS = postgresql.ENUM(*BUNDLE_STATUS_VALUES, name="bundle_status", create_type=False)

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

#: append-only（``04 §7.16``）：无 version / deleted_at / updated_*，也不设 remark。
_APPEND_ONLY_COLS: tuple[sa.Column, ...] = (
    sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.Column("created_by", PgUUID(as_uuid=True), nullable=False),
)

#: 先父后子。downgrade 反向、权限收口按类分别用。
_BUNDLING_TABLES: tuple[str, ...] = (
    "bundling_orders",
    "bundling_order_lines",
    "bundles",
    "bundle_label_prints",
)
_SOFT_DELETE_TABLES: tuple[str, ...] = (
    "bundling_orders",
    "bundling_order_lines",
    "bundles",
)
_APPEND_ONLY_TABLES: tuple[str, ...] = ("bundle_label_prints",)


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
    _create_bundling_orders()
    _create_bundling_order_lines()
    _create_bundles()
    _create_bundle_label_prints()
    _create_soft_delete_indexes()
    _revoke_mutations()


def downgrade() -> None:
    # ⚠️ 先子后父：label_prints → bundles → lines → orders
    for table in reversed(_BUNDLING_TABLES):
        op.drop_table(table)
    # ⚠️ 用 ``IF EXISTS`` 无条件删，而**不是**靠 ``config.attributes`` 里 upgrade 时
    # 记下的「我建过」标记：CLI 的 ``upgrade`` / ``downgrade`` 是两个进程，那份标记
    # 在 downgrade 时是空的（T-BUND-001 的 TC-B07 实测撞到 —— 枚举没被删）。
    # ``bundle_status`` 只由本迁移创建、没有别的途径，删它不会误伤。
    for name in _CREATED_ENUMS:
        op.execute(f"DROP TYPE IF EXISTS {name}")


def _create_enums() -> None:
    """建 ``bundle_status``（PG 无 ``CREATE TYPE IF NOT EXISTS``，先查 ``pg_type``）。"""
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

    列表查询恒带 ``WHERE deleted_at IS NULL``，没索引就是全表扫（抄 0009 同款）。
    """
    for table in _SOFT_DELETE_TABLES:
        op.create_index(f"ix_{table}_deleted_at", table, ["deleted_at"], unique=False)


def _revoke_mutations() -> None:
    """软删表 ``REVOKE DELETE``；留痕表 ``REVOKE UPDATE, DELETE``（04 §6.2.1 双保险）。"""
    if not _app_role_exists():
        return
    for table in _SOFT_DELETE_TABLES:
        op.execute(f"REVOKE DELETE ON {table} FROM erp_app")
    for table in _APPEND_ONLY_TABLES:
        op.execute(f"REVOKE UPDATE, DELETE ON {table} FROM erp_app")


def _app_role_exists() -> bool:
    query = sa.text("SELECT 1 FROM pg_roles WHERE rolname = :name")
    return bool(op.get_bind().execute(query, {"name": "erp_app"}).scalar())


def _create_bundling_orders() -> None:
    """打菲单表头（``04 §7.16`` + §7.3）。``hands_total`` 的 CHECK 是 ``>= 0``：草稿态必然为 0。"""
    op.create_table(
        "bundling_orders",
        *_audit_cols(),
        sa.Column("doc_no", sa.String(32), nullable=False),
        sa.Column("status", _DOCUMENT_STATUS, server_default=sa.text("'DRAFT'"), nullable=False),
        sa.Column("doc_date", sa.Date(), nullable=False),
        sa.Column(
            "workshop_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("workshops.id", name="fk_bundling_orders_workshop"),
            nullable=False,
        ),
        sa.Column("approved_by", PgUUID(as_uuid=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_reason", sa.Text(), nullable=True),
        sa.Column("cancelled_reason", sa.Text(), nullable=True),
        sa.Column("style_no", sa.String(32), nullable=False),
        sa.Column(
            "operation_no",
            sa.String(16),
            sa.ForeignKey("operations.operation_no", name="fk_bundling_orders_operation"),
            nullable=False,
        ),
        sa.Column("color_group", sa.String(32), nullable=False),
        sa.Column("color_code", sa.String(16), nullable=False),
        sa.Column("bundle_qty", sa.Integer(), nullable=False),
        sa.Column("hands_total", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("output_qty", sa.Numeric(14, 3), nullable=False),
        sa.Column("balance_qty", sa.Numeric(14, 3), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "source_cutting_order_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("cutting_orders.id", name="fk_bundling_orders_source_cutting"),
            nullable=False,
        ),
        sa.Column("label_print_qty", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_bundling_orders"),
        sa.UniqueConstraint("doc_no", name="uq_bundling_orders_doc_no"),
        _ver("bundling_orders"),
        sa.CheckConstraint(
            "output_qty > 0 AND output_qty = trunc(output_qty)",
            name="ck_bundling_orders_output",
        ),
        sa.CheckConstraint("hands_total >= 0", name="ck_bundling_orders_hands"),
        comment="打菲单表头（04 §7.16；ADR-0016 按手打菲）",
    )
    op.create_index(
        "idx_bundling_orders_workshop_doc_date",
        "bundling_orders",
        ["workshop_id", sa.text("doc_date DESC")],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "idx_bundling_orders_status_workshop",
        "bundling_orders",
        ["status", "workshop_id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "idx_bundling_orders_style_operation_date",
        "bundling_orders",
        ["style_no", "operation_no", "doc_date"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def _create_bundling_order_lines() -> None:
    """打菲明细（``04 §7.16`` + §7.3 行表）。``available_qty_before`` 不建 FK（cutting_outputs 归 009-2）。"""
    op.create_table(
        "bundling_order_lines",
        *_audit_cols(),
        sa.Column(
            "doc_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey(
                "bundling_orders.id", name="fk_bundling_order_lines_doc", ondelete="RESTRICT"
            ),
            nullable=False,
        ),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("color_code", sa.String(16), nullable=False),
        sa.Column("size_code", sa.String(16), nullable=False),
        sa.Column("operation_no", sa.String(16), nullable=False),
        sa.Column(
            "cutting_size_line_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey(
                "cutting_order_size_lines.id",
                name="fk_bundling_order_lines_cutting_size_line",
            ),
            nullable=False,
        ),
        sa.Column("hands", sa.Integer(), nullable=False),
        sa.Column("planned_qty", sa.Numeric(14, 3), nullable=False),
        sa.Column("available_qty_before", sa.Numeric(14, 3), nullable=False),
        sa.Column("group_no", sa.String(32), nullable=True),
        sa.Column("workstation_no", sa.String(32), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_bundling_order_lines"),
        sa.UniqueConstraint("doc_id", "line_no", name="uq_bundling_order_lines_line"),
        _ver("bundling_order_lines"),
        sa.CheckConstraint("hands > 0", name="ck_bundling_lines_hands"),
        sa.CheckConstraint("planned_qty = trunc(planned_qty)", name="ck_bundling_lines_planned"),
        comment="打菲明细（按尺码；04 §7.16）",
    )
    op.create_index(
        "idx_bundling_order_lines_doc_size",
        "bundling_order_lines",
        ["doc_id", "size_code"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "idx_bundling_order_lines_cutting",
        "bundling_order_lines",
        ["cutting_size_line_id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def _create_bundles() -> None:
    """打菲件表（照抄 ``04 §7.0``，不得改写）。``cutting_size_line_id`` 不建 FK。"""
    op.create_table(
        "bundles",
        *_audit_cols(),
        sa.Column(
            "doc_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("bundling_orders.id", name="fk_bundles_doc", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "line_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("bundling_order_lines.id", name="fk_bundles_line", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("bundle_no", sa.String(64), nullable=False),
        sa.Column("hands", sa.Integer(), nullable=False),
        sa.Column("style_no", sa.String(32), nullable=False),
        sa.Column("color_code", sa.String(32), nullable=False),
        sa.Column("size_code", sa.String(32), nullable=False),
        sa.Column("operation_no", sa.String(16), nullable=False),
        sa.Column(
            "cutting_size_line_id",
            PgUUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("bundle_qty", sa.Numeric(14, 3), nullable=False),
        sa.Column("counted_qty", sa.Numeric(14, 3), server_default=sa.text("0"), nullable=False),
        sa.Column("counted_by", PgUUID(as_uuid=True), nullable=True),
        sa.Column("counted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", _BUNDLE_STATUS, server_default=sa.text("'ACTIVE'"), nullable=False),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("void_reason", sa.Text(), nullable=True),
        sa.Column("qr_content", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_bundles"),
        sa.UniqueConstraint("bundle_no", name="uq_bundles_no"),
        sa.UniqueConstraint("doc_id", "color_code", "size_code", "hands", name="uq_bundles_hand"),
        _ver("bundles"),
        sa.CheckConstraint(
            "bundle_qty > 0 AND bundle_qty = trunc(bundle_qty)", name="ck_bundles_qty"
        ),
        sa.CheckConstraint("counted_qty >= 0 AND counted_qty <= bundle_qty", name="ck_bundles_cnt"),
        sa.CheckConstraint("counted_qty = 0 OR counted_by IS NOT NULL", name="ck_bundles_one"),
        comment="打菲件表（一码一手；04 §7.0 / ADR-0016）",
    )
    op.create_index(
        "idx_bundles_counted_pending",
        "bundles",
        ["doc_id", "color_code", "size_code"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL AND status = 'ACTIVE' AND counted_at IS NULL"),
    )
    op.create_index(
        "idx_bundles_line_id",
        "bundles",
        ["line_id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "idx_bundles_cutting",
        "bundles",
        ["cutting_size_line_id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def _create_bundle_label_prints() -> None:
    """标签打印留痕（**append-only**，``04 §7.16``）。``bundle_no`` 不建 FK。"""
    op.create_table(
        "bundle_label_prints",
        _pk(),
        sa.Column("bundle_no", sa.String(64), nullable=False),
        sa.Column(
            "doc_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey(
                "bundling_orders.id", name="fk_bundle_label_prints_doc", ondelete="RESTRICT"
            ),
            nullable=False,
        ),
        sa.Column("hands_seq", sa.Integer(), nullable=False),
        sa.Column("hands_total_of_size", sa.Integer(), nullable=True),
        sa.Column("printed_qty", sa.Integer(), nullable=False),
        sa.Column("is_reprint", sa.Boolean(), nullable=False),
        sa.Column("print_seq", sa.Integer(), nullable=True),
        sa.Column("printed_by", PgUUID(as_uuid=True), nullable=False),
        sa.Column("printed_at", sa.DateTime(timezone=True), nullable=False),
        *_APPEND_ONLY_COLS,
        sa.PrimaryKeyConstraint("id", name="pk_bundle_label_prints"),
        comment="标签打印留痕（append-only；04 §7.16）",
    )
    # ⚠️ 不带 ``WHERE deleted_at IS NULL`` —— 那一列不存在（append-only）
    op.create_index(
        "idx_bundle_label_prints_bundle_no",
        "bundle_label_prints",
        ["bundle_no", sa.text("printed_at DESC")],
        unique=False,
    )
