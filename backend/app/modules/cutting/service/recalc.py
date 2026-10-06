"""三级汇总重算（原 service.py 978-1047）—— **REQ-000 核心复用资产**。

``recalc_color`` / ``recalc_line`` / ``recalc_order`` 是**纯函数**，不碰 DB，
供 ``bundling``（打菲单）以及后续 ``purchase`` / ``sales`` / ``stock`` 单据复用
（REQ-000 §2）。它们既可从包入口 ``from app.modules.cutting.service import
recalc_order`` 导入（稳定契约），也可走深路径
``from app.modules.cutting.service.recalc import recalc_order``（额外便利）。
"""

from collections.abc import Callable, Sequence
from decimal import Decimal

from app.core.errors import BusinessError, ErrorCode
from app.modules.cutting.models import (
    CuttingOrder,
    CuttingOrderLine,
    CuttingOrderLineColor,
    CuttingOrderSizeLine,
)

from .common import ZERO


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
