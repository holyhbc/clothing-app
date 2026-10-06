from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811 —— 别名照抄 docs/04 §2
from sqlalchemy.orm import Mapped, mapped_column

from app.common.models import Base, BaseModel, IdMixin
from app.modules.base.models._shared import TRGM_CUSTOMERS, TRGM_STYLES, _trgm_index

# ==================================================================
# 组 D：款号、色码尺码、比例、款号工序、工序单价（迁移 0005）
# ==================================================================


class Customer(BaseModel):
    """客户（modules/01 §3.5）。"""

    __tablename__ = "customers"

    code: Mapped[str] = mapped_column(String(32), nullable=False, comment="客户编码")
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="客户全称")
    short_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="简称")
    contact: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="联系人")
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="联系电话")
    address: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="地址")
    tax_no: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="税号")
    settlement_period_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="账期天数，应收核销用"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        comment="停用后不可新建单据，历史照常",
    )

    __table_args__ = (
        CheckConstraint("version > 0", name="ck_customers_version_positive"),
        CheckConstraint("settlement_period_days >= 0", name="ck_customers_settlement_period_days"),
        Index("uq_customers_code", "code", unique=True),
        Index("idx_customers_is_active", "is_active"),
        Index("idx_customers_settlement_period_days", "settlement_period_days"),
        _trgm_index("idx_customers_trgm", TRGM_CUSTOMERS),
        {"comment": "客户（modules/01 §3.5）"},
    )


class Style(BaseModel):
    """款号（modules/01 §3.2、R1/R2）。

    ⚠️ ``style_no`` 的唯一索引是 **部分索引**（``WHERE deleted_at IS NULL``），
    所以款号软删后**可以重建同号**。正因如此 PostgreSQL 无法让子表外键引用它
    （外键只能引用普通唯一约束），子表一律存 ``style_id`` 指向本表主键 ——
    详见 docs/12 §5 L-029 与迁移 0005 的文件头注 4。

    ``category_id`` 在款号档案上定死（ADR-0020），单据不冗余存分类。
    ``merchandiser_id`` 是跟单数据范围的依据（Q-P0-05：跟单只看本人款号）。
    """

    __tablename__ = "styles"

    style_no: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="款号，存库统一转大写（R1）"
    )
    customer_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("customers.id", name="fk_styles_customers"),
        nullable=True,
        comment="归属客户（仅用于建议号分组与筛选）",
    )
    customer_style_no: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="客户款号，印在唛头上"
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="款名")
    bulk_qty: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="大货数量（09 §1.1）"
    )
    category_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("product_categories.id", name="fk_styles_product_categories"),
        nullable=False,
        comment="商品分类，04 §7.11：分类在款号档案上定死，单据不冗余",
    )
    merchandiser_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", name="fk_styles_merchandiser"),
        nullable=True,
        comment="跟单员；Q-P0-05：跟单数据范围按此隔离（SELF → 本人款号）",
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="最近使用；05 §9.5.2 候选默认按此 DESC（常用优先）",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        comment="停用即 R2：不允许新建裁剪/打菲单，历史照常",
    )

    __table_args__ = (
        CheckConstraint("version > 0", name="ck_styles_version_positive"),
        Index("uq_styles_no", "style_no", unique=True, postgresql_where=text("deleted_at IS NULL")),
        Index("idx_styles_customer_id", "customer_id"),
        Index("idx_styles_merchandiser_id", "merchandiser_id"),
        Index("idx_styles_last_used_at", "last_used_at"),
        _trgm_index("idx_styles_trgm", TRGM_STYLES),
        {"comment": "款号（modules/01 §3.2；R2：已产生计件或库存的款号只能停用）"},
    )


class StyleColor(BaseModel):
    """款号色组（modules/01 §3.2）。"""

    __tablename__ = "style_colors"

    style_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("styles.id", name="fk_style_colors_styles"),
        nullable=False,
        comment="款号主键（外键指向 styles.id，见文件头注 4）",
    )
    style_no: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="款号（业务键，冗余便于查询与唯一约束）"
    )
    color_group: Mapped[str] = mapped_column(String(32), nullable=False, comment="色组（09 §1.1）")
    color_code: Mapped[str] = mapped_column(String(32), nullable=False, comment="色码 BLK / WHT")
    color_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="中文色名")
    material_color_code: Mapped[str | None] = mapped_column(
        String(32), nullable=True, comment="面料对应色/缸别标识，供排料对色"
    )

    __table_args__ = (
        CheckConstraint("version > 0", name="ck_style_colors_version_positive"),
        Index("uq_style_colors_style_color", "style_no", "color_code", unique=True),
        Index("uq_style_colors_style_group", "style_no", "color_group", unique=True),
        Index("idx_style_colors_color_code", "color_code"),
        {"comment": "款号色组（modules/01 §3.2）"},
    )


class StyleSize(BaseModel):
    """款号尺码集合（modules/01 §3.2）。"""

    __tablename__ = "style_sizes"

    style_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("styles.id", name="fk_style_sizes_styles"),
        nullable=False,
        comment="款号主键（外键指向 styles.id，见文件头注 4）",
    )
    style_no: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="款号（业务键，冗余便于查询与唯一约束）"
    )
    size_code: Mapped[str] = mapped_column(String(32), nullable=False, comment="尺码码 S / M / L")
    size_name: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="实际尺码 S(155/80A)"
    )
    sort_no: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="排序，报表按序输出"
    )

    __table_args__ = (
        CheckConstraint("version > 0", name="ck_style_sizes_version_positive"),
        Index("uq_style_sizes_style_size", "style_no", "size_code", unique=True),
        Index("idx_style_sizes_size_code", "size_code"),
        {"comment": "款号尺码集合（modules/01 §3.2）"},
    )


class StyleColorSizeRatio(BaseModel):
    """款号 × 颜色 × 尺码 → 手数（**建议值**，04 §7.7.1、ADR-0013/0014）。

    ⚠️ **比例只是建议，裁剪单行才是权威**（ADR-0014 第三批修订）：件数一律由
    裁剪单行的 ``hands × qty_per_hand`` 算出，本表只用于「带出建议」。
    部分尺码缺配只提示不拦；完全没有比例才报 ``20006``，多余尺码报 ``20007``。
    """

    __tablename__ = "style_color_size_ratios"

    style_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("styles.id", name="fk_style_color_size_ratios_styles"),
        nullable=False,
        comment="款号主键（外键指向 styles.id，见文件头注 4）",
    )
    style_no: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="款号（业务键，冗余便于查询与唯一约束）"
    )
    color_code: Mapped[str] = mapped_column(String(32), nullable=False, comment="色码")
    size_code: Mapped[str] = mapped_column(String(32), nullable=False, comment="尺码码")
    ratio: Mapped[Decimal] = mapped_column(
        Numeric(14, 4),
        nullable=False,
        comment="手数（可小数，如 1.5 手）。⚠️ 比例只是**建议值**，裁剪单行的 hands × qty_per_hand 才是权威（ADR-0014）",
    )

    __table_args__ = (
        CheckConstraint("version > 0", name="ck_style_color_size_ratios_version_positive"),
        CheckConstraint("ratio > 0", name="ck_style_color_size_ratio"),
        Index("uq_style_color_size_ratios", "style_no", "color_code", "size_code", unique=True),
        Index("idx_style_color_size_ratios_lookup", "style_no", "color_code"),
        {"comment": "款号尺码比例（建议值，04 §7.7.1）"},
    )


class StyleOperation(BaseModel):
    """款号工序配置（04 §7.8.2；模板载体）。

    ``bundle_qty`` 覆盖工序字典的 ``default_bundle_qty``（R9：一扎 1 件 vs
    一打 12 件按款号工序定）。
    """

    __tablename__ = "style_operations"

    style_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("styles.id", name="fk_style_operations_styles"),
        nullable=False,
        comment="款号主键（外键指向 styles.id，见文件头注 4）",
    )
    style_no: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="款号（业务键，冗余便于查询与唯一约束）"
    )
    operation_no: Mapped[str] = mapped_column(
        String(16),
        ForeignKey("operations.operation_no", name="fk_style_operations_operations"),
        nullable=False,
        comment="工序号",
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, comment="工序顺序 1,2,3…")
    bundle_qty: Mapped[Decimal] = mapped_column(
        Numeric(14, 3),
        nullable=False,
        default=Decimal("1"),
        server_default=text("1"),
        comment="该款该工序一扎几件（R9：覆盖 operations.default_bundle_qty）",
    )
    is_piecework: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        comment="该款该工序是否计件（可覆盖工序字典默认值）",
    )
    is_final_operation: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment="是否最后一道工序（默认整烫；识别不到必须人工指定）",
    )

    __table_args__ = (
        CheckConstraint("version > 0", name="ck_style_operations_version_positive"),
        CheckConstraint("sequence > 0", name="ck_style_operations_sequence_positive"),
        CheckConstraint("bundle_qty > 0", name="ck_style_operations_bundle_qty_positive"),
        Index("uq_style_operations", "style_no", "operation_no", unique=True),
        Index("idx_style_operations_operation_no", "operation_no"),
        {"comment": "款号工序配置（04 §7.8.2；模板载体）"},
    )


class StyleNoSequence(IdMixin, Base):
    """建议货号的序号计数器（Q-P0-05：序号按客户分组递增）。

    ⚠️ 纯计数表，**豁免 04 §2 公共字段**：没有"谁改的"的概念，它由取号逻辑在
    事务内 ``SELECT ... FOR UPDATE`` 递增。款号本身由**用户自定义**（Q-P0-04），
    这里只为"给个不重复的建议"。

    ⚠️ 唯一性用**两条部分唯一索引**而不是复合主键 ``(customer_id, year)``：
    主键列隐式 NOT NULL，而"客户为空 = 全厂序列"要求 ``customer_id`` 真的能是
    NULL —— 用主键的话全厂序列那一行永远插不进去。
    """

    __tablename__ = "style_no_sequences"

    customer_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("customers.id", name="fk_style_no_sequences_customers"),
        nullable=True,
        comment="客户；空 = 全厂序列",
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False, comment="年份，如 2026")
    next_no: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
        comment="下一个可用序号，从 1 开始",
    )

    __table_args__ = (
        CheckConstraint("next_no >= 1", name="ck_style_no_sequences_next_no"),
        Index(
            "uq_style_no_sequences_customer",
            "customer_id",
            "year",
            unique=True,
            postgresql_where=text("customer_id IS NOT NULL"),
        ),
        Index(
            "uq_style_no_sequences_factory",
            "year",
            unique=True,
            postgresql_where=text("customer_id IS NULL"),
        ),
        {"comment": "建议货号序号计数器（Q-P0-05：序号按客户分组递增）"},
    )
