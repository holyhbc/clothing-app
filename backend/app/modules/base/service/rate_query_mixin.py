"""工序单价列表 / 导出 / 可见款号（原 service.py 2180-2259）。"""

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessError, ErrorCode
from app.core.numbering import normalize_style_no
from app.core.permissions import AuthContext
from app.core.scope import apply_data_scope
from app.modules.base.models import OperationRate, Style
from app.modules.base.schemas import OperationRateOut

from .common import MAX_EXPORT_ROWS, RATE_SORT_WHITELIST, RateQuery
from .rate_helpers import rate_out
from .style_service import StyleService


class RateQueryMixin:
    session: AsyncSession
    ctx: AuthContext

    # -------------------------------------------------------------- 读

    async def list_rates(self, query: RateQuery) -> tuple[list[OperationRateOut], int]:
        """历史区间列表 + 派生 ``is_current``。"""
        query.validate()
        stmt = await self._filtered(query)
        column = getattr(OperationRate, RATE_SORT_WHITELIST[query.sort_by or "effective_from"])
        direction = column.desc() if query.sort_order == "desc" else column.asc()
        stmt = stmt.order_by(direction, OperationRate.operation_no.asc())

        count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
        total = int((await self.session.execute(count_stmt)).scalar_one())
        offset = (query.page - 1) * query.size
        rows = list(
            (await self.session.execute(stmt.offset(offset).limit(query.size))).scalars().all()
        )
        return [rate_out(row) for row in rows], total

    async def _filtered(self, query: RateQuery) -> Select[OperationRate]:
        """单价列表 / 导出**共用**的筛选条件（docs/07 §3.2 铁律 3）。

        ⚠️ 必须共用：另写一条导出路径的话，"列表看到的"与"导出的"会不一致 ——
        那类问题只有在对账时才发现，而那时已经导出并入账了。

        数据范围：``style_no`` 给了就按款号校验（越权 → ``12002``）；没给时，
        非全厂范围的用户只能看到「自己款号的档位 1」+「全厂口径的档位 2/3」。
        """
        stmt = apply_data_scope(select(OperationRate), OperationRate, self.ctx)
        if query.style_no is not None:
            style = await StyleService(self.session, self.ctx).get_required(query.style_no)
            stmt = stmt.where(OperationRate.style_no == style.style_no)
        elif not self.ctx.is_factory_scoped:
            visible = await self._visible_style_nos()
            stmt = stmt.where(
                or_(
                    OperationRate.style_no.is_(None),
                    OperationRate.style_no.in_(visible),
                )
            )
        if query.operation_no is not None:
            stmt = stmt.where(OperationRate.operation_no == query.operation_no)
        if query.product_category_id is not None:
            stmt = stmt.where(OperationRate.product_category_id == query.product_category_id)
        if query.effective_from is not None:
            stmt = stmt.where(OperationRate.effective_from >= query.effective_from)
        if query.effective_to is not None:
            stmt = stmt.where(OperationRate.effective_from <= query.effective_to)
        return stmt

    async def export_rates(self, query: RateQuery) -> list[OperationRateOut]:
        """导出：**与列表同一套筛选**、不分页（docs/07 §3.2 铁律 3、docs/05 §9.1）。

        行数上限 ``11011``：超了直接拒绝而不是截断 —— 截断出来的导出会让用户
        以为导全了，那比报错危险得多。
        """
        query.validate()
        stmt = await self._filtered(query)
        column = getattr(OperationRate, RATE_SORT_WHITELIST[query.sort_by or "effective_from"])
        direction = column.desc() if query.sort_order == "desc" else column.asc()
        stmt = stmt.order_by(direction, OperationRate.operation_no.asc())
        total = int(
            (
                await self.session.execute(
                    select(func.count()).select_from(stmt.order_by(None).subquery())
                )
            ).scalar_one()
        )
        if total > MAX_EXPORT_ROWS:
            raise BusinessError(
                ErrorCode.EXPORT_RANGE_TOO_LARGE,
                f"导出结果 {total} 行超过上限 {MAX_EXPORT_ROWS}，请缩小筛选范围",
                details={"row_count": total, "max_rows": MAX_EXPORT_ROWS},
            )
        rows = list((await self.session.execute(stmt)).scalars().all())
        return [rate_out(row) for row in rows]

    async def _visible_style_nos(self) -> list[str]:
        """当前用户在数据范围内可见的款号（只查款号列，不加载整行）。"""
        stmt = apply_data_scope(select(Style.style_no), Style, self.ctx)
        return [normalize_style_no(item) for item in (await self.session.execute(stmt)).scalars()]
