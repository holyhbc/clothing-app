from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811 —— 别名照抄 docs/04 §2
from sqlalchemy.orm import Mapped, mapped_column

from app.common.models import Base, BaseModel, IdMixin

# ---------------------------------------------------------------- 库存基础表
# T-BASE-006 / 迁移 0008。DDL 权威来源：docs/04 §7.2.0（material_stocks）
# 与 §7.10（wip_*）。⚠️ **只有基础表，没有库存业务**（出入库单 / 调拨 / 盘点）——
# 那是 P3。见 docs/00 §7 路线图「方案 A」。


class MaterialStock(BaseModel):
    """面料/辅料结存（**批次粒度**，ADR-0011 批次实际成本）。

    ⚠️ **唯一键不含 `batch_no`**：业务确认 2026-10-04「不存在同缸不同价」，所以
    同缸同匹就是唯一的一行，价格靠「批次内恒定」保证；要改价走红冲重入。
    ⚠️ **`dye_lot_no` / `bolt_no` 必须 NOT NULL**：它们在唯一键里，而 PG 的 NULL
    不参与唯一判定 —— 可空会让「无缸号」的批次（辅料最常见）重复插入任意多次。
    ⚠️ **`color_code` 与款号色不是同一概念**（业务确认）：面料缸色 ≠ 成衣色卡色，
    只是取值共用 `colors` 字典。所以**不加外键** —— 加了会在数据库层面暗示两者等价。
    """

    __tablename__ = "material_stocks"

    warehouse_id: Mapped[UUID] = mapped_column(
        ForeignKey("warehouses.id", name="fk_material_stocks_warehouse"), nullable=False
    )
    material_id: Mapped[UUID] = mapped_column(
        ForeignKey("materials.id", name="fk_material_stocks_material"), nullable=False
    )
    supplier_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("suppliers.id", name="fk_material_stocks_supplier"),
        nullable=True,
        comment="来源供应商（ADR-0022：由到货登记写入，不可手改）",
    )
    batch_no: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="对外批次号（09 §1.6，展示用，不进唯一键）"
    )
    color_code: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="-",
        server_default=text("'-'"),
        comment="缸号对应色；⚠️ 与款号色不是同一概念，取值共用 colors 字典",
    )
    dye_lot_no: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="-",
        server_default=text("'-'"),
        comment="缸号；无缸号时填 `-`（Q-ST05）。NOT NULL 的原因见类注释",
    )
    bolt_no: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="-",
        server_default=text("'-'"),
        comment="匹号；同缸多匹 → 多行；不分匹时填 `-`",
    )
    width_cm: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False, comment="门幅（cm）")
    effective_width_cm: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 2), nullable=True, comment="有效门幅（02 C24 门幅校验用）"
    )
    weight_kg: Mapped[Decimal] = mapped_column(
        Numeric(14, 3),
        nullable=False,
        default=Decimal("0"),
        server_default=text("0"),
        comment="重量（kg），布料主计量",
    )
    stock_qty: Mapped[Decimal] = mapped_column(
        Numeric(14, 3),
        nullable=False,
        default=Decimal("0"),
        server_default=text("0"),
        comment="账面结存（布料按米）",
    )
    total_length_m: Mapped[Decimal] = mapped_column(
        Numeric(14, 3),
        nullable=False,
        default=Decimal("0"),
        server_default=text("0"),
        comment="总米数",
    )
    locked_qty: Mapped[Decimal] = mapped_column(
        Numeric(14, 3),
        nullable=False,
        default=Decimal("0"),
        server_default=text("0"),
        comment="冗余锁定量缓存，与 stock_reservations 聚合一致",
    )
    safety_stock: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 3), nullable=True, comment="安全库存，低于触发 stock_low"
    )
    purpose: Mapped[str] = mapped_column(
        postgresql.ENUM(
            "NORMAL",
            "REWORK_RECEIPT",
            "RETURN",
            "SAMPLE",
            name="purchase_purpose",
            # ⚠️ create_type 必须 False：类型由迁移 0008 建
            create_type=False,
        ),
        nullable=False,
        default="NORMAL",
        server_default=text("'NORMAL'"),
        comment="采购用途（ADR-0012）：返修布成本天然分开的唯一依据（BR-ST-25）",
    )
    unit_cost: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, comment="批次实际成本（元/米），批次内恒定（ADR-0011）"
    )
    total_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 4), nullable=True, comment="= ROUND(stock_qty × unit_cost, 4)，冗余供报表"
    )
    in_date: Mapped[date] = mapped_column(Date, nullable=False, comment="入库日（FIFO 排序键）")
    expiry_date: Mapped[date | None] = mapped_column(
        Date, nullable=True, comment="有效期（FEFO 排序键；空即视为 FEFO 不适用）"
    )

    __table_args__ = (
        CheckConstraint("version > 0", name="ck_material_stocks_version_positive"),
        CheckConstraint("stock_qty >= 0", name="ck_material_stocks_qty"),
        CheckConstraint(
            "locked_qty >= 0 AND locked_qty <= stock_qty", name="ck_material_stocks_lock"
        ),
        CheckConstraint("width_cm > 0", name="ck_material_stocks_width"),
        Index(
            "uq_material_stocks_lot",
            "warehouse_id",
            "material_id",
            "dye_lot_no",
            "bolt_no",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # ⚠️ 部分索引 + ``stock_qty > locked_qty``：选批候选只查「还有可用量」的批次
        Index(
            "idx_material_stocks_pick",
            "material_id",
            "warehouse_id",
            "in_date",
            postgresql_where=text("deleted_at IS NULL AND stock_qty > locked_qty"),
        ),
        {"comment": "面料/辅料结存（批次粒度；04 §7.2.0）"},
    )


class WipStock(BaseModel):
    """在制品 WIP（裁片，ADR-0018）。

    ⚠️ ``style_no`` **无外键**（``styles.uq_styles_no`` 是部分索引，PG 不允许外键引用），
    外键在 ``style_id`` 上 —— 与 0005 子表同一口径。
    """

    __tablename__ = "wip_stocks"

    workshop_id: Mapped[UUID] = mapped_column(
        ForeignKey("workshops.id", name="fk_wip_stocks_workshop"), nullable=False
    )
    style_id: Mapped[UUID] = mapped_column(
        ForeignKey("styles.id", name="fk_wip_stocks_style"), nullable=False
    )
    style_no: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="冗余：历史单据按它引用"
    )
    product_category_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("product_categories.id", name="fk_wip_stocks_category"),
        nullable=True,
        comment="建单时快照，便于按分类统计",
    )
    color_code: Mapped[str] = mapped_column(String(32), nullable=False, comment="色码")
    size_code: Mapped[str] = mapped_column(String(32), nullable=False, comment="尺码码")
    qty: Mapped[Decimal] = mapped_column(
        Numeric(14, 3),
        nullable=False,
        default=Decimal("0"),
        server_default=text("0"),
        comment="裁片件数",
    )
    in_qty: Mapped[Decimal] = mapped_column(
        Numeric(14, 3),
        nullable=False,
        default=Decimal("0"),
        server_default=text("0"),
        comment="累计由裁剪转入",
    )
    out_qty: Mapped[Decimal] = mapped_column(
        Numeric(14, 3),
        nullable=False,
        default=Decimal("0"),
        server_default=text("0"),
        comment="累计转成衣/报损",
    )
    source_doc_type: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="'CUTTING_ORDER'"
    )
    source_doc_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False, comment="来源单据 id"
    )

    __table_args__ = (
        CheckConstraint("version > 0", name="ck_wip_stocks_version_positive"),
        CheckConstraint("qty >= 0 AND out_qty <= in_qty", name="ck_wip_qty"),
        Index(
            "uq_wip_stocks_src",
            "style_no",
            "color_code",
            "size_code",
            "source_doc_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("idx_wip_stocks_style", "style_id", "color_code", "size_code"),
        Index("idx_wip_stocks_source", "source_doc_type", "source_doc_id"),
        {"comment": "在制品 WIP 裁片（ADR-0018；04 §7.10）"},
    )


class WipLedgerLine(IdMixin, Base):
    """WIP 出入库流水（**append-only**，ADR-0018）。

    ⚠️ **既不用 :class:`BaseModel` 也不用 :class:`AuditMixin`**：
    append-only 表不该有 ``deleted_at`` / ``version``（用「软删 + 乐观锁」表达
    「可撤销」是自相矛盾的 —— 行一旦写入就不该变），也**不该有**
    ``updated_at`` / ``updated_by``（没有「更新」这个动作，所以「谁在什么时候改的」
    这两个字段永远是空的，而空着比没有更糟：读的人会以为「没人改过」而不是
    「这表不能改」）。口径与 ``document_logs`` 完全一致，只留
    ``id`` + ``created_at`` + ``created_by``。
    """

    __tablename__ = "wip_ledger_lines"

    __table_args__ = (
        Index("idx_wip_ledger_lines_stock", "wip_stock_id"),
        Index("idx_wip_ledger_lines_doc", "doc_type", "doc_id"),
        {"comment": "WIP 出入库流水（append-only；04 §7.10）"},
    )

    wip_stock_id: Mapped[UUID] = mapped_column(
        ForeignKey("wip_stocks.id", name="fk_wip_ledger_lines_stock"), nullable=False
    )
    direction: Mapped[str] = mapped_column(String(8), nullable=False, comment="IN / OUT")
    qty: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False, comment="数量")
    unit_cost: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 6),
        nullable=True,
        comment="WIP 不结转成本，恒为 NULL；⚠️ 刻意**不加 CHECK (unit_cost IS NULL)**",
    )
    doc_type: Mapped[str] = mapped_column(String(32), nullable=False)
    doc_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    operator_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    created_by: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False, comment="写入人（append-only：不设 updated_*）"
    )
