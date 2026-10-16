"""计件单价查询（``GET /piecework/rates``，T-PIECE-009）。

## 为什么这里**不用** ``RateService.list_rates``

``list_rates`` 服务于 ``01 §9`` 的单价维护页，它的 ``effective_from`` /
``effective_to`` 参数是**「生效起始日落在该闭区间内」**（筛选区间行本身），
而本端点要的是**「某 ``work_date`` 当天生效」**（``effective_from <= work_date AND
(effective_to IS NULL OR effective_to > work_date)``）。两者不是同一个条件：
前者会把「今天生效但三个月前设的价」漏掉、把「今天设的下月生效价」误报成生效。
所以取价谓词必须走 :func:`build_effective_rates_stmt`（与 ``build_rate_resolve_stmt`` 同源）。

## 为什么直接查 ``styles`` 而不走 ``StyleService.get_required``

与 ``pricing.py::resolve_price`` 同款理由：``styles`` 的数据范围口径是「只看自己
负责的款号」（``docs/12`` L-121），套进一个数据范围本就是 ``FACTORY`` 的只读端点
会让「款号负责人之外的人」拿到 ``12002`` —— 而本端点对未知款号登记的错误码是
``20001``。这里只取 ``category_id``（三档里 CATEGORY 档要用），查不到即 ``20001``。
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessError, ErrorCode
from app.core.numbering import normalize_style_no
from app.modules.base.models import Style
from app.modules.base.service.common import SCALE_UNIT_PRICE, numeric_str
from app.modules.base.service.rate_helpers import build_effective_rates_stmt, rate_source_of
from app.modules.piecework.schemas import PieceworkRateOut


async def list_effective_rates(
    session: AsyncSession,
    *,
    style_no: str,
    operation_no: str | None,
    work_date: date,
) -> list[PieceworkRateOut]:
    """列出 ``work_date`` 当天生效的单价行；未知款号 ``20001``、无生效价 ``20004``。

    ⚠️ **不返回空列表**：``04-计件 §6.1`` 给本端点登记了 ``20004``（与取价同码）。
    空列表会被调用方当成「这款确实没设价」还是「请求写错了」分不清，而
    ``20004`` 的 ``details`` 能把 ``style_no`` / ``operation_no`` / ``work_date``
    原样回给用户，指向「去基础资料设价」。

    ⚠️ **金额用 :func:`numeric_str` 按列标度 quantize**（``docs/05 §3``）：
    直接 ``str(Decimal)`` 会漏掉尾随零（``0.42`` 而非 ``0.420000``），
    对账时同一列两种写法看起来像两个不同的价。
    """
    code = normalize_style_no(style_no)
    style = (
        await session.execute(
            select(Style.category_id).where(Style.style_no == code, Style.deleted_at.is_(None))
        )
    ).one_or_none()
    if style is None:
        raise BusinessError(
            ErrorCode.BASE_DATA_NOT_FOUND,
            f"款号 {style_no} 不存在",
            details={"style_no": style_no},
        )
    rows = list(
        (
            await session.execute(
                build_effective_rates_stmt(
                    operation_no=operation_no,
                    style_no=code,
                    category_id=style.category_id,
                    work_date=work_date,
                )
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        scope = f"工序 {operation_no}" if operation_no is not None else "任意工序"
        raise BusinessError(
            ErrorCode.OPERATION_RATE_NOT_SET,
            f"款号 {style_no} 的{scope}在 {work_date.isoformat()} 没有生效单价，请先设价",
            details={
                "style_no": style_no,
                "operation_no": operation_no,
                "work_date": work_date.isoformat(),
            },
        )
    return [
        PieceworkRateOut(
            operation_no=row.operation_no,
            style_no=row.style_no,
            product_category_id=row.product_category_id,
            unit_price=numeric_str(row.unit_price, SCALE_UNIT_PRICE),
            effective_from=row.effective_from,
            effective_to=row.effective_to,
            rate_source=rate_source_of(row),
            is_current=(
                row.effective_from <= work_date
                and (row.effective_to is None or row.effective_to > work_date)
            ),
        )
        for row in rows
    ]


__all__ = ["list_effective_rates"]
