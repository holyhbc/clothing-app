"""裁剪单四层表模型（T-CUT-001a / 迁移 0009，DDL 权威来源 ``docs/04 §7.7.2``）。

## 三层结构（ADR-0017）

```
cutting_orders                       裁剪单（日期 / 车间 / 款号）
└── cutting_order_lines               ★ 行 = 布批（缸号 + 匹号）—— 耗料记在行
    └── cutting_order_line_colors     ★ 颜色（一床可多个）—— 录入模式 + 比例快照
        └── cutting_order_size_lines  尺码 + hands + qty_per_hand + output_qty
```

「为什么是三层而不是把颜色平铺」写在 ``modules/02 §3.0``（照抄 04 §7.7.3）：
平铺会丢失「每个颜色一套录入模式与一套比例」的语义，而模式**恰恰**是
「这匹布上这个颜色」的属性 —— 换一匹布就可能换模式（同一缸布被两个颜色用时
各自的模式与手数独立）。

## 三条贯穿四张表的建模口径

1. **耗料记在行、出数记在尺码明细**。耗料是**布的属性**，与裁几个颜色无关
   （同一缸布被多色共用时只录一次）；出数是**颜色的属性**。
   余量归行：``line.balance_qty = line.output_qty - Σ(颜色尺码 output_qty)``。
2. **业务键冗余、外键指主键**。``style_no`` / ``dye_lot_no`` / ``bolt_no`` /
   ``color_code`` 一律**不建外键**：``styles.uq_styles_no`` 与
   ``material_stocks.uq_material_stocks_lot`` 都是 ``WHERE deleted_at IS NULL``
   的**部分索引**（为了让款号 / 缸号软删后可复用），而 PG 的外键只能引用普通
   唯一约束或主键 —— 照抄会报 ``there is no unique constraint matching given keys``
   （T-DOCS-003 实测）。与 0005 / 0008 同一口径。
3. **四张表全部软删 + 乐观锁**（都用 :class:`BaseModel`），**没有一张 append-only**。
   裁剪单要反审核、要留痕、要软删历史单据；append-only 那套
   （只有 ``id`` + ``created_at`` + ``created_by``）表达不了「可撤销」。
   唯一的 append-only 表是 ``wip_ledger_lines``（迁移 0008）。

## 本文件**不包含**的东西（T-CUT-001a 范围之外）

数量口径（头汇总重算 / ``floor`` 取整 / 行耗料下限 C33）、乐观锁 UPDATE、
锁批（``material_stocks.locked_qty`` 累加）、``docs/08 §2.1`` 的审核七步 ——
全在 **T-CUT-001b**；接口与页面在 **T-CUT-001c**。所以这里**没有 relationship**：
父子导航是 service 的事，而 service 还没写；先摆 relationship 只会让人误以为
ORM 的级联已经处理了 ``ON DELETE RESTRICT``（它没有 —— RESTRICT 是数据库行为，
ORM 一旦配了 ``cascade="all, delete-orphan"`` 就会与应用账号无 DELETE 权限打架）。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
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
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811 —— 别名照抄 docs/04 §2
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.enums import DocumentStatus
from app.common.models import Base, BaseModel, IdMixin


class CuttingEntryMode(StrEnum):
    """裁剪明细的录入模式（``docs/04 §7.7.2`` 的 ``CREATE TYPE``，ADR-0014）。

    ⚠️ **挂在行内颜色级**（``cutting_order_line_colors.entry_mode``），不是单据级 ——
    「这个颜色用哪种模式」是**这匹布上这个颜色**的属性，换一匹布就可能换模式
    （ADR-0017 修订了 ADR-0014 的单据级挂载）。

    ==================  ==================================  ==================
    值                  场景                                界面上填什么
    ==================  ==================================  ==================
    ``MASTER``         常规，尺码比例固定                  点「按比例带出」生成行
    ``UNIFORM``        所有尺码件数一样                    点「统一件数」填一个数
    ``MANUAL``         裁床个别尺码数量不一致              任意增 / 删 / 改
    ==================  ==================================  ==================

    ⚠️ 三种模式**可以在同一张单里混用**（行1 红色走 A、行2 黑色走 C）
    —— 模式是「行 × 颜色」级属性。
    """

    MASTER = "MASTER"
    UNIFORM = "UNIFORM"
    MANUAL = "MANUAL"


#: PG enum ``document_status``（``docs/04 §3`` / ``docs/08 §1.1``），由迁移 0009 建。
#:
#: ⚠️ **必须是真正的 PG enum**，不能拿 ``String`` 糊弄 —— 否则枚举值失去数据库层
#: 的约束，而 ``alembic check`` 会一直报 ``modify_type``（与 ``size_class`` 同一理由）。
#: ``values_callable`` 不给的话 SQLAlchemy 存**成员名**，恰好与 PG enum 的值一致。
#:
#: ⚠️ **下一个单据表（``bundling_orders`` / ``purchase_orders`` …）必须从这里 import**，
#: 不要重新声明一遍 —— 两次 ``SAEnum(..., name="document_status")`` 会在
#: ``Base.metadata`` 里留下两个同名类型，autogenerate 见到就报 ``duplicate object``。
DOCUMENT_STATUS = SAEnum(DocumentStatus, name="document_status", create_type=False)

#: PG enum ``cutting_entry_mode``，由迁移 0009 建。
#: ⚠️ ``create_type=False``：类型由迁移手写 ``CREATE TYPE`` 建，
#: 不加这个 SQLAlchemy 会**自己再发一条**，撞车报 ``type already exists``
#: （0004 / 0007 / 0008 各踩过一次）。
CUTTING_ENTRY_MODE = SAEnum(CuttingEntryMode, name="cutting_entry_mode", create_type=False)


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


class CuttingDocNoSequence(IdMixin, Base):
    """裁剪单号按天计数器（**纯计数表**，C1）。

    ⚠️ **豁免 ``04 §2`` 公共字段**，理由与 ``style_no_sequences`` 完全一样：
    纯计数表没有「谁改的」的概念 —— 它由取号逻辑在事务内
    ``UPDATE ... SET next_no = next_no + 1 RETURNING next_no - 1`` 递增，
    而 ``created_by`` / ``updated_by`` 是 ``NOT NULL`` 的**操作人**字段；
    计数行的「创建者」只能是系统，那才是谎话。

    ⚠️ **为什么不用 PG ``SEQUENCE``**：C1 的单号**按天重置**，而 ``setval`` 做不了
    「新的一天自动从头来」（要么在应用里判断日期、要么跑 DDL）。``(doc_date, prefix)``
    作唯一键的话，新的一天自然就是新的一行。

    ⚠️ **回滚会不会导致重号**：会 —— 事务回滚时 ``next_no`` 一起退回。但那个号
    **从未出现在任何响应里**（单据没建成），业务上不可观测；真正保证「发出即唯一」
    的是 ``uq_cutting_orders_doc_no``。
    """

    __tablename__ = "cutting_doc_no_sequences"

    doc_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        comment="按天分组：单据号 CT-{YYYYMMDD}-{6 位} 的 YYYYMMDD 部分",
    )
    prefix: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="CT",
        server_default=text("'CT'"),
        comment="单据前缀（09 §2.1：裁剪 = CT）；写成一列是为了将来分单据类型时不必改表",
    )
    next_no: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
        comment="下一个可用序号，从 1 开始（09 §2.1：6 位）",
    )

    __table_args__ = (
        UniqueConstraint("doc_date", "prefix", name="uq_cutting_doc_no_sequences_date_prefix"),
        CheckConstraint("next_no >= 1", name="ck_cutting_doc_no_sequences_next_no"),
        {"comment": "裁剪单号按天计数器（纯计数表，豁免 04 §2 公共字段，同 style_no_sequences）"},
    )
