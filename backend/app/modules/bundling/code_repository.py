"""打菲**码**（``bundles``）的查询与作废写入（T-BUND-007b）。

与 :mod:`.repository`（单据只读）、:mod:`.label_repository`（标签）、:mod:`.approve_repository`
（审核写路径）分开放，理由同为 ADR-0031 的「按主题分文件」。本文件**不判断业务**：
码不存在该报 ``31001``、已作废该报 ``31002``、已计件该报 ``32003`` —— 全部由
:mod:`.service.code` 决定。

## 数据范围为什么不在本文件里

``bundles`` **没有** ``workshop_id``（``03 §3.3``：可从 ``bundling_orders`` 关联取），
所以列表与统计都走 :func:`~app.core.scope.apply_data_scope` 的 ``via`` 机制 ——
join 与 ``where`` 都由 scope 层加，本文件**不自己拼** ``workshop_id in (...)``。
详情 / 作废两条走「先取行、再对父单 ``assert_in_scope``」（越权报 ``12002``、
不存在报 ``31001``，两个码不能混）。``via.model`` 与本文件 import 的父单模型必须一致，
由 ``test_scope.py`` 的守卫钉住。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Final, NamedTuple, cast
from uuid import UUID

from sqlalchemy import Select, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from app.core.errors import BusinessError, ErrorCode
from app.core.permissions import AuthContext
from app.core.scope import apply_data_scope
from app.modules.auth.models import User
from app.modules.bundling.label_repository import PrintRecordRow
from app.modules.bundling.models import Bundle, BundleStatus, BundlingOrder
from app.modules.cutting.models import CuttingOrderLine, CuttingOrderSizeLine

#: 深分页上限（docs/04 §5.1）
MAX_OFFSET: Final[int] = 10000
#: 单页最大行数（与单据列表同口径，05 §2）
MAX_PAGE_SIZE: Final[int] = 200

#: SQLAlchemy 行的类型别名（与 :mod:`.repository` 同款：行在 service 里被转成响应模型）。
type Row = Any

#: 计件人与裁剪来源都要 **outerjoin**（未计件的码没有计件人；裁剪行被软删的码没有缸号），
#: 而同一张表两次 join 必须起别名，否则 SQLAlchemy 报表名歧义。
_counted_by = aliased(User)
_source_line = aliased(CuttingOrderLine)


class BundleRow(NamedTuple):
    """一行码（列表 / 详情共用同一份投影，避免两条路径口径分叉）。"""

    id: UUID
    doc_id: UUID
    line_id: UUID
    bundle_no: str
    hands: int
    style_no: str
    color_code: str
    size_code: str
    operation_no: str
    cutting_size_line_id: UUID
    bundle_qty: Decimal
    counted_qty: Decimal
    counted_at: datetime | None
    status: str
    qr_content: str
    #: 作废痕迹（扫码枪要显示「为什么作废」，05 §4 的 31002 引导）
    voided_at: datetime | None
    void_reason: str | None
    counted_by_name: str | None
    dye_lot_no: str | None
    bolt_no: str | None
    #: 以下两列由 **service** 补（``_replace``），不是 SQL 查出来的：都要按
    #: ``(单, 色, 尺码)`` 批量算，一行一行查就是 N+1 次往返。
    #: ⚠️ 带默认值是为了让「取行」与「补齐」可以分开 —— 列表不查打印留痕，详情才查。
    hands_total_of_size: int = 0
    print_records: tuple[PrintRecordRow, ...] = ()


class VoidTargetRow(NamedTuple):
    """被 ``FOR UPDATE`` 锁住、正准备作废的那个码（只要判定与乐观锁要用的列）。"""

    id: UUID
    doc_id: UUID
    version: int
    status: str
    counted_at: datetime | None


class StatRow(NamedTuple):
    """一行统计（款号 × 尺码，**只数 ACTIVE 码**，理由见 ``schemas/stat_schemas``）。"""

    style_no: str
    size_code: str
    hands: int
    qty: Decimal
    counted_hands: int


@dataclass(frozen=True, slots=True)
class BundleListQuery:
    """码列表 / 导出**共用**的筛选条件（modules/03 §6）。

    ⚠️ ``bundle_no`` 是**前缀**搜索（03 §6）：扫码枪输错一位时要给候选，而不是「查无此码」。
    """

    bundle_no: str | None = None
    style_no: str | None = None
    color_code: str | None = None
    size_code: str | None = None
    operation_no: str | None = None
    status: str | None = None
    counted: bool | None = None
    hands: int | None = None
    doc_id: UUID | None = None
    page: int = 1
    size: int = 20

    def validate(self) -> None:
        """校验分页。**必须在拼 SQL 之前调用**（docs/04 §5.1）。"""
        if self.size < 1 or self.size > MAX_PAGE_SIZE:
            raise BusinessError(
                ErrorCode.PARAM_INVALID,
                f"page_size 必须在 1~{MAX_PAGE_SIZE} 之间，收到 {self.size}",
            )
        if self.page < 1:
            raise BusinessError(ErrorCode.PARAM_INVALID, "page 从 1 起")
        offset = (self.page - 1) * self.size
        if offset > MAX_OFFSET:
            raise BusinessError(
                ErrorCode.PARAM_INVALID,
                f"翻页太深（offset={offset} > {MAX_OFFSET}），请改用码前缀或款号缩小范围",
            )

    def filters(self) -> tuple[ColumnElement[bool], ...]:
        """筛选条件。**列表与计数共用**（07 §3.2 铁律 3 的同源要求）。"""
        conditions: list[ColumnElement[bool]] = []
        if self.bundle_no:
            conditions.append(Bundle.bundle_no.startswith(self.bundle_no))
        if self.style_no:
            conditions.append(Bundle.style_no == self.style_no)
        if self.color_code:
            conditions.append(Bundle.color_code == self.color_code)
        if self.size_code:
            conditions.append(Bundle.size_code == self.size_code)
        if self.operation_no:
            conditions.append(Bundle.operation_no == self.operation_no)
        if self.status:
            conditions.append(Bundle.status == BundleStatus(self.status))
        if self.counted is not None:
            # ⚠️ 判「未计件」看 ``counted_at IS NULL``，不看 ``counted_qty``
            # （部分生产时 counted_qty < bundle_qty 仍算已计件，03 §3.3）
            conditions.append(
                Bundle.counted_at.is_not(None) if self.counted else Bundle.counted_at.is_(None)
            )
        if self.hands is not None:
            conditions.append(Bundle.hands == self.hands)
        if self.doc_id is not None:
            conditions.append(Bundle.doc_id == self.doc_id)
        return tuple(conditions)


@dataclass(frozen=True, slots=True)
class StatQuery:
    """统计筛选条件（modules/03 §6：``date_from`` / ``date_to`` / ``style_no``）。

    ⚠️ **日期落在单据上**（``bundling_orders.doc_date``）：码本身没有日期，而「本月打了多少
    手」问的是**单据**的月份 —— 按入库时间算，审核分批会让同一个月裂成两段。
    """

    date_from: date | None = None
    date_to: date | None = None
    style_no: str | None = None

    def filters(self) -> tuple[ColumnElement[bool], ...]:
        conditions: list[ColumnElement[bool]] = [
            Bundle.deleted_at.is_(None),
            # ⚠️ 只数 ACTIVE 码：「已计 / 未计」是二分，VOIDED 两边都不属于
            Bundle.status == BundleStatus.ACTIVE,
        ]
        if self.style_no:
            conditions.append(Bundle.style_no == self.style_no)
        return tuple(conditions)

    def order_filters(self) -> tuple[ColumnElement[bool], ...]:
        """落在**父单**上的条件（单独一个方法：那些不是 ``bundles`` 的列）。"""
        conditions: list[ColumnElement[bool]] = []
        if self.date_from is not None:
            conditions.append(BundlingOrder.doc_date >= self.date_from)
        if self.date_to is not None:
            conditions.append(BundlingOrder.doc_date <= self.date_to)
        return tuple(conditions)


#: 列表 / 详情共用的投影表达式（**显式列**而不是 ``select(Bundle)``：05 §3「避免 SELECT
#: *」，而 ``bundles`` 是年增数十万行的大表，多带一列就是白付 IO）。
_ROW_COLUMNS: Final[tuple[Any, ...]] = (
    Bundle.id,
    Bundle.doc_id,
    Bundle.line_id,
    Bundle.bundle_no,
    Bundle.hands,
    Bundle.style_no,
    Bundle.color_code,
    Bundle.size_code,
    Bundle.operation_no,
    Bundle.cutting_size_line_id,
    Bundle.bundle_qty,
    Bundle.counted_qty,
    Bundle.counted_at,
    Bundle.status,
    Bundle.qr_content,
    Bundle.voided_at,
    Bundle.void_reason,
    _counted_by.name,
    _source_line.dye_lot_no,
    _source_line.bolt_no,
)


def _detail_stmt() -> Select[Any]:
    """详情 / 作废前的取行（**不带数据范围**，见模块 docstring）。"""
    stmt = select(*_ROW_COLUMNS).where(Bundle.deleted_at.is_(None))
    return (
        stmt.outerjoin(_counted_by, Bundle.counted_by == _counted_by.id)
        .outerjoin(CuttingOrderSizeLine, Bundle.cutting_size_line_id == CuttingOrderSizeLine.id)
        .outerjoin(_source_line, CuttingOrderSizeLine.line_id == _source_line.id)
    )


def _scoped_stmt(ctx: AuthContext) -> Select[Any]:
    """列表 / 统计的取行：数据范围由 ``apply_data_scope`` 注入（经父单回查车间）。

    ⚠️ ``apply_data_scope`` 必须在 ``group_by`` / ``order_by`` **之前**调：它内部会
    ``join(BundlingOrder)``，顺序反了会渲染出 ``GROUP BY ... JOIN``。
    """
    stmt = apply_data_scope(select(*_ROW_COLUMNS), Bundle, ctx)
    return (
        stmt.outerjoin(_counted_by, Bundle.counted_by == _counted_by.id)
        .outerjoin(CuttingOrderSizeLine, Bundle.cutting_size_line_id == CuttingOrderSizeLine.id)
        .outerjoin(_source_line, CuttingOrderSizeLine.line_id == _source_line.id)
    )


async def list_bundles(
    session: AsyncSession, ctx: AuthContext, q: BundleListQuery
) -> list[BundleRow]:
    """码列表。

    ⚠️ **固定按 ``bundle_no`` 升序**：唯一索引 ⇒ 顺序稳定 ⇒ 分页不漏行重行。
    """
    stmt = _scoped_stmt(ctx).where(*q.filters()).order_by(Bundle.bundle_no)
    stmt = stmt.offset((q.page - 1) * q.size).limit(q.size)
    return [BundleRow(*row) for row in (await session.execute(stmt)).all()]


async def count_bundles(session: AsyncSession, ctx: AuthContext, q: BundleListQuery) -> int:
    """总数。与列表**共用** ``_scoped_stmt`` + ``filters``，所以总数与行数不会不一致。"""
    stmt = _scoped_stmt(ctx).where(*q.filters())
    return int(
        (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    )


async def get_bundle_by_no(session: AsyncSession, bundle_no: str) -> BundleRow | None:
    """按码取一行（查无此码返回 ``None`` → service 报 ``31001``）。"""
    stmt = _detail_stmt().where(Bundle.bundle_no == bundle_no)
    rows = [BundleRow(*row) for row in (await session.execute(stmt)).all()]
    return rows[0] if rows else None


async def lock_bundle_for_void(session: AsyncSession, bundle_no: str) -> VoidTargetRow | None:
    """``FOR UPDATE`` 锁住这个码（03 §7：作废前先锁码，与计件侧串行）。

    ⚠️ **先锁再判 ``counted_at``**：否则会出现「判完没计件、紧接着另一个事务扫码写上
    ``counted_at``、本事务把码作废」。
    """
    stmt = (
        select(Bundle.id, Bundle.doc_id, Bundle.version, Bundle.status, Bundle.counted_at)
        .where(Bundle.bundle_no == bundle_no, Bundle.deleted_at.is_(None))
        .with_for_update()
        # ⚠️ ``populate_existing`` **不可省**：同一个 session 里这条码可能已经被读过
        # （列表 / 详情），而 SQLAlchemy 默认**不覆盖**已加载对象的属性 —— 那会让
        # 「锁住之后判 counted_at」拿到旧值，把已计件的码放过去作废。
        .execution_options(populate_existing=True)
    )
    row = (await session.execute(stmt)).one_or_none()
    return None if row is None else VoidTargetRow(*row)


async def mark_bundle_voided(
    session: AsyncSession,
    *,
    bundle_id: UUID,
    expected_version: int,
    reason: str,
    operator_id: UUID,
) -> bool:
    """条件 UPDATE：这个码置 ``VOIDED``（**行保留**，B12）。

    ⚠️ 条件是 ``version + status='ACTIVE'``（08 §1.2 R3）：并发两次作废只有一个生效，
    另一个拿到 ``False`` 并被 service 翻译成「已作废」。
    ⚠️ **不删行、不软删**：作废的码仍要被扫码枪查到并回报「已作废（31002）」。
    """
    stmt = (
        update(Bundle)
        .where(
            Bundle.id == bundle_id,
            Bundle.version == expected_version,
            Bundle.status == BundleStatus.ACTIVE,
        )
        .values(
            status=BundleStatus.VOIDED,
            voided_at=func.now(),
            void_reason=reason,
            version=Bundle.version + 1,
            updated_by=operator_id,
        )
        .returning(Bundle.id)
        .execution_options(synchronize_session=False)
    )
    return len((await session.execute(stmt)).all()) > 0


async def aggregate_bundle_stats(
    session: AsyncSession, ctx: AuthContext, q: StatQuery
) -> list[StatRow]:
    """按款号 × 尺码聚合**手数 / 件数 / 已计手数**（modules/03 §6）。

    ⚠️ **未计件手数不在 SQL 里算**：它是 ``手数 - 已计手数``，两列都已在手里，再写一个
    ``count(*) FILTER (...)`` 只是把同一条等式写两遍 —— 两遍迟早对不上。
    """
    # ⚠️ 先把**行集**（款号 / 尺码 / 件数 / 计件时间）过一遍数据范围，再在外层聚合：
    #    ① 范围过滤与聚合各管各的，聚合表达式里不会混进车间条件；
    #    ② 避免 ``GROUP BY`` 早于 ``apply_data_scope`` 内部的 join（那样渲染出的 SQL 是
    #       ``... GROUP BY ... JOIN ...``，PG 直接报错）。
    # ⚠️ 那句 ``cast`` 不是装饰：``Select`` 的类型参数是 TypeVarTuple，mypy 不接受
    #    ``Select[str, str, ...]`` 传给 ``Select[Any]``，而 ``apply_data_scope`` 内部
    #    本来就按 ``Select[Any]`` 用。
    rows = apply_data_scope(
        cast(
            "Select[Any]",
            select(Bundle.style_no, Bundle.size_code, Bundle.bundle_qty, Bundle.counted_at),
        ),
        Bundle,
        ctx,
    ).where(*q.filters(), *q.order_filters())
    sub = rows.subquery()
    stmt = (
        select(
            sub.c.style_no,
            sub.c.size_code,
            func.count(),
            func.coalesce(func.sum(sub.c.bundle_qty), 0),
            func.count(sub.c.counted_at),
        )
        .select_from(sub)
        .group_by(sub.c.style_no, sub.c.size_code)
        .order_by(sub.c.style_no, sub.c.size_code)
    )
    return [
        StatRow(style_no, size_code, int(hands), Decimal(qty), int(counted))
        for style_no, size_code, hands, qty, counted in (await session.execute(stmt)).all()
    ]


__all__ = [
    "MAX_OFFSET",
    "MAX_PAGE_SIZE",
    "BundleListQuery",
    "BundleRow",
    "StatQuery",
    "StatRow",
    "VoidTargetRow",
    "aggregate_bundle_stats",
    "count_bundles",
    "get_bundle_by_no",
    "list_bundles",
    "lock_bundle_for_void",
    "mark_bundle_voided",
]
