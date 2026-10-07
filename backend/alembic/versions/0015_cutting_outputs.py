"""Create cutting_outputs and cutting_scrap_records tables.

T-BASE-009-2: P1 缺表建表第 2 张卡 - 裁剪结转与布头登记

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-06
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM, NUMERIC, UUID

from alembic import op

# revision identifiers, used by Alembic.
revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ===== ENUM: scrap_type =====
    scrap_type_enum = ENUM(
        "REUSABLE",
        "WASTE",
        name="scrap_type",
        create_type=False,  # created by this migration
    )
    scrap_type_enum.create(op.get_bind(), checkfirst=True)

    # ===== cutting_outputs =====
    op.create_table(
        "cutting_outputs",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "style_id",
            UUID(as_uuid=True),
            sa.ForeignKey("styles.id", name="fk_cutting_outputs_style"),
            nullable=False,
        ),
        sa.Column("style_no", sa.String(32), nullable=False, comment="冗余：历史结转按它引用"),
        sa.Column(
            "color_code",
            sa.String(32),
            nullable=False,
            comment="⚠️ 原字段表写 color_group，见 04 §7.7.5 第 1 条",
        ),
        sa.Column("size_code", sa.String(16), nullable=False, comment="C10：尺码级汇总"),
        sa.Column(
            "workshop_id",
            UUID(as_uuid=True),
            sa.ForeignKey("workshops.id", name="fk_cutting_outputs_workshop"),
            nullable=False,
            comment="数据范围过滤依据（INV-8）",
        ),
        sa.Column(
            "output_qty",
            NUMERIC(14, 3),
            nullable=False,
            server_default=sa.text("0"),
            comment="裁剪累计出数（approve 累加，reverse 减）",
        ),
        sa.Column(
            "balance_qty",
            NUMERIC(14, 3),
            nullable=False,
            server_default=sa.text("0"),
            comment="累计尾数。不入库、不出码、不计件（09 §4.2），已含在 cut_waste_qty 中",
        ),
        sa.Column(
            "bundled_qty",
            NUMERIC(14, 3),
            nullable=False,
            server_default=sa.text("0"),
            comment="打菲累计占用（打菲审核累加）",
        ),
        sa.Column(
            "reserved_qty",
            NUMERIC(14, 3),
            nullable=False,
            server_default=sa.text("0"),
            comment="待审核打菲单预占",
        ),
        sa.Column(
            "cut_waste_qty",
            NUMERIC(14, 3),
            nullable=False,
            server_default=sa.text("0"),
            comment="累计裁损，含累计尾数",
        ),
        # 公共字段
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            onupdate=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_by", UUID(as_uuid=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("remark", sa.Text(), nullable=True),
        # CHECK 约束
        sa.CheckConstraint("version > 0", name="ck_cutting_outputs_version_positive"),
        sa.CheckConstraint(
            "output_qty >= 0 AND balance_qty >= 0 AND bundled_qty >= 0 AND reserved_qty >= 0",
            name="ck_cutting_outputs_qty",
        ),
        sa.CheckConstraint("cut_waste_qty >= balance_qty", name="ck_cutting_outputs_waste"),
        # ⚠️ 这里刻意没有 INV-6 那条 CHECK（output_qty + balance_qty >= bundled_qty + reserved_qty）
        #    理由：submit 预占态下它恒为假，预占行插不进去。由 service 在 approve 收尾时断言。
        sa.Index(
            "uq_cutting_outputs_style_color_size",
            "style_no",
            "color_code",
            "size_code",
            unique=True,
            postgresql_where=sa.text("deleted_at IS NULL"),
        ),
        sa.Index(
            "idx_cutting_outputs_workshop",
            "workshop_id",
            "style_no",
            "color_code",
            postgresql_where=sa.text("deleted_at IS NULL"),
        ),
        sa.Index(
            "idx_cutting_outputs_scope",
            "workshop_id",
            "style_no",
            "size_code",
            postgresql_where=sa.text("deleted_at IS NULL"),
        ),
        comment="裁剪结转（权威 DDL 在 04 §7.7.5；T-BASE-009-2）",
    )

    # 视图：可打菲余量
    op.execute("""
        CREATE OR REPLACE VIEW v_cutting_output_available AS
        SELECT id, style_no, color_code, size_code, workshop_id,
               output_qty, balance_qty, bundled_qty, reserved_qty, cut_waste_qty,
               (output_qty - bundled_qty - reserved_qty) AS available_qty
        FROM cutting_outputs
        WHERE deleted_at IS NULL
    """)

    # ===== cutting_scrap_records =====
    op.create_table(
        "cutting_scrap_records",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "doc_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                "cutting_orders.id", name="fk_cutting_scrap_records_doc", ondelete="RESTRICT"
            ),
            nullable=False,
        ),
        sa.Column(
            "line_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                "cutting_order_lines.id", name="fk_cutting_scrap_records_line", ondelete="RESTRICT"
            ),
            nullable=False,
        ),
        sa.Column(
            "scrap_qty", NUMERIC(14, 3), nullable=False, comment="布头数量（仍可再裁的整段布）"
        ),
        sa.Column(
            "scrap_type",
            scrap_type_enum,
            nullable=False,
            comment="REUSABLE 可再用（入库）/ WASTE 废料（只进成本）",
        ),
        sa.Column(
            "stock_id",
            UUID(as_uuid=True),
            sa.ForeignKey("material_stocks.id", name="fk_cutting_scrap_records_stock"),
            nullable=True,
            comment="REUSABLE 时指向入库后的批次行",
        ),
        sa.Column(
            "unit_cost",
            NUMERIC(12, 6),
            nullable=True,
            comment="结转单位成本 = 被裁缸号批次的实际成本（ADR-0011）",
        ),
        # 公共字段
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            onupdate=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_by", UUID(as_uuid=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("remark", sa.Text(), nullable=True),
        # CHECK 约束
        sa.CheckConstraint("version > 0", name="ck_cutting_scrap_records_version_positive"),
        sa.CheckConstraint("scrap_qty > 0", name="ck_cutting_scrap_qty"),
        # REUSABLE 必须有 stock_id 和 unit_cost；WASTE 必须没有
        sa.CheckConstraint(
            "(scrap_type = 'REUSABLE' AND stock_id IS NOT NULL AND unit_cost IS NOT NULL) "
            "OR (scrap_type = 'WASTE' AND stock_id IS NULL)",
            name="ck_cutting_scrap_stock",
        ),
        sa.Index(
            "idx_cutting_scrap_records_doc",
            "doc_id",
            postgresql_where=sa.text("deleted_at IS NULL"),
        ),
        sa.Index(
            "idx_cutting_scrap_records_line",
            "line_id",
            postgresql_where=sa.text("deleted_at IS NULL"),
        ),
        sa.Index(
            "idx_cutting_scrap_records_stock",
            "stock_id",
            postgresql_where=sa.text("deleted_at IS NULL"),
        ),
        comment="裁剪布头/废料登记（权威 DDL 在 04 §7.7.5；布头归行；T-BASE-009-2）",
    )


def downgrade() -> None:
    op.drop_index("idx_cutting_scrap_records_stock", table_name="cutting_scrap_records")
    op.drop_index("idx_cutting_scrap_records_line", table_name="cutting_scrap_records")
    op.drop_index("idx_cutting_scrap_records_doc", table_name="cutting_scrap_records")
    op.drop_table("cutting_scrap_records")

    op.execute("DROP VIEW IF EXISTS v_cutting_output_available")
    op.drop_index("idx_cutting_outputs_scope", table_name="cutting_outputs")
    op.drop_index("idx_cutting_outputs_workshop", table_name="cutting_outputs")
    op.drop_index("uq_cutting_outputs_style_color_size", table_name="cutting_outputs")
    op.drop_table("cutting_outputs")

    # Drop enum type
    scrap_type_enum = ENUM(name="scrap_type", create_type=False)
    scrap_type_enum.drop(op.get_bind(), checkfirst=True)
