"""打菲单表头 ``bundling_orders`` 与明细 ``bundling_order_lines``。

DDL 权威来源：``docs/04 §7.16``（打菲单表与明细表）+ ``docs/04 §7.3``
（单据公共列）。字段表的「读语义」保留在 ``modules/03 §3.1/§3.2``，两者由守卫
``test_bundling_tables_match_module_field_tables`` / ``test_field_tables_match_04_ddl``
保持双向一致。

## 与裁剪单的三处关键衔接

1. ``source_cutting_order_id`` → ``cutting_orders(id)``：建单时必须 `APPROVED`（B8）。
2. ``cutting_size_line_id`` → ``cutting_order_size_lines(id)``：**件数与手数的权威来源**
   （ADR-0016 + ADR-0017 §4），每一行必填（B26）。
3. ``hands`` 是 **integer**（ADR-0020 / 变更 0111）：手数是用户直接输入的权威值，
   件数 = ``hands × 每手件数`` 是整数 × 整数的**精确值，不 floor / 不 round**。
   ❌ 旧口径「可小数（如 1.5 手），审核时 round(hands)」已作废。

## 本文件**不包含**的东西（T-BUND-001 范围之外）

状态机迁移、``submit`` 预占 / ``approve`` 结转、``bundle_no`` 生成、接口与页面 ——
全在 T-BUND-002 起的卡。所以这里没有 ``Bundle`` 的写入逻辑，也没有状态迁移方法；
``hands_total = count(bundles)`` 这类跨表不变量由 service 在审核事务内断言
（CHECK 不能跨表，§7.16 原文已注明）。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811 —— 别名照抄 docs/04 §2
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.enums import DocumentStatus
from app.common.models import BaseModel

# ⚠️ ``document_status`` 是**全仓共用**的 PG 枚举（迁移 0009 建）。按
#    ``cutting/models/enums.py`` 的明确要求从那里 import，**不要**在本地再
#    ``SAEnum(..., name="document_status")`` 一次 —— 两次会在 ``Base.metadata``
#    里留下两个同名类型，autogenerate 见到就报 ``duplicate object``。
from app.modules.cutting.models.enums import DOCUMENT_STATUS

if TYPE_CHECKING:
    from app.modules.bundling.models.bundle import Bundle


class BundlingOrder(BaseModel):
    """打菲单表头（``docs/04 §7.16`` + §7.3 单据公共列）。

    ⚠️ ``bundle_qty`` 是「一扎几件」的**录入参考值**（来自
    ``style_operations.bundle_qty`` / ``operations.default_bundle_qty``），
    **审核后不作权威**；权威值是 :attr:`Bundle.bundle_qty`（该手件数）——
    这是 §12 Q-B11 的名称冲突，本模型保留原列名以免与文档分叉。

    ⚠️ ``hands_total`` 是**审核前就存在**的列，草稿态必然是 0（还没生成码），
    所以 CHECK 是 ``>= 0`` 而非 ``> 0``（§7.16 REV-2026-10 修正）。写成 ``> 0``
    会让**任何新建的草稿单都插不进去** —— 与 ``cutting_orders.hands_total`` 同款。

    ❌ **作废列**：``source_bundle_seq_from`` / ``source_bundle_seq_to``（件号区间）。
    ADR-0016 后码内不再有全局件序号，无区间概念。**不要照字段表加这两个真列**。
    """

    __tablename__ = "bundling_orders"

    __table_args__ = (
        UniqueConstraint("doc_no", name="uq_bundling_orders_doc_no"),
        CheckConstraint("version > 0", name="ck_bundling_orders_version_positive"),
        CheckConstraint(
            "output_qty > 0 AND output_qty = trunc(output_qty)",
            name="ck_bundling_orders_output",
        ),
        CheckConstraint("hands_total >= 0", name="ck_bundling_orders_hands"),
        Index(
            "idx_bundling_orders_workshop_doc_date",
            "workshop_id",
            text("doc_date DESC"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # 待审核列表：status 在前，因为所有车间的待审核单都要查
        Index(
            "idx_bundling_orders_status_workshop",
            "status",
            "workshop_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_bundling_orders_style_operation_date",
            "style_no",
            "operation_no",
            "doc_date",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        {"comment": "打菲单表头（04 §7.16；ADR-0016 按手打菲）"},
    )

    # ---- §7.3 单据公共列 ----
    doc_no: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[DocumentStatus] = mapped_column(
        DOCUMENT_STATUS,
        nullable=False,
        default=DocumentStatus.DRAFT,
        server_default=text("'DRAFT'"),
    )
    doc_date: Mapped[date] = mapped_column(Date, nullable=False)
    workshop_id: Mapped[UUID] = mapped_column(
        ForeignKey("workshops.id", name="fk_bundling_orders_workshop"),
        nullable=False,
    )
    approved_by: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)
    cancelled_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)
    # ---- 业务列（§7.16）----
    style_no: Mapped[str] = mapped_column(String(32), nullable=False)
    operation_no: Mapped[str] = mapped_column(
        String(16),
        ForeignKey("operations.operation_no", name="fk_bundling_orders_operation"),
        nullable=False,
    )
    color_group: Mapped[str] = mapped_column(String(32), nullable=False)
    color_code: Mapped[str] = mapped_column(String(16), nullable=False)
    bundle_qty: Mapped[int] = mapped_column(Integer, nullable=False)
    hands_total: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    output_qty: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    balance_qty: Mapped[Decimal] = mapped_column(
        Numeric(14, 3),
        nullable=False,
        default=Decimal("0"),
        server_default=text("0"),
    )
    source_cutting_order_id: Mapped[UUID] = mapped_column(
        ForeignKey("cutting_orders.id", name="fk_bundling_orders_source_cutting"),
        nullable=False,
    )
    label_print_qty: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )

    #: 明细导航。⚠️ ``viewonly=True`` 是刻意的：子行增删改一律由 service 显式写，
    #: 不走 relationship —— 一旦配 ``cascade="all, delete-orphan"``，ORM 会在
    #: 删父行时先删子行，而 ``ON DELETE RESTRICT`` 与应用账号无 DELETE 权限
    #: 都会与之打架（与 cutting 同款理由）。
    lines: Mapped[list[BundlingOrderLine]] = relationship(
        "BundlingOrderLine",
        back_populates="order",
        lazy="selectin",
        viewonly=True,
        order_by="BundlingOrderLine.line_no",
    )


class BundlingOrderLine(BaseModel):
    """打菲明细（**按尺码**，``docs/04 §7.16`` + §7.3 行表公共列）。

    ⚠️ **同一 ``(doc_id, size_code)`` 允许多行**（同尺码可来自裁剪的多个布批 /
    多条尺码明细行，ADR-0017 §4）；唯一键是 ``(doc_id, line_no)``。

    ⚠️ ``hands`` 是 **integer**（ADR-0020）：审核时生成 ``hands`` 个码（一码一手），
    ``CHECK (hands > 0)``，``<= 0`` → ``10001``。❌ 旧列 ``bundle_count``（扎数）作废。

    ⚠️ ``available_qty_before`` 是提交时从 ``cutting_outputs`` 读到的可用量**快照**；
    该表属 T-BASE-009-2，所以本卡只建列、不建外键（§7.16 原文）。建了外键会让
    0014 依赖一张尚未存在的表，迁移无法独立执行。
    """

    __tablename__ = "bundling_order_lines"

    __table_args__ = (
        UniqueConstraint("doc_id", "line_no", name="uq_bundling_order_lines_line"),
        CheckConstraint("version > 0", name="ck_bundling_order_lines_version_positive"),
        CheckConstraint("hands > 0", name="ck_bundling_lines_hands"),
        CheckConstraint("planned_qty = trunc(planned_qty)", name="ck_bundling_lines_planned"),
        Index(
            "idx_bundling_order_lines_doc_size",
            "doc_id",
            "size_code",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_bundling_order_lines_cutting",
            "cutting_size_line_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        {"comment": "打菲明细（按尺码；04 §7.16）"},
    )

    doc_id: Mapped[UUID] = mapped_column(
        ForeignKey("bundling_orders.id", name="fk_bundling_order_lines_doc", ondelete="RESTRICT"),
        nullable=False,
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    color_code: Mapped[str] = mapped_column(String(16), nullable=False)
    size_code: Mapped[str] = mapped_column(String(16), nullable=False)
    operation_no: Mapped[str] = mapped_column(String(16), nullable=False)
    cutting_size_line_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "cutting_order_size_lines.id",
            name="fk_bundling_order_lines_cutting_size_line",
        ),
        nullable=False,
    )
    hands: Mapped[int] = mapped_column(Integer, nullable=False)
    planned_qty: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    available_qty_before: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    group_no: Mapped[str | None] = mapped_column(String(32), nullable=True)
    workstation_no: Mapped[str | None] = mapped_column(String(32), nullable=True)

    #: 反向导航。⚠️ ``raise_on_sql``：写入路径不需要读父单，而「不需要」不该等于
    #: 「静默返回 None」—— 拼错时应当报错（与 cutting 同款）。
    order: Mapped[BundlingOrder] = relationship(
        "BundlingOrder", back_populates="lines", lazy="raise_on_sql", viewonly=True
    )
    #: 本行生成的码。按 ``hands``（手序号）升序 —— 与标签「第 N 手」展示一致。
    bundles: Mapped[list[Bundle]] = relationship(
        "Bundle",
        back_populates="line",
        lazy="selectin",
        viewonly=True,
        order_by="Bundle.hands",
    )
