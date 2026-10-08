"""打菲**统计**的出参（T-BUND-007b / modules/03 §6）。

⚠️ **维度是文档定的，不是这里定的**：`03 §6` 只写了一句「按款号/尺码聚合**手数**、
件数、已计手数、未计件手数」，所以 :class:`StatItemOut` 就只有这两个维度（款号 × 尺码）
和这三组量 —— 不加比例、不加组别、不加车间：加一个维度就要多一个索引，
而 `04 §5` 明确「禁止为不存在的查询建索引」。

⚠️ **「已计 / 未计」是二分**：所以统计只数 ``ACTIVE`` 码（``03 §3.3`` 的派生状态表里，
``VOIDED`` 既不是「未计件」也不是「已计件」，把它算进任何一边都会让
「手数 = 已计 + 未计」这条等式对不上，而报表里对不上的等式没人会去核）。

⚠️ **数量是字符串、计数是整数**（05 §3）：``qty`` 走 ``Str``（``numeric(14,3)``），
``hands`` / ``counted_hands`` / ``uncounted_hands`` 是**计数**，与既有出参
（``hands_total`` / ``label_print_qty``）同口径用 ``int``。
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.core.pydantic_types import Str


class StatItemOut(BaseModel):
    """一行统计（一个款号 × 一个尺码）。"""

    model_config = ConfigDict(from_attributes=True)

    style_no: str
    size_code: str
    hands: Annotated[int, Field(description="手数（= 码数）")]
    qty: Str = Field(description="件数合计（各码 bundle_qty 之和）")
    counted_hands: Annotated[int, Field(description="已计件手数（counted_at 非空）")]
    uncounted_hands: Annotated[int, Field(description="未计件手数（counted_at 为空）")]


class StatTotalsOut(BaseModel):
    """合计行（**与 items 同口径**，不是另一套算法）。"""

    model_config = ConfigDict(from_attributes=True)

    hands: Annotated[int, Field(description="手数合计")]
    qty: Str = Field(description="件数合计")
    counted_hands: Annotated[int, Field(description="已计件手数合计")]
    uncounted_hands: Annotated[int, Field(description="未计件手数合计")]


class StatisticsOut(BaseModel):
    """统计响应（``GET /bundling-orders/statistics``）。

    ⚠️ 数据范围由 service 的 ``apply_data_scope`` 强制：车间主管统计到的只有本车间，
    且**不能靠传 ``workshop_id`` 放大范围**（07 §3.2 铁律 1）。
    """

    model_config = ConfigDict(from_attributes=True)

    items: list[StatItemOut] = Field(description="按款号 × 尺码聚合的明细")
    totals: StatTotalsOut = Field(description="合计行（前端表尾直接用，不要自己再算一遍）")


__all__ = ["StatItemOut", "StatTotalsOut", "StatisticsOut"]
