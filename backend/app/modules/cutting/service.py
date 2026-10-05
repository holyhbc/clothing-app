"""裁剪单的**业务逻辑**与**事务边界**（docs/03 §1.3：service 是唯一的核心）。

## 本文件只做「草稿态」（T-CUT-001b-1）

**不做**状态机的任何动作（``submit`` / ``approve`` / …）。原因不是省事，而是
那些动作**依赖四张还不存在的表**（详见任务卡）：审核第 ① 步要写
``stock_ledger_lines``（**连字段表都没有**）、第 ④ 步要 ``bom_items``、
第 ⑦ 步要 ``cutting_outputs``、反审核要 ``bundles.counted_at``。

## 三条贯穿全文件的口径

### ① 三级汇总一律重算，**从不信任前端**（C6）

```
cutting_order_size_lines.output_qty     ← 唯一的出数权威来源
    ↓ Σ
cutting_order_line_colors.output_qty_total / balance_qty_total / hands_total
    ↓ Σ
cutting_order_lines.output_qty(用户录入的估算值) / balance_qty / fabric_qty / waste_qty
    ↓ Σ
cutting_orders.fabric_qty / output_qty / cut_waste_qty / balance_qty / hands_total
```

⚠️ **表头的五列在入参 Schema 里连字段都没有**（``CuttingOrderCreateIn``）——
传了会被 ``extra="forbid"`` 报 ``10001``，而不是「悄悄被忽略」。
**能被忽略的入参是最坏的一种**：前端以为设的值生效了。

### ② 取整口径：整数精确乘法，**不 floor**（C13 / C21 / ADR-0020）

``output_qty = hands × qty_per_hand``，两个都是 ``int``，所以**乘法本身就没有
小数**，``floor`` / ``round`` 在这里是**多余的代码**。⚠️ 一旦引入 ``floor``，
就等于允许 ``1.5 手`` 这种非法值悄悄通过（P0 的错误码 ``30006`` 就是为它准备的）。

### ③ 行余量：正向录入值 - 明细合计（C34 口径 A，业务确认 2026-10-04）

```
行 balance_qty = 行 output_qty（用户按铺布实耗正向录入）
                 - Σ(颜色 Σ尺码 output_qty)
```

⚠️ **不要**把行 ``output_qty`` 覆盖成明细合计 —— 那会让 ``balance_qty`` 恒为 0，
「行余量」这个概念整个消失。这正是本次修掉的原缺陷（``modules/02 §6`` ①
原写「服务端算」）。为负 → ``30002``。
"""

from collections.abc import Callable, Sequence
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import DocumentStatus
from app.core.db import unit_of_work
from app.core.errors import BusinessError, ErrorCode
from app.core.numbering import DOC_PREFIX_CUTTING, take_doc_no
from app.core.permissions import AuthContext
from app.core.scope import assert_in_scope
from app.modules.base.models import MaterialStock, Style
from app.modules.cutting.models import (
    CuttingEntryMode,
    CuttingOrder,
    CuttingOrderLine,
    CuttingOrderLineColor,
    CuttingOrderSizeLine,
)
from app.modules.cutting.repository import (
    OrderListQuery,
    count_orders,
    get_order_three_levels,
    list_orders,
)
from app.modules.cutting.schemas import CuttingOrderCreateIn

#: 数量零值。**用常量而不是字面量 ``0``**：三级汇总里 ``0`` 出现十几次，
#: 而写成常量后「这一处的 0 是件数还是米数」一眼可辨。
ZERO = Decimal("0")

__all__ = ["CuttingOrderService", "recalc_color", "recalc_line", "recalc_order"]


class CuttingOrderService:
    """裁剪单服务。**事务边界唯一入口**（docs/03 §1.4）。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---------------------------------------------------------------- 建单

    async def create(
        self, payload: CuttingOrderCreateIn, operator_id: UUID, ctx: AuthContext | None = None
    ) -> CuttingOrder:
        """建一张**草稿态**裁剪单（含三层明细）。

        ⚠️ **取号在事务内**（C1）。放事务外的后果：号被发出后事务回滚，
        那个号就永久跳过了 —— 而 C1 要求「生成即占用、永不复用」。

        :param ctx: 传了就校验建单人落在数据范围内（车间必须在可见车间内）。
            **不传则跳过校验** —— 内部调用方（seed / 测试）没有 AuthContext，
            而 :func:`app.core.scope.assert_in_scope` 拿不到 ctx 就没法判。
            ⚠️ 这个「可跳过」是刻意的，但**Router 必须传**（INV-8）。
        """
        # ⚠️ 必须用 `unit_of_work` 而不是 `session.begin()`（docs/03 §1.1 第 5 条）：
        #    前面的 SELECT 已触发 autobegin 时 `begin()` 会抛
        #    "A transaction is already begun"（base 模块踩过，13 个用例全红）
        async with unit_of_work(self.session):
            style = await self._assert_style_usable(payload.style_id)
            # ⚠️ **先取号再插单**：C1「生成即占用」，而取号与插入必须在**同一事务**
            #    —— 放事务外的话号被发出后事务回滚，那个号就永久跳过了。
            #    ⚠️ 反过来「先校验批次再取号」是有意的：批次不可用时报错，
            #    此时号还没消耗，不留空洞
            doc_no = await take_doc_no(
                self.session, prefix=DOC_PREFIX_CUTTING, doc_date=payload.doc_date
            )
            order = CuttingOrder(
                doc_no=doc_no,
                workshop_id=payload.workshop_id,
                style_id=style.id,
                style_no=style.style_no,
                color_codes="",  # ← 汇总算完才有值（它是 Σ 各行颜色）
                doc_date=payload.doc_date,
                delivery_date=payload.delivery_date,
                ply_count=payload.ply_count,
                entry_mode_default=payload.entry_mode_default,
                status=DocumentStatus.DRAFT,
                remark_source=payload.remark_source,
                remark=payload.remark,
                created_by=operator_id,
                updated_by=operator_id,
            )
            self.session.add(order)
            await self.session.flush()

            built = await self._insert_lines(order, payload, operator_id)
            # ⚠️ 汇总在**全部明细落库之后**算，且**自底向上**（颜色 → 行 → 头）：
            #    先算头等于算了三次空值，而 `recalc_order` 依赖行的 `balance_qty`
            for line, colors in built:
                recalc_line(line, colors)
            colors_by_line = {line.id: colors for line, colors in built}
            recalc_order(
                order,
                [line for line, _ in built],
                lambda line: colors_by_line[line.id],
            )
        return order

    # ---------------------------------------------------------------- 读

    async def get(self, order_id: UUID, ctx: AuthContext) -> CuttingOrder:
        """单据详情（三层结构）。

        ⚠️ ``apply_data_scope`` 在 repository 里，这里只做
        :func:`assert_in_scope` —— **详情按 ID 直查是越权的经典入口**
        （docs/07 §3.2 铁律 2）。顺序刻意是「先查存在性再判范围」：
        越权与不存在要报**不同的码**（``12002`` vs ``30001``），
        否则攻击者能靠错误码探测单据是否存在。
        """
        order = await get_order_three_levels(self.session, order_id)
        if order is None:
            raise BusinessError(ErrorCode.CUTTING_STATUS_NOT_ALLOWED, "裁剪单不存在或已删除")
        assert_in_scope(order, ctx)
        return order

    async def list_orders(
        self, query: OrderListQuery, ctx: AuthContext
    ) -> tuple[list[CuttingOrder], int]:
        """单据列表。**导出必须复用本方法**（docs/07 §3.2 铁律 3）。"""
        query.validate()
        return (
            await list_orders(self.session, ctx, query),
            await count_orders(self.session, ctx, query),
        )

    # ---------------------------------------------------------------- 私有

    async def _assert_style_usable(self, style_id: UUID) -> Style:
        """款号必须存在且启用（``08 §2.1`` 提交校验的第一条，草稿态就拦）。

        ⚠️ **在草稿态就拦**，而不是留到 ``submit``：让用户建完一整张单才发现
        款号被停用，是最难解释的一种失败 —— 而拦它的成本只是一次查询。
        """
        style = await self.session.get(Style, style_id)
        if style is None:
            raise BusinessError(ErrorCode.BASE_DATA_NOT_FOUND, "款号不存在，请先建款号档案")
        if not style.is_active:
            raise BusinessError(
                ErrorCode.BASE_DATA_REFERENCED, f"款号 {style.style_no} 已停用，不能开裁剪单"
            )
        if style.category_id is None:
            # ADR-0020 / C37：款号必须有商品分类，分类是取价的前提
            raise BusinessError(
                ErrorCode.PARAM_INVALID,
                f"款号 {style.style_no} 还没选商品分类，请先在款号档案里补上",
            )
        return style

    async def _insert_lines(
        self, order: CuttingOrder, payload: CuttingOrderCreateIn, operator_id: UUID
    ) -> list[tuple[CuttingOrderLine, list[CuttingOrderLineColor]]]:
        """落库三层明细，返回 ``[(行, 该行的颜色)]``。

        ⚠️ **返回对象图而不是 id**，是为了让汇总层（:func:`recalc_line` /
        :func:`recalc_order`）不必回查数据库。写入路径上刚 ``add`` 的行还没进过
        任何 SELECT，依赖 lazy load 会多出三次查询，而「汇总算错」是这一层
        最贵的 bug —— 让它能被单独测比让它跑得快重要。
        """
        built: list[tuple[CuttingOrderLine, list[CuttingOrderLineColor]]] = []
        for line_in in payload.lines:
            stock = await self._resolve_stock(line_in.stock_id)
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
                size_line_no=size_in.size_line_no,
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


# ---------------------------------------------------------------- 三级汇总（纯函数）


def recalc_color(color: CuttingOrderLineColor, size_lines: Sequence[CuttingOrderSizeLine]) -> None:
    """重算**颜色**层三个汇总 —— **纯内存函数，不碰 DB**。

    ⚠️ 子行**由调用方传入**而不是从 ``color.size_lines`` 取。理由：写入路径上
    刚 ``add`` 的行还没进过任何 SELECT，靠关系回读就多一次查询；而
    「汇总是最该能单独测的逻辑」—— 传进来之后它连 ORM session 都不需要。

    ⚠️ 三个 ``sum`` 都给 ``start=ZERO``：空列表时 ``sum()`` 返回 **``int`` 0**，
    而写成 ``sum(...) or ZERO`` 的类型是 ``int | Decimal``，mypy 会拦；
    更糟的是「空列表返回 int 0」会让该列的类型在有数据 / 无数据之间摇摆。
    """
    color.hands_total = sum((Decimal(row.hands) for row in size_lines), start=ZERO)
    color.output_qty_total = sum((Decimal(row.output_qty) for row in size_lines), start=ZERO)
    color.balance_qty_total = sum((Decimal(row.balance_qty) for row in size_lines), start=ZERO)


def recalc_line(line: CuttingOrderLine, colors: Sequence[CuttingOrderLineColor]) -> None:
    """重算**行**层的 ``balance_qty``（耗料与行出数**不重算**）。

    ⚠️ **只有 ``balance_qty`` 是算出来的**。``fabric_qty`` / ``waste_qty`` /
    ``output_qty`` 三列都是用户按铺布实耗正向录入的，服务端**不覆盖**
    （C34 口径 A / C35）。所以本函数的全部内容就是那一个减法 —— 而它的
    **负数检查是整张单最重要的一道业务校验**：它拦的是「你登记的可出件数
    比实际裁出来的件数还少」，那会让 ``cut_waste_qty`` 变成负数、
    损耗率报表彻底失去意义。
    """
    size_line_sum = sum((c.output_qty_total for c in colors), start=ZERO)
    line.balance_qty = line.output_qty - size_line_sum
    if line.balance_qty < 0:
        raise BusinessError(
            ErrorCode.CUTTING_QTY_CONFLICT,
            f"第 {line.line_no} 行：可出件数 {line.output_qty} 件装不下 "
            f"尺码明细合计 {size_line_sum} 件，请调整行可出件数或明细",
            details={
                "line_no": line.line_no,
                "line_output_qty": str(line.output_qty),
                "size_line_sum_qty": str(size_line_sum),
            },
        )


def recalc_order(
    order: CuttingOrder,
    lines: Sequence[CuttingOrderLine],
    colors_of: Callable[[CuttingOrderLine], Sequence[CuttingOrderLineColor]],
) -> None:
    """重算**表头**五列 + ``color_codes``（C6）。

    ⚠️ ``cut_waste_qty = Σ行 waste_qty + balance_qty``（C13「裁损**含**尾数」）。
    余量**不得再单独扣一次** —— 那是 C5 明确点名的经典错误。

    ⚠️ ``colors_of`` 是回调而不是直接读 ``line.colors``：见 :func:`recalc_color`
    的同款理由（写入路径上子集合未必已加载）。

    ⚠️ ``hands_total`` 是 ``int``（表头列是 ``integer``），所以逐项 ``int()``：
    ``hands_total`` 在颜色层是 ``numeric(14,4)``（比例之和**可以是小数**，
    ADR-0013 允许 1.5 手的建议值），而表头是整数 —— 直接把 ``Decimal``
    赋给 ``Mapped[int]`` 时 SQLAlchemy 不会替你转，它会把 ``Decimal('6')``
    塞进 ``integer`` 列，Postgres 接受（能隐式转），但**列的值与 Python 侧
    不一致**，后面比较就出鬼。
    """
    order.fabric_qty = sum((line.fabric_qty for line in lines), start=ZERO)
    order.output_qty = sum((line.output_qty for line in lines), start=ZERO)
    order.balance_qty = sum((line.balance_qty for line in lines), start=ZERO)
    order.cut_waste_qty = sum((line.waste_qty for line in lines), start=ZERO) + order.balance_qty
    order.hands_total = sum((int(c.hands_total) for line in lines for c in colors_of(line)), 0)
    order.color_codes = ",".join(sorted({c.color_code for line in lines for c in colors_of(line)}))
