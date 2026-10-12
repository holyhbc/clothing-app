"""新建打菲单的**来源选择**（modules/03 §5.1 第 2 步「选来源裁剪单（只列 ``APPROVED``）」）。

## 为什么需要这个文件

建单时要选一张来源裁剪单，但此前**没有接口能列出候选**：

- ``GET /bundling-orders/{order_id}/available-outputs`` 收的是**打菲单 id**，
  且「款号不接受传入，一律取本单的 style_no」—— 新建表单上压根还没有打菲单 id，
  这条路走不通；
- ``GET /cutting-orders`` 要 ``cutting:read`` 权限 —— 打菲主管未必有这个权限
  （权限应跟着**动作**走，不是跟着对方模块走，见 07 §4）。

所以补两个只读端点，都挂在打菲侧、用 ``bundling:read``，**不动裁剪模块**。

## 两条硬边界

1. **只读**：不插码、不预占、不写 ``document_logs`` —— 与本模块 ``preview.py``
   同款理由：录入辅助一旦改了数据，它就从「看单」变成了「改单」。
2. **只列 ``APPROVED``**（B8 + §5.1 明写）。**不过滤零余量的单** ——
   沿用 ``available-outputs`` 已写明的原则「主管要看见『这个尺码一件都打不了』」，
   悄悄藏起来只会让人以为是系统没有这张单。

## 为什么候选单上**没有**余量字段

一开始想给候选单带上「该色可打多少」，查了数据模型才发现**算不出来**：
``CuttingOutput`` 按 **(款号 + 色 + 尺码)** 登记，**没有 ``cutting_order_id``** ——
也就是说可用量是款号/色/尺码级别的，多张同款同色的裁剪单**共享同一份余量**。

所以余量**不属于单张裁剪单**，硬算出来只会是个看着像、实则没有依据的数字
（照 B7 的做法「照抄前端会把错的值永久留在留痕里」，这里同理）。
余量放在 :meth:`SourceService.available_lines` —— 选定裁剪单之后再按尺码明细行给，
那才是余量真正成立的地方。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import DocumentStatus
from app.core.permissions import AuthContext
from app.modules.bundling.repository import (
    get_cutting_size_lines_by_cutting_order,
    list_available_outputs,
)
from app.modules.cutting.repository import OrderListQuery, list_orders

ZERO = Decimal("0")

# 候选单最多列这么多：正常一个款号的已审核裁剪单远少于这个数，
# 超过说明前端漏传了筛选条件，宁可截断也不要一次拖出整库。
MAX_SOURCE_ORDERS = 50


@dataclass(frozen=True)
class SourceOrder:
    """一张候选来源裁剪单。

    ⚠️ **刻意不带 ``available_qty``** —— 见模块 docstring。
    """

    id: UUID
    doc_no: str
    doc_date: str
    style_no: str
    workshop_id: UUID | None


@dataclass(frozen=True)
class SourceLine:
    """该裁剪单某个尺码下的可打菲来源明细（与 ``AvailableOutput`` 同口径）。"""

    cutting_size_line_id: UUID
    size_code: str
    hands: int
    qty_per_hand: Decimal
    output_qty: Decimal
    available_qty: Decimal


class SourceService:
    """来源裁剪单的选择与来源明细行查询（**只读**）。"""

    def __init__(self, session: AsyncSession, ctx: AuthContext) -> None:
        self.session = session
        self.ctx = ctx

    async def available_orders(
        self, style_no: str, *, workshop_id: UUID | None = None
    ) -> list[SourceOrder]:
        """候选来源裁剪单：``APPROVED`` + 款号匹配 + 在数据范围内。

        :param workshop_id: 传了只是**再过滤一次**，**不能放大范围**（07 §3.2 铁律 1）。
        """
        rows = await list_orders(
            self.session,
            self.ctx,
            OrderListQuery(
                status=DocumentStatus.APPROVED.value,
                style_no=style_no,
                workshop_id=workshop_id,
                size=MAX_SOURCE_ORDERS,
            ),
        )
        return [
            SourceOrder(
                id=row.id,
                doc_no=row.doc_no,
                doc_date=str(row.doc_date),
                style_no=row.style_no,
                workshop_id=row.workshop_id,
            )
            for row in rows
        ]

    async def available_lines(
        self, cutting_order_id: UUID, style_no: str, *, color_code: str
    ) -> list[SourceLine]:
        """某张来源裁剪单、某个色码下，每个尺码的可打菲来源明细。

        与 :meth:`PreviewMixin.available_outputs` 同一套口径，只是入口不同：
        那个以「已存在的打菲单」为根（取它的 style_no 与来源单），
        这个以「刚选中的裁剪单」为根 —— **新建表单上还没有打菲单**。
        """
        availability = {
            row.size_code: row.available_qty
            for row in await list_available_outputs(
                self.session, self.ctx, style_no=style_no, color_code=color_code
            )
        }
        size_lines = await get_cutting_size_lines_by_cutting_order(
            self.session, cutting_order_id, color_code=color_code
        )
        return [
            SourceLine(
                cutting_size_line_id=line.id,
                size_code=line.size_code,
                hands=line.hands,
                qty_per_hand=Decimal(line.qty_per_hand),
                output_qty=Decimal(line.output_qty),
                available_qty=availability.get(line.size_code, ZERO),
            )
            for line in size_lines
        ]


__all__ = ["MAX_SOURCE_ORDERS", "SourceLine", "SourceOrder", "SourceService"]
