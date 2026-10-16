"""计件领域事件的发布（``docs/modules/04-计件.md`` §5.1 ⑪、``docs/05`` §8）。

⚠️ **发布是旁路，绝不参与计件事务（INV-1）**：调用点在 ``count_piecework`` 提交
**之后**，且失败只 WARN（:func:`app.core.events.publish_event` 不抛）。原因：
计件落库成功与「看板能看到它」是两件事，后者失败不能把前者变成 500。
"""

from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import publish_event
from app.modules.auth.models import User
from app.modules.piecework.schemas import CountPieceworkOut

#: 事件名 ``<资源>_<动作>`` 蛇形（``05 §8``）。
EVENT_PIECEWORK_COUNTED = "piecework_counted"


async def employee_display_name(session: AsyncSession, employee_id: UUID) -> str | None:
    """取员工姓名，供事件里显示「谁做的」。

    ⚠️ 计件响应（``CountPieceworkOut``）**不含**姓名，而 ``05 §8`` 的事件示例要它；
    故这里单查一次 ``users.name``。查不到（数据不自洽）返回 ``None``，由调用方降级为空串，
    **不因为查不到姓名就不发事件**。
    """
    return (
        await session.execute(select(User.name).where(User.id == employee_id))
    ).scalar_one_or_none()


async def publish_counted_event(
    session: AsyncSession, *, out: CountPieceworkOut, employee_id: UUID
) -> None:
    """广播 ``piecework_counted``（字段与 ``05 §8`` 示例一致）。

    :param out: 计件响应。只用它的**业务白名单字段**（INV-3：无 token / 无口令）。
    :param employee_id: 干活的那个员工（不是操作员/组长）。响应里没有它，故显式传入。
    """
    name = await employee_display_name(session, employee_id)
    await publish_event(
        EVENT_PIECEWORK_COUNTED,
        {
            "bundle_no": out.bundle_no,
            "style_no": out.style_no,
            "operation_no": out.operation_no,
            "employee_name": name or "",
            # 件数是整件（09 §4.2），事件里给数字而不是字符串（05 §8 示例是 `1`）
            "qty": int(Decimal(out.qty)),
        },
    )
