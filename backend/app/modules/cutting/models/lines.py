from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811 —— 别名照抄 docs/04 §2
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.models import BaseModel
from app.modules.cutting.models.enums import CUTTING_ENTRY_MODE, CuttingEntryMode

if TYPE_CHECKING:
    from app.modules.cutting.models.order import CuttingOrder


class CuttingOrderLine(BaseModel):
    """裁剪单布批行（★ **耗料记在行**，ADR-0017 三层结构的第 2 层的父）。

    ⚠️ **耗料（``fabric_qty`` / ``waste_qty`` / ``fabric_weight_kg``）记在行，
    出数（``output_qty``）记在尺码明细** —— 理由是耗料是**布的属性**，
    与裁几个颜色无关；同一缸布被多色共用时只录一次（``04 §7.7.3``）。

    ⚠️ ``stock_id`` **必填**（ADR-0022）：不允许自由输入缸号，必须从
    ``material_stocks`` 里选一个已存在的布批行；``fabric_qty > 可用量`` → ``40006``。
    ``dye_lot_no`` / ``bolt_no`` / ``supplier_id`` / ``material_id`` 是它的**快照** ——
    加它们不是冗余而是必需：审核与对账要「按缸号 + 匹号」查
    （``idx_cutting_order_lines_lot``），而那次查询不应该 JOIN 库存表与供应商表。

    ⚠️ ``fabric_qty`` 由**铺布实耗正向录入**，不由出数反推（C35）；
    行耗料下限 ``fabric_qty ≥ Σ(颜色 Σ(尺码 output_qty × BOM 单件用量)) + waste_qty``
    在 service 校验（C33 → ``30002``），**不是**数据库 CHECK。
    """

    __tablename__ = "cutting_order_lines"

    doc_id: Mapped[UUID] = mapped_column(
        ForeignKey("cutting_orders.id", name="fk_cutting_order_lines_doc", ondelete="RESTRICT"),
        nullable=False,
    )
    line_no: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="1 起；UNIQUE (doc_id, line_no)"
    )
    stock_id: Mapped[UUID] = mapped_column(
        ForeignKey("material_stocks.id", name="fk_cutting_order_lines_stock"),
        nullable=False,
        comment="★ 锁定的布批行（ADR-0022：必填，不允许自由输入缸号）",
    )
    supplier_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("suppliers.id", name="fk_cutting_order_lines_supplier"),
        nullable=True,
        comment="快照（只读，便于按供应商查裁剪，ADR-0022）",
    )
    material_id: Mapped[UUID] = mapped_column(
        ForeignKey("materials.id", name="fk_cutting_order_lines_material"), nullable=False
    )
    style_no: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="冗余：便于按款号汇总；⚠️ 不建外键（部分索引不可引用）",
    )
    dye_lot_no: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="缸号（09 §1.2）；行身份的一部分，耗料/扣料/台账/门幅校验都以此为准",
    )
    bolt_no: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="匹号（09 §1.2）；与 dye_lot_no 共同构成批次身份（ADR-0012）",
    )
    color_plan: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="铺布排版说明（可选）"
    )
    width_cm: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 2),
        nullable=True,
        comment="铺布用门幅（默认取批次实测值）；有效门幅校验的输入（02 C24）",
    )
    fabric_qty: Mapped[Decimal] = mapped_column(
        Numeric(14, 3),
        nullable=False,
        comment="★ 耗料（米），行级 —— 布的属性；由铺布实耗正向录入，不由出数反推（C35）",
    )
    fabric_weight_kg: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 3), nullable=True, comment="用掉的重量（kg）；布料主计量是重量（09 §1.2）"
    )
    waste_qty: Mapped[Decimal] = mapped_column(
        Numeric(14, 3),
        nullable=False,
        default=Decimal("0"),
        server_default=text("0"),
        comment="布头（可再裁，入库）+ 布损；C33 耗料下限校验里必须加上这一项",
    )
    output_qty: Mapped[Decimal] = mapped_column(
        Numeric(14, 3),
        nullable=False,
        default=Decimal("0"),
        server_default=text("0"),
        comment="行出数 = Σ(颜色 Σ(尺码 output_qty))；审核时按此重算",
    )
    balance_qty: Mapped[Decimal] = mapped_column(
        Numeric(14, 3),
        nullable=False,
        default=Decimal("0"),
        server_default=text("0"),
        comment="行余量 = output_qty - Σ(颜色尺码 output_qty)；负数 → 30002（C34）",
    )

    __table_args__ = (
        # ⚠️ **部分唯一索引**（迁移 0011，REV-2026-10）：原为普通 UNIQUE 约束，
        #    而 modules/02 §6 的 PUT /lines 是**全量替换**语义 —— 硬约束下
        #    「软删旧行 + 插新行」必然撞 `duplicate key ... (doc_id, line_no)`。
        #    唯一约束要表达的是「**当前有效**的行之间不重号」；软删行已不在业务上，
        #    用它占号没有意义。与 styles.uq_styles_no / uq_material_stocks_lot 同一口径。
        Index(
            "uq_cutting_order_lines",
            "doc_id",
            "line_no",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint("version > 0", name="ck_cutting_order_lines_version_positive"),
        CheckConstraint("fabric_qty > 0", name="ck_cutting_lines_fabric"),
        Index(
            "idx_cutting_order_lines_lot",
            "dye_lot_no",
            "bolt_no",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_cutting_order_lines_sup",
            "supplier_id",
            "doc_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        {"comment": "裁剪单布批行（★ 耗料记在行；04 §7.7.2 / ADR-0017）"},
    )

    #: 反向导航。⚠️ ``lazy="raise_on_sql"``：写入路径不需要读父单，
    #: 而「不需要」不该等于「静默返回 None」—— 那会让 ``order.lines`` 拼错时
    #: 变成一个 ``None`` 而不是报错。（SQLAlchemy 2.1 起 ``noload`` 已废弃，
    #: 它正是那个「静默返回 None」的策略。）
    order: Mapped[CuttingOrder] = relationship(
        "CuttingOrder", back_populates="lines", lazy="raise_on_sql", viewonly=True
    )
    #: 行内颜色。⚠️ **不按 ``color_code`` 排序** —— 业务上「这个颜色先录」没有意义，
    #: 而排序会让「返回顺序」依赖字典序，页面上颜色块会跳来跳去。
    colors: Mapped[list[CuttingOrderLineColor]] = relationship(
        "CuttingOrderLineColor",
        back_populates="line",
        lazy="selectin",
        viewonly=True,
        order_by="CuttingOrderLineColor.id",
    )


class CuttingOrderLineColor(BaseModel):
    """裁剪单行内颜色（ADR-0017 第 2 层：一床可多个颜色）。

    ⚠️ **唯一键是 ``(line_id, color_code)`` 而不是 ``(doc_id, color_code)``** ——
    这正是 ADR-0017 把颜色从单据级下沉到行内颜色级的**全部理由**：
    「这个颜色用哪种录入模式、这套比例」是**这匹布上这个颜色**的属性，
    换一匹布就可能换模式。同一缸布被两个颜色用时，两者的模式与手数各自独立
    （``modules/02 §5.1`` 的示例：行1 的 WHT 走 MASTER、行2 的 WHT 走 MANUAL）。

    ⚠️ 三个汇总列（``hands_total`` / ``output_qty_total`` / ``balance_qty_total``）
    一律 service 算完回写（不信任前端），本迁移**不建跨表一致性约束** ——
    那需要 CHECK，而 CHECK 不能跨表。

    ⚠️ ``ratio_snapshot`` 是 jsonb（``04 §8`` 允许快照用 jsonb）：它**不参与查询与
    聚合条件**，只用于事后回答「当时为什么这么裁」（C29）。存快照而不实时 JOIN
    比例表，是因为反审核与审计要能还原「当时按什么算的」（INV-3 同理）。
    """

    __tablename__ = "cutting_order_line_colors"

    line_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "cutting_order_lines.id", name="fk_cutting_line_colors_line", ondelete="RESTRICT"
        ),
        nullable=False,
    )
    color_code: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="必须是该款已定义的颜色，否则 20007"
    )
    entry_mode: Mapped[CuttingEntryMode] = mapped_column(
        CUTTING_ENTRY_MODE,
        nullable=False,
        default=CuttingEntryMode.MASTER,
        server_default=text("'MASTER'"),
        comment="录入模式：A 按比例带出 / B 统一件数 / C 自定义明细（ADR-0014）",
    )
    qty_per_hand: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 4),
        nullable=True,
        comment="模式 A/B 的默认每手件数（尺码明细行的 qty_per_hand 才是权威值）",
    )
    uniform_qty: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 3), nullable=True, comment="模式 B 的统一件数"
    )
    ratio_snapshot: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="下单时的比例建议值快照 {size_code: ratio}；事后解释「为什么这么裁」（C29）",
    )
    hands_total: Mapped[Decimal] = mapped_column(
        Numeric(14, 4),
        nullable=False,
        default=Decimal("0"),
        server_default=text("0"),
        comment="= Σ(尺码 hands)，service 重算（可小数，比例之和即手数合计）",
    )
    output_qty_total: Mapped[Decimal] = mapped_column(
        Numeric(14, 3),
        nullable=False,
        default=Decimal("0"),
        server_default=text("0"),
        comment="= Σ(尺码 output_qty)，service 重算",
    )
    balance_qty_total: Mapped[Decimal] = mapped_column(
        Numeric(14, 3),
        nullable=False,
        default=Decimal("0"),
        server_default=text("0"),
        comment="= Σ(尺码 balance_qty)；仅人工改出数时 > 0",
    )
    entry_mode_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="人工改出数自动转 MANUAL 的时刻（C28）",
    )
    entry_mode_changed_by: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        nullable=True,
        comment="模式切换留痕人（配合 C27 与 document_logs）",
    )

    __table_args__ = (
        # ⚠️ 部分唯一索引，理由同 uq_cutting_order_lines（迁移 0011）
        Index(
            "uq_cutting_line_colors",
            "line_id",
            "color_code",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint("version > 0", name="ck_cutting_order_line_colors_version_positive"),
        {"comment": "裁剪单行内颜色（ADR-0017：一床可多个颜色；04 §7.7.2）"},
    )

    line: Mapped[CuttingOrderLine] = relationship(
        "CuttingOrderLine", back_populates="colors", lazy="raise_on_sql", viewonly=True
    )
    #: 尺码明细。按 ``size_line_no`` 升序 —— 那是**录入顺序**，
    #: 也是唯一键 ``(line_color_id, size_line_no)`` 的那一半，排序后与用户所见一致。
    size_lines: Mapped[list[CuttingOrderSizeLine]] = relationship(
        "CuttingOrderSizeLine",
        back_populates="line_color",
        lazy="selectin",
        viewonly=True,
        order_by="CuttingOrderSizeLine.size_line_no",
    )


class CuttingOrderSizeLine(BaseModel):
    """裁剪单尺码明细（★ **出数的权威来源**，ADR-0017 第 3 层）。

    ⚠️ ``hands`` / ``qty_per_hand`` / ``output_qty`` 三个都是 **integer**（ADR-0020）：
    手数是权威输入，件数 = 乘法精确值，**不再 floor、不允许 1.5 手**。
    与之相对，**比例主数据** ``style_color_size_ratios.ratio`` 仍是
    ``numeric(14,4)`` 可小数（1.5 手是合法的**建议值**，只是不能直接当权威输入）。

    ⚠️ ``line_id`` 是**冗余外键**（打菲 / 计件按布批反查），所以要有
    ``idx_cutting_size_lines_line`` —— ``uq_cutting_size_lines`` 只覆盖
    ``line_color_id``，覆盖不到它。而「按布批反查这个布批裁了哪些尺码」
    正是打菲（``bundles.cutting_size_line_id``）与计件的入口查询。

    ⚠️ **允许 ``(line_color_id, size_code)`` 重复**（C30）：同一颜色同一尺码可以
    多行，各行手数 / 每手件数不同（ADR-0014 的模式 C 例子：
    ``XL 2手×60`` + ``XL 1手×30``）。唯一键是 ``(line_color_id, size_line_no)``，
    即行号在**同颜色内**唯一即可。
    """

    __tablename__ = "cutting_order_size_lines"

    line_color_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "cutting_order_line_colors.id",
            name="fk_cutting_size_lines_color",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    line_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "cutting_order_lines.id", name="fk_cutting_size_lines_line", ondelete="RESTRICT"
        ),
        nullable=False,
        comment="冗余：打菲 / 计件按布批反查（04 §5 要求这根外键建索引）",
    )
    size_line_no: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="明细行号；UNIQUE (line_color_id, size_line_no)"
    )
    size_code: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="尺码码；⚠️ 同一 (line_color_id, size_code) **允许**多行（C30）",
    )
    hands: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="★ 本行裁几手（整数，用户直接输入；ADR-0020）"
    )
    qty_per_hand: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="★ 本行每手几件（整数）；同尺码多行可不同"
    )
    output_qty: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="= hands × qty_per_hand（精确整数，**不取整**）"
    )
    output_qty_manual: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment="人工指定件数 → 该颜色转 MANUAL，差额进 balance_qty（C28）",
    )
    balance_qty: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
        comment="人工指定出数时的差额（不足件 → 损耗）；不入库 / 不出码 / 不计件",
    )
    hands_seq: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="手序号（打菲按手拆分用，ADR-0016）；阶段一由 service 审核时回填",
    )
    # ⚠️ **`remark` 不在这里声明** —— 它由 :class:`BaseModel` 经 ``AuditMixin`` 提供
    #    （04 §2 的公共字段之一，注释「追加行」「加急」等说明）。
    #    04 §7.7.2 原先在本表显式写了一遍 `remark text`，紧接着又写 `...公共字段` ——
    #    照抄建表报 `DuplicateColumnError`。两处是同一列，所以只留公共字段那份。

    __table_args__ = (
        # ⚠️ 部分唯一索引，理由同 uq_cutting_order_lines（迁移 0011）
        Index(
            "uq_cutting_size_lines",
            "line_color_id",
            "size_line_no",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint("version > 0", name="ck_cutting_order_size_lines_version_positive"),
        CheckConstraint("hands > 0 AND qty_per_hand > 0", name="ck_cutting_size_hands"),
        CheckConstraint("balance_qty >= 0", name="ck_cutting_size_balance"),
        Index(
            "idx_cutting_size_lines_line",
            "line_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        {"comment": "裁剪单尺码明细（出数权威来源；04 §7.7.2 / ADR-0017 / ADR-0020）"},
    )

    line_color: Mapped[CuttingOrderLineColor] = relationship(
        "CuttingOrderLineColor",
        back_populates="size_lines",
        lazy="raise_on_sql",
        viewonly=True,
    )
