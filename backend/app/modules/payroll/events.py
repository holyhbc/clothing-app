"""工资领域事件的发布（``docs/modules/05-工资.md`` §5.5、``docs/05`` §8）。

⚠️ **发布是旁路，绝不参与发放事务**：调用点在 ``pay`` **提交之后**，且失败只 WARN
（:func:`app.core.events.publish_event` 不抛）。理由与计件侧同款（``piecework/events.py``）：
工资已发放与「看板/员工端能看到它」是两件事，后者失败不能把前者变成 500。

## 只有业务白名单字段

``payroll_settled`` 的载荷**只出业务字段**（工资金额、所属周期、车间），
**不含** token / 口令 / 银行卡号 / 身份证等敏感信息（INV-3、07 §5）。金额用字符串
（``05 §3``：金额一律字符串，避免 JSON 浮点误差），时间用 ISO-8601。

## 字段与 ``05 §8``

``05 §8`` 只给了 ``piecework_counted`` 的示例，**未定义** ``payroll_settled`` 的字段集
（本卡已登记为规范待补条目）。本卡取最小可用集：接收端（车间主管 / 本人）凭
``sheet_id`` / ``doc_no`` / ``period_code`` 即可定位并回查真值（``05 §8``：SSE 是实时
提示、不是真值来源，禁止用事件下发写操作结果）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from app.core.events import publish_event

#: 事件名 ``<资源>_<动作>`` 蛇形（``05 §8``）。发放 = ``settled``（结清）。
EVENT_PAYROLL_SETTLED = "payroll_settled"


@dataclass(frozen=True, slots=True)
class SettledEvent:
    """一条 ``payroll_settled`` 的**业务白名单**载荷（构造于事务内、发布在提交后）。

    ⚠️ 做成冻结 dataclass 而不是传 ORM 实体：发布发生在 ``unit_of_work`` 提交**之后**，
    此时 ORM 行可能已过期（``expire_on_commit``），惰性刷新会撞 ``MissingGreenlet``。
    在事务内把字段拷出来，提交后发布的就是一份自洽的不可变快照。
    """

    sheet_id: UUID
    doc_no: str
    period_code: str
    workshop_id: UUID
    employee_count: int
    total_net: Decimal
    paid_at: datetime


async def publish_paid_event(event: SettledEvent) -> None:
    """广播 ``payroll_settled``（best-effort：Redis 不可用 / 发布失败只 WARN，不抛）。"""
    await publish_event(
        EVENT_PAYROLL_SETTLED,
        {
            "sheet_id": str(event.sheet_id),
            "doc_no": event.doc_no,
            "period_code": event.period_code,
            "workshop_id": str(event.workshop_id),
            "employee_count": event.employee_count,
            # 金额字符串（05 §3），不是 float：21.00 不能在传输里变成 21.0 或 20.999…
            "total_net": str(event.total_net),
            "paid_at": event.paid_at.isoformat(),
        },
    )


__all__ = ["EVENT_PAYROLL_SETTLED", "SettledEvent", "publish_paid_event"]
