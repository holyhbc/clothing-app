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
from app.modules.cutting.models.enums import (
    CUTTING_ENTRY_MODE,
    DOCUMENT_STATUS,
    CuttingEntryMode,
)

if TYPE_CHECKING:
    from app.modules.cutting.models.lines import CuttingOrderLine


class CuttingOrder(BaseModel):
    """裁剪单表头（``docs/04 §7.7.2``）。

    ⚠️ **头汇总五列一律由 service 重算并覆盖入参**（``modules/02 §2`` C6「不信任前端」）：
    ``fabric_qty`` / ``output_qty`` / ``cut_waste_qty`` / ``balance_qty`` /
    ``hands_total``。本迁移**不建任何跨表一致性约束** —— 那需要 CHECK，
    而 CHECK 不能跨表（同 ``bundling_orders.hands_total`` 的说明）。

    ⚠️ **没有 ``product_category_id``**（ADR-0020，业务确认 2026-10-02）：
    单据分类不可改，一律 ``JOIN styles.category_id`` 取值。分类只在款号档案维护。

    ⚠️ ``status`` 原先在 ``04 §7.7.2`` 里写的是 ``cutting_status`` —— 那个类型
    **全仓从未定义**（只有基线提交出现过那一次，连 ``CREATE TYPE`` 都没有），
    照抄建表报 ``type "cutting_status" does not exist``。已改为 ``document_status``
    （``08 §1.1`` 的迁移图与 ``modules/02 §3.1`` 写的都是它）。**消除笔误，不是改决策。**

    ⚠️ ``color_codes`` 是**逗号分隔的字符串**，不是数组也不是子表：它只是列表页 /
    选批的展示与筛选辅助值，**权威配色在 :class:`CuttingOrderLineColor` 逐行逐色**
    （ADR-0017 一床多色）。用字符串而非数组是因为 ``04 §5`` 红线「不使用 PG 特有
    特性导致未来迁移困难」；用子表则与行内颜色表职责重复。

    ⚠️ 审核四列（``approved_by`` / ``approved_at`` / ``rejected_reason`` /
    ``cancelled_reason``）来自 ``04 §7.3``「所有单据表共享」，本节 DDL 原先漏了。
    不补的后果不是报错，而是 001b 做状态机时必然要回来 ``ALTER TABLE``。
    """

    __tablename__ = "cutting_orders"

    doc_no: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="CT-YYYYMMDD-6位序号（09 §2.1）"
    )
    workshop_id: Mapped[UUID] = mapped_column(
        ForeignKey("workshops.id", name="fk_cutting_orders_workshop"),
        nullable=False,
        comment="数据范围过滤依据（INV-8）",
    )
    # ⚠️ 外键指主键、业务键冗余：styles.uq_styles_no 是部分索引，PG 不允许外键引用
    style_id: Mapped[UUID] = mapped_column(
        ForeignKey("styles.id", name="fk_cutting_orders_style"), nullable=False
    )
    style_no: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="冗余：历史单据按它引用；⚠️ 不建外键（部分索引不可引用）",
    )
    color_codes: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="多色（本单要裁的颜色码，逗号分隔）；★ 权威配色在行内颜色表（ADR-0017）",
    )
    doc_date: Mapped[date] = mapped_column(Date, nullable=False)
    delivery_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    ply_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1"), comment="铺布层数"
    )
    entry_mode_default: Mapped[CuttingEntryMode] = mapped_column(
        CUTTING_ENTRY_MODE,
        nullable=False,
        default=CuttingEntryMode.MASTER,
        server_default=text("'MASTER'"),
        comment="本单默认录入模式（逐颜色可覆盖，04 §7.7.4）",
    )
    fabric_qty: Mapped[Decimal] = mapped_column(
        Numeric(14, 3),
        nullable=False,
        default=Decimal("0"),
        server_default=text("0"),
        comment="= Σ行 fabric_qty（service 重算）",
    )
    output_qty: Mapped[Decimal] = mapped_column(
        Numeric(14, 3),
        nullable=False,
        default=Decimal("0"),
        server_default=text("0"),
        comment="= Σ颜色 Σ尺码 output_qty（service 重算）",
    )
    cut_waste_qty: Mapped[Decimal] = mapped_column(
        Numeric(14, 3),
        nullable=False,
        default=Decimal("0"),
        server_default=text("0"),
        comment="裁损合计，**含 balance_qty**；不变式 cut_waste_qty >= balance_qty",
    )
    balance_qty: Mapped[Decimal] = mapped_column(
        Numeric(14, 3),
        nullable=False,
        default=Decimal("0"),
        server_default=text("0"),
        comment="尾数（不足件，不入库 / 不出码 / 不计件，09 §4.2）",
    )
    status: Mapped[DocumentStatus] = mapped_column(
        DOCUMENT_STATUS,
        nullable=False,
        default=DocumentStatus.DRAFT,
        server_default=text("'DRAFT'"),
        comment="⚠️ 04 §7.7.2 原写 cutting_status —— 那个类型全仓从未定义，照抄建表会失败",
    )
    hands_total: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
        comment="总手数 = Σ尺码 hands；草稿态为 0（还没明细），> 0 由 submit 校验",
    )
    remark_source: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="改单原因（红冲链，ADR-0021）"
    )
    # ⚠️ 04 §7.3 的单据公共列。不在本节 DDL 里重复抄，抄一遍就多一个可能不一致的副本
    approved_by: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True, comment="审核人（08 §1.1）"
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="审核时间"
    )
    rejected_reason: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="驳回原因（08 §1.1 必填）"
    )
    cancelled_reason: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="作废原因（08 §1.1 必填）"
    )

    __table_args__ = (
        UniqueConstraint("doc_no", name="uq_cutting_orders_doc_no"),
        CheckConstraint("version > 0", name="ck_cutting_orders_version_positive"),
        CheckConstraint("ply_count >= 1", name="ck_cutting_orders_ply"),
        CheckConstraint(
            # ⚠️⚠️ `>= 0` 而非 `> 0`：这两列有 DEFAULT 0，而**草稿态还没有布批行**，
            #    两者必然是 0 —— 写成 `> 0` 会让**任何草稿单都插不进去**
            #    （T-CUT-001a 实测撞出：`violates check constraint`）。
            #    与 `hands_total` 是同一个病，两条一起修。`> 0` 由 service 在
            #    `submit` 时校验（modules/02 §2 C6/C8）
            "fabric_qty >= 0 AND output_qty >= 0 AND cut_waste_qty >= 0 AND balance_qty >= 0",
            name="ck_cutting_orders_qty",
        ),
        # ⚠️ `>= 0` 而非 `> 0`：hands_total 有 DEFAULT 0，而**草稿态必然是 0**。
        #    写成 `> 0` 会让**任何新建的草稿单都插不进去**（同 bundling_orders）
        CheckConstraint("hands_total >= 0", name="ck_cutting_orders_hand"),
        # ⚠️ 三个索引都是**部分索引**（04 §5）：列表查询恒带 deleted_at IS NULL
        Index(
            "idx_cutting_orders_scope",
            "workshop_id",
            text("doc_date DESC"),
            "style_no",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_cutting_orders_trace",
            "style_no",
            text("doc_date DESC"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # 待审核列表（主管工作台）：status 在前，因为所有车间的待审核单都要查
        Index(
            "idx_cutting_orders_status_workshop",
            "status",
            "workshop_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        {"comment": "裁剪单表头（04 §7.7.2；ADR-0017 三层结构第 1 层）"},
    )

    #: 三层导航。⚠️ **``viewonly=True`` 是刻意的**：子行的增删改一律由 service
    #: **显式**写（``CuttingOrderLine(doc_id=...)``），不走 relationship。
    #:
    #: 理由不是「不信任 ORM」，而是 **cascade 的语义会与应用账号无 DELETE 权限打架**：
    #: 子表的 ``ON DELETE RESTRICT`` 是数据库层的删除保护，而一旦给 relationship
    #: 配上 ``cascade="all, delete-orphan"``，ORM 就会在删父行时先删子行 ——
    #: 那等于让「软删单据」变成「级联硬删三层明细」，而 ``04 §6.2.1`` 明确禁止。
    #:
    #: ``lazy="selectin"`` 让详情查询一次拿完三层（2 条语句而不是逐层懒加载的 4 条），
    #: 而写入路径**不依赖**它 —— 那里传的是显式构造好的列表（见 service 的 recalc_*）。
    lines: Mapped[list[CuttingOrderLine]] = relationship(
        "CuttingOrderLine",
        back_populates="order",
        lazy="selectin",
        viewonly=True,
        order_by="CuttingOrderLine.line_no",
    )
