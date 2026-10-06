"""ADR-0026 §2 唯一取价 SQL 与单价响应映射（原 service.py 630-723）。

``build_rate_resolve_stmt`` 供集成测试 ``EXPLAIN`` 与 P1 计件模块复用（设计稿 §11）。
"""

from datetime import date
from uuid import UUID

from sqlalchemy import Select, and_, case, or_, select

from app.common.enums import RateSource
from app.modules.base.models import OperationRate
from app.modules.base.schemas import OperationRateOut

from .common import SCALE_UNIT_PRICE, numeric_str


def build_rate_resolve_stmt(
    *,
    operation_no: str,
    style_no: str,
    category_id: UUID | None,
    work_date: date,
) -> Select[OperationRate]:
    """**ADR-0026 §2 的唯一取价 SQL**，逐字照抄那份决策。

    抽成模块级函数有两个理由：

        1. 让集成测试能对**生产语句本身**做 ``EXPLAIN``（断言命中
           ``idx_operation_rates_lookup``），而不是对一份抄写副本断言 ——
           抄写副本会与生产代码悄悄分叉，而分叉之后测试依然全绿。
        2. 计件模块（P1）要复用同一口径，届时直接 import，不再抄第三遍。

    ⚠️ ``ORDER BY`` 的两段**不可交换**：先按档位（``CASE``），同档位内再取最新
    生效日。写成 ``effective_from DESC, CASE ...`` 会让"三个月前公布的款号价"
    盖掉"今天生效的分类价" —— 而款号价本来就该赢。
    """
    tier = case(
        (OperationRate.style_no == style_no, 0),
        (OperationRate.product_category_id == category_id, 1),
        else_=2,
    )
    return (
        select(OperationRate)
        .where(
            OperationRate.operation_no == operation_no,
            OperationRate.effective_from <= work_date,
            or_(OperationRate.effective_to.is_(None), OperationRate.effective_to > work_date),
            or_(
                and_(
                    OperationRate.style_no == style_no,
                    OperationRate.product_category_id.is_(None),
                ),
                and_(
                    OperationRate.style_no.is_(None),
                    OperationRate.product_category_id == category_id,
                ),
                and_(
                    OperationRate.style_no.is_(None),
                    OperationRate.product_category_id.is_(None),
                ),
            ),
            OperationRate.deleted_at.is_(None),
        )
        .order_by(tier, OperationRate.effective_from.desc())
        .limit(1)
    )


def rate_source_of(rate: OperationRate) -> RateSource:
    """由命中行的形态派生 ``rate_source``（ADR-0026 §2 三档）。"""
    if rate.style_no is not None:
        return RateSource.STYLE
    if rate.product_category_id is not None:
        return RateSource.CATEGORY
    return RateSource.OPERATION


def rate_out(row: OperationRate) -> OperationRateOut:
    """``operation_rates`` 行 → 响应模型（``is_current`` 与 ``rate_source`` 都派生）。"""
    return OperationRateOut(
        id=row.id,
        version=row.version,
        remark=row.remark,
        created_at=row.created_at,
        updated_at=row.updated_at,
        operation_no=row.operation_no,
        style_no=row.style_no,
        product_category_id=row.product_category_id,
        effective_from=row.effective_from,
        effective_to=row.effective_to,
        unit_price=numeric_str(row.unit_price, SCALE_UNIT_PRICE),
        reason=row.reason,
        is_current=row.effective_to is None,
        rate_source=rate_source_of(row),
    )
