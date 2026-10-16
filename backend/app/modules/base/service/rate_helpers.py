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


def build_effective_rates_stmt(
    *,
    operation_no: str | None,
    style_no: str,
    category_id: UUID | None,
    work_date: date,
) -> Select[OperationRate]:
    """某 (款号, 可选工序) 在 ``work_date`` **当天生效**的全部单价行（T-PIECE-009）。

    与 :func:`build_rate_resolve_stmt` 是**同一套 ADR-0026 三档语义的两个视图**：

        ========================  ==========================  ==========================
        函数                       取几条                       用途
        ========================  ==========================  ==========================
        ``build_rate_resolve``    ``LIMIT 1``（命中那一档）    真正计件落库取价
        ``build_effective``       全部适用行（最多三档各一条）  「当前生效单价列表」只读展示
        ========================  ==========================  ==========================

    ⚠️ **两条 SQL 的「生效区间」与「三档适用」谓词必须逐字一致**：展示用的价与真正
    计件用的价若不是同一条，偏差只会在对账时暴露（「页面写着 0.42，工资按 0.35 算」），
    而那时代码已经跑了几千条流水。改动任一条时**必须同步另一条**。

    ⚠️ **``LIMIT 1`` 不是笔误**：这里**不**加 limit —— 「列表」的本意就是把当天的
    三档都列出来（款号被分类价遮蔽时也要看得见，ADR-0026 §7 要求显示来源与遮蔽）。
    ``operation_no`` 为 ``None`` 时跨工序返回，按 ``operation_no`` 归组。
    """
    tier = case(
        (OperationRate.style_no == style_no, 0),
        (OperationRate.product_category_id == category_id, 1),
        else_=2,
    )
    stmt = select(OperationRate).where(
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
    if operation_no is not None:
        stmt = stmt.where(OperationRate.operation_no == operation_no)
    return stmt.order_by(
        OperationRate.operation_no.asc(),
        tier,
        OperationRate.effective_from.desc(),
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
