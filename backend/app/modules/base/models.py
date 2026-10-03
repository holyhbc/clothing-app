"""组织、字典与工序主数据模型（docs/04 §7.4 / §7.8.1 / §7.11 + modules/01 §3.1）。

本模块十张表，分三类，**软删与删除策略完全不同**：

============  ==========================================================
表            删除策略
============  ==========================================================
字典四表      **未被引用可真删**，被引用只能停用（ADR-0025）
              ``colors`` / ``sizes`` / ``size_groups`` / ``size_group_items``
其余六表      只能软删（``deleted_at``），应用账号连 DELETE 权限都没有
============  ==========================================================

⚠️ **模型必须与迁移 0004 逐字一致**，否则 autogenerate 反复产生 diff。
T-AUTH-001 已经吃过一次亏（CHECK 约束、悬空 FK），这里的每个约束名都对着迁移核过。

⚠️ ``size_class`` 用 PG enum（04 §7.4 的 ``CREATE TYPE``），而
``users.data_scope`` 等是 ``String`` —— 两者不一致是规范本身的现状
（04 §3 规定 PG enum 只能追加值、不得改值，所以新设计一律走 enum）。
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811 —— 别名照抄 docs/04 §2
from sqlalchemy.orm import Mapped, mapped_column

from app.common.enums import SizeClass
from app.common.models import Base, BaseModel

#: PG enum ``size_class``（04 §7.4 的 ``CREATE TYPE``）。
#:
#: ⚠️ 必须是**真正的 enum 类型**，不能拿 ``String(16)`` 糊弄 ——
#: 后者与迁移里的 ``size_class`` enum 类型对不上，``alembic check`` 会一直报
#: "modify_type"，而且枚举值本身失去了数据库层的约束。
#: ``values_callable`` 不给的话 SQLAlchemy 默认存**成员名**（MENS/WOMENS/KIDS），
#: 恰好与 PG enum 的值一致，所以不需要它。
SIZE_CLASS = SAEnum(SizeClass, name="size_class")


def _trgm_index(name: str, expression: str) -> Index:
    """建 docs/04 §5.1 要求的三元组 GIN 索引。

    ⚠️ 必须用**与候选查询完全相同的表达式**，否则 planner 匹配不上、索引白建。
    所以这个表达式和 :mod:`app.modules.base.repository` 里的候选谓词是同一个
    字符串常量 —— 两处必须同时改。

    表达式索引用 ``sa.text`` 声明：SQLAlchemy 没有"字符串拼接列"的类型化写法。
    """
    return Index(
        name,
        sa.text(expression),
        postgresql_using="gin",
        postgresql_ops={sa.text(expression): "gin_trgm_ops"},
    )


#: 候选搜索的模糊匹配表达式（§5.1：``<编码> || ' ' || <名称>``）
TRGM_COLORS = "(color_code || ' ' || name)"
TRGM_SIZES = "(size_code || ' ' || name)"
TRGM_OPERATIONS = "(operation_no || ' ' || name)"


class Workshop(BaseModel):
    """车间（modules/01 §3.1）。

    ⚠️ ``workshops`` 一旦被 ``users.workshop_id`` 引用就不能删（子表有 FK），
    所以车间实际只能停用。仓库/组别同理。
    """

    __tablename__ = "workshops"

    code: Mapped[str] = mapped_column(String(32), nullable=False, comment="车间编码，如 CUT / SEW")
    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="车间名")
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        comment="停用后不允许新建本车间单据",
    )

    __table_args__ = (
        CheckConstraint("version > 0", name="ck_workshops_version_positive"),
        Index("uq_workshops_code", "code", unique=True),
        Index("idx_workshops_is_active", "is_active"),
        {"comment": "车间（modules/01 §3.1）"},
    )


class WorkshopGroup(BaseModel):
    """车间组别（modules/01 §3.1）。

    表名不用 ``groups``：``GROUP`` 是 SQL 保留字；且组别唯一作用域是车间，
    所以唯一键是 ``(workshop_id, group_no)`` 而非单列。
    """

    __tablename__ = "workshop_groups"

    workshop_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("workshops.id", name="fk_workshop_groups_workshops"),
        nullable=False,
        comment="所属车间",
    )
    group_no: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="组别号（09 §1.4 group_no）"
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="如 03 组 拼前")
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        comment="停用不影响历史",
    )

    __table_args__ = (
        CheckConstraint("version > 0", name="ck_workshop_groups_version_positive"),
        Index("uq_workshop_groups_workshop_no", "workshop_id", "group_no", unique=True),
        Index("idx_workshop_groups_workshop_id", "workshop_id"),
        {"comment": "车间组别（表名不用 groups：GROUP 是 SQL 保留字）"},
    )


class Warehouse(BaseModel):
    """仓库（modules/01 §3.1）。``warehouse_type`` 与库存双栈对应。"""

    __tablename__ = "warehouses"

    code: Mapped[str] = mapped_column(String(32), nullable=False, comment="如 FABRIC / TRIM / FG")
    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="仓库名")
    warehouse_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="FABRIC / TRIMMING / FINISHED_GOOD，与库存双栈对应",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        comment="停用后不得出入库",
    )

    __table_args__ = (
        CheckConstraint("version > 0", name="ck_warehouses_version_positive"),
        Index("uq_warehouses_code", "code", unique=True),
        Index("idx_warehouses_type_is_active", "warehouse_type", "is_active"),
        {"comment": "仓库（modules/01 §3.1）"},
    )


class UomUnit(BaseModel):
    """计量单位（modules/01 §3.4）。

    ``decimal_places`` 参与 ``numeric`` 精度对齐：docs/04 §1 要求数量一律
    ``numeric``、禁止 float，那么"这个数量允许几位小数"就必须由单位声明，
    否则前端传 3.14159 没人拦。
    """

    __tablename__ = "uom_units"

    code: Mapped[str] = mapped_column(String(16), nullable=False, comment="M / YD / PCS / KG")
    name: Mapped[str] = mapped_column(String(32), nullable=False, comment="米 / 码 / 个 / 公斤")
    decimal_places: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=3,
        server_default=text("3"),
        comment="参与 numeric 精度对齐（docs/04：数量一律 numeric，不 float）",
    )

    __table_args__ = (
        CheckConstraint("version > 0", name="ck_uom_units_version_positive"),
        CheckConstraint(
            "decimal_places >= 0 AND decimal_places <= 6", name="ck_uom_units_decimal_places"
        ),
        Index("uq_uom_units_code", "code", unique=True),
        {"comment": "计量单位（modules/01 §3.4）"},
    )


class Color(BaseModel):
    """颜色字典（docs/04 §7.4）。

    **可物理删除**：未被任何单据引用时（ADR-0025 白名单含本表）。
    ``is_builtin`` 只是界面角标，**不影响删除权限**（modules/01 R27：
    内置项与用户项完全对等）。
    """

    __tablename__ = "colors"

    color_code: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="WHT / BLK / KHK / GRN"
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="本白 / 黑 / 卡其 / 军绿")
    color_family: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        comment="标准色卡族 Pantone TCX / 客户色卡 / 空",
    )
    pantone_code: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="潘通号")
    is_builtin: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment="是否 seed 内置（仅界面角标，不影响删除权限，R27）",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        comment="停用后不参与新建单据，历史照常可查",
    )

    __table_args__ = (
        CheckConstraint("version > 0", name="ck_colors_version_positive"),
        Index("uq_colors_code", "color_code", unique=True),
        _trgm_index("idx_colors_trgm", TRGM_COLORS),
        {"comment": "颜色字典（04 §7.4；未被引用可真删，ADR-0025）"},
    )


class Size(BaseModel):
    """尺码字典（docs/04 §7.4 + **ADR-0027**）。

    ⚠️ 唯一键是 ``(size_code, size_class)``，**不是** ``size_code``：
    内置码表里 ``L`` / ``XL`` 同时属于女款与男款，单列唯一存不下。
    配套：``20007`` 校验只按码表成员关系判定，不附加 ``size_class`` 相等条件。
    """

    __tablename__ = "sizes"

    size_code: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="S / M / L / XL / 3XL"
    )
    name: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="XL(170/92A) 这类围度标注"
    )
    size_class: Mapped[SizeClass] = mapped_column(
        SIZE_CLASS,
        nullable=False,
        default=SizeClass.WOMENS,
        comment="MENS / WOMENS / KIDS",
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    is_builtin: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment="是否 seed 内置",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        comment="停用后不参与新建单据，历史照常可查",
    )

    __table_args__ = (
        CheckConstraint("version > 0", name="ck_sizes_version_positive"),
        # ⚠️ ADR-0027：唯一键含 size_class
        Index("uq_sizes_code_class", "size_code", "size_class", unique=True),
        Index("idx_sizes_active_sort_order", "is_active", "sort_order"),
        _trgm_index("idx_sizes_trgm", TRGM_SIZES),
        Index("idx_sizes_size_class", "size_class"),
        {"comment": "尺码字典（04 §7.4 + ADR-0027）"},
    )


class SizeGroup(BaseModel):
    """尺码模板 / 码表（docs/04 §7.4）。

    一个码表 = 一个尺码类 + 一套有序尺码（成员在 :class:`SizeGroupItem`）。
    **可物理删除**，且删除时**级联删**成员行（04 §7.4 的 ``ON DELETE RESTRICT``
    是为了防止误删成员，不是为了阻止删码表 —— 删码表本来就该连成员一起没）。
    """

    __tablename__ = "size_groups"

    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="男装衬衫码表")
    size_class: Mapped[SizeClass] = mapped_column(
        SIZE_CLASS,
        nullable=False,
        default=SizeClass.WOMENS,
        comment="一个码表 = 一个尺码类 + 一套有序尺码",
    )
    is_builtin: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment="是否 seed 内置（只内置 2 个）",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        comment="停用后不参与新建单据，历史照常可查",
    )

    __table_args__ = (
        CheckConstraint("version > 0", name="ck_size_groups_version_positive"),
        Index("uq_size_groups_name", "name", unique=True),
        Index("idx_size_groups_size_class", "size_class"),
        {"comment": "尺码模板 / 码表（04 §7.4）"},
    )


class SizeGroupItem(Base):
    """码表成员（有序）。

    ⚠️ **纯关联表，豁免 docs/04 §2 公共字段**：没有 ``id`` / ``version`` /
    ``updated_*`` / ``deleted_at`` / ``remark``。理由是它没有独立生命周期 ——
    它的存在只由 ``(size_group_id, size_id)`` 决定，加 ``version`` 就得跟着
    每次改动 +1，而没人会去读它的版本号。复合主键照 04 §7.4 字面。
    """

    __tablename__ = "size_group_items"

    size_group_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("size_groups.id", name="fk_size_group_items_groups", ondelete="CASCADE"),
        primary_key=True,
    )
    size_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("sizes.id", name="fk_size_group_items_sizes", ondelete="RESTRICT"),
        primary_key=True,
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    __table_args__ = (
        Index("idx_size_group_items_sort_order", "size_group_id", "sort_order"),
        {"comment": "码表成员（有序）。纯关联表，豁免 04 §2 公共字段"},
    )


class Operation(BaseModel):
    """工序主数据（docs/04 §7.8.1）。

    ⚠️ ``workshop_id`` **可空** = 通用工序（如「查勘」全厂通用）。§7.8.1 的 DDL
    写的是 ``NOT NULL``，但同一行注释写「可空 = 通用」，modules/01 §3.4 也明确
    「故实体可空」—— 两处文字都指向可空，``NOT NULL`` 是笔误。
    """

    __tablename__ = "operations"

    operation_no: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="01 / 02 …用户自定义"
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="裁 / 打边 / 拼前 / 查勘")
    workshop_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("workshops.id", name="fk_operations_workshops"),
        nullable=True,
        comment="归属车间；空 = 通用工序（如 查勘 全厂通用）",
    )
    is_piecework: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        comment="是否计件工序（查勘 / 包装可能不计件）",
    )
    default_bundle_qty: Mapped[Decimal] = mapped_column(
        Numeric(14, 3),
        nullable=False,
        default=Decimal("1"),
        server_default=text("1"),
        comment="默认一扎几件；**仅默认值**，权威值在 style_operations.bundle_qty（R9）",
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        comment="停用后不参与新建单据，历史照常可查（R17）",
    )

    __table_args__ = (
        CheckConstraint("version > 0", name="ck_operations_version_positive"),
        CheckConstraint("default_bundle_qty > 0", name="ck_operations_bundle_qty_positive"),
        Index("uq_operations_no", "operation_no", unique=True),
        Index("idx_operations_workshop_id_is_active", "workshop_id", "is_active"),
        _trgm_index("idx_operations_trgm", TRGM_OPERATIONS),
        {"comment": "工序主数据（04 §7.8.1；用户自定义，不内置枚举，R16）"},
    )


class ProductCategory(BaseModel):
    """商品分类（docs/04 §7.11，ADR-0020）。

    ⚠️ 列名是 ``sort`` 而不是 ``sort_order`` —— 照 §7.11 字面实现，
    与其他表命名不一致，已登记 docs/12 待修文档。

    **不可物理删除**：分类被款号引用后只能停用（§7.11 规则表第一行）。
    """

    __tablename__ = "product_categories"

    code: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="SET / DRESS / TROUSERS / UNDERWEAR / VEST / THERMAL"
    )
    name: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="套装 / 单衣 / 单裤 / 棉毛 / 背心 / 打底裤"
    )
    sort: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="界面排序"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        comment="停用后不参与新建款号",
    )

    __table_args__ = (
        CheckConstraint("version > 0", name="ck_product_categories_version_positive"),
        Index("uq_product_categories_code", "code", unique=True),
        {"comment": "商品分类（04 §7.11，ADR-0020；内置 6 类，被引用不可删只能停用）"},
    )
