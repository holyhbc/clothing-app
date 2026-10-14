"""计件只读查询的 SQL（``docs/modules/04-计件.md`` §6.1 的 5 个读端点）。

## 为什么与 :mod:`.repository` 分开

:mod:`.repository` 是**写路径**的 SQL（锁码 / 插流水 / 回写），它的每一条都跟着
「不这么做会出事」的并发论证；而这里全是**读**，论证是「少 JOIN 一次少一次往返」。
两者混在一个文件里，写路径那几条最重要的语句会被读的淹没。

## 三条反复出现的口径

1. **手数与件数并列**（ADR-0016 §6、BR-PW-31）：``COUNT(*)`` 是手数、
   ``SUM(qty)`` 是件数。汇总与工位机今日卡**都要两个**，
   只给件数的话主管看到的是「做完了 3600 件」而不是「做了 60 手」——
   而车间真正在意的单位是手（责任单元是手，ADR-0016 决策 3）。
2. **结算标记是 ``payroll_id IS NULL``**，不是布尔列（04 §7.1 要点）。
   两个来源必然漂移：结算回写是 service 行为，不允许手工填 ``is_settled``。
3. **数据范围一律由 service 调** :func:`app.core.scope.apply_data_scope` **注入**
   （07 §3.2 铁律 1）。本模块**不写任何车间 / 组别 / 本人过滤** ——
   写了就会出现「两份实现」，而越权缺陷恰恰是「改了一处、漏了另一处」那种。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import FromClause

from app.modules.auth.models import User
from app.modules.base.models import Color, Size, Workshop
from app.modules.bundling.models import Bundle
from app.modules.piecework.models import PieceworkLog
from app.modules.piecework.schemas import LogQuery

#: 列表的联表列（全部 LEFT OUTER JOIN，理由见 :func:`build_list_stmt`）。
_JOIN_COLUMNS: tuple[Any, ...] = (
    Bundle.color_code,
    Bundle.size_code,
    User.name.label("employee_name"),
    User.employee_no.label("employee_no"),
    PieceworkLog.payroll_id.is_not(None).label("is_settled"),
    Bundle.hands.label("hands"),
    Bundle.bundle_qty.label("bundle_qty"),
    Bundle.counted_qty.label("counted_qty"),
)

#: 列表列清单（顺序即响应字段顺序）。
_LIST_COLUMNS: tuple[Any, ...] = (
    PieceworkLog.id,
    PieceworkLog.bundle_no,
    PieceworkLog.style_no,
    PieceworkLog.operation_no,
    PieceworkLog.workshop_id,
    PieceworkLog.employee_id,
    PieceworkLog.group_no,
    PieceworkLog.work_date,
    PieceworkLog.qty,
    PieceworkLog.unit_price,
    PieceworkLog.amount,
    PieceworkLog.log_type,
    PieceworkLog.payroll_id,
    PieceworkLog.reversal_of_id,
    *_JOIN_COLUMNS,
)

#: **排序白名单**（``sort_by`` 的唯一合法取值）。
#:
#: ⚠️ 必须是白名单而不是直接拼列名：``sort_by`` 来自 query string，
#: 直接 ``getattr(PieceworkLog, sort_by)`` 等于把任意属性名拼进 SQL
#: （``.`` / ``(`` 都能进来）。抄 ``base`` 的 ``STYLE_SORT_WHITELIST`` 同一个理由。
#: 值是**属性名**（不是列对象），由 :func:`_order` 用 ``getattr`` 取 ——
#: 与 ``base`` 的 ``STYLE_SORT_WHITELIST`` 同一个形状：白名单是「字符串 → 字符串」，
#: 所以即便将来加错了名字，落到 SQL 里的也只会是一个 ``InstrumentedAttribute``。
SORT_WHITELIST: dict[str, str] = {
    "work_date": "work_date",
    "employee_no": "employee_no",
    "style_no": "style_no",
    "amount": "amount",
    "bundle_no": "bundle_no",
}


def _log_columns() -> tuple[Any, ...]:
    """详情要带的关联流水列（红冲 ↔ 原单 / 原单 ↔ 红冲）。"""
    return (
        PieceworkLog.id,
        PieceworkLog.bundle_no,
        PieceworkLog.style_no,
        PieceworkLog.operation_no,
        PieceworkLog.employee_id,
        PieceworkLog.workshop_id,
        PieceworkLog.group_no,
        PieceworkLog.work_date,
        PieceworkLog.bundle_id,
        PieceworkLog.qty,
        PieceworkLog.unit_price,
        PieceworkLog.amount,
        PieceworkLog.log_type,
        PieceworkLog.payroll_id,
        PieceworkLog.reversal_of_id,
        PieceworkLog.remark,
        PieceworkLog.scanned_at,
        PieceworkLog.created_at,
    )


def _from_joined() -> FromClause:
    """列表 / 导出 / 汇总共用的 FROM + JOIN。

    ⚠️ **颜色名 / 尺码名不在这里 JOIN**，只 JOIN ``bundles`` 与 ``users``（1:1）。
    原因是 ``sizes`` 的唯一键是 ``(size_code, size_class)`` —— **同一个 ``size_code``
    可以合法地存在于多个尺码类**（``XL`` 在上衣类与裤装类各一行）。按 ``size_code``
    单列 JOIN 会让每一行**翻成两行**，而症状是「列表总数是实际的两倍、同一条流水
    出现两次」—— 看起来像分页坏了或重复写入，真因是 JOIN 把行乘了出来。
    ``colors`` 同理（本次虽未观察到重复，但不假设）。
    名字改走 :func:`fetch_size_color_names` 的**批量 IN 查询**（与车间名同一个套路），
    并且是「按码取一个名字」，绝不参与行数乘法。

    ⚠️ ``Bundle`` 与 ``User`` 保持 LEFT JOIN 而不是 INNER：字典 / 用户行可能被停用，
    而流水的 ``bundle_no`` / ``style_no`` 是**快照语义** —— 内连接会让
    「当时叫『本白』、现在字典改名了」的那一行从报表里消失，而流水是工资依据。
    """
    return PieceworkLog.__table__.join(
        Bundle, Bundle.id == PieceworkLog.bundle_id, isouter=True
    ).join(User, User.id == PieceworkLog.employee_id, isouter=True)


def build_list_stmt(query: LogQuery, *, limit: int, offset: int) -> Select[Any]:
    """``GET /piecework-logs`` 列表。**不含数据范围与软删过滤**（由 service 注入）。

    ⚠️ ``bundle_no`` **完整返回、不截断**（BR-PW-30）：码是追溯主路径，
    前端要拿它去扫回那一张单，截断过的码扫不回来。
    """
    # ⚠️ 列清单放在一个常量里（而不是 ``select(A, B, *_JOIN_COLUMNS)``）：
    #   后者会让 mypy 把返回类型算成 ``Select[UUID, str, ...]`` 那样的一长串精确形状，
    #   每加一列就得改一次签名 —— 而「加一列」本该只改这一个常量。
    return (
        select(*_LIST_COLUMNS)
        .select_from(_from_joined())
        .where(*_filters(query))
        .order_by(*_order(query))
        .limit(limit)
        .offset(offset)
    )


def build_count_stmt(query: LogQuery) -> Select[int]:
    """总数。⚠️ 与列表**共用** :func:`_filters` —— 少写一个条件就是「翻到第 2 页
    变空」那种看起来像分页坏了的现象。"""
    return select(func.count(PieceworkLog.id)).select_from(_from_joined()).where(*_filters(query))


def build_summary_stmt(query: LogQuery, group_by: str) -> Select[Any]:
    """``GET /piecework/summary`` 汇总。

    ⚠️ **手数与件数并列**（BR-PW-31、ADR-0016 §6）：``total_hands = COUNT(*)``、
    ``total_qty = SUM(qty)``、``total_amount = SUM(amount)``。
    ``total_amount`` 天然是**净额**（红冲取负值，``ck_piecework_logs_sign`` 钉死），
    所以「汇总与明细相加对不上」在红冲场景下不是 bug。
    """
    dimension = _dimension_columns(group_by)
    return (
        select(
            *dimension,
            # ⚠️ 三个聚合**必须显式 label**：不 label 时 SQLAlchemy 生成的名字是
            #   ``count_1`` / ``sum_1`` / ``sum_2`` 之类，而服务层按 ``row["hands"]``
            #   取值 —— 名字一漂移就是 KeyError，且只在 SQL 层看得见。
            func.count(PieceworkLog.id).label("hands"),
            func.sum(PieceworkLog.qty).label("qty_sum"),
            func.sum(PieceworkLog.amount).label("amount_sum"),
        )
        .select_from(_from_joined())
        .where(*_filters(query))
        .group_by(*dimension)
        .order_by(func.sum(PieceworkLog.amount).desc())
    )


def _dimension_columns(group_by: str) -> tuple[Any, ...]:
    if group_by == "style":
        # ⚠️ 按款号分组时**带颜色**（§6.1 的示例），颜色名走 ``fetch_size_color_names``
        #    而不是 JOIN（见 :func:`_from_joined` 的注）。
        return (PieceworkLog.style_no, Bundle.color_code)
    if group_by == "operation":
        return (PieceworkLog.operation_no,)
    return (
        PieceworkLog.employee_id,
        User.employee_no,
        User.name.label("employee_name"),
        PieceworkLog.group_no,
    )


def build_station_today_stmt(station_no: str, work_date: date) -> Select[int, Decimal, Decimal]:
    """``GET /piecework/stations/{station_no}/today``。

    ⚠️ **按 ``work_date`` 而非 ``scanned_at`` 的日期区间**（BR-PW-11）：
    ``work_date`` 是业务日（Asia/Shanghai 推导），跨零点扫码要算前一天 ——
    用 ``scanned_at`` 会让「晚上 23:58 扫的」在第二天早上查不到。
    """
    return (
        select(
            func.count(PieceworkLog.id),
            func.coalesce(func.sum(PieceworkLog.qty), 0),
            func.coalesce(func.sum(PieceworkLog.amount), 0),
        )
        .select_from(PieceworkLog)
        .where(
            PieceworkLog.source_station == station_no,
            PieceworkLog.work_date == work_date,
            PieceworkLog.deleted_at.is_(None),
        )
    )


def build_station_last_stmt(station_no: str) -> Select[str, int, datetime]:
    """该工位最近一次扫码（``last_bundle_no`` / ``last_hands`` / ``last_scanned_at``）。"""
    return (
        select(PieceworkLog.bundle_no, Bundle.hands, PieceworkLog.scanned_at)
        .select_from(
            PieceworkLog.__table__.join(Bundle, Bundle.id == PieceworkLog.bundle_id, isouter=True)
        )
        .where(PieceworkLog.source_station == station_no, PieceworkLog.deleted_at.is_(None))
        .order_by(PieceworkLog.scanned_at.desc())
        .limit(1)
    )


def _detail_columns() -> tuple[Any, ...]:
    return (*_log_columns(), *_JOIN_COLUMNS, Bundle.cutting_size_line_id)


def build_detail_stmt(log_id: UUID) -> Select[Any]:
    """``GET /piecework-logs/{log_id}``：流水本体 + 码信息。

    ⚠️ **不 JOIN colors / sizes**（同 :func:`_from_joined` 的理由：``sizes`` 的唯一键是
    ``(size_code, size_class)``，按码单列 JOIN 会把一行翻成两行 → ``one_or_none``
    直接抛 ``MultipleResultsFound``）。名字由服务层批量取。
    """
    return (
        select(*_detail_columns())
        .select_from(
            PieceworkLog.__table__.join(
                Bundle, Bundle.id == PieceworkLog.bundle_id, isouter=True
            ).join(User, User.id == PieceworkLog.employee_id, isouter=True)
        )
        .where(PieceworkLog.id == log_id)
    )


def build_related_logs_stmt(log_id: UUID) -> Select[Any]:
    """红冲 ↔ 原单（``reversal_of`` 与 ``reversed_by`` 两条）。"""
    return (
        select(*_log_columns())
        .select_from(PieceworkLog)
        .where(
            or_(
                PieceworkLog.reversal_of_id == log_id,
                PieceworkLog.id
                == select(PieceworkLog.reversal_of_id)
                .where(PieceworkLog.id == log_id)
                .scalar_subquery(),
            ),
            PieceworkLog.deleted_at.is_(None),
        )
    )


def build_bundle_stmt(
    bundle_id: UUID | None,
) -> Select[int, Decimal, Decimal, UUID | None, UUID, str] | None:
    if bundle_id is None:
        return None
    return (
        select(
            Bundle.hands,
            Bundle.bundle_qty,
            Bundle.counted_qty,
            Bundle.counted_by,
            Bundle.cutting_size_line_id,
            User.name.label("counted_by_name"),
        )
        .select_from(Bundle.__table__.outerjoin(User, User.id == Bundle.counted_by))
        .where(Bundle.id == bundle_id)
    )


async def fetch_workshop_names(session: AsyncSession, ids: set[UUID]) -> dict[UUID, str]:
    """导出用：车间 id → 名称。⚠️ 单独一次 ``IN`` 查询而不是每行一次 ——
    导出 500 行时那是 500 次往返，在 2C VPS 上要好几秒。"""
    if not ids:
        return {}
    rows = (
        await session.execute(select(Workshop.id, Workshop.name).where(Workshop.id.in_(ids)))
    ).all()
    return {row[0]: row[1] for row in rows}


async def fetch_size_color_names(
    session: AsyncSession, *, size_codes: set[str], color_codes: set[str]
) -> tuple[dict[str, str], dict[str, str]]:
    """颜色码 / 尺码码 → 显示名。**按码取一个名字**（见 :func:`_from_joined` 的注）。

    ⚠️ ``(size_code, size_class)`` 才是 ``sizes`` 的唯一键，同一码多行时这里取
    ``sort_order`` 最小的一行 —— 确定性优先，显示名只是个便利。
    行的**数量口径**绝不能依赖这个名字（那是 JOIN 会犯的错）。
    """
    sizes: dict[str, str] = {}
    if size_codes:
        rows = (
            await session.execute(
                select(Size.size_code, Size.name)
                .where(Size.size_code.in_(size_codes))
                .order_by(Size.size_code, Size.sort_order)
            )
        ).all()
        for code, name in rows:
            sizes.setdefault(code, name)
    colors: dict[str, str] = {}
    if color_codes:
        rows = (
            await session.execute(
                select(Color.color_code, Color.name).where(Color.color_code.in_(color_codes))
            )
        ).all()
        for code, name in rows:
            colors.setdefault(code, name)
    return sizes, colors


def _filters(query: LogQuery) -> tuple[ColumnElement[bool], ...]:
    """筛选条件。⚠️ 与列表 / 导出 / 汇总**共用**同一份 —— 三者不一致是对账时才发现的。"""
    conds: list[ColumnElement[bool]] = [PieceworkLog.deleted_at.is_(None)]
    if query.work_date_from is not None:
        conds.append(PieceworkLog.work_date >= query.work_date_from)
    if query.work_date_to is not None:
        conds.append(PieceworkLog.work_date <= query.work_date_to)
    for attr in ("style_no", "operation_no", "bundle_no", "group_no"):
        value = getattr(query, attr, None)
        if value:
            conds.append(getattr(PieceworkLog, attr) == value)
    if getattr(query, "workshop_id", None) is not None:
        conds.append(PieceworkLog.workshop_id == query.workshop_id)
    if getattr(query, "employee_id", None) is not None:
        conds.append(PieceworkLog.employee_id == query.employee_id)
    if query.log_type is not None:
        conds.append(PieceworkLog.log_type == query.log_type)
    if getattr(query, "is_settled", None) is not None:
        # ⚠️ 判据是 payroll_id（04 §7.1），不是某个布尔列
        conds.append(
            and_(PieceworkLog.payroll_id.is_not(None))
            if query.is_settled
            else and_(PieceworkLog.payroll_id.is_(None))
        )
    return tuple(conds)


def _order(query: LogQuery) -> tuple[ColumnElement[Any], ...]:
    """默认排序 ``-work_date, employee_id``（§6.1）。

    ⚠️ **默认带 ``employee_id`` 兜底**：同一天同一个人的流水顺序不稳定时，
    翻页会出现「同一行在第 1 页和第 2 页各出现一次」—— 那不是分页坏了，
    是排序键不唯一，而用户看到的症状就是「重复了 / 少了」。
    """
    name = query.sort or "-work_date"
    if name not in SORT_WHITELIST:
        return (PieceworkLog.work_date.desc(), PieceworkLog.employee_id.asc())
    # ⚠️ ``employee_no`` 排序排在**联表的 users** 上，所以查白名单要分两处 ——
    #   写成一张字典的话，就得把两张表的列混在一起，而「这个键属于哪张表」
    #   正是白名单要保证的那件事。
    source: Any = User if name == "employee_no" else PieceworkLog
    column: ColumnElement[Any] = getattr(source, SORT_WHITELIST[name])
    return (
        (column.desc(), PieceworkLog.employee_id.asc())
        if name.startswith("-")
        else (column.asc(), PieceworkLog.employee_id.asc())
    )


__all__ = [
    "SORT_WHITELIST",
    "build_bundle_stmt",
    "build_count_stmt",
    "build_detail_stmt",
    "build_list_stmt",
    "build_related_logs_stmt",
    "build_station_last_stmt",
    "build_station_today_stmt",
    "build_summary_stmt",
    "fetch_size_color_names",
    "fetch_workshop_names",
]
