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
不存在报 ``31001``，两个码不能混）。

⚠️ **作废的写路径（``FOR UPDATE`` + 条件 UPDATE）在** :mod:`.code_void_repository` ——
本文件已到 400 行硬线（ADR-0030），按 ADR-0031「按主题分文件」拆开，纯搬运、
零行为变化。``via.model`` 与本文件 import 的父单模型必须一致，
由 ``test_scope.py`` 的守卫钉住。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Final, NamedTuple, cast
from uuid import UUID

from sqlalchemy import Select, func, select
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
        """筛选条件。**列表与计数共用**（07 §3.2 铁律 3 的同源要求）。

        ⚠️ ``counted=false`` 的谓词是 ``deleted_at IS NULL AND status='ACTIVE' AND
        counted_at IS NULL``，而 ``idx_bundles_counted_pending`` 的 ``indexpred`` 正是
        这三条 —— **三者齐全规划器才认得出这个部分索引可用**（TC-30 明写「命中该索引」）。
        少了上面那条默认排除，PG 只能把 ``counted_at IS NULL`` 降级成 Filter 挂在别的
        索引上，索引名就不会出现在计划里。
        """
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
        # ⚠️ **默认排除 ``VOIDED``**（modules/03 §6；T-BUND-007c 口径）：``VOIDED``
        #    既不是「已计件」也不是「未计件」—— 算进任何一边都会让「本单手数 = 已计 + 未计」
        #    对不上（与统计只数 ACTIVE 码同一条理由，L-105 ③）。要取作废码得显式
        #    ``status=VOIDED``。
        #    这一行**同时**是 ``counted=false`` 能走索引的前提（见下）。
        if self.status:
            conditions.append(Bundle.status == BundleStatus(self.status))
        else:
            conditions.append(Bundle.status == BundleStatus.ACTIVE)
        if self.counted is not None:
            # ⚠️ 判「未计件」看 ``counted_at IS NULL``，**不看** ``counted_qty``（03 §3.3）：
            #    只做了 28 件的那一手（``counted_qty=28 < bundle_qty=60``）是**已计件**的手
            #    —— 它已进过计件流水、已算过工钱，列进「还没开始做的手」会让主管重复安排
            #    同一批活，而症状是「未计件手清单越查越长，而那批手早就在流水里了」。
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
    "aggregate_bundle_stats",
    "count_bundles",
    "get_bundle_by_no",
    "list_bundles",
]
