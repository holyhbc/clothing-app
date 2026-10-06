"""打菲件表 ``bundles`` 与标签打印留痕 ``bundle_label_prints``。

DDL 权威来源：``docs/04 §7.0``（``bundles``，ADR-0016）与 ``docs/04 §7.16``
（``bundle_label_prints``，2026-10-06 变更 0111 补齐）。

## 两张表的口径完全相反

1. **``bundles`` 是软删 + 乐观锁表**（:class:`BaseModel`）：它要作废（``VOIDED``，
   业务终态不是软删）、要反审核、要留痕「谁在什么时候作废了哪一手」。
2. **``bundle_label_prints`` 是 append-only 表**（只有 ``id`` + ``created_at`` +
   ``created_by``）：一次打印 = 一条不可变事实，重打是**新增一行**，
   由 ``is_reprint`` / ``print_seq`` 表达。给留痕配「软删 + 乐观锁」是自相矛盾的 ——
   行一旦写入就不该变，纠错靠新增一行（先例 ``document_logs`` / ``stock_ledgers``）。

## ``bundles`` 为什么照抄 §7.0 而不改写

``modules/03 §3.3`` 明写「照抄 04 §7.0，本模块不得改写」：这张表是打菲 / 计件 /
标签三方共用的物理契约，任何一侧单方面改列都会让另外两侧静默错位。本文件与
§7.0 **逐列一致**，差异只体现在 ORM 声明方式上（约束名 / 索引名一致）。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING
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
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811 —— 别名照抄 docs/04 §2
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.models import Base, BaseModel, IdMixin

if TYPE_CHECKING:
    from app.modules.bundling.models.order import BundlingOrderLine


class BundleStatus(StrEnum):
    """打菲码状态（``docs/04 §7.0`` 的 ``CREATE TYPE bundle_status``）。

    ``VOIDED`` 是**业务终态，不是软删**：作废的码仍要能被扫码枪查到并回报
    「该打菲号已作废（31002）」，若软删掉就只剩「查无此码（31001）」，
    用户无法区分「码打错了」与「这手被作废了」。
    """

    ACTIVE = "ACTIVE"
    VOIDED = "VOIDED"


#: PG enum ``bundle_status``，由迁移 0014 建。
#: ⚠️ ``create_type=False``：类型由迁移手写 ``CREATE TYPE`` 建，不加这个
#: SQLAlchemy 会**自己再发一条**，撞车报 ``type already exists``
#: （0004 / 0007 / 0008 / 0009 / 0012 各踩过一次）。
BUNDLE_STATUS = SAEnum(BundleStatus, name="bundle_status", create_type=False)


class Bundle(BaseModel):
    """打菲件表（**一码一手**，``docs/04 §7.0`` / ADR-0016）。

    ⚠️ ``hands`` 是**手序号**（同单 + 同色 + 同尺码内从 1 起），不是手数合计。
    ``uq_bundles_hand (doc_id, color_code, size_code, hands)`` 保证不会出现
    两个「第 2 手」（ADR-0016 §1）。

    ⚠️ ``counted_qty`` / ``counted_by`` / ``counted_at`` 由**计件模块**回写，
    本模块只提供列。判定「未计件」看 ``counted_at IS NULL``（派生状态），
    不看 ``counted_qty`` —— 部分生产时 ``counted_qty < bundle_qty`` 仍算已计件。

    ⚠️ ``cutting_size_line_id`` **刻意不建外键**（§7.0 原文如此）：码要能在来源
    裁剪行被软删 / 调整后继续追溯，与 ``bundle_label_prints.bundle_no`` 同款
    「逻辑引用」。建了外键会阻止裁剪侧合法的软删操作。
    """

    __tablename__ = "bundles"

    __table_args__ = (
        UniqueConstraint("bundle_no", name="uq_bundles_no"),
        # ⚠️ 唯一键含 ``color_code``：ADR-0016 一码一色，同单多色需开多张单
        UniqueConstraint("doc_id", "color_code", "size_code", "hands", name="uq_bundles_hand"),
        CheckConstraint("version > 0", name="ck_bundles_version_positive"),
        # 整件口径（09 §4.2）：该手件数必须是正整数，尾数不入库
        CheckConstraint("bundle_qty > 0 AND bundle_qty = trunc(bundle_qty)", name="ck_bundles_qty"),
        CheckConstraint("counted_qty >= 0 AND counted_qty <= bundle_qty", name="ck_bundles_cnt"),
        CheckConstraint("counted_qty = 0 OR counted_by IS NOT NULL", name="ck_bundles_one"),
        # ⚠️ §7.0 明列的**部分索引**：未计件手清单（ADR-0016 验证方式）
        Index(
            "idx_bundles_counted_pending",
            "doc_id",
            "color_code",
            "size_code",
            postgresql_where=text(
                "deleted_at IS NULL AND status = 'ACTIVE' AND counted_at IS NULL"
            ),
        ),
        Index(
            "idx_bundles_line_id",
            "line_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # 追溯链：按裁剪尺码明细行回查码（modules/03 §8）
        Index(
            "idx_bundles_cutting",
            "cutting_size_line_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        {"comment": "打菲件表（一码一手；04 §7.0 / ADR-0016）"},
    )

    doc_id: Mapped[UUID] = mapped_column(
        ForeignKey("bundling_orders.id", name="fk_bundles_doc", ondelete="RESTRICT"),
        nullable=False,
    )
    line_id: Mapped[UUID] = mapped_column(
        ForeignKey("bundling_order_lines.id", name="fk_bundles_line", ondelete="RESTRICT"),
        nullable=False,
    )
    bundle_no: Mapped[str] = mapped_column(String(64), nullable=False)
    hands: Mapped[int] = mapped_column(Integer, nullable=False)
    style_no: Mapped[str] = mapped_column(String(32), nullable=False)
    color_code: Mapped[str] = mapped_column(String(32), nullable=False)
    size_code: Mapped[str] = mapped_column(String(32), nullable=False)
    operation_no: Mapped[str] = mapped_column(String(16), nullable=False)
    cutting_size_line_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        nullable=False,
    )
    bundle_qty: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    counted_qty: Mapped[Decimal] = mapped_column(
        Numeric(14, 3),
        nullable=False,
        default=Decimal("0"),
        server_default=text("0"),
    )
    counted_by: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    counted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[BundleStatus] = mapped_column(
        BUNDLE_STATUS,
        nullable=False,
        default=BundleStatus.ACTIVE,
        server_default=text("'ACTIVE'"),
    )
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    void_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)
    qr_content: Mapped[str] = mapped_column(String(64), nullable=False)

    #: 所属打菲明细行。⚠️ 写入路径不用它（service 显式写），
    #: 配 ``raise_on_sql`` 是为了「拼错时立刻报错」而不是静默返回 None。
    line: Mapped[BundlingOrderLine] = relationship(
        "BundlingOrderLine", back_populates="bundles", lazy="raise_on_sql", viewonly=True
    )


class BundleLabelPrint(IdMixin, Base):
    """标签打印记录（**append-only**，``docs/04 §7.16``）。

    ⚠️ **既不用 :class:`BaseModel` 也不用 :class:`AuditMixin`** —— 只有
    ``id`` + ``created_at`` + ``created_by``，**没有** ``version`` / ``deleted_at`` /
    ``updated_*``，也**不设 ``remark``**（Q-B14 已闭环，变更 0111）。
    本表每行都由 ``printed_qty`` / ``is_reprint`` / ``print_seq`` 自我说明，
    与 ``stock_ledgers`` 那类需要人工备注的流水不同。

    ⚠️ ``bundle_no`` **不建外键**指向 ``bundles``：打印留痕必须能在码被作废 /
    软删后继续追溯（§7.16 原文如此）。
    """

    __tablename__ = "bundle_label_prints"

    __table_args__ = (
        Index(
            "idx_bundle_label_prints_bundle_no",
            "bundle_no",
            text("printed_at DESC"),
        ),
        {"comment": "标签打印留痕（append-only；04 §7.16）"},
    )

    bundle_no: Mapped[str] = mapped_column(String(64), nullable=False)
    doc_id: Mapped[UUID] = mapped_column(
        ForeignKey("bundling_orders.id", name="fk_bundle_label_prints_doc", ondelete="RESTRICT"),
        nullable=False,
    )
    hands_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    hands_total_of_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    printed_qty: Mapped[int] = mapped_column(Integer, nullable=False)
    is_reprint: Mapped[bool] = mapped_column(Boolean, nullable=False)
    print_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    printed_by: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    printed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # ⚠️ append-only 的公共字段：只有 created_at / created_by（§7.16）
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    created_by: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
