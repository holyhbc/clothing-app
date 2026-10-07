"""裁剪结转与布头登记（T-BASE-009-2 / 迁移 0015，DDL 权威来源 ``docs/04 §7.7.5``）。

⚠️ 这两张表**不属于裁剪单三层结构**（ADR-0017），而是：
- ``cutting_outputs``：裁剪审核后写入的**结转台账**（按款号+色码+尺码+车间汇总）
- ``cutting_scrap_records``：裁剪完成后登记的**布头/废料**（归属到具体裁剪行）

所以放在 ``outputs.py`` 而不是三层结构的 ``order.py`` / ``lines.py`` 里。
"""

from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index
from sqlalchemy.dialects.postgresql import ENUM, NUMERIC
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811 —— 别名照抄 docs/04 §2
from sqlalchemy.orm import Mapped, mapped_column

from app.common.models import BaseModel

# =================================================================== ENUM
scrap_type_enum = ENUM(
    "REUSABLE",
    "WASTE",
    name="scrap_type",
    create_type=False,
)


# =================================================================== cutting_outputs
class CuttingOutput(BaseModel):
    """裁剪结转（按款号+色码+尺码+车间汇总）。

    关键口径（modules/02 §3.6、§3.7）：
    - ``output_qty``：裁剪审核累加、反审核减
    - ``bundled_qty``：打菲审核累加
    - ``reserved_qty``：打菲提交预占、审核/驳回/撤回释放
    - ``balance_qty``：仅裁剪侧人工指定出数时产生，不入库、不出码、不计件
    - ``cut_waste_qty``：累计裁损（含累计尾数），>= balance_qty

    **权威 DDL 在 04 §7.7.5**，本模型照抄、不得改动。
    """

    __tablename__ = "cutting_outputs"

    # 业务键（冗余 + 外键）
    style_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("styles.id", name="fk_cutting_outputs_style"),
        nullable=False,
        comment="冗余：历史结转按它引用",
    )
    style_no: Mapped[str] = mapped_column(
        comment="冗余：历史结转按它引用",
        nullable=False,
    )
    color_code: Mapped[str] = mapped_column(
        comment="⚠️ 原字段表写 color_group，见 04 §7.7.5 第 1 条",
        nullable=False,
    )
    size_code: Mapped[str] = mapped_column(
        comment="C10：尺码级汇总",
        nullable=False,
    )
    workshop_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("workshops.id", name="fk_cutting_outputs_workshop"),
        nullable=False,
        comment="数据范围过滤依据（INV-8）",
    )

    # 数量核算列（numeric(14,3)）
    output_qty: Mapped[str] = mapped_column(
        NUMERIC(14, 3),
        nullable=False,
        server_default="0",
        comment="裁剪累计出数（approve 累加，reverse 减）",
    )
    balance_qty: Mapped[str] = mapped_column(
        NUMERIC(14, 3),
        nullable=False,
        server_default="0",
        comment="累计尾数。不入库、不出码、不计件（09 §4.2），已含在 cut_waste_qty 中",
    )
    bundled_qty: Mapped[str] = mapped_column(
        NUMERIC(14, 3),
        nullable=False,
        server_default="0",
        comment="打菲累计占用（打菲审核累加）",
    )
    reserved_qty: Mapped[str] = mapped_column(
        NUMERIC(14, 3),
        nullable=False,
        server_default="0",
        comment="待审核打菲单预占",
    )
    cut_waste_qty: Mapped[str] = mapped_column(
        NUMERIC(14, 3),
        nullable=False,
        server_default="0",
        comment="累计裁损，含累计尾数",
    )

    # 表约束
    __table_args__ = (
        CheckConstraint("version > 0", name="ck_cutting_outputs_version_positive"),
        CheckConstraint(
            "output_qty >= 0 AND balance_qty >= 0 AND bundled_qty >= 0 AND reserved_qty >= 0",
            name="ck_cutting_outputs_qty",
        ),
        CheckConstraint("cut_waste_qty >= balance_qty", name="ck_cutting_outputs_waste"),
        # ⚠️ 这里刻意没有 INV-6 那条 CHECK（output_qty + balance_qty >= bundled_qty + reserved_qty）
        #    理由：submit 预占态下它恒为假，预占行插不进去。由 service 在 approve 收尾时断言。
        Index(
            "uq_cutting_outputs_style_color_size",
            "style_no",
            "color_code",
            "size_code",
            unique=True,
            postgresql_where="deleted_at IS NULL",
        ),
        Index(
            "idx_cutting_outputs_workshop",
            "workshop_id",
            "style_no",
            "color_code",
            postgresql_where="deleted_at IS NULL",
        ),
        Index(
            "idx_cutting_outputs_scope",
            "workshop_id",
            "style_no",
            "size_code",
            postgresql_where="deleted_at IS NULL",
        ),
        {"comment": "裁剪结转（权威 DDL 在 04 §7.7.5；T-BASE-009-2）"},
    )


# =================================================================== cutting_scrap_records
class CuttingScrapRecord(BaseModel):
    """裁剪布头/废料登记（权威 DDL 在 04 §7.7.5；布头归行；T-BASE-009-2）。

    - ``scrap_type = REUSABLE``：布头（仍可再裁的整段布），入库到 material_stocks，需填 stock_id + unit_cost
    - ``scrap_type = WASTE``：废料（只进成本），stock_id 为空
    """

    __tablename__ = "cutting_scrap_records"

    doc_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("cutting_orders.id", name="fk_cutting_scrap_records_doc", ondelete="RESTRICT"),
        nullable=False,
    )
    line_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey(
            "cutting_order_lines.id", name="fk_cutting_scrap_records_line", ondelete="RESTRICT"
        ),
        nullable=False,
    )
    scrap_qty: Mapped[str] = mapped_column(
        NUMERIC(14, 3),
        nullable=False,
        comment="布头数量（仍可再裁的整段布）",
    )
    scrap_type: Mapped[str] = mapped_column(
        scrap_type_enum,
        nullable=False,
        comment="REUSABLE 可再用（入库）/ WASTE 废料（只进成本）",
    )
    stock_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("material_stocks.id", name="fk_cutting_scrap_records_stock"),
        nullable=True,
        comment="REUSABLE 时指向入库后的批次行",
    )
    unit_cost: Mapped[str | None] = mapped_column(
        NUMERIC(12, 6),
        nullable=True,
        comment="结转单位成本 = 被裁缸号批次的实际成本（ADR-0011）",
    )

    __table_args__ = (
        CheckConstraint("version > 0", name="ck_cutting_scrap_records_version_positive"),
        CheckConstraint("scrap_qty > 0", name="ck_cutting_scrap_qty"),
        # REUSABLE 必须有 stock_id 和 unit_cost；WASTE 必须没有
        CheckConstraint(
            "(scrap_type = 'REUSABLE' AND stock_id IS NOT NULL AND unit_cost IS NOT NULL) "
            "OR (scrap_type = 'WASTE' AND stock_id IS NULL)",
            name="ck_cutting_scrap_stock",
        ),
        Index(
            "idx_cutting_scrap_records_doc",
            "doc_id",
            postgresql_where="deleted_at IS NULL",
        ),
        Index(
            "idx_cutting_scrap_records_line",
            "line_id",
            postgresql_where="deleted_at IS NULL",
        ),
        Index(
            "idx_cutting_scrap_records_stock",
            "stock_id",
            postgresql_where="deleted_at IS NULL",
        ),
        {"comment": "裁剪布头/废料登记（权威 DDL 在 04 §7.7.5；布头归行；T-BASE-009-2）"},
    )
