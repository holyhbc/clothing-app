from __future__ import annotations

from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, SmallInteger, String, text
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811 —— 别名照抄 docs/04 §2
from sqlalchemy.orm import Mapped, mapped_column

from app.common.models import BaseModel


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
