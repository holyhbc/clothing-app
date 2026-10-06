"""明细 / 颜色 / 尺码落库与布批校验（原 service.py 672-717、817-975）。

:class:`InsertMixin` 承载所有「新建子行」的方法：``_insert_lines`` /
``_insert_color`` / ``_insert_size_line`` 与它们依赖的 ``_resolve_stock`` /
``_assert_fabric_within_available`` / ``_next_size_line_no``。这些方法**互不依赖
兄弟 Mixin**，故无需 ``TYPE_CHECKING`` 前置声明。
"""

from collections.abc import Sequence
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessError, ErrorCode
from app.modules.base.models import MaterialStock
from app.modules.cutting.models import (
    CuttingEntryMode,
    CuttingOrder,
    CuttingOrderLine,
    CuttingOrderLineColor,
    CuttingOrderSizeLine,
)
from app.modules.cutting.schemas import OrderLineIn

from .common import ZERO
from .recalc import recalc_color


class InsertMixin:
    """三层明细的落库原语与单行布批校验。"""

    session: AsyncSession

    async def _insert_size_line(
        self,
        color: CuttingOrderLineColor,
        draft: object,
        operator_id: UUID,
        already_created: Sequence[CuttingOrderSizeLine],
    ) -> CuttingOrderSizeLine:
        """插一条尺码明细，行号省略时由服务端分配。"""
        computed = draft.hands * draft.qty_per_hand  # type: ignore[attr-defined]
        manual = draft.output_qty is not None and draft.output_qty != computed  # type: ignore[attr-defined]
        row = CuttingOrderSizeLine(
            line_color_id=color.id,
            # ⚠️ **冗余外键**：打菲 / 计件按布批反查（04 §5 要求它建索引）
            line_id=color.line_id,
            size_line_no=draft.size_line_no  # type: ignore[attr-defined]
            or self._next_size_line_no(color, already_created),
            size_code=draft.size_code,  # type: ignore[attr-defined]
            hands=draft.hands,  # type: ignore[attr-defined]
            qty_per_hand=draft.qty_per_hand,  # type: ignore[attr-defined]
            output_qty=draft.output_qty if manual else computed,  # type: ignore[attr-defined]
            output_qty_manual=manual,
            balance_qty=max(computed - draft.output_qty, 0) if manual else 0,  # type: ignore[attr-defined]
            hands_seq=draft.hands_seq,  # type: ignore[attr-defined]
            remark=draft.remark,  # type: ignore[attr-defined]
            created_by=operator_id,
            updated_by=operator_id,
        )
        self.session.add(row)
        return row

    @staticmethod
    def _next_size_line_no(
        color: CuttingOrderLineColor, already_created: Sequence[CuttingOrderSizeLine]
    ) -> int:
        """分配尺码明细行号 = **max(现存与本次新建) + 1**。

        ⚠️ **必须是 max + 1 而不是 count + 1**：软删的行不计入 ``count``，
        所以一张「原本 3 行、删了 2 行、现在还有 1 行」的明细表，``count`` 是 1、
        下一个号该是 4 —— 用 ``count + 1`` 会发出 2，与软删行撞号。

        ⚠️ 锁由调用方负责（``get_line_color_for_update`` 已锁住该颜色行）——
        ``modules/02 §7``「在锁住该颜色行的前提下分配」正是防两个并发请求拿到同一个号。
        """
        candidates = [row.size_line_no for row in color.size_lines if row.deleted_at is None]
        candidates += [row.size_line_no for row in already_created]
        return (max(candidates) if candidates else 0) + 1

    async def _insert_lines(
        self, order: CuttingOrder, lines: Sequence[OrderLineIn], operator_id: UUID
    ) -> list[tuple[CuttingOrderLine, list[CuttingOrderLineColor]]]:
        """落库三层明细，返回 ``[(行, 该行的颜色)]``。

        ⚠️ **返回对象图而不是 id**，是为了让汇总层（:func:`recalc_line` /
        :func:`recalc_order`）不必回查数据库。写入路径上刚 ``add`` 的行还没进过
        任何 SELECT，依赖 lazy load 会多出三次查询，而「汇总算错」是这一层
        最贵的 bug —— 让它能被单独测比让它跑得快重要。
        """
        built: list[tuple[CuttingOrderLine, list[CuttingOrderLineColor]]] = []
        for line_in in lines:
            stock = await self._resolve_stock(line_in.stock_id)
            self._assert_fabric_within_available(stock, line_in.fabric_qty)
            line = CuttingOrderLine(
                doc_id=order.id,
                line_no=line_in.line_no,
                stock_id=stock.id,
                # ⚠️ 快照列**从 stock 反查**，不接受前端传 —— 两个来源会互相矛盾
                supplier_id=stock.supplier_id,
                material_id=stock.material_id,
                style_no=order.style_no,
                dye_lot_no=stock.dye_lot_no,
                bolt_no=stock.bolt_no,
                color_plan=line_in.color_plan,
                width_cm=line_in.width_cm if line_in.width_cm is not None else stock.width_cm,
                fabric_qty=line_in.fabric_qty,
                waste_qty=line_in.waste_qty,
                # ★ 正向录入的估算值，**不覆盖**（C34 口径 A）
                output_qty=line_in.output_qty,
                remark=line_in.remark,
                created_by=operator_id,
                updated_by=operator_id,
            )
            self.session.add(line)
            await self.session.flush()
            colors = [
                await self._insert_color(line, color_in, operator_id) for color_in in line_in.colors
            ]
            await self.session.flush()
            built.append((line, colors))
        return built

    @staticmethod
    def _assert_fabric_within_available(stock: MaterialStock, fabric_qty: Decimal) -> None:
        """行耗料不得超过该布批的可用量（C38 / ADR-0022 → ``40006``）。

        ⚠️ **按 ``available_qty = stock_qty - locked_qty`` 判**，不是 ``stock_qty``
        （C16 明确）。差额就是「已被别处锁住的那部分」—— 按 ``stock_qty`` 判会让
        用户以为能裁一匹已经被另一张单预留的布，而真正扣料时才发现超了。

        ⚠️ **这里只校验单行**。跨行 / 跨单的**累计**占用（C36：同一缸布可被多行
        占用，但累加不得超可用量）必须等审核时的条件 UPDATE 才能判 ——
        那时才是真正扣料的地方，也是 b-3 的活。草稿态只看单行，是为了让用户
        在录入时就发现「这匹布不够」而不是填完一整张单。

        ⚠️ 边界是**严格大于**：``fabric_qty == available_qty`` 要放行。
        """
        available = (stock.stock_qty or ZERO) - (stock.locked_qty or ZERO)
        if fabric_qty > available:
            raise BusinessError(
                ErrorCode.BATCH_STOCK_INSUFFICIENT,
                f"缸号 {stock.dye_lot_no} 匹号 {stock.bolt_no} 可用 {available} 米，"
                f"本次登记耗料 {fabric_qty} 米，不够。请换一匹布或调整耗料。",
                details={
                    "stock_id": str(stock.id),
                    "dye_lot_no": stock.dye_lot_no,
                    "bolt_no": stock.bolt_no,
                    "available_qty": str(available),
                    "fabric_qty": str(fabric_qty),
                },
            )

    async def _resolve_stock(self, stock_id: UUID) -> MaterialStock:
        """按 ``stock_id`` 取布批行（ADR-0022：级联选料，不允许自由输入缸号）。

        ⚠️ 这一条就是「级联选料」的落地点：请求里**只有** ``stock_id``，
        缸号 / 匹号 / 物料 / 供应商 / 门幅全部从它反查 ——
        所以「缸号与匹号对不上」「门幅填了别的批次的」这类错误**结构上不可能发生**。
        """
        stock = await self.session.get(MaterialStock, stock_id)
        if stock is None or stock.deleted_at is not None:
            raise BusinessError(ErrorCode.BASE_DATA_NOT_FOUND, "所选布批不存在或已停用，请重新选批")
        return stock

    async def _insert_color(
        self, line: CuttingOrderLine, color_in: object, operator_id: UUID
    ) -> CuttingOrderLineColor:
        """落库行内颜色 + 其尺码明细（ADR-0017 第 2 层）。

        ⚠️ ``entry_mode_changed_*`` **不在这里写**：那是「模式被切换」时的留痕
        （C27 / C28），而建单时的初值不是「切换」。初始就是 ``entry_mode`` 本身。
        """
        color = CuttingOrderLineColor(
            line_id=line.id,
            color_code=color_in.color_code,  # type: ignore[attr-defined]
            entry_mode=color_in.entry_mode,  # type: ignore[attr-defined]
            qty_per_hand=color_in.qty_per_hand,  # type: ignore[attr-defined]
            uniform_qty=color_in.uniform_qty,  # type: ignore[attr-defined]
            # ⚠️ ratio_snapshot **不在建单时写** —— 那是「按比例带出」那条路径
            #    （suggest-lines）的事，且 C29 铁律：改行绝不写回比例主数据。
            #    建单时无条件写空快照会让人误以为「快照了但没内容」
            hands_total=ZERO,
            output_qty_total=ZERO,
            balance_qty_total=ZERO,
            created_by=operator_id,
            updated_by=operator_id,
        )
        self.session.add(color)
        await self.session.flush()
        # ⚠️ **显式收集**建好的尺码明细，不靠 ``color.size_lines`` 回读 ——
        #    写入路径上依赖 lazy load 会多一次 SELECT，而汇总逻辑是最该能
        #    单独测的部分（见 recalc_color 的 docstring）
        created_size_lines: list[CuttingOrderSizeLine] = []
        for size_in in color_in.size_lines:  # type: ignore[attr-defined]
            computed = size_in.hands * size_in.qty_per_hand
            manual = size_in.output_qty is not None and size_in.output_qty != computed
            row = CuttingOrderSizeLine(
                line_color_id=color.id,
                line_id=line.id,
                # ⚠️ ★ **省略时按「该颜色已建的最大行号 + 1」分配**（T-CUT-001c-4 抓到）：
                #   `SizeLineIn.size_line_no` 的契约是「省略则服务端分配」，而这一条
                #   路径**没有分配** —— 直接把 `None` 交给 ORM 就是
                #   `NotNullViolationError` → 500。`PUT /size-lines` 那条路径一直有
                #   分配（`_next_size_line_no`），于是「同一个字段在两个端点上语义不同」，
                #   而**契约只写了一次**。
                #   而前端整树提交时正是「省略号」最自然的选择 —— 不写号就不用管重排。
                #   ⚠️ 这里**不能**调 `_next_size_line_no(color, ...)`：它会遍历
                #   `color.size_lines`，而这条路径上的 color 是**刚 flush 的新行**、
                #   那个集合**没有加载** → 惰性加载在 async 里就是 `MissingGreenlet`。
                #   本路径的颜色是新建的、没有任何既有行，所以「已建最大值 + 1」
                #   等价于「本次已建条数 + 1」。
                size_line_no=size_in.size_line_no
                or max((row.size_line_no for row in created_size_lines), default=0) + 1,
                size_code=size_in.size_code,
                hands=size_in.hands,
                qty_per_hand=size_in.qty_per_hand,
                output_qty=size_in.output_qty if manual else computed,
                output_qty_manual=manual,
                # ⚠️ 差额只在**人工指定**时产生，且符号固定为「算出来的 - 人工给的」。
                #    正数 = 裁多了（零头），进 cut_waste_qty；人工给得比算出来多时
                #    取 0（多出来的那部分是**超出铺布能力**的，按 C34 由行余量去管）
                balance_qty=max(computed - size_in.output_qty, 0) if manual else 0,
                hands_seq=size_in.hands_seq,
                remark=size_in.remark,
                created_by=operator_id,
                updated_by=operator_id,
            )
            created_size_lines.append(row)
            self.session.add(row)
        await self.session.flush()
        recalc_color(color, created_size_lines)
        # ⚠️ C28：人工指定过出数的颜色**自动转 MANUAL** —— 否则它在 MASTER 模式下
        #    会显得「按比例带出来的」，而下一次编辑时比例会覆盖人工的判断
        if any(row.output_qty_manual for row in created_size_lines) and (
            color.entry_mode != CuttingEntryMode.MANUAL
        ):
            color.entry_mode = CuttingEntryMode.MANUAL
        return color
