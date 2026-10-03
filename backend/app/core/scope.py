"""数据范围强制过滤（docs/07-认证与权限规范.md §3.2）。

⚠️ **必须在此层，不许放 Router**（INV-8）。Router 里过滤有两个致命问题：
    1) 让前端传 ``workshop_id`` 决定范围 → 越权
    2) 只在 Router 过滤 → service 的其他调用路径绕过

三条铁律（docs/07 §3.2 原文）：
    1. 每个列表/详情/导出 service 方法第一行调用本模块
    2. 详情方法额外校验 ``in_scope``，防越权按 ID 直查
    3. 导出与列表**共用同一 service 方法**，禁止另写导出路径
"""

from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from sqlalchemy import Select, false, or_
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement

from app.common.enums import DataScope
from app.core.permissions import AuthContext


@dataclass(frozen=True, slots=True)
class ScopeSpec:
    """声明某张表与四种数据范围的**列映射**。

    为什么必须显式声明而不是按列名猜：主数据表（``styles`` / ``colors`` …）根本没有
    ``workshop_id`` 列；``SELF`` 的含义也随资源而变 —— 计件/工资是"本人"，
    款号是"我负责的款号"（``styles.merchandiser_id``）。靠列名推断必然出错。

    :param user_column: ``SELF`` 用哪一列过滤；``None`` 表示该资源不支持 SELF
        （例如仓库字典没有归属人，查不到任何数据）
    :param workshop_column: ``WORKSHOP`` / ``GROUP`` 用哪一列
    :param group_column: ``GROUP`` 用的组别列
    :param soft_delete: 是否附加 ``deleted_at IS NULL``（INV-7）
    :param include_null_workshop: ``WORKSHOP`` 范围下，是否把「车间为空」的行也算可见。

        ⚠️ **必须按资源显式声明，不能一刀切**。车间为空在不同表里含义完全不同：

        - ``operations``：§7.8.1 定义「可空 = **通用**工序」，通用就该全厂可见
        - ``users``：车间为空表示「**非车间人员**」（财务、仓管），
          让车间主管看到全厂财务人员显然不对

        所以这是一个 opt-in 标志。默认 ``False``（车间为空即不可见），
        只有语义上确实是"通用"的资源才打开。
    """

    user_column: str | None = None
    workshop_column: str | None = None
    group_column: str | None = None
    soft_delete: bool = True
    include_null_workshop: bool = False


def visible_workshops(ctx: AuthContext) -> frozenset[UUID]:
    """``WORKSHOP`` 范围下可见的车间集合。

    = 角色授予的车间的并集与本人所属车间的合集。空集意味着**查不到任何数据**。
    """
    granted = ctx.allowed_workshop_ids
    own = frozenset({ctx.workshop_id}) if ctx.workshop_id else frozenset()
    return granted | own


#: ⚠️ **前瞻登记**的表：这些表由后续卡片创建，现在还不在库里。
#:
#: 提前写在这里而不是等建表时再写，有两个理由：
#:   1. 建表那张卡片如果忘了登记，数据范围会**静默失效**（表现为界面一片空白，
#:      而不是越权）—— 那类 bug 从日志里根本看不出来
#:   2. 契约先于实现，能在建表时就看出设计是否自洽
#:
#: 代价是表名可能与最终设计不一致，所以测试
#: ``test_scope_specs_keys_are_real_tables`` 会要求它们出现在这里（而不是随便
#: 一个名字蒙混过关）。
PENDING_TABLES: Final[frozenset[str]] = frozenset(
    {
        # P1 的单据表
        "cutting_orders",
        "bundling_orders",
        "piecework_logs",
        "stock_ledgers",
        # T-BASE-002 已建（迁移 0005），从待建清单移出
    }
)

#: 有车间列但**不做**数据范围过滤的表，及豁免理由。
#: 显式列出而不是靠"没登记就算豁免" —— 后者会让人以为漏登记是安全的。
SCOPE_EXEMPT_TABLES: Final[dict[str, str]] = {
    # 角色可用车间是**角色配置**，本身就是"哪些车间可见"的定义。
    # 再按车间过滤它，超管以外的人就没法给自己配车间了。
    # 它的访问控制靠 system:role:manage 权限点，不靠数据范围。
    "role_workshops": "角色配置表，访问控制靠 system:role:manage 权限点",
    # append-only 台账，只进不出，不参与列表查询。
    "auth_refresh_tokens": "append-only 台账，无列表查询",
}

#: 常用资源的映射。**新增单据时必须在这里登记**，否则数据范围会静默失效。
SCOPE_SPECS: dict[str, ScopeSpec] = {
    # 单据类：车间 + 组别 + 制单人/归属人
    "cutting_orders": ScopeSpec(
        workshop_column="workshop_id", group_column=None, user_column="created_by"
    ),
    "bundling_orders": ScopeSpec(
        workshop_column="workshop_id", group_column=None, user_column="created_by"
    ),
    "piecework_logs": ScopeSpec(
        workshop_column="workshop_id", group_column="group_no", user_column="employee_id"
    ),
    "stock_ledgers": ScopeSpec(workshop_column=None, group_column=None, user_column="created_by"),
    # 组织类：按车间
    "users": ScopeSpec(workshop_column="workshop_id", group_column="group_no", user_column="id"),
    # 组别天然属于某个车间，车间范围必须过滤。
    # include_null_workshop 保持 False：workshop_groups.workshop_id 是 NOT NULL，
    # 根本不存在"空车间"的情况，写明这一点是为了让后来者知道这是有意的
    "workshop_groups": ScopeSpec(workshop_column="workshop_id"),
    # 主数据类：全厂共享，无车间概念；SELF 落到归属人
    "styles": ScopeSpec(user_column="merchandiser_id"),
    "customers": ScopeSpec(user_column="created_by"),
    # 工序的 workshop_id 可空 = 通用（§7.8.1），所以车间范围下通用工序人人可见
    "operations": ScopeSpec(workshop_column="workshop_id", include_null_workshop=True),
    "colors": ScopeSpec(),
    "sizes": ScopeSpec(),
}


def _column(model: type[Any], name: str | None) -> InstrumentedAttribute[Any] | None:
    """按名字取列；``spec`` 里为 ``None`` 表示该资源不支持这个范围。"""
    if name is None:
        return None
    return getattr(model, name, None)


def _restrict(
    stmt: Select[Any],
    column: InstrumentedAttribute[Any] | None,
    *values: object,
) -> Select[Any]:
    """加等值/IN 过滤；**列不存在则查不到任何数据**。

    这里必须是 ``where(False)`` 而不是"不加过滤"：列缺失时若放行，等于把
    全表返回给一个本该被限制的请求 —— 这是最典型的数据范围漏洞形态。
    """
    if column is None or not values:
        return stmt.where(false())
    return stmt.where(column.in_(values))


def _workshop_filter(
    stmt: Select[Any], model: type[Any], spec: ScopeSpec, ctx: AuthContext
) -> Select[Any]:
    """``WORKSHOP`` 范围的过滤，额外处理「车间为空 = 通用」的行。"""
    column = _column(model, spec.workshop_column)
    if column is None:
        return stmt.where(false())
    allowed = visible_workshops(ctx)
    if not allowed:
        return stmt.where(false())
    condition: ColumnElement[bool] = column.in_(allowed)
    if spec.include_null_workshop:
        # 通用行对所有人可见：车间范围的过滤不能把"全厂通用"的东西藏起来
        condition = or_(condition, column.is_(None))
    return stmt.where(condition)


def _one(value: object | None) -> tuple[object, ...]:
    """单值包成元组。

    ``None`` → 空元组 → :func:`_restrict` 走 ``where(false())``。
    不用"哨兵对象"占位：``column.in_(哨兵)`` 会把那个 Python 对象当绑定参数
    传给 asyncpg，直接报 ``expected str, got object``。
    """
    return () if value is None else (value,)


def apply_data_scope(
    stmt: Select[Any],
    model: type[Any],
    ctx: AuthContext,
    *,
    spec: ScopeSpec | None = None,
) -> Select[Any]:
    """给查询加数据范围过滤 + 软删过滤。

    :param stmt: 原始查询
    :param model: SQLAlchemy 模型
    :param ctx: :class:`app.core.permissions.AuthContext`
    :param spec: 列映射；``None`` 时按表名从 :data:`SCOPE_SPECS` 查
    :returns: 加了过滤条件的查询

    语义要点：
        - ``FACTORY`` 不加任何范围条件（少一次查询）
        - ``WORKSHOP`` 且可见车间为空 → ``where(False)``（查不到任何数据，
          **不能退化成"不加过滤"**，那等于全厂可见）
        - 资源不支持该范围（``spec`` 里对应列为 ``None``）→ 同样 ``where(False)``
    """
    table_name = getattr(model, "__tablename__", "")
    resolved = spec if spec is not None else SCOPE_SPECS.get(table_name)

    if resolved is not None:
        scope = ctx.data_scope
        if scope == DataScope.SELF:
            stmt = _restrict(stmt, _column(model, resolved.user_column), ctx.user_id)
        elif scope == DataScope.GROUP:
            # 组别范围必须同时有 workshop_id 与 group_no；缺任一都查不到数据
            stmt = _restrict(stmt, _column(model, resolved.workshop_column), *_one(ctx.workshop_id))
            stmt = _restrict(stmt, _column(model, resolved.group_column), *_one(ctx.group_no))
        elif scope == DataScope.WORKSHOP:
            stmt = _workshop_filter(stmt, model, resolved, ctx)

    # 软删过滤在此统一附加（INV-7），业务代码不必重复写
    if resolved is None or resolved.soft_delete:
        deleted_at = getattr(model, "deleted_at", None)
        if deleted_at is not None:
            stmt = stmt.where(deleted_at.is_(None))
    return stmt


def in_scope(obj: object, ctx: AuthContext, *, spec: ScopeSpec | None = None) -> bool:
    """判断单个对象是否在数据范围内（防越权按 ID 直查，docs/07 §3.2 铁律 2）。"""
    if ctx.data_scope == DataScope.FACTORY:
        return True

    table_name = getattr(type(obj), "__tablename__", "")
    resolved = spec if spec is not None else SCOPE_SPECS.get(table_name)
    if resolved is None:
        # 未登记映射的资源：按最严处理（SELF → 只认自己创建的）
        if ctx.data_scope == DataScope.SELF:
            return getattr(obj, "created_by", None) == ctx.user_id
        return False

    if ctx.data_scope == DataScope.SELF:
        if resolved.user_column is None:
            return False
        return getattr(obj, resolved.user_column, None) == ctx.user_id

    if ctx.data_scope == DataScope.GROUP:
        if resolved.workshop_column is None or resolved.group_column is None:
            return False
        return (
            getattr(obj, resolved.workshop_column, None) == ctx.workshop_id
            and getattr(obj, resolved.group_column, None) == ctx.group_no
        )

    # WORKSHOP
    column_value = (
        getattr(obj, resolved.workshop_column, None) if resolved.workshop_column else None
    )
    if resolved.include_null_workshop and column_value is None:
        return True  # 通用行（车间为空）人人可见
    if resolved.workshop_column is None:
        return False
    return column_value in visible_workshops(ctx)


def assert_in_scope(obj: object, ctx: AuthContext, *, spec: ScopeSpec | None = None) -> None:
    """不在范围内则抛 ``12002``。"""
    if in_scope(obj, ctx, spec=spec):
        return
    from app.core.errors import BusinessError, ErrorCode

    raise BusinessError(
        ErrorCode.DATA_SCOPE_DENIED,
        "该记录超出你的数据范围，如需查看请联系管理员调整数据范围",
    )
