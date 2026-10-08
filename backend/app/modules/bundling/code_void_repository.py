"""打菲**码**的作废写路径（``FOR UPDATE`` + 条件 UPDATE，T-BUND-007c 按 ADR-0031 拆出）。

从 :mod:`.code_repository` 搬来，理由同那份文件里 :class:`~.code_repository.BundleListQuery`
的注释所述：``code_repository`` 已到 400 行硬线（ADR-0030），而作废写路径与列表查询是
两件事（一个 ``SELECT … FOR UPDATE`` 带乐观锁、一个拼 ``SELECT``），混在一个文件里
读的人要跨着两个关注点找同一条码的状态判定。

⚠️ **只搬运，零行为变化**：判定（已作废 → ``31002``、已计件 → ``32003``）仍在
:mod:`.service.code`，本文件**不判断业务** —— 条件 UPDATE 落空时返回 ``False``，
由 service 翻译成错误码（08 §1.2 R3 的 rowcount == 0 分支）。

⚠️ **锁序**：:func:`lock_bundle_for_void` 的调用方**必须先锁单据再锁码**（03 §7）；
反过来先锁码会与并发的 ``approve`` / ``reverse`` 互等死锁 —— 那两个动作是
「锁单据 → 锁码」，顺序相反即死锁。
"""

from __future__ import annotations

from datetime import datetime
from typing import NamedTuple
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.bundling.models import Bundle, BundleStatus


class VoidTargetRow(NamedTuple):
    """被 ``FOR UPDATE`` 锁住、正准备作废的那个码（只要判定与乐观锁要用的列）。"""

    id: UUID
    doc_id: UUID
    version: int
    status: str
    counted_at: datetime | None


async def lock_bundle_for_void(session: AsyncSession, bundle_no: str) -> VoidTargetRow | None:
    """``FOR UPDATE`` 锁住这个码（03 §7：作废前先锁码，与计件侧串行）。

    ⚠️ **先锁再判 ``counted_at``**：否则会出现「判完没计件、紧接着另一个事务扫码写上
    ``counted_at``、本事务把码作废」。
    """
    stmt = (
        select(Bundle.id, Bundle.doc_id, Bundle.version, Bundle.status, Bundle.counted_at)
        .where(Bundle.bundle_no == bundle_no, Bundle.deleted_at.is_(None))
        .with_for_update()
        # ⚠️ ``populate_existing`` **不可省**：同一个 session 里这条码可能已经被读过
        # （列表 / 详情），而 SQLAlchemy 默认**不覆盖**已加载对象的属性 —— 那会让
        # 「锁住之后判 counted_at」拿到旧值，把已计件的码放过去作废。
        .execution_options(populate_existing=True)
    )
    row = (await session.execute(stmt)).one_or_none()
    return None if row is None else VoidTargetRow(*row)


async def mark_bundle_voided(
    session: AsyncSession,
    *,
    bundle_id: UUID,
    expected_version: int,
    reason: str,
    operator_id: UUID,
) -> bool:
    """条件 UPDATE：这个码置 ``VOIDED``（**行保留**，B12）。

    ⚠️ 条件是 ``version + status='ACTIVE'``（08 §1.2 R3）：并发两次作废只有一个生效，
    另一个拿到 ``False`` 并被 service 翻译成「已作废」。
    ⚠️ **不删行、不软删**：作废的码仍要被扫码枪查到并回报「已作废（31002）」。
    """
    stmt = (
        update(Bundle)
        .where(
            Bundle.id == bundle_id,
            Bundle.version == expected_version,
            Bundle.status == BundleStatus.ACTIVE,
        )
        .values(
            status=BundleStatus.VOIDED,
            voided_at=func.now(),
            void_reason=reason,
            version=Bundle.version + 1,
            updated_by=operator_id,
        )
        .returning(Bundle.id)
        .execution_options(synchronize_session=False)
    )
    return len((await session.execute(stmt)).all()) > 0


__all__ = ["VoidTargetRow", "lock_bundle_for_void", "mark_bundle_voided"]
