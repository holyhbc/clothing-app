from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811 —— 别名照抄 docs/04 §2
from sqlalchemy.orm import Mapped, mapped_column

from app.common.models import BaseModel


class OperationRate(BaseModel):
    """工序单价历史（**ADR-0026** 三档，取代 04 §7.8.3 的单档版本）。

    三档（``rate_source`` 的来源）：

    =============  =======================================  ==================
    档位          匹配条件                                 rate_source
    =============  =======================================  ==================
    1（最高）     ``style_no`` 有值 + 分类空               ``STYLE``
    2            款号空 + ``product_category_id`` 有值    ``CATEGORY``
    3（最低）     两者都空                                 ``OPERATION``
    =============  =======================================  ==================

    ⚠️ **只追加，永不 UPDATE ``unit_price``**（R11 / INV-3）：调价 = 旧行只改
    ``effective_to`` + 插入新行。已落库的 ``piecework_logs.unit_price`` 是快照，
    任何调价都不得回溯。

    ⚠️ ``ck_operation_rates_target`` 是 **"两者不能同时有值"**
    （``NOT (都有值)``），不是 ADR-0026 §1 原文写的"至少指定其一" —— 后者会把
    档位 3 整个禁掉，见 docs/12 §5 L-030。
    """

    __tablename__ = "operation_rates"

    operation_no: Mapped[str] = mapped_column(
        String(16),
        ForeignKey("operations.operation_no", name="fk_operation_rates_operations"),
        nullable=False,
        comment="工序号",
    )
    style_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("styles.id", name="fk_operation_rates_styles"),
        nullable=True,
        comment="★可空：款号主键，外键指向 styles.id（见文件头注 4）",
    )
    style_no: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        comment="★可空：空 = 该档不限定款号（ADR-0026）。业务键，冗余便于取价与审计",
    )
    product_category_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("product_categories.id", name="fk_operation_rates_product_categories"),
        nullable=True,
        comment="★可空：空 = 不限分类（ADR-0026）",
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False, comment="生效日（含）")
    effective_to: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="失效日（不含）；NULL = 当前有效。调价只改这一列，绝不 UPDATE unit_price（R11）",
    )
    unit_price: Mapped[Decimal] = mapped_column(
        Numeric(12, 6),
        nullable=False,
        comment="单价；0 允许（免费工序），但调价仍需 reason（R20）",
    )
    reason: Mapped[str | None] = mapped_column(
        Text(), nullable=True, comment="调价原因（R20 调价必填）"
    )

    __table_args__ = (
        CheckConstraint("version > 0", name="ck_operation_rates_version_positive"),
        CheckConstraint(
            "NOT (style_no IS NOT NULL AND product_category_id IS NOT NULL)",
            name="ck_operation_rates_target",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_operation_rates_range",
        ),
        CheckConstraint("unit_price >= 0", name="ck_operation_rates_price"),
        # ★★ NULLS NOT DISTINCT 是 ADR-0026 的关键技术点（PG 15+，本项目 PG 16）。
        #    没有它，"款号通用 + 分类 NULL + 同工序 + 同生效日"能插任意多行，
        #    取价变成不确定 —— 而计件金额错就是工资错。
        Index(
            "uq_operation_rates",
            "style_no",
            "product_category_id",
            "operation_no",
            "effective_from",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
        Index(
            "idx_operation_rates_lookup",
            "operation_no",
            "style_no",
            "product_category_id",
            text("effective_from DESC"),
        ),
        Index(
            "idx_operation_rates_operation_current",
            "operation_no",
            "effective_from",
            postgresql_where=text("effective_to IS NULL"),
        ),
        {"comment": "工序单价历史（ADR-0026 三档；只追加，调价只关区间）"},
    )
