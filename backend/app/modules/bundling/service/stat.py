"""打菲的**统计**与**导出**（T-BUND-007b / modules/03 §6）。

## 两个能力的边界

| 方法 | 端点 | 数据来源 | 数据范围 |
| --- | --- | --- | --- |
| :meth:`StatMixin.statistics` | ``GET /bundling-orders/statistics`` | **码**（按款号 × 尺码聚合） | ``apply_data_scope`` 的 ``via``（经单据回查车间） |
| :meth:`StatMixin.export_orders` | ``GET /bundling-orders/exports`` | **单据**列表（复用 ``list_orders``） | 同上，单据自身 |

⚠️ **统计按码、导出按单**，不是同一个报表的两种格式：前者回答「本月打了多少手、多少件、
计了多少」，后者回答「把符合条件的那批单子导出来给 Excel 对账」。把它们合成一个报表会让
「导出的行数」与「统计的行数」永远对不上。

## 导出必须复用列表 service（docs/07 §3.2 铁律 3）

另写一条导出查询的话，「列表看到的」与「导出的」会不一致，而那只有对账时才发现 ——
而且是那种「两个人各自核了一遍，都觉得自己没错」的差异。故本方法只做**取行 + 渲染
CSV**，筛选条件由同一个 :class:`~app.modules.bundling.repository.BundlingOrderListQuery`
承载。

## 统计的两个口径（写在代码里，免得后来的人重新发明）

1. **只数 ACTIVE 码**：``03 §3.3`` 的派生状态里 ``VOIDED`` 既不是「已计件」也不是
   「未计件」，算进任何一边都会让「手数 = 已计 + 未计」对不上。
2. **日期落在单据上**：码没有日期；按入库时间算的话，审核分批会把同一个月裂成两段。
"""

from __future__ import annotations

import csv
from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from io import StringIO
from typing import TYPE_CHECKING, Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessError, ErrorCode
from app.core.permissions import AuthContext
from app.modules.bundling.code_repository import StatQuery, StatRow, aggregate_bundle_stats
from app.modules.bundling.models import BundlingOrder
from app.modules.bundling.repository import MAX_PAGE_SIZE, BundlingOrderListQuery

ZERO: Final[Decimal] = Decimal("0")

#: 单次导出的行数上限（05 §9.1「单次上限，超出报 11011」）。
#: ⚠️ **必须报错而不是截断**：截断出来的 CSV 与列表对不上，用户会拿它当完整数据入账。
MAX_EXPORT_ROWS: Final[int] = 10_000

#: 导出的**中文表头**（顺序即列序）。
#: ⚠️ 与标签 CSV 同一理由：读 CSV 的是仓管与跟单，不是开发。
ORDER_CSV_HEADER: Final[tuple[str, ...]] = (
    "单据号",
    "单据日期",
    "款号",
    "工序号",
    "色组",
    "色码",
    "一扎几件",
    "手数",
    "件数",
    "余数",
    "状态",
)


@dataclass(frozen=True, slots=True)
class StatItem:
    """一行统计（款号 × 尺码）。``uncounted_hands`` 在这里减出来，不在 SQL 里。"""

    style_no: str
    size_code: str
    hands: int
    qty: Decimal
    counted_hands: int
    uncounted_hands: int


@dataclass(frozen=True, slots=True)
class StatTotals:
    """合计行。**由 items 汇总**而不是另跑一条 SQL —— 两个查询的过滤条件一旦不同步，
    表尾与明细的合计就对不上，而报表里没人会去核那个差。"""

    hands: int
    qty: Decimal
    counted_hands: int
    uncounted_hands: int


@dataclass(frozen=True, slots=True)
class Statistics:
    """统计结果。"""

    items: tuple[StatItem, ...]
    totals: StatTotals


class StatMixin:
    """统计与导出（由 :class:`~.bundling_order_service.BundlingOrderService` 组装）。"""

    session: AsyncSession

    if TYPE_CHECKING:
        # 导出**复用列表 service**（07 §3.2 铁律 3）：另写一条查询的话「列表看到的」与
        # 「导出的」会不一致，而那只有对账时才发现。运行期不 import 兄弟 Mixin。
        async def list_orders(
            self, query: BundlingOrderListQuery, ctx: AuthContext
        ) -> tuple[list[BundlingOrder], int]: ...

    # ================================================================== 统计（只读）

    async def statistics(self, query: StatQuery, ctx: AuthContext) -> Statistics:
        """按款号 × 尺码聚合手数 / 件数 / 已计手数 / 未计件手数（modules/03 §6）。

        ⚠️ **只读**：不开事务、不写库（与 :meth:`~.preview.PreviewMixin.preview_split` 同款）。
        数据范围由 repository 的 ``apply_data_scope`` 强制：车间主管统计到的只有本车间，
        传 ``workshop_id`` 也放大不了范围（07 §3.2 铁律 1）。
        """
        rows = await aggregate_bundle_stats(self.session, ctx, query)
        items = tuple(_to_item(row) for row in rows)
        return Statistics(items=items, totals=_sum(items))

    # ================================================================== 导出（只读）

    async def export_orders(self, query: BundlingOrderListQuery, ctx: AuthContext) -> str:
        """导出打菲单 **CSV 文本**（``GET /bundling-orders/exports``，modules/03 §6）。

        ⚠️ **翻页取完而不是把 ``size`` 调大**：列表 service 的 ``validate()`` 把 ``size``
        限在 200（``04 §5.1``），绕过它就得在导出路径上造第二份分页逻辑。翻页取的是**同一个**
        方法的同一份筛选条件，所以列表与导出不可能对不上。

        ⚠️ **超限报错不截断**（``11011``）：截断出来的 CSV 会被当完整数据入账。
        ⚠️ **不导出数据范围之外的单据**：导出是绕过界面直接拿数据的地方，比界面更容易泄露。
        """
        rows: list[BundlingOrder] = []
        page = 1
        while True:
            chunk, total = await self.list_orders(
                replace(query, page=page, size=MAX_PAGE_SIZE), ctx
            )
            assert_exportable(total)
            rows.extend(chunk)
            if len(rows) >= total or not chunk:
                return render_orders_csv(rows)
            page += 1


# ====================================================================== 导出（纯函数部分）


def _to_item(row: StatRow) -> StatItem:
    return StatItem(
        style_no=row.style_no,
        size_code=row.size_code,
        hands=row.hands,
        qty=row.qty,
        counted_hands=row.counted_hands,
        uncounted_hands=row.hands - row.counted_hands,
    )


def _sum(items: Sequence[StatItem]) -> StatTotals:
    return StatTotals(
        hands=sum(item.hands for item in items),
        qty=sum((item.qty for item in items), start=ZERO),
        counted_hands=sum(item.counted_hands for item in items),
        uncounted_hands=sum(item.uncounted_hands for item in items),
    )


def csv_row(order: BundlingOrder) -> tuple[str, ...]:
    """一行打菲单的 CSV 数据行（列序照 :data:`ORDER_CSV_HEADER`）。"""
    return (
        order.doc_no,
        order.doc_date.isoformat(),
        order.style_no,
        order.operation_no,
        order.color_group,
        order.color_code,
        str(order.bundle_qty),
        str(order.hands_total),
        str(Decimal(order.output_qty)),
        str(Decimal(order.balance_qty)),
        order.status.value,
    )


def render_orders_csv(rows: Sequence[BundlingOrder]) -> str:
    """渲染成 CSV 文本（1 行表头 + 每单 1 行；**内存里渲染，不落盘**）。

    ⚠️ 数量按原精度出参（``numeric(14,3)``），不四舍五入 —— 导出的数字与界面上的必须
    一致，Excel 里再格式化。
    """
    buffer = StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(ORDER_CSV_HEADER)
    for row in rows:
        writer.writerow(csv_row(row))
    return buffer.getvalue()


def assert_exportable(total: int) -> None:
    """导出行数超上限 → ``11011``（05 §9.1）。

    ⚠️ **报错而不是截断**：截断出来的 CSV 与列表对不上，而它会被当完整数据入账。
    """
    if total > MAX_EXPORT_ROWS:
        raise BusinessError(
            ErrorCode.EXPORT_RANGE_TOO_LARGE,
            f"符合条件的单据有 {total} 条，超过单次导出上限 {MAX_EXPORT_ROWS} 条，"
            "请缩小日期或款号范围",
            details={"total": total, "limit": MAX_EXPORT_ROWS},
        )


__all__ = [
    "MAX_EXPORT_ROWS",
    "ORDER_CSV_HEADER",
    "StatItem",
    "StatMixin",
    "StatTotals",
    "Statistics",
    "assert_exportable",
    "csv_row",
    "render_orders_csv",
]
