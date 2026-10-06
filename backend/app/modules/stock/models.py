"""库存台账、批次耗用明细与锁定（T-BASE-009 / 迁移 0012，DDL 权威来源 ``docs/04 §7.2.5``）。

## 三张表的关系与它们各自的不变性

```
material_stocks（结存，迁移 0008，在 base/models.py）
└── stock_ledgers            一次动作一条：IN / OUT / ADJUST
    └── stock_ledger_lines   一条台账 → N 行批次耗用（缸号 / 匹号 / 数量 / 成本快照）

material_stocks ←── stock_reservations   单据占用（释放用置 released_at）
```

1. **耗料记在台账、成本记在明细**。台账是「一次动作」，一条动作可能跨 N 个批次，
   单行放不下成本 —— 所以 ``modules/06 §3.3`` REV-2026-10 把 ``unit_cost`` /
   ``amount`` 从台账移除，改为 ``SUM(明细.amount)`` 汇总。**定价一律取明细行的
   批次 ``unit_cost``**，台账汇总单价只给报表用。
2. **两张台账表 append-only，只有 ``stock_reservations`` 是软删表**。见下方
   「为什么台账不带 deleted_at」。
3. **业务键冗余、外键指主键 / 不建 FK**。``stock_ledgers.stock_id`` 是
   ``stock_type`` 多态（物料栈 or 成衣栈），跨表多态**无法建 FK**，由 service 保证
   + ``40003`` 对账兜底（``modules/06 §3.6``）；``source_doc_id`` 不建 FK 是因为
   单据可整体软删或反审核，建 FK 会阻止这两件合法操作。

## 为什么台账不带 ``deleted_at`` / ``version`` / ``updated_*``

抄 ``app/common/models.py`` 里 ``WipLedgerLine`` 的类注释（**同一理由**）：

> 用「软删 + 乐观锁」表达「可撤销」是自相矛盾的 —— 行一旦写入就不该变；
> 而 ``updated_*`` 永远为空，**空着比没有更糟**（读的人会以为「没人改过」，
> 而不是「这表不能改」）。

台账的纠错手段**只有**红冲 + 重入（INV-4 / ADR-0005）：
``reversal_of_id`` 自引用 + ``direction`` 取反 + 数量取反，写成**新行**。
原行永远保留 —— 「这批布什么时候被哪张单扣过、后来冲回去没有」正是对账要回答的问题，
而一条被软删的流水会让 ``v_stock_reconciliation`` 永远对不上且**无从追溯**。

⚠️ 连带后果：台账相关的索引**不能**带 ``WHERE deleted_at IS NULL`` 谓词
（那一列不存在，写了建表就失败），两个对账视图也**不需要**软删过滤。

## 本文件**不包含**的东西

写入台账的业务逻辑（裁剪单审核第 ①② 步、到货登记第 ③ 步）、锁批与释放 ——
全在 **T-CUT-001b-3**。所以这里**没有 relationship**：父子导航是 service 的事，
而 service 还没写。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
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
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811 —— 别名照抄 docs/04 §2
from sqlalchemy.orm import Mapped, mapped_column

from app.common.models import Base, BaseModel, IdMixin


class StockType(StrEnum):
    """库存双栈（``modules/06 §1.2``：物料与成衣分表，避免一堆可空列）。

    ⚠️ 多态列的取值**只增不改**。两栈各自一张结存表，``stock_ledgers.stock_id``
    按本列指向其中一张，而跨表多态**建不了 FK** —— 所以「新栈没登记进来」
    的后果是查询静默返回空，而不是报错。``v_stock_reconciliation`` 是兜底。
    """

    MATERIAL = "MATERIAL"
    FINISHED_GOODS = "FINISHED_GOODS"


class StockDirection(StrEnum):
    """出入库方向（``modules/06 §3.3``）。

    ⚠️ **数量带符号**（``IN`` 正 / ``OUT`` 负），所以对账视图可以直接 ``SUM``
    得到净变动，不必按 ``direction`` 分组求和再相减。

    ==============  ==============================================================
    值              场景
    ==============  ==============================================================
    ``IN``          到货入库 / 布头入库 / 完工入库 / 盘盈 / **红冲出库的反向**
    ``OUT``         裁剪耗料出库 / 领料 / 销售发货 / 调拨出 / 盘亏
    ``ADJUST``      盘点调整（按差额取号，不保证与前两者同号）
    ==============  ==============================================================

    ⚠️ ``ADJUST`` 之所以单列而不复用 ``IN``/``OUT``：盘点差异的成因与业务动作
    无关（盘盈盘亏都是「盘出来的」），把它混进 ``IN``/``OUT`` 会让报表在
    「按来源单据统计入库量」时把盘点调整算成正常入库。
    """

    IN = "IN"
    OUT = "OUT"
    ADJUST = "ADJUST"


class StockDocType(StrEnum):
    """台账来源单据类型（``modules/06 §3.3``，``04 §7.2.5`` 的枚举值域）。

    ⚠️ **PG enum 只能追加值，不能删改**（``docs/04 §3``）。加新单据类型要用
    ``ALTER TYPE stock_doc_type ADD VALUE '...'``，而那条**不能进事务块**，
    迁移里必须包 ``autocommit_block()``（抄迁移 0004 的 ``size_class`` 限制）。
    ⚠️ ``PurchaseOrder`` 取代了**已取消**的 ``PurchaseReceipt``（ADR-0012 取消了
    独立入库单与单号 ``RC``）；到货登记（``purchase_arrivals``）的入库台账
    按 ADR-0015 写 ``PurchaseOrder``。
    """

    PURCHASE_ORDER = "PurchaseOrder"
    CUTTING_ORDER = "CuttingOrder"
    MATERIAL_ISSUE = "MaterialIssue"
    FINISHED_GOODS_RECEIPT = "FinishedGoodsReceipt"
    SALES_DELIVERY = "SalesDelivery"
    STOCK_TRANSFER = "StockTransfer"
    STOCKTAKING = "Stocktaking"


#: 三个 PG enum 都由迁移 0012 建。
#: ⚠️ ``create_type=False``：类型由迁移手写 ``CREATE TYPE`` 建，不加这个
#: SQLAlchemy 会**自己再发一条**，撞车报 ``type already exists``
#: （0004 / 0007 / 0008 / 0009 各踩过一次）。
#: ⚠️ ``values_callable`` 不给的话 SQLAlchemy 存**成员名**。``StockDocType`` 的
#: 成员名与值**不一致**（``PURCHASE_ORDER`` vs ``"PurchaseOrder"``），所以这三个
#: 都必须显式给 ``values_callable`` —— 否则库里会存下 ``PURCHASE_ORDER``，
#: 而迁移建的枚举里没有那个值，**第一条 INSERT 就报 invalid input value**。
def _pg_enum(python_enum: type[StrEnum], name: str) -> SAEnum:
    return SAEnum(
        python_enum,
        name=name,
        create_type=False,
        values_callable=lambda enum_cls: [member.value for member in enum_cls],
    )


STOCK_TYPE = _pg_enum(StockType, "stock_type")
STOCK_DIRECTION = _pg_enum(StockDirection, "stock_direction")
STOCK_DOC_TYPE = _pg_enum(StockDocType, "stock_doc_type")


class StockLedger(IdMixin, Base):
    """出入库台账（**append-only**，`docs/04 §7.2.5`）。

    ⚠️ **既不用 :class:`BaseModel` 也不用 :class:`AuditMixin`** —— 见模块 docstring
    「为什么台账不带 deleted_at」。口径与 ``document_logs`` / ``wip_ledger_lines``
    完全一致，只留 ``id`` + ``created_at`` + ``created_by`` + ``remark``。
    """

    __tablename__ = "stock_ledgers"

    __table_args__ = (
        CheckConstraint("qty <> 0", name="ck_stock_ledgers_qty"),
        # ⚠️ 唯一键**只有**这一条：`stock_id` 进唯一约束会在第二张裁剪单审核时撞
        #    duplicate key（同一缸布必然被多张单反复消耗），而 `direction` **必须**
        #    在键里 —— 反审核的反向台账与原台账前三个列完全相同，靠它才放得下两条
        UniqueConstraint(
            "source_doc_type",
            "source_doc_id",
            "source_line_no",
            "direction",
            name="uq_stock_ledgers_source",
        ),
        Index("idx_stock_ledgers_stock", "stock_type", "stock_id", text("created_at DESC")),
        Index("idx_stock_ledgers_source", "source_doc_type", "source_doc_id"),
        Index("idx_stock_ledgers_reversal", "reversal_of_id"),
        {"comment": "出入库流水（append-only；04 §7.2.5）"},
    )

    stock_type: Mapped[StockType] = mapped_column(
        STOCK_TYPE, nullable=False, comment="哪一栈（多态，不建 FK）"
    )
    stock_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False, comment="结存行 id"
    )
    warehouse_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("warehouses.id", name="fk_stock_ledgers_warehouse"),
        nullable=False,
        comment="冗余，报表免 join",
    )
    direction: Mapped[StockDirection] = mapped_column(STOCK_DIRECTION, nullable=False)
    qty: Mapped[Decimal] = mapped_column(
        Numeric(14, 3), nullable=False, comment="有符号：IN 正 / OUT 负 / ADJUST 按差额"
    )
    source_doc_type: Mapped[StockDocType] = mapped_column(STOCK_DOC_TYPE, nullable=False)
    source_doc_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False, comment="不建 FK（单据可软删/反审核）"
    )
    source_line_no: Mapped[int] = mapped_column(
        Integer, server_default=text("0"), nullable=False, comment="0 = 单据级动作"
    )
    reversal_of_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("stock_ledgers.id", name="fk_stock_ledgers_reversal_of"),
        nullable=True,
        comment="INV-4 红冲链：反向台账指向原台账行",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    created_by: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False, comment="写入人（append-only：不设 updated_*）"
    )
    remark: Mapped[str | None] = mapped_column(Text(), nullable=True)


class StockLedgerLine(IdMixin, Base):
    """台账批次耗用明细（**append-only**，`docs/04 §7.2.5`）。

    ⚠️ 这是「这件成衣用了哪几缸布、各多少米、各多少钱」的**唯一答案来源**
    （``modules/06 BR-ST-19``）。所以缸号 / 匹号 / 缸色存的是**快照**：
    批次行后来归零软删，这里仍可追溯。
    ⚠️ ``material_id`` 建 **NOT NULL**（字段表的「必填」）。同段那句
    「成衣栈台账此列为 ``NULL``」是为 P3 的 ``finished_goods_stocks`` 预留的，
    届时**必须** ``ALTER COLUMN material_id DROP NOT NULL``（已登记 docs/12 §5）。
    """

    __tablename__ = "stock_ledger_lines"

    __table_args__ = (
        CheckConstraint("qty <> 0", name="ck_stock_ledger_lines_qty"),
        # ⚠️ 金额一致性放**行级**：台账已无 amount 列，「台账 ↔ 明细」由
        #    v_stock_cost_check 兜，而金额是行内静态的，CHECK 恰好够用
        CheckConstraint("ROUND(qty * unit_cost, 4) = amount", name="ck_stock_ledger_lines_amount"),
        # 同一台账内同批次只允许一行，避免重复扣减。⚠️ **不需要**部分索引：
        # append-only 从不软删，deleted_at 恒为 NULL（cutting_order_lines 那批要）
        UniqueConstraint("ledger_id", "dye_lot_no", "bolt_no", name="uq_stock_ledger_lines_batch"),
        Index("idx_stock_ledger_lines_ledger", "ledger_id"),
        Index(
            "idx_stock_ledger_lines_material", "material_id", "dye_lot_no", text("created_at DESC")
        ),
        Index("idx_stock_ledger_lines_reversal", "reversal_of_id"),
        {"comment": "台账批次耗用明细（append-only；04 §7.2.5）"},
    )

    ledger_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("stock_ledgers.id", name="fk_stock_ledger_lines_ledger"),
        nullable=False,
        comment="必建 FK（04 §7.2.4「每条出库都有批次耗用」）",
    )
    material_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("materials.id", name="fk_stock_ledger_lines_material"),
        nullable=False,
        comment="⚠️ P3 建成衣栈时必须放宽为可空",
    )
    stock_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True, comment="被扣减的批次行 id（不建 FK）"
    )
    dye_lot_no: Mapped[str] = mapped_column(
        String(64), server_default=text("'-'"), nullable=False, comment="批次快照：无缸号填 -"
    )
    bolt_no: Mapped[str] = mapped_column(
        String(32), server_default=text("'-'"), nullable=False, comment="批次快照：不分匹填 -"
    )
    color: Mapped[str] = mapped_column(
        String(32), server_default=text("'-'"), nullable=False, comment="⚠️ 缸色 ≠ 款色"
    )
    qty: Mapped[Decimal] = mapped_column(
        Numeric(14, 3), nullable=False, comment="有符号，与父台账同向"
    )
    unit_cost: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, comment="批次成本快照（ADR-0011），永不被改价影响"
    )
    amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, comment="= ROUND(qty × unit_cost, 4)"
    )
    reversal_of_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("stock_ledger_lines.id", name="fk_stock_ledger_lines_reversal_of"),
        nullable=True,
        comment="红冲明细逐条反向指回原明细（modules/06 §7 K6）",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    created_by: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False, comment="写入人（append-only：不设 updated_*）"
    )
    remark: Mapped[str | None] = mapped_column(Text(), nullable=True)


class StockReservation(BaseModel):
    """库存锁定（``docs/04 §7.2.5``，``modules/06 §3.5``）。

    ⚠️ **三张表里唯一的软删表**：``material_stocks.locked_qty`` 是本表的**冗余聚合
    缓存**，两者的同步由 service 在锁定 / 释放时用条件 UPDATE 保证。
    ⚠️ **释放用置 ``released_at`` 不用删行**（INV-7）：行本身还要留着回答
    「这批布曾经被哪张单占过、什么时候放的」。
    ⚠️ ``locked_qty`` 与本表的一致性**目前没有任何视图断言** —— 本仓还没有一处
    会写这两张表（裁剪 ``submit`` 锁批属 T-CUT-001b-3），所以那个缺口还没有实际
    危害；已登记 ``docs/12`` §5 L-086，随锁批那张卡一起补。
    """

    __tablename__ = "stock_reservations"

    __table_args__ = (
        CheckConstraint("version > 0", name="ck_stock_reservations_version_positive"),
        CheckConstraint("qty > 0", name="ck_stock_reservations_qty"),
        UniqueConstraint(
            "stock_type", "stock_id", "doc_type", "doc_id", name="uq_stock_reservations_doc"
        ),
        Index(
            "idx_stock_reservations_doc",
            "doc_type",
            "doc_id",
            postgresql_where=text("deleted_at IS NULL AND released_at IS NULL"),
        ),
        {"comment": "库存锁定（释放用 released_at，INV-7；04 §7.2.5）"},
    )

    stock_type: Mapped[StockType] = mapped_column(
        STOCK_TYPE, nullable=False, comment="哪一栈（多态，不建 FK）"
    )
    stock_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False, comment="被占用的结存行 id"
    )
    doc_type: Mapped[StockDocType] = mapped_column(
        STOCK_DOC_TYPE, nullable=False, comment="占用方单据类型"
    )
    doc_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False, comment="占用方单据 id"
    )
    qty: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False, comment="占用量（米）")
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="NULL = 仍锁定"
    )
