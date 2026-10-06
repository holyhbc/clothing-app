from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.common.models import BaseModel

# ------------------------------------------------------------------ 物料与供应商
# T-BASE-005。DDL 权威来源：docs/04 §7.15（设计见 tasks/T-BASE-003 的 D1~D5）


class MaterialCategory(BaseModel):
    """物料类目（09 §2.3 物料编码 ``F-{类目码}-{6 位}`` 的第二段）。

    ⚠️ **不在 ADR-0025 的硬删白名单里**（那 4 张是 colors / sizes / size_groups /
    size_group_items）：类目码是十几个级别的字典，停用足够，而白名单每加一张表
    要在闸门 4 兑现三处。理由见 04 §7.15.1。
    """

    __tablename__ = "material_categories"

    code: Mapped[str] = mapped_column(String(16), nullable=False, comment="类目码，如 CT / ZL")
    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="纯棉布 / 拉链")
    sort: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="排序"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        comment="停用后不可新建物料，历史照常",
    )
    is_builtin: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment="内置类目（seed 写入）；墓碑机制见 ADR-0025 决策 3",
    )

    __table_args__ = (
        CheckConstraint("version > 0", name="ck_material_categories_version_positive"),
        Index("uq_material_categories_code", "code", unique=True),
        Index("idx_material_categories_is_active", "is_active"),
        {"comment": "物料类目（09 §2.3 物料编码第二段）"},
    )


class Material(BaseModel):
    """物料档案（``material_stocks.material_id`` 与 ``cutting_order_lines`` 的外键目标）。

    ⚠️ **档案层刻意没有** ``width_cm`` / ``weight_kg`` / 缸色 / ``unit_cost``：
    它们全在**批次**上（``material_stocks``）。同一缸不同批的门幅可以不同
    （缩水 / 织造偏差），而裁剪的门幅校验（02 C24）要用的正是**这一批**的门幅 ——
    放到档案层就丢掉了这个事实。
    """

    __tablename__ = "materials"

    code: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="F-CT-000128 / T-ZL-000456（09 §2.3）"
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="32支全棉府绸")
    material_type: Mapped[str] = mapped_column(
        SAEnum(
            "FABRIC",
            "TRIMMING",
            "LABEL",
            "FINISHED_GOODS",
            name="material_type",
            # ⚠️ create_type 必须 False：模型层不建枚举，类型由迁移 0007 建
            create_type=False,
        ),
        nullable=False,
        comment="面料 / 辅料 / 唛头 / 成衣；FABRIC 的批次要求 width_cm 非空",
    )
    category_id: Mapped[UUID] = mapped_column(
        ForeignKey("material_categories.id", name="fk_materials_category_id"),
        nullable=False,
        comment="物料类目，编码第二段的来源",
    )
    uom_unit_id: Mapped[UUID] = mapped_column(
        ForeignKey("uom_units.id", name="fk_materials_uom_unit_id"),
        nullable=False,
        comment="主计量单位；库存数量/单价/耗用单位都跟着它走",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        comment="停用后不可新建单据，历史照常",
    )

    __table_args__ = (
        CheckConstraint("version > 0", name="ck_materials_version_positive"),
        # ⚠️ 部分唯一索引：软删后可以复用同一个 code（与 styles.uq_styles_no 同口径）
        Index(
            "uq_materials_code",
            "code",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("idx_materials_category_id", "category_id"),
        Index("idx_materials_material_type", "material_type"),
        Index("idx_materials_uom_unit_id", "uom_unit_id"),
        {"comment": "物料档案（modules/06 §3；批次属性见 material_stocks）"},
    )


class Supplier(BaseModel):
    """供应商（与 ``Customer`` 字段高度重合但**不合表**，理由见 04 §7.15.3）。

    ⚠️ 账期字段是 ``settlement_period_days`` 而不是 ``payment_period_days`` /
    ``credit_days``（Q-F-07 已闭环）：跟随 ``customers`` 上的既有实现。
    ⚠️ 银行四项**可空**：辅料商与个体工商户常无对公账户（T-BASE-003 D4）。
    """

    __tablename__ = "suppliers"

    code: Mapped[str] = mapped_column(String(32), nullable=False, comment="供应商编码，转大写")
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="供应商全称")
    short_name: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="简称，单据抬头用"
    )
    contact: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="联系人")
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="联系电话")
    address: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="地址")
    tax_no: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="税号")
    settlement_period_days: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
        comment="账期天数，应付核销用（Q-F-07：与 customers 同名）",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        comment="停用后不可新建单据，历史照常",
    )
    # ---- 往来扩展（modules/08 §3.1）
    settlement_method: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="NET",
        server_default=text("'NET'"),
        comment="PREPAY / COD / NET（modules/08 F-03）",
    )
    credit_limit: Mapped[Decimal] = mapped_column(
        Numeric(18, 4),
        nullable=False,
        default=Decimal("0"),
        server_default=text("0"),
        comment="信用额度（docs/04 §4：金额一律 numeric(18,4)）",
    )
    default_warehouse_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("warehouses.id", name="fk_suppliers_default_warehouse_id"),
        nullable=True,
        comment="采购收货默认仓；可空（未指定则按行选）",
    )
    # ---- 银行信息**可空**（T-BASE-003 D4）
    bank_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="开户行名称")
    bank_account_no: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="银行账号"
    )
    bank_branch: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="开户支行")
    bank_account_name: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="账户名"
    )

    __table_args__ = (
        CheckConstraint("version > 0", name="ck_suppliers_version_positive"),
        CheckConstraint("credit_limit >= 0", name="ck_suppliers_credit_limit"),
        Index("uq_suppliers_code", "code", unique=True),
        Index("idx_suppliers_is_active", "is_active"),
        Index("idx_suppliers_default_warehouse_id", "default_warehouse_id"),
        {"comment": "供应商（modules/01 §3.5 + modules/08 §3.1；DDL 见 04 §7.15.3）"},
    )
