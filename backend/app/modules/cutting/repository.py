"""裁剪单的**只读**查询（docs/03 §1.3：repository 只查不判断业务）。

⚠️ 本模块**不做**任何业务判断：不检查权限、不算汇总、不决定能不能改。
   那些全在 :mod:`service`。混进来的后果是同一规则在两条路径上不一致，
   而「算汇总」这种事不一致意味着**详情页看到的数和列表页对不上**。

## 三条硬约束（docs/07 §3.2）

1. 每个列表方法**第一行**调 :func:`apply_data_scope`（INV-8）
2. 详情方法额外校验 :func:`assert_in_scope`，防越权按 ID 直查
3. **导出与列表共用同一 service 方法**，禁止另写导出路径 —— 本文件不提供
   「绕过 service 直接导出」的入口，就是为了让那条规则无法被违反
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.elements import ColumnElement

from app.core.errors import BusinessError, ErrorCode
from app.core.permissions import AuthContext
from app.core.scope import apply_data_scope
from app.modules.cutting.models import (
    CuttingOrder,
    CuttingOrderLine,
    CuttingOrderLineColor,
    CuttingOrderSizeLine,
)

#: 列表行的类型别名。⚠️ **只有列表用它** —— 详情 / 加锁那几条返回的是具体的
#: :class:`CuttingOrder`，因为那两条的返回类型要能被 mypy 检查
#: （写成别名就变成 ``Any``，``Returning Any from function`` 那类问题会被放过）。
#: 列表返回 ``list[Any]`` 是可接受的：它只在 service 里被转成响应模型。
type Row = Any

#: 深分页上限（docs/04 §5.1：offset > 10000 直接 10001）
MAX_OFFSET = 10000
#: 单页最大行数。裁剪单列表默认 20 —— 上限比基础资料的 200 略宽松，
#: 因为车间主管常要按日期区间拉一整段（modules/02 §6 的列表接口）。
MAX_PAGE_SIZE = 200


@dataclass(frozen=True, slots=True)
class OrderListQuery:
    """裁剪单列表 / 导出**共用**的筛选条件（docs/05 §2）。"""

    status: str | None = None
    style_no: str | None = None
    workshop_id: UUID | None = None
    doc_date_from: Any | None = None
    doc_date_to: Any | None = None
    dye_lot_no: str | None = None
    sort_by: str | None = None
    sort_order: str = "desc"
    page: int = 1
    size: int = 20

    def validate(self) -> None:
        """校验分页、排序与日期区间。**必须在拼 SQL 之前调用**。

        ⚠️ ``sort_by`` 走**白名单映射**（docs/04 §5.1：禁止字符串拼接进 SQL）。
        白名单里**没有** ``output_qty`` 之类 —— 加进去之前先想清楚有没有对应索引，
        ``04 §5`` 明确「禁止为不存在的查询建索引」，而反过来的「为不存在的排序建
        索引」同样浪费。
        """
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
                f"翻页太深（offset={offset} > {MAX_OFFSET}），请改用日期区间缩小范围",
            )
        if self.sort_order not in ("asc", "desc"):
            raise BusinessError(ErrorCode.PARAM_INVALID, "sort_order 只能是 asc / desc")

    def sort_column(self) -> ColumnElement[Any]:
        """把 ``sort_by`` 映射成**列对象**（而不是字符串）。

        ⚠️ 白名单顺序即优先级：``-doc_date,doc_no`` 是 modules/02 §6 指定的默认排序
        —— 单据日期倒序，同日按单号倒序（保证分页时同一页里的单稳定）。
        """
        allowed = {
            "doc_date": CuttingOrder.doc_date,
            "doc_no": CuttingOrder.doc_no,
            "style_no": CuttingOrder.style_no,
            "output_qty": CuttingOrder.output_qty,
            "created_at": CuttingOrder.created_at,
        }
        column = allowed.get(self.sort_by or "doc_date", CuttingOrder.doc_date)
        return column.desc() if self.sort_order == "desc" else column.asc()


def _base_stmt(ctx: AuthContext) -> Select[tuple[Row]]:
    """带数据范围过滤的查询起点（docs/07 §3.2 铁律 1）。

    ⚠️ ``apply_data_scope`` 会**自动附加** ``deleted_at IS NULL``（INV-7），
    所以本文件任何地方都不要再手写一次 —— 写了两次不会更安全，只会让
    「软删过滤到底在哪一层」这件事变得要看两处。
    """
    return apply_data_scope(select(CuttingOrder), CuttingOrder, ctx)


async def count_orders(session: AsyncSession, ctx: AuthContext, q: OrderListQuery) -> int:
    """总数。``_filters`` 与列表查询**共用**，所以分页总数与实际行数不会不一致。"""
    stmt = _base_stmt(ctx).where(*_filters(q))
    return int(
        (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    )


async def list_orders(session: AsyncSession, ctx: AuthContext, q: OrderListQuery) -> list[Row]:
    """单据列表（**不含**三层明细）。

    ⚠️ 刻意不 ``selectinload`` 三层：列表页不需要它们，而一次加载三层会让
    「取 20 张单」变成 20 + 20×10 + 20×10×50 = 上万行 —— modules/02 §6 的
    「单据详情」才需要三层。
    """
    stmt = _base_stmt(ctx).where(*_filters(q)).order_by(q.sort_column())
    stmt = stmt.offset((q.page - 1) * q.size).limit(q.size)
    return list((await session.execute(stmt)).scalars().all())


async def get_order_three_levels(session: AsyncSession, order_id: UUID) -> CuttingOrder | None:
    """取单据 + 三层明细（**详情页用**）。

    ⚠️ 三层用**三个 ``selectinload`` 一次拿完**，而不是逐层懒加载：
    逐层会在同一个 session 里发 4 条语句，而 ``selectinload`` 是 2 条
    （主表 + 每层一条 IN 查询）。层数再增加时这个差距会拉大。

    ⚠️ **不带数据范围过滤**：调用方（service）必须先
    :func:`assert_in_scope` —— 详情按 ID 直查是越权的经典入口（07 §3.2 铁律 2）。
    这里若顺手把过滤加上，就多了一份「过滤规则」而它只在一处被更新。
    """
    # ⚠️ **`populate_existing=True` 不是可选的**。它有两个理由，缺一个都会出事：
    #   1. service 的 ``_bump_header`` 用 Core ``UPDATE`` 改表头，而 Core UPDATE 对
    #      identity map 里的对象是 ``synchronize_session='fetch'`` —— 会把那一行
    #      **expire**。此后访问任何列都触发惰性刷新，而在 Router 的**同步**
    #      Pydantic 上下文里就是 ``MissingGreenlet: greenlet_spawn has not been called``。
    #   2. 即使不 expire，SQLAlchemy 默认也**不覆盖**已加载对象的属性值
    #      （``populate_existing`` 默认关闭）—— 于是重读拿到的还是**旧值**。
    #   两个症状都指向「重读没生效」，而报错完全看不出根因在这一层。
    stmt = (
        select(CuttingOrder)
        .where(CuttingOrder.id == order_id)
        .where(CuttingOrder.deleted_at.is_(None))
        .execution_options(populate_existing=True)
        .options(
            selectinload(CuttingOrder.lines).options(
                selectinload(CuttingOrderLine.colors).options(
                    selectinload(CuttingOrderLineColor.size_lines)
                )
            )
        )
    )
    return (await session.execute(stmt)).unique().scalar_one_or_none()


async def get_order_for_update(session: AsyncSession, order_id: UUID) -> CuttingOrder | None:
    """取单据并 ``FOR UPDATE``（写操作入口用）。

    ⚠️ 与 :func:`get_order_three_levels` 分开是刻意的：**加锁的查询不能带
    ``selectinload``** —— ``SELECT ... FOR UPDATE`` 配 ``SELECT`` 子查询时，
    PG 只锁外层那一张表，子表照样可能被并发改。这里锁住表头，
    子表的并发由它们的乐观锁 + 唯一键兜。
    """
    stmt = select(CuttingOrder).where(CuttingOrder.id == order_id).with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_lines_for_update(session: AsyncSession, doc_id: UUID) -> list[CuttingOrderLine]:
    """取某单全部布批行 + 颜色 + 尺码明细，**并锁住三层**。

    ⚠️ **三层都要 ``with_for_update``**，而 ``selectinload`` 的子查询**不带锁**
    —— 只锁外层的话，子表照样能被并发改，而重算汇总正是基于子表。
    这也是 ``docs/02`` 第 7 节反复强调「校验必须用**已加行锁**的数据」的原因。

    ⚠️ **不用 ``selectinload``**：它对子查询发的是独立的 SELECT，无法附加
    ``FOR UPDATE``。所以这里手写三条带锁查询。

    ⚠️ **三条都带 ``deleted_at IS NULL``**：调用方（service 的 ``_recalc_all``）
    直接拿这个列表算汇总，而汇总**绝不能**把软删行算进去 ——
    踩过一次的症状是「替换后表头耗料 = 96 + 100 = 196 而不是 100」，
    即旧行虽然软删了，却仍然参与求和。
    """
    lines = list(
        (
            await session.execute(
                select(CuttingOrderLine)
                .where(CuttingOrderLine.doc_id == doc_id, CuttingOrderLine.deleted_at.is_(None))
                .order_by(CuttingOrderLine.line_no)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    if not lines:
        return lines
    line_ids = [line.id for line in lines]
    colors = list(
        (
            await session.execute(
                select(CuttingOrderLineColor)
                .where(
                    CuttingOrderLineColor.line_id.in_(line_ids),
                    CuttingOrderLineColor.deleted_at.is_(None),
                )
                .order_by(CuttingOrderLineColor.id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    if colors:
        size_lines = list(
            (
                await session.execute(
                    select(CuttingOrderSizeLine)
                    .where(
                        CuttingOrderSizeLine.line_color_id.in_([c.id for c in colors]),
                        CuttingOrderSizeLine.deleted_at.is_(None),
                    )
                    .order_by(CuttingOrderSizeLine.size_line_no)
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        by_color: dict[UUID, list[CuttingOrderSizeLine]] = {}
        for row in size_lines:
            by_color.setdefault(row.line_color_id, []).append(row)
        for color in colors:
            # ⚠️ 直接赋值给 relationship 的集合属性：ORM 会把它当「已加载」处理，
            #    之后 ``color.size_lines`` 不再发查询。这里是**读路径的重算**用途，
            #    不涉及写子表，所以不需要 viewonly 之外的任何配置
            color.size_lines = by_color.get(color.id, [])
    by_line: dict[UUID, list[CuttingOrderLineColor]] = {}
    for color in colors:
        by_line.setdefault(color.line_id, []).append(color)
    for line in lines:
        line.colors = by_line.get(line.id, [])
    return lines


async def get_line_color_for_update(
    session: AsyncSession, line_color_id: UUID
) -> CuttingOrderLineColor | None:
    """取一个行内颜色并锁住（切模式 / 替换明细时用，§7 固定锁序的关键一步）。"""
    return (
        await session.execute(
            select(CuttingOrderLineColor)
            .where(CuttingOrderLineColor.id == line_color_id)
            .with_for_update()
        )
    ).scalar_one_or_none()


async def get_lines(session: AsyncSession, doc_id: UUID) -> list[Row]:
    """取某单的全部布批行（service 重算汇总时用）。"""
    stmt = (
        select(CuttingOrderLine)
        .where(CuttingOrderLine.doc_id == doc_id)
        .order_by(CuttingOrderLine.line_no)
    )
    return list((await session.execute(stmt)).scalars().all())


def _filters(q: OrderListQuery) -> tuple[ColumnElement[bool], ...]:
    """列表筛选条件。**列表与计数共用**（docs/07 §3.2 铁律 3 的同源要求）。"""
    conditions: list[ColumnElement[bool]] = []
    if q.status:
        conditions.append(CuttingOrder.status == q.status)
    if q.style_no:
        conditions.append(CuttingOrder.style_no == q.style_no)
    if q.workshop_id:
        conditions.append(CuttingOrder.workshop_id == q.workshop_id)
    if q.doc_date_from is not None:
        conditions.append(CuttingOrder.doc_date >= q.doc_date_from)
    if q.doc_date_to is not None:
        conditions.append(CuttingOrder.doc_date <= q.doc_date_to)
    return tuple(conditions)
