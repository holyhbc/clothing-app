"""标签导出与打印留痕的**查询**（T-BUND-006）。

与 :mod:`.repository`（只读单据）、:mod:`.approve_repository`（码本身的写路径）分开放，
理由和 :mod:`.approve_repository` 一样：**按主题分文件**（ADR-0031）。本文件管
「标签要印哪些手」与「这单印过几次」。

本文件**不判断业务**：哪些手能印、``print_seq`` 重复算不算错，全部由
:mod:`.service.label` 决定；这里只把行取出来、把行写进去。

## 为什么 ``bundle_label_prints`` 只能 INSERT

该表是 **append-only**（``04 §7.16`` / Q-B14）：只有 ``id`` + ``created_at`` +
``created_by``，没有 ``version`` / ``deleted_at`` / ``updated_*``，迁移 0014 还对
``erp_app`` ``REVOKE UPDATE, DELETE``。所以本文件**没有** update / delete 函数 ——
这不是遗漏，是权限层已经把它钉死了：重打纠错只能**新增一行**。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final, NamedTuple
from uuid import UUID

from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.bundling.models import Bundle, BundleLabelPrint, BundleStatus

#: 打印留痕批量 INSERT 的列清单。
#:
#: ⚠️ **刻意不列** ``id`` / ``created_at``：两者都有 ``server_default``
#: （``gen_random_uuid()`` / ``now()``），写进 VALUES 只会让语句长一倍。
#: ``printed_at`` **必须显式给**：它是业务字段（打印动作发生的时间，与行写入时间
#: 可能不同 —— 批量导入历史留痕时会差几天），且没有 server_default。
_PRINT_COLUMNS: Final[tuple[str, ...]] = (
    "bundle_no",
    "doc_id",
    "hands_seq",
    "hands_total_of_size",
    "printed_qty",
    "is_reprint",
    "print_seq",
    "printed_by",
    "printed_at",
    "created_by",
)


class LabelBundleRow(NamedTuple):
    """标签要印的**一手**（导出与登记共用同一份行，避免两条路径口径分叉）。"""

    bundle_no: str
    style_no: str
    color_code: str
    size_code: str
    operation_no: str
    hands_seq: int
    bundle_qty: Decimal
    qr_content: str
    status: str


async def list_label_bundles(
    session: AsyncSession,
    *,
    doc_id: UUID,
    size_code: str | None = None,
    hands_from: int | None = None,
    hands_to: int | None = None,
    active_only: bool = True,
) -> list[LabelBundleRow]:
    """取本单要印的手（按尺码 + 手号排序，顺序即打印顺序）。

    ⚠️ **手号区间是「每个尺码各自」的**：Q-B13 定的是「同一打菲单内同尺码手号连续编到 N」，
    所以 ``hands BETWEEN 1 AND 2`` 命中的是**每个尺码**的 1、2 手，而不是全单的前两手
    （全单的前两手会全是头一个尺码的 1、2 手，那不是任何人想印的东西）。
    命中最左列是 ``uq_bundles_hand (doc_id, color_code, size_code, hands)`` 的 ``doc_id``。

    :param active_only: ``True``（导出）只取 ``ACTIVE``；``False``（登记）全取，
        让 service 能区分「码不存在 ``31001``」与「码已作废 ``31002``」——
        这两种情况若都过滤掉，用户只会看到一句「区间内没有码」。
    """
    stmt = select(
        Bundle.bundle_no,
        Bundle.style_no,
        Bundle.color_code,
        Bundle.size_code,
        Bundle.operation_no,
        Bundle.hands,
        Bundle.bundle_qty,
        Bundle.qr_content,
        Bundle.status,
    ).where(Bundle.doc_id == doc_id, Bundle.deleted_at.is_(None))
    if active_only:
        stmt = stmt.where(Bundle.status == BundleStatus.ACTIVE)
    if size_code is not None:
        stmt = stmt.where(Bundle.size_code == size_code)
    if hands_from is not None:
        stmt = stmt.where(Bundle.hands >= hands_from)
    if hands_to is not None:
        stmt = stmt.where(Bundle.hands <= hands_to)
    rows = (await session.execute(stmt.order_by(Bundle.size_code, Bundle.hands))).all()
    return [LabelBundleRow(*row) for row in rows]


async def hands_total_by_size(session: AsyncSession, doc_id: UUID) -> dict[tuple[str, str], int]:
    """逐 ``(色码, 尺码)`` 的**共 M 手**（``max(hands)``）。

    ⚠️ 取 ``max`` 而不是 ``count``：审核断言 ③ 已经保证手号恰为 ``1..N`` 无缺号
    （:func:`~app.modules.bundling.approve_repository.hand_spans`），两者此刻相等；
    但 ``count`` 在有人手工改坏数据时会静默给出另一个数，而「共 M 手」是要印在
    标签上给员工看的数 —— 少印一手和印错一手同样让人认错领。
    """
    stmt = (
        select(Bundle.color_code, Bundle.size_code, func.max(Bundle.hands))
        .where(
            Bundle.doc_id == doc_id,
            Bundle.deleted_at.is_(None),
            Bundle.status == BundleStatus.ACTIVE,
        )
        .group_by(Bundle.color_code, Bundle.size_code)
    )
    return {
        (color_code, size_code): int(total)
        for color_code, size_code, total in (await session.execute(stmt)).all()
    }


async def existing_print_seq_hands(
    session: AsyncSession, *, doc_id: UUID, bundle_nos: list[str], print_seq: int
) -> list[str]:
    """已用过该 ``print_seq`` 的码（重入检测）。

    ⚠️ ``04 §7.16`` **没有**给 ``print_seq`` 建唯一索引，而唯一索引才是并发下的最终
    防线（``bundles`` 有 ``uq_bundles_hand`` 才敢「不查后插」）。这里靠
    「锁住表头 + 同事务内查」挡住**同单并发**（两次登记都 ``FOR UPDATE`` 同一行，
    后到者必然看见前者的行）；跨单 / 跨并发实例的幂等靠 ``Idempotency-Key``（05 §5，
    T-BUND-007 在 Router 层接线）。真要库层兜底得改 ``04 §7.16``，登记在 docs/12 §5。
    """
    if not bundle_nos:
        return []
    stmt = select(BundleLabelPrint.bundle_no).where(
        BundleLabelPrint.doc_id == doc_id,
        BundleLabelPrint.print_seq == print_seq,
        BundleLabelPrint.bundle_no.in_(bundle_nos),
    )
    return sorted(str(row) for row in (await session.execute(stmt)).scalars().all())


async def append_label_prints(
    session: AsyncSession,
    *,
    doc_id: UUID,
    rows: list[dict[str, object]],
    operator_id: UUID,
) -> list[UUID]:
    """**只追加**打印留痕（一条语句多组 VALUES），返回落库的 ``id``。

    :param rows: :attr:`_PRINT_COLUMNS` 的列值字典（``printed_at`` 由 service 给）。
    """
    values = [{**row, "doc_id": doc_id, "created_by": operator_id} for row in rows]
    stmt = (
        insert(BundleLabelPrint)
        .values(values)
        .returning(BundleLabelPrint.id)
        .execution_options(synchronize_session=False)
    )
    return [row[0] for row in (await session.execute(stmt)).all()]


async def sum_printed_qty(session: AsyncSession, doc_id: UUID) -> int:
    """本单**累计**打印张数（``label_print_qty`` 的权威来源 = 留痕表求和）。

    ⚠️ 重算而不是 ``+= n``：累加一旦漏掉一次回滚/并发就会永久漂移，而这张表是
    append-only（没有 UPDATE 权限），**求和是唯一能被复核出来的口径**。
    """
    stmt = select(func.coalesce(func.sum(BundleLabelPrint.printed_qty), 0)).where(
        BundleLabelPrint.doc_id == doc_id
    )
    return int((await session.execute(stmt)).scalar_one())


__all__ = [
    "LabelBundleRow",
    "append_label_prints",
    "existing_print_seq_hands",
    "hands_total_by_size",
    "list_label_bundles",
    "sum_printed_qty",
]
