"""基础资料的**只读**查询（docs/03 §1.3：repository 只查不判断业务）。

⚠️ 本模块**不做**任何业务判断：不检查权限、不校验引用、不决定能不能删。
   那些全在 :mod:`service`。混进来的后果是同一规则在两条路径上不一致，
   而"能不能删"这种事不一致意味着有的入口能绕过去。

三个查询方法都接受同一个 :class:`ListQuery`，这样**列表与导出共用同一套筛选**
（docs/07 §3.2 铁律 3：禁止另写导出路径）。
"""

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import Select, func, literal_column, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.common.enums import DataScope
from app.core.errors import BusinessError, ErrorCode
from app.core.permissions import AuthContext
from app.core.scope import apply_data_scope
from app.modules.base.resources import DictResource
from app.modules.base.schemas import OptionOut

#: ORM 实体行。九个资源的模型是动态注册的，写不出一个共同的具体类型；
#: 用别名集中一处而不是散落 ``Any``，这样 ruff 的 ANN401 能守住其他参数。
type Row = Any

#: 候选下拉的结果硬上限（docs/05 §9.5.2：选择器 20 条）
OPTION_MAX_SIZE = 20
#: 深分页上限（docs/04 §5.1：offset > 10000 直接 10001）
MAX_OFFSET = 10000
#: 导出行数上限（docs/05 §4 的 11011：超过 10 万行）
MAX_EXPORT_ROWS = 100_000


@dataclass(frozen=True, slots=True)
class ListQuery:
    """列表 / 候选 / 导出**共用**的筛选条件。"""

    q: str | None = None
    is_active: bool | None = None
    is_builtin: bool | None = None
    filters: dict[str, Any] = field(default_factory=dict)
    sort_by: str | None = None
    sort_order: str = "asc"
    page: int = 1
    size: int = 20
    offset: int | None = None

    def validate(self, resource: DictResource) -> None:
        """校验分页与排序参数。**必须在拼 SQL 之前调用**（docs/05 §2）。"""
        if self.size < 1 or self.size > 200:
            raise BusinessError(
                ErrorCode.PARAM_INVALID, f"page_size 必须在 1~200 之间，收到 {self.size}"
            )
        if self.page < 1:
            raise BusinessError(ErrorCode.PARAM_INVALID, "page 从 1 起")
        if self.sort_order not in ("asc", "desc"):
            raise BusinessError(ErrorCode.PARAM_INVALID, "sort_order 只能是 asc 或 desc")
        if self.sort_by is not None and self.sort_by not in resource.sort_whitelist:
            # ⚠️ 绝不能把 sort_by 直接拼进 SQL（SQL 注入）。白名单之外一律拒绝，
            #    报错文案要列出可用值，否则前端只能猜
            raise BusinessError(
                ErrorCode.PARAM_INVALID,
                f"sort_by={self.sort_by} 不是可排序字段",
                details={"allowed": sorted(resource.sort_whitelist)},
            )
        if self.offset is not None and self.offset > MAX_OFFSET:
            raise BusinessError(
                ErrorCode.PARAM_INVALID,
                f"offset 不能超过 {MAX_OFFSET}，请改用游标翻页（search_after）",
                details={"max_offset": MAX_OFFSET},
            )
        for key in self.filters:
            if key not in resource.filter_columns:
                raise BusinessError(
                    ErrorCode.PARAM_INVALID,
                    f"{key} 不是可筛选字段",
                    details={"allowed": sorted(resource.filter_columns)},
                )


class DictRepository:
    """基础资料只读查询。"""

    def __init__(self, session: AsyncSession, resource: DictResource) -> None:
        self.session = session
        self.resource = resource

    # ------------------------------------------------------------ 基础查询

    def _base_stmt(self, ctx: AuthContext) -> Select[Any]:
        """查询基线：软删过滤 + （按需）数据范围过滤。

        数据范围只在资源的 ``data_scope`` 不是 ``FACTORY`` 时施加 —— §4.4 对九个
        基础资料资源一律标 FACTORY（主数据全厂共享）。若在这里无条件套用用户的
        范围，``SELF`` 范围的用户会看到**空的**颜色下拉。
        """
        stmt = select(self.resource.model)
        if self.resource.data_scope == DataScope.FACTORY:
            return stmt.where(self.resource.model.deleted_at.is_(None))
        return apply_data_scope(stmt, self.resource.model, ctx)

    def _apply_filters(self, stmt: Select[Any], query: ListQuery) -> Select[Any]:
        model = self.resource.model
        if query.q:
            stmt = stmt.where(self._keyword_condition(query.q))
        if query.is_active is not None and self.resource.is_active_column:
            stmt = stmt.where(getattr(model, self.resource.is_active_column) == query.is_active)
        if query.is_builtin is not None and self.resource.has_builtin_flag:
            stmt = stmt.where(model.is_builtin == query.is_builtin)
        for key, value in query.filters.items():
            if value is None:
                continue
            stmt = stmt.where(getattr(model, key) == value)
        return stmt

    def _keyword_condition(self, keyword: str) -> ColumnElement[bool]:
        """候选搜索的模糊条件。

        ⚠️ ``trgm_expression`` 存在时必须走它 —— 那个表达式与 GIN 索引**逐字相同**，
        planner 才能用上索引。这里换成别的写法（比如 ``ILIKE '%q%'`` 拆成两列 OR）
        索引就白建了，docs/04 §5.1 正是为此要求统一形态。
        """
        model = self.resource.model
        pattern = f"%{keyword}%"
        if self.resource.trgm_expression is not None:
            # ⚠️ 必须用**与 GIN 索引完全相同的表达式**。写成
            # ``code ILIKE ? OR name ILIKE ?`` 语义等价但 planner 匹配不上索引 ——
            # 索引挂在 ``(code || ' ' || name)`` 上，OR 两列走不到它，
            # 于是 docs/04 §5.1 精心要求的模糊检索退化成全表扫，而且毫无征兆。
            return literal_column(self.resource.trgm_expression).ilike(pattern)
        return or_(
            getattr(model, self.resource.code_column).ilike(pattern),
            getattr(model, self.resource.name_column).ilike(pattern),
        )

    def _order_by(self, stmt: Select[Any], query: ListQuery) -> Select[Any]:
        if not query.sort_by:
            # 默认按编码排序：候选列表要稳定，否则同一条记录会在两次查询里换位置
            column = getattr(self.resource.model, self.resource.code_column)
            return stmt.order_by(column.asc())
        column_name = self.resource.sort_whitelist[query.sort_by]
        column = getattr(self.resource.model, column_name)
        direction = column.desc() if query.sort_order == "desc" else column.asc()
        return stmt.order_by(direction, self.resource.model.id.asc())

    # -------------------------------------------------------------- 读方法

    async def list_rows(self, ctx: AuthContext, query: ListQuery) -> tuple[list[Row], int]:
        """分页列表 + 总数。"""
        stmt = self._order_by(self._apply_filters(self._base_stmt(ctx), query), query)
        count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
        total = int((await self.session.execute(count_stmt)).scalar_one())

        offset = query.offset if query.offset is not None else (query.page - 1) * query.size
        rows = (await self.session.execute(stmt.offset(offset).limit(query.size))).scalars().all()
        return list(rows), total

    async def iter_export_rows(self, ctx: AuthContext, query: ListQuery) -> list[Row]:
        """导出用：按同一套筛选取全部行（**不加分页**）。

        行数上限 ``11011``：超了直接拒绝而不是截断 —— 截断出来的导出文件会让
        用户以为导全了，那比报错危险得多。
        """
        stmt = self._order_by(self._apply_filters(self._base_stmt(ctx), query), query)
        count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
        total = int((await self.session.execute(count_stmt)).scalar_one())
        if total > MAX_EXPORT_ROWS:
            raise BusinessError(
                ErrorCode.EXPORT_RANGE_TOO_LARGE,
                f"导出结果 {total} 行超过上限 {MAX_EXPORT_ROWS}，请缩小筛选范围",
                details={"row_count": total, "max_rows": MAX_EXPORT_ROWS},
            )
        rows = (await self.session.execute(stmt)).scalars().all()
        # 转成 dict：导出层按 ``Column.key`` 取值（``row.get(key)``），
        # 直接传 ORM 实体会 AttributeError —— 实体没有 .get
        return [self._as_dict(row) for row in rows]

    def _as_dict(self, row: Row) -> dict[str, Any]:
        """ORM 实体 → 扁平 dict。Decimal / 枚举由 excel 层统一转换。"""
        return {
            column.name: getattr(row, column.name, None)
            for column in self.resource.model.__table__.columns
        }

    async def options(
        self, ctx: AuthContext, keyword: str | None, size: int, offset: int = 0
    ) -> list[OptionOut]:
        """候选下拉（docs/05 §9.5.2）。

        ``q`` 为空时返回默认排序的前 N 条；``size`` 强制 ≤ 20。
        """
        if size < 1 or size > OPTION_MAX_SIZE:
            raise BusinessError(
                ErrorCode.PARAM_INVALID, f"候选接口 size 必须在 1~{OPTION_MAX_SIZE} 之间"
            )
        query = ListQuery(q=keyword, size=size, offset=offset)
        if offset > MAX_OFFSET:
            raise BusinessError(ErrorCode.PARAM_INVALID, f"offset 不能超过 {MAX_OFFSET}")
        stmt = self._order_by(self._apply_filters(self._base_stmt(ctx), query), query)
        rows = (await self.session.execute(stmt.offset(offset).limit(size))).scalars().all()
        return [self._to_option(row) for row in rows]

    def _to_option(self, row: Row) -> OptionOut:
        code = str(getattr(row, self.resource.code_column))
        name = str(getattr(row, self.resource.name_column, "") or "")
        label = f"{code} {name}".strip() if name else code
        is_active = (
            bool(getattr(row, self.resource.is_active_column))
            if self.resource.is_active_column
            else True
        )
        return OptionOut(value=code, label=label, sub=self._option_sub(row), disabled=not is_active)

    def _option_sub(self, row: Row) -> str | None:
        """副文本：尺码类 / 仓库类型 / 色卡族等辅助信息。"""
        model_name = self.resource.model.__name__
        if model_name == "Size":
            return str(getattr(row, "size_class", "") or "") or None
        if model_name == "SizeGroup":
            return str(getattr(row, "size_class", "") or "") or None
        if model_name == "Warehouse":
            return str(getattr(row, "warehouse_type", "") or "") or None
        if model_name == "Color":
            return getattr(row, "color_family", None)
        return None

    async def ref_counts(self, rows: list[Row]) -> dict[UUID, int]:
        """批量算引用计数。

        ⚠️ **一条 SQL 算完，不做 N+1**：界面要显示每行的 ref_count，九个资源
        每行一次子查询在 100 行时就是 100 次往返。
        """
        if not self.resource.ref_checkers or not rows:
            return {}
        ids = [row.id for row in rows]
        result: dict[UUID, int] = {}
        for checker in self.resource.ref_checkers:
            column = getattr(checker.model, checker.column)
            stmt = select(column, func.count()).where(column.in_(ids)).group_by(column)
            if checker.active_only and hasattr(checker.model, "deleted_at"):
                stmt = stmt.where(checker.model.deleted_at.is_(None))
            for target_id, count in (await self.session.execute(stmt)).all():
                key = target_id if isinstance(target_id, UUID) else UUID(str(target_id))
                result[key] = result.get(key, 0) + int(count)
        return result

    async def references_of(self, obj: Row) -> list[dict[str, Any]]:
        """列出引用来源（写进 ``20003`` 的 ``details.references[]``）。"""
        if not self.resource.ref_checkers:
            return []
        sources: list[dict[str, Any]] = []
        for checker in self.resource.ref_checkers:
            column = getattr(checker.model, checker.column)
            stmt = select(func.count()).select_from(checker.model).where(column == obj.id)
            if checker.active_only and hasattr(checker.model, "deleted_at"):
                stmt = stmt.where(checker.model.deleted_at.is_(None))
            count = int((await self.session.execute(stmt)).scalar_one())
            if count:
                sources.append({"source": checker.label, "count": count})
        return sources

    async def find_by_code(self, code: str, *, include_deleted: bool = False) -> Row | None:
        """按业务编码取单条。"""
        column = getattr(self.resource.model, self.resource.path_column)
        stmt = select(self.resource.model).where(column == code)
        if not include_deleted:
            stmt = stmt.where(self.resource.model.deleted_at.is_(None))
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def exists_by_code(self, code: str, *, exclude_id: UUID | None = None) -> bool:
        """编码是否已被占用。**仅用于给出友好报错**，真正兜底靠唯一索引。"""
        column = getattr(self.resource.model, self.resource.code_column)
        stmt = select(self.resource.model.id).where(column == code)
        if exclude_id is not None:
            stmt = stmt.where(self.resource.model.id != exclude_id)
        return (await self.session.execute(stmt)).first() is not None


__all__ = ["MAX_EXPORT_ROWS", "MAX_OFFSET", "OPTION_MAX_SIZE", "DictRepository", "ListQuery"]
