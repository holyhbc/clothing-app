"""⑪ 留痕 + 计件那一刻的员工快照（``docs/modules/04-计件.md`` §5.1 ⑪）。

## 为什么要单独一个文件

这两个函数都很短，但都带**一条容易被后人改掉的判断**，放进 400 行的 ``service.py``
里就看不见了：

- :func:`employee_group_no` —— 快照的是「计件那一刻的组别」，不是「查询时的组别」。
- :func:`write_document_log` —— 必须记**操作员**与**计件人**两个 id。

## 为什么计件流水要写 ``document_logs``

计件流水**不是单据**（无状态机、无审核），但 AGENTS §2.2 要求「任何状态变更必须写
``document_logs``」，这里的情况更该写：一次扫码会**同时改两张表**
（``piecework_logs`` 插一行 + ``bundles.counted_*`` 回写），只有留痕表能回答
「这手码为什么显示成已计」。而流水本身是 append-only 的，改不得。
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.models import DocumentLog
from app.modules.auth.models import User


async def employee_group_no(session: AsyncSession, employee_id: UUID) -> str | None:
    """取员工**当时**所属组别（``piecework_logs.group_no`` 行级冗余，04 §7.1）。

    ⚠️ 在**计件那一刻**取，不是查询时反查：员工调组后，历史流水仍属当时的组别
    （``app/core/scope.py`` 里 ``piecework_logs`` 的 GROUP 范围注释就是这个理由 ——
    按现在的 ``users.group_no`` 过滤，组别主管会凭空看见别人调组前的历史）。
    """
    return (
        await session.execute(select(User.group_no).where(User.id == employee_id))
    ).scalar_one_or_none()


async def write_document_log(
    session: AsyncSession,
    *,
    log_id: UUID,
    bundle: Mapping[str, Any],
    operator_id: UUID,
    employee_id: UUID,
    qty: Decimal,
    amount: Decimal,
) -> None:
    """写 ``document_logs``。

    ⚠️ **记「操作人」与「计件人」两个 id**：工位机是组长替员工扫码（``operator_id``
    与 ``employee_id`` 指向不同的人）。只记一个就分不清「这条流水是谁扫的」与
    「这活是谁做的」—— 同一台机子两个人用时，出了纠纷无从查证。

    ⚠️ ``doc_id`` 是**流水 id**（``piecework_logs.id``），不是 ``bundles.id``：
    一手码理论上可能有多条流水（红冲 + 补录），按码记会把它们合并成一条，
    反而丢掉了「这一手被计过几次」这个信息。
    """
    session.add(
        DocumentLog(
            doc_type=DOC_TYPE_PIECEWORK,
            doc_id=log_id,
            doc_no=str(bundle["bundle_no"]),
            action="COUNT",
            from_status=None,
            to_status="COUNTED",
            operator_id=operator_id,
            operator_name="系统",  # TODO: 接真实操作人姓名（同打菲 _write_log）
            reason=None,
            changed_fields={
                "employee_id": str(employee_id),
                "hands": bundle["hands"],
                "qty": str(qty),
                "bundle_qty": str(bundle["bundle_qty"]),
                "amount": str(amount),
            },
        )
    )


#: ``document_logs.doc_type``。⚠️ 计件流水**不是单据**（无状态机、无审核），
#: 但仍要留痕 —— 见模块 docstring。
DOC_TYPE_PIECEWORK = "PIECEWORK"

__all__ = ["DOC_TYPE_PIECEWORK", "employee_group_no", "write_document_log"]
