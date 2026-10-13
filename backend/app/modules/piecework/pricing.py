"""④ 取价（``docs/modules/04-计件.md`` §5.1）。

## 为什么**不调** ``RateService.resolve``

``RateService.resolve`` 是「取价**预演**」接口（[ADR-0026](./../../../adr/0026-操作单价与取价.md) §2），
它内部先 ``_require_style`` → ``assert_in_scope`` 判**数据范围**。
而 ``app/core/scope.py`` 里 ``SCOPE_SPECS["styles"] = ScopeSpec(user_column="merchandiser_id")``
—— 款号**没有车间列**，所以 ``in_scope`` 对任何非 FACTORY 范围**一律返回 False**。

后果很具体：[04-计件 §6.1](./../../../docs/modules/04-计件.md) 规定本接口数据范围是
``WORKSHOP``，§10 的真实用户是 ``workshop_supervisor``(WORKSHOP) / ``line_leader``(GROUP)。
两者都不是款号负责人，于是**每一个工位机用户扫任何一个码都会拿到 12002**，
而错误文案说的是「该记录超出你的数据范围」—— 谁都不会想到是款号归属的口径问题。

⚠️ **本模块只绕开「预演接口带范围判定」这一件事，不主张「``styles`` 的口径是错的」**：
款号管理页「SELF = 只看我负责的款号」大概率是有意为之。本卡保证链路 A 能跑，
**口径本身留给架构定论**（``docs/12`` L-121）。

## 这里的授权边界是**码**，不是款号

① 已经按数据范围锁定了这手码（``FOR UPDATE``），② 已校验款号配了这个计件工序。
取价只是这两步之后的**内部读取**，它不该再引入一层与被测资源无关的范围判定。

## 取价的 SQL 仍是共享的那一份

``build_rate_resolve_stmt`` 是 ADR-0026 §2 的权威实现，三档优先级
（``STYLE`` > ``CATEGORY`` > ``OPERATION``）全在里面，**勿改**。
本模块只决定「谁可以调它」。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessError, ErrorCode
from app.core.numbering import normalize_style_no
from app.modules.base.models import Style
from app.modules.base.service.rate_helpers import build_rate_resolve_stmt


async def resolve_price(
    session: AsyncSession, style_no: str, operation_no: str, work_date: date
) -> Decimal:
    """取当日生效单价；命中不到抛 ``20004``。

    ⚠️ **不回退旧价、不取 0**（04-计件 §9）。「取不到就拿最近的一条」会让历史流水
    在改价之后按新价结算，而工资单早按旧价发出去了 —— 差额没有任何记录可查。

    ⚠️ ``category_id`` 取自 ``styles``（取价三档里 CATEGORY 档要用）。查不到就按
    「没有品类档」处理 —— 让 STYLE / OPERATION 两档继续比，而不是整条取价失败：
    款号在 ② 已校验过存在，这里查不到只可能是并发删除，而那属于 20001 的范畴。
    """
    code = normalize_style_no(style_no)
    style = (
        await session.execute(select(Style.category_id).where(Style.style_no == code))
    ).one_or_none()
    hit = (
        await session.execute(
            build_rate_resolve_stmt(
                operation_no=operation_no,
                style_no=code,
                category_id=style.category_id if style is not None else None,
                work_date=work_date,
            )
        )
    ).scalar_one_or_none()
    if hit is None:
        raise BusinessError(
            ErrorCode.OPERATION_RATE_NOT_SET,
            f"款号 {style_no} 的工序 {operation_no} 在 {work_date.isoformat()} 没有生效单价，请先设价",
            details={
                "style_no": style_no,
                "operation_no": operation_no,
                "work_date": work_date.isoformat(),
            },
        )
    return hit.unit_price


__all__ = ["resolve_price"]
