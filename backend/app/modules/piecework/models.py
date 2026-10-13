"""计件流水与员工工序绑定（T-PIECE-001 / 迁移 0019）。

DDL 权威来源：``docs/04 §7.1``（``piecework_logs``）、``docs/04 §7.1.1``
（``piecework_bindings``）、``docs/04 §2``（公共字段）。逐列照抄，约束与索引写成 ORM。

## 两条表的语义（动手前必读，否则会用错字段）

1. **``qty`` 是「本次实际完成的件数」，不是「这一手有多少件」**（ADR-0016）。
   一码一手、一码一件数；60 件的手可以只记 28（部分生产），但
   **不许记 28.5**（``ck_piecework_logs_whole``，尾数不计件不入库，09 §4.2）。
   "这一手总共多少件"在 ``bundles.bundle_qty``，不在这里。
2. **结算标记是 ``payroll_id IS NULL``，不是 ``is_settled`` 布尔列**（04 §7.1 要点）：
   两个来源必然漂移，而结算回写是 service 行为、不允许手工填。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811 —— 别名照抄 docs/04 §2
from sqlalchemy.orm import Mapped, mapped_column

from app.common.models import BaseModel


class PieceworkLogType(StrEnum):
    """计件流水的四种来源（``docs/04 §7.1`` 的 ``CREATE TYPE piecework_log_type``）。

    ⚠️ ``REVERSAL`` 是**唯一取负值**的类型，其余三类都为正 ——
    ``ck_piecework_logs_sign`` 把它钉死。纠错**永远走红冲 + 重入**（INV-2），
    不允许改原行。
    """

    SCAN = "SCAN"
    MANUAL = "MANUAL"
    REVERSAL = "REVERSAL"
    SUPPLEMENT = "SUPPLEMENT"


#: PG enum ``piecework_log_type``，由迁移 0019 建。
#: ⚠️ ``create_type=False``：类型由迁移手写 ``CREATE TYPE`` 建，不加这个
#: SQLAlchemy 会**自己再发一条**，撞车报 ``type already exists``（0004/0007/0008/
#: 0009/0012/0014 各踩过一次）。
PIECEWORK_LOG_TYPE = SAEnum(PieceworkLogType, name="piecework_log_type", create_type=False)


class PieceworkLog(BaseModel):
    """计件流水（``docs/04 §7.1``；append-only，纠错靠红冲 + 重入）。

    ⚠️ **禁止 ``UPDATE qty / amount / bundle_id / bundle_no``**：只有
    ``remark / updated_*`` 可改（04 §7.1 要点）。用「软删 + 乐观锁」表达
    「可撤销」是自相矛盾的 —— 行一旦写入就不该变。

    ⚠️ ``employee_id`` / ``workshop_id`` / ``group_no`` **刻意不建外键**：
    人事离职、车间停用重组都是合法的档案维护动作，建 FK 会阻止它们。
    数据范围过滤走 ``app/core/scope.py`` 的列名约定（``employee_id`` 是 SELF 列）。

    ⚠️ ``scanned_at`` **NOT NULL 且无默认值**：客户端必须给扫码时刻，
    ``work_date`` 由它按 ``Asia/Shanghai`` 推导（BR-PW-11）。给 ``DEFAULT now()``
    会让「历史补录」与「实时扫码」落成同一条口径，之后再也分不开。

    ⚠️ ``payroll_id`` **只建列不建外键**（``piecework_settlements`` 是工资阶段的表，
    本卡还不存在）；``bundle_id`` **建外键**（BR-PW-30：`bundle_no` 是可变字符串
    格式，FK 不会被格式变更打断，且可直接 JOIN 取码的行数口径）。
    """

    __tablename__ = "piecework_logs"

    __table_args__ = (
        # ⚠️ 幂等键是**数据库兜底**（04 §7.1 要点）。它**不含**「码是否已被他人
        #   计过」—— 一码一员工（BR-PW-29）靠 ``bundles.counted_at`` + ``FOR UPDATE``
        #   在 service 层保证，不靠这条唯一键（靠它的话换个员工重扫就成了「另一条
        #   合法流水」而不是 32001）。
        UniqueConstraint(
            "bundle_no",
            "operation_no",
            "employee_id",
            "work_date",
            "log_type",
            name="uq_piecework_logs_idem",
        ),
        CheckConstraint("version > 0", name="ck_piecework_logs_version_positive"),
        # 符号规则：只有红冲为负。⚠️ 旧的 `qty > 0` CHECK 会拒掉红冲行，不得再用
        CheckConstraint(
            "((log_type = 'REVERSAL' AND qty < 0 AND amount < 0)"
            " OR (log_type <> 'REVERSAL' AND qty > 0 AND amount > 0))",
            name="ck_piecework_logs_sign",
        ),
        # 整件口径（09 §4.2）：部分生产只允许整数件，60 的手可以记 28、不能记 28.5
        CheckConstraint("qty = trunc(qty)", name="ck_piecework_logs_whole"),
        # §5 的「工厂典型查询」逐字照抄
        Index(
            "idx_piecework_logs_work_date_workshop_id_style_no",
            # ⚠️ DESC 必须与列名写在**同一个** text 片段里（抄 0014 的 idx_bundling_orders）：
            #   分成两个元素会渲染成 `(work_date, DESC, ...)` —— 语法错误
            text("work_date DESC"),
            "workshop_id",
            "style_no",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # 追溯主路径（BR-PW-30）
        Index(
            "idx_piecework_logs_bundle_id",
            "bundle_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_piecework_logs_employee_work_date",
            "employee_id",
            "work_date",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_piecework_logs_bundle_no",
            "bundle_no",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_piecework_logs_reversal_of_id",
            "reversal_of_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # 未结算流水清单：payroll_id IS NULL 是权威判据（04 §7.1 要点）
        Index(
            "idx_piecework_logs_unsettled",
            "work_date",
            postgresql_where=text("deleted_at IS NULL AND payroll_id IS NULL"),
        ),
        Index(
            "idx_piecework_logs_station",
            "source_station",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        {"comment": "计件流水（04 §7.1；append-only，纠错靠红冲 + 重入）"},
    )

    bundle_no: Mapped[str] = mapped_column(String(64), nullable=False)
    style_no: Mapped[str] = mapped_column(String(32), nullable=False)
    operation_no: Mapped[str] = mapped_column(String(16), nullable=False)
    employee_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    workshop_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    group_no: Mapped[str | None] = mapped_column(String(32), nullable=True)
    work_date: Mapped[date] = mapped_column(Date, nullable=False)
    bundle_id: Mapped[UUID | None] = mapped_column(
        # ⚠️ ON DELETE RESTRICT（BR-PW-30）：码的合法终结方式是作废（VOIDED），
        #   不是物理删 —— 物理删一条已计件的码等于抹掉一条工资依据
        ForeignKey("bundles.id", name="fk_piecework_logs_bundle", ondelete="RESTRICT"),
        nullable=True,
    )
    # ⚠️ DEFAULT 1 是**兜底不是便利**：服务层漏写 bundles.bundle_qty 就会落成 1 件，
    #   正好退回「扫码 +1 件」的旧口径。服务层必须显式赋值（04-计件 TC-39 钉死）
    qty: Mapped[Decimal] = mapped_column(
        Numeric(14, 3),
        nullable=False,
        default=Decimal(1),
        # ⚠️ `server_default` 必须与迁移一致：`alembic/env.py` 开了
        #   `compare_server_default=True`，只写 Python 侧 `default` 会被判成漂移
        server_default=text("1"),
    )
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    log_type: Mapped[PieceworkLogType] = mapped_column(
        PIECEWORK_LOG_TYPE,
        nullable=False,
        default=PieceworkLogType.SCAN,
        server_default=text("'SCAN'"),  # 同上：server_default 必须与迁移一致
    )
    # ⚠️ 刻意无 ForeignKey：piecework_settlements 是工资阶段的表，由那张卡补
    payroll_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    reversal_of_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("piecework_logs.id", name="fk_piecework_logs_reversal_of", ondelete="RESTRICT"),
        nullable=True,
    )
    source_station: Mapped[str | None] = mapped_column(String(32), nullable=True)
    scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    device_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)


class PieceworkBinding(BaseModel):
    """员工工序绑定（``docs/04 §7.1.1``，搬运自 ``04-计件 §3.2``）。

    ⚠️ **绑定当天有效，次日需重扫**（BR-PW-04）：所以唯一键里带 ``work_date``，
    而不是简单地「一员工一工序只有一条」。

    ⚠️ 命中唯一键**不是报错**，而是幂等返回既有绑定（04 §7.1.1 要点）——
    员工连扫两下同一个码是日常操作，不该弹红。

    ⚠️ ``bundle_no`` **不建外键**：码会被作废、打菲单会被反审核（``03 §4.1``），
    建 FK 会阻止这些合法动作。绑定只需要「当时扫的是哪张码」这一条追溯信息。

    ⚠️ ``style_no`` **由前端扫的 ``bundle_no`` 反查带出**，不许自由填写（防伪造）。
    """

    __tablename__ = "piecework_bindings"

    __table_args__ = (
        UniqueConstraint(
            "user_id", "style_no", "operation_no", "work_date", name="uq_piecework_bindings"
        ),
        CheckConstraint("version > 0", name="ck_piecework_bindings_version_positive"),
        Index(
            "idx_piecework_bindings_user_date",
            "user_id",
            "work_date",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_piecework_bindings_bundle_no",
            "bundle_no",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        {"comment": "员工工序绑定（04 §7.1.1；绑定当天有效，次日需重扫）"},
    )

    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    style_no: Mapped[str] = mapped_column(String(32), nullable=False)
    operation_no: Mapped[str] = mapped_column(String(16), nullable=False)
    bundle_no: Mapped[str] = mapped_column(String(64), nullable=False)
    workshop_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    work_date: Mapped[date] = mapped_column(Date, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),  # 同上：server_default 必须与迁移一致
    )
