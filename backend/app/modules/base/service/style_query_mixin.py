"""款号读：列表 / 导出 / 详情 / 候选 / 色码尺码读（原 service.py 726-741、767-1022）。"""

from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import Select, func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessError, ErrorCode
from app.core.numbering import normalize_style_no
from app.core.permissions import AuthContext
from app.core.scope import apply_data_scope, assert_in_scope
from app.modules.auth.models import User
from app.modules.base.models import Customer, ProductCategory, Style, StyleColor, StyleSize
from app.modules.base.schemas import (
    OperationRateOut,
    OptionOut,
    StyleColorOut,
    StyleDetailOut,
    StyleListOut,
    StyleOperationOut,
    StyleOut,
    StyleSizeOut,
)

from .common import (
    MAX_EXPORT_ROWS,
    MAX_OFFSET,
    MAX_OPTION_SIZE,
    STYLE_SORT_WHITELIST,
    STYLE_TRGM_QUALIFIED,
    StyleQuery,
)


def _style_list_stmt(ctx: AuthContext) -> Select[Any]:
    """款号列表 / 详情共用的 SELECT：款号 + 客户名 + 分类名。

    ⚠️ **必须显式 ``LEFT JOIN``**（``customer_id`` 可空）：写成
    ``select(Style, Customer, ProductCategory)`` 时 SQLAlchemy **不会**从外键推断
    连接，会生成 ``FROM styles, customers, product_categories`` 的笛卡尔积 ——
    表现是行数爆炸 + ``SAWarning``，而本项目 ``filterwarnings = error``，
    一条警告就变成 500。列表与详情共用这一条，避免两处 SQL 悄悄分叉。
    """
    return (
        apply_data_scope(select(Style), Style, ctx)
        .outerjoin(Customer, Customer.id == Style.customer_id)
        .outerjoin(ProductCategory, ProductCategory.id == Style.category_id)
        .outerjoin(User, User.id == Style.merchandiser_id)
        .add_columns(Customer.name, ProductCategory.name, User.name)
    )


class StyleQueryMixin:
    session: AsyncSession
    ctx: AuthContext

    if TYPE_CHECKING:

        async def _suggest(self, customer_id: UUID | None) -> str: ...

        async def list_style_operations(
            self,
            style_no: str,
            *,
            is_piecework: bool | None = None,
            include_inactive: bool = False,
        ) -> list[StyleOperationOut]: ...

        async def _current_rates(self, style_no: str) -> list[OperationRateOut]: ...

    # ------------------------------------------------------------ 款号读

    async def list_styles(self, query: StyleQuery) -> tuple[list[StyleListOut], int]:
        """款号分页列表。

        第一行就是 ``apply_data_scope``（docs/07 §3.2 铁律 1）：跟单（``SELF``）
        只能看到 ``merchandiser_id`` 是自己的款号，其他人是 **0 条**（不是全部）。
        """
        stmt = self._filtered_styles(query)
        column = getattr(Style, STYLE_SORT_WHITELIST[query.sort_by or "style_no"])
        direction = column.desc() if query.sort_order == "desc" else column.asc()
        stmt = stmt.order_by(direction, Style.style_no.asc())

        count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
        total = int((await self.session.execute(count_stmt)).scalar_one())
        offset = (query.page - 1) * query.size
        rows = (await self.session.execute(stmt.offset(offset).limit(query.size))).all()
        return [self._style_row(row) for row in rows], total

    async def list_options(
        self, keyword: str | None, size: int = MAX_OPTION_SIZE, offset: int = 0
    ) -> list[OptionOut]:
        """款号候选。**默认按 ``last_used_at DESC NULLS LAST``**（05 §9.5.2 常用优先）。"""
        if not 1 <= size <= MAX_OPTION_SIZE:
            raise BusinessError(
                ErrorCode.PARAM_INVALID, f"候选接口 size 必须在 1~{MAX_OPTION_SIZE} 之间"
            )
        if offset > MAX_OFFSET:
            raise BusinessError(ErrorCode.PARAM_INVALID, "offset 过大，请改用关键字搜索")
        stmt = apply_data_scope(select(Style), Style, self.ctx)
        if keyword:
            stmt = stmt.where(literal_column(STYLE_TRGM_QUALIFIED).ilike(f"%{keyword}%"))
        stmt = stmt.order_by(Style.last_used_at.desc().nullslast(), Style.style_no.asc())
        rows = (await self.session.execute(stmt.offset(offset).limit(size))).scalars().all()
        return [
            OptionOut(
                value=row.style_no,
                label=f"{row.style_no} {row.name}",
                sub=None if row.is_active else "已停用",
                disabled=not row.is_active,
            )
            for row in rows
        ]

    async def suggest(self, customer_id: UUID | None) -> str:
        """取一个建议款号（**不建档**）。

        Q-P0-04：款号由用户自定义，建议号只作参考。表单上的「生成建议号」按钮要的是
        "填进去让我改"，而 :meth:`create` 里那个建议号是**建档时**额外回一个 ——
        复用它等于每点一次按钮就多一个款号。

        ⚠️ 取号会消耗一个序号（`next_no + 1`）：这是设计上的取舍，见
        :class:`~app.modules.base.schemas.SuggestedStyleNoOut` 的注释。
        """
        return await self._suggest(customer_id)

    async def get_required(self, style_no: str, *, for_update: bool = False) -> Style:
        """取款号；不存在 → ``20001``，越权 → ``12002``。

        :param for_update: ``True`` 时加 ``FOR UPDATE``。**先加锁再校验 version**
            是乐观锁能生效的唯一顺序 —— 反过来两个并发请求会读到同一个 version
            并双双通过校验（TC-B31）。
        """
        code = normalize_style_no(style_no)
        stmt = select(Style).where(Style.style_no == code, Style.deleted_at.is_(None))
        if for_update:
            stmt = stmt.with_for_update()
        style = (await self.session.execute(stmt)).scalar_one_or_none()
        if style is None:
            raise BusinessError(
                ErrorCode.BASE_DATA_NOT_FOUND,
                f"款号 {code} 不存在或已删除",
                details={"style_no": code},
            )
        assert_in_scope(style, self.ctx)
        return style

    def _filtered_styles(self, query: StyleQuery) -> Select[Any]:
        """款号列表与导出**共用**的筛选 + 数据范围。

        ⚠️ 抽出来是因为有**两个**消费方（列表分页、导出不分页）：复制一份的
        后果是将来改了一个筛选条件忘了改另一个 —— 而「列表看到的」与「导出的」
        不一致，只有对账时才发现（docs/07 §3.2 铁律 3）。

        第一行就是 ``apply_data_scope``（铁律 1）：跟单（``SELF``）只能看到
        ``merchandiser_id`` 是自己的款号，导出也一样 —— 导出是绕过界面直接拿数据的
        地方，比界面更容易泄露。
        """
        query.validate()
        stmt = _style_list_stmt(self.ctx)
        if query.q:
            # ⚠️ 必须用与 ``idx_styles_trgm`` **同一个表达式**，否则 planner 匹配不上，
            #    04 §5.1 精心要求的模糊检索退化成全表扫且毫无征兆
            stmt = stmt.where(literal_column(STYLE_TRGM_QUALIFIED).ilike(f"%{query.q}%"))
        if query.is_active is not None:
            stmt = stmt.where(Style.is_active == query.is_active)
        if query.customer_id is not None:
            stmt = stmt.where(Style.customer_id == query.customer_id)
        if query.category_id is not None:
            stmt = stmt.where(Style.category_id == query.category_id)
        if query.merchandiser_id is not None:
            stmt = stmt.where(Style.merchandiser_id == query.merchandiser_id)
        return stmt

    async def export_styles(self, query: StyleQuery) -> list[StyleListOut]:
        """导出货号：**与列表同一套筛选**、不分页（docs/07 §3.2 铁律 3、docs/05 §9.1）。

        ⚠️ 刻意复用 :meth:`_filtered_styles`（也就是列表用的那一份 WHERE）而不是
        另写一条 SELECT：另写的话，「列表看到的」与「导出的」会不一致 —— 那只有
        对账时才发现（比如列表按 ``last_used_at`` 排、导出按款号排，用户以为漏了行）。

        ⚠️ 行数上限 ``11011``：超了直接拒绝而不是截断 —— 截断出来的导出会让用户
        以为导全了，那比报错危险得多（与 :meth:`RateService.export_rates` 同判据）。
        """
        stmt = self._filtered_styles(query)
        column = getattr(Style, STYLE_SORT_WHITELIST[query.sort_by or "style_no"])
        direction = column.desc() if query.sort_order == "desc" else column.asc()
        stmt = stmt.order_by(direction, Style.style_no.asc())

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
        rows = list((await self.session.execute(stmt)).all())
        return [self._style_row(row) for row in rows]

    async def get_detail(self, style_no: str) -> StyleDetailOut:
        """款号详情 = 款号 + 色组 + 尺码 + 款号工序 + 现行价（设计稿 §4.5）。"""
        style = await self.get_required(style_no)
        row = (
            await self.session.execute(_style_list_stmt(self.ctx).where(Style.id == style.id))
        ).one()
        return StyleDetailOut(
            style=self._style_row(row),
            colors=await self._list_colors(style.style_no),
            sizes=await self._list_sizes(style.style_no),
            operations=await self.list_style_operations(style.style_no),
            current_rates=await self._current_rates(style.style_no),
        )

    async def _list_colors(self, style_no: str) -> list[StyleColorOut]:
        rows = (
            (
                await self.session.execute(
                    select(StyleColor)
                    .where(StyleColor.style_no == style_no, StyleColor.deleted_at.is_(None))
                    .order_by(StyleColor.color_group, StyleColor.color_code)
                )
            )
            .scalars()
            .all()
        )
        return [
            StyleColorOut(
                id=row.id,
                version=row.version,
                remark=row.remark,
                created_at=row.created_at,
                updated_at=row.updated_at,
                style_no=row.style_no,
                color_group=row.color_group,
                color_code=row.color_code,
                color_name=row.color_name,
                material_color_code=row.material_color_code,
            )
            for row in rows
        ]

    async def _list_sizes(self, style_no: str) -> list[StyleSizeOut]:
        rows = (
            (
                await self.session.execute(
                    select(StyleSize)
                    .where(StyleSize.style_no == style_no, StyleSize.deleted_at.is_(None))
                    .order_by(StyleSize.sort_no, StyleSize.size_code)
                )
            )
            .scalars()
            .all()
        )
        return [
            StyleSizeOut(
                id=row.id,
                version=row.version,
                remark=row.remark,
                created_at=row.created_at,
                updated_at=row.updated_at,
                style_no=row.style_no,
                size_code=row.size_code,
                size_name=row.size_name,
                sort_no=row.sort_no,
            )
            for row in rows
        ]

    async def style_size_codes(self, style_no: str) -> set[str]:
        """该款已定义的尺码集合（比例缺配提示与 20007 校验的共同依据）。"""
        rows = (
            await self.session.execute(
                select(StyleSize.size_code).where(
                    StyleSize.style_no == style_no, StyleSize.deleted_at.is_(None)
                )
            )
        ).scalars()
        return set(rows)

    @staticmethod
    def _style_row(row: tuple[Any, ...]) -> StyleListOut:
        # ⚠️ 这里取的是 ``add_columns`` 出来的**标量列**（客户名 / 分类名），
        # 不是 Customer / ProductCategory 实体 —— 写成实体解包会得到 str
        style, customer_name, category_name, merchandiser_name = row
        return StyleListOut(
            id=style.id,
            version=style.version,
            remark=style.remark,
            created_at=style.created_at,
            updated_at=style.updated_at,
            style_no=style.style_no,
            name=style.name,
            category_id=style.category_id,
            category_name=category_name,
            customer_id=style.customer_id,
            customer_name=customer_name,
            customer_style_no=style.customer_style_no,
            bulk_qty=style.bulk_qty,
            merchandiser_id=style.merchandiser_id,
            merchandiser_name=merchandiser_name,
            is_active=style.is_active,
            last_used_at=style.last_used_at,
        )

    def _style_out(self, style: Style, suggested: str | None = None) -> StyleOut:
        return StyleOut(
            id=style.id,
            version=style.version,
            remark=style.remark,
            created_at=style.created_at,
            updated_at=style.updated_at,
            style_no=style.style_no,
            name=style.name,
            category_id=style.category_id,
            customer_id=style.customer_id,
            customer_style_no=style.customer_style_no,
            bulk_qty=style.bulk_qty,
            merchandiser_id=style.merchandiser_id,
            last_used_at=style.last_used_at,
            is_active=style.is_active,
            suggested_style_no=suggested,
        )
