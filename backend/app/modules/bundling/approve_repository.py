"""审核 / 反审核这条**写路径**的全部查询（T-BUND-005b）。

``bundles``（打菲码）审核 / 反审核这条**写路径**的全部查询。
:mod:`.repository` 定位只读、:mod:`.state_repository` 管 ``cutting_outputs`` 的行锁与
预占 / 释放 / 结转，本文件管**码本身**。

**本文件不判断业务**：条件 UPDATE 不满足时返回 ``None``（翻译成哪个错误码是 service 的
决定，抄 :mod:`.state_repository` 的约定）；唯一索引冲突**不吞**、原样抛 ``IntegrityError``
由 service 按约束名翻译成 ``31005`` —— repository 猜「大概是哪个约束」会在改名时静默说错。

**批量生成只用一条语句**（03 §5.3）：``INSERT ... SELECT ... FROM generate_series(1, :hands)``
—— 每条明细一行一条语句，语句数 = 明细行数、**与手数无关**（2000 手的大单是 1 条 INSERT
而不是 2000 次往返，2C VPS 上逐条 INSERT 的行锁持有时间会到秒级）。⚠️ 手序号**只在服务端
展开**：``generate_series`` 产出 1..hands，不接受前端传入的手号 —— 那才是「两个并发请求各要
第 2 手」的唯一来源（03 §7 三层防线之二）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Final, NamedTuple, cast
from uuid import UUID

from sqlalchemy import Integer, Numeric, String, func, literal, select, update
from sqlalchemy import cast as sa_cast
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811 —— 别名照抄 docs/04 §2
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.modules.bundling.models import Bundle, BundleStatus
from app.modules.bundling.state_repository import OutputKey

if TYPE_CHECKING:
    from sqlalchemy import Table

#: ``bundles`` 批量 INSERT 的列清单。
#:
#: ⚠️ **刻意不列** ``id`` / ``created_at`` / ``updated_at`` / ``version`` /
#: ``counted_qty`` / ``status`` / ``deleted_at``：它们全有 ``server_default``
#: （``gen_random_uuid()`` / ``now()`` / ``1`` / ``0`` / ``'ACTIVE'`` / ``NULL``），
#: 写进 VALUES 只会让语句长一倍，并多一个「默认值改了这里忘了改」的同步点。
_INSERT_COLUMNS: Final[tuple[str, ...]] = (
    "doc_id",
    "line_id",
    "bundle_no",
    "hands",
    "style_no",
    "color_code",
    "size_code",
    "operation_no",
    "cutting_size_line_id",
    "bundle_qty",
    "qr_content",
    "created_by",
    "updated_by",
)


class BundleHand(NamedTuple):
    """一行被锁住的 ACTIVE 码（``FOR UPDATE``）。"""

    bundle_no: str
    color_code: str
    size_code: str
    hands: int
    counted_at: datetime | None
    counted_by: UUID | None


@dataclass(frozen=True, slots=True)
class HandSpan:
    """一个 ``(色码, 尺码)`` 内已生成手的**分布**（审核断言 ③ 用）。"""

    color_code: str
    size_code: str
    span_count: int
    min_seq: int
    max_seq: int
    distinct_seq: int


# =================================================================== 审核：按手批量生成


async def insert_bundle_series(
    session: AsyncSession,
    *,
    doc_id: UUID,
    line_id: UUID,
    bundle_no_prefix: str,
    hands_seq_digits: int,
    item_suffix: str,
    start_hand_seq: int,
    hands: int,
    style_no: str,
    color_code: str,
    size_code: str,
    operation_no: str,
    cutting_size_line_id: UUID,
    bundle_qty: Decimal,
    operator_id: UUID,
) -> int:
    """一条裁剪尺码明细行 → ``hands`` 个码，**一条** INSERT 语句（03 §5.3）。

    :param bundle_no_prefix: ``{doc_no}-{尺码码}-``，**由 service 从
        :mod:`.service.numbering` 的常量拼好传进来** —— 本文件不 import service 包（会与
        ``service/__init__`` 成环），也不自己重写一遍格式。
    :param hands_seq_digits / item_suffix: 手序号位数（= 2）/ 件序号后缀（``-0001``）。
    :param start_hand_seq: 本行第一手的序号（Q-B13：同尺码跨布批行**接着编**）。
    :param hands: 本行手数 = **码数**。
    :returns: 实际插入行数（应恒等于 ``hands``；不等由 service 的断言 ① 拦下）。
    :raises IntegrityError: ``uq_bundles_hand`` / ``uq_bundles_no`` 冲突（03 §7 第五层防线）。
    """
    # ⚠️ **必须用 ``column_valued`` 而不是 ``table_valued``**：后者渲染成
    # ``FROM generate_series(...) AS anon_1``（别名丢掉了列名），PG 于是报
    # ``column anon_1.hands_seq does not exist``。``column_valued`` 才渲染成
    # ``AS hands_seq``，即「整条 FROM 就是那一列」。
    # ⚠️ ``sa_cast``（SQL 层 CAST）不能省：``generate_series`` 返回 ``integer``，
    # ``lpad`` 只接受 ``text`` —— 少了它 PG 直接报 ``function lpad(integer, ...)``。
    hand_seq = sa_cast(
        func.generate_series(1, hands).column_valued("hands_seq") + (start_hand_seq - 1), Integer
    )
    bundle_no: ColumnElement[str] = (
        literal(bundle_no_prefix, String(64))
        + func.lpad(sa_cast(hand_seq, String), hands_seq_digits, "0")
        + literal(item_suffix, String(8))
    )
    rows = select(
        literal(doc_id, PgUUID(as_uuid=True)),
        literal(line_id, PgUUID(as_uuid=True)),
        bundle_no,
        hand_seq,
        literal(style_no, String(32)),
        literal(color_code, String(32)),
        literal(size_code, String(32)),
        literal(operation_no, String(16)),
        literal(cutting_size_line_id, PgUUID(as_uuid=True)),
        literal(bundle_qty, Numeric(14, 3)),
        bundle_no,
        literal(operator_id, PgUUID(as_uuid=True)),
        literal(operator_id, PgUUID(as_uuid=True)),
    )
    table = cast("Table", Bundle.__table__)
    stmt = (
        pg_insert(table)
        .from_select(list(_INSERT_COLUMNS), rows)
        .returning(table.c.id)
        .execution_options(synchronize_session=False)
    )
    return len((await session.execute(stmt)).all())


async def void_active_bundles(
    session: AsyncSession, *, doc_id: UUID, reason: str, operator_id: UUID
) -> int:
    """本单全部 ACTIVE 码置 ``VOIDED``（**行保留**，B10 / B12）。

    ⚠️ **不删行、不软删**：作废的码仍要被扫码枪查到并回报「已作废（31002）」，
    且它**继续占着手号**（``uq_bundles_hand`` 不看 status）—— 反审核后重打必须新开
    单据（B12），否则「同一个号两次扫到两个不同的手」。
    """
    stmt = (
        update(Bundle)
        .where(
            Bundle.doc_id == doc_id,
            Bundle.deleted_at.is_(None),
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
    return len((await session.execute(stmt)).all())


# =================================================================== 反审核前置：锁码


async def lock_active_bundle_hands(session: AsyncSession, doc_id: UUID) -> list[BundleHand]:
    """``FOR UPDATE`` 锁本单全部 ACTIVE 码（03 §7 第二条）。

    ⚠️ **先锁再判 ``counted_at``**，与计件侧串行（03 §7）：反过来会出现「判完没计件、
    紧接着另一个事务扫码写上 ``counted_at``、本事务把码作废」。
    """
    stmt = (
        select(
            Bundle.bundle_no,
            Bundle.color_code,
            Bundle.size_code,
            Bundle.hands,
            Bundle.counted_at,
            Bundle.counted_by,
        )
        .where(
            Bundle.doc_id == doc_id,
            Bundle.deleted_at.is_(None),
            Bundle.status == BundleStatus.ACTIVE,
        )
        .order_by(Bundle.color_code, Bundle.size_code, Bundle.hands)
        .with_for_update()
    )
    return [BundleHand(*row) for row in (await session.execute(stmt)).all()]


# =================================================================== 审核断言用的聚合读


async def count_doc_bundles(session: AsyncSession, doc_id: UUID) -> int:
    """本单已生成码数（断言 ①：码数 = 手数）。"""
    stmt = (
        select(func.count())
        .select_from(Bundle)
        .where(Bundle.doc_id == doc_id, Bundle.deleted_at.is_(None))
    )
    return int((await session.execute(stmt)).scalar_one())


async def sum_doc_bundle_qty(session: AsyncSession, doc_id: UUID) -> Decimal:
    """本单已生成手的**件数合计**（断言 ②：Σ``bundle_qty`` + 余数 = 本单件数）。"""
    stmt = select(func.coalesce(func.sum(Bundle.bundle_qty), 0)).where(
        Bundle.doc_id == doc_id, Bundle.deleted_at.is_(None)
    )
    return Decimal((await session.execute(stmt)).scalar_one())


async def sum_bundle_qty_by_key(session: AsyncSession, doc_id: UUID) -> dict[OutputKey, Decimal]:
    """逐 ``(色码, 尺码)`` 的已结转件数 —— **反审核减回的权威依据**。

    ⚠️ 反审核**不能**重算 ``Σ(hands × qty_per_hand)``：裁剪侧可能在审核之后改过
    ``qty_per_hand``，重算的数与当初加上去的不是同一个，结转再也回不到原点。
    「减回当初加了多少」只有一条路 —— 读当初写下的那个数。
    """
    stmt = (
        select(Bundle.color_code, Bundle.size_code, func.sum(Bundle.bundle_qty))
        .where(Bundle.doc_id == doc_id, Bundle.deleted_at.is_(None))
        .group_by(Bundle.color_code, Bundle.size_code)
    )
    return {
        (color_code, size_code): Decimal(total)
        for color_code, size_code, total in (await session.execute(stmt)).all()
    }


async def hand_spans(session: AsyncSession, doc_id: UUID) -> list[HandSpan]:
    """逐 ``(色码, 尺码)`` 的手号分布（断言 ③：手号恰为 1..N 无缺号）。

    ⚠️ 用 ``count(*)`` 与 ``count(distinct hands)`` **两个数**：``1,1,3`` 的
    min=1、max=3、count=3，只看这三个恰好能蒙混过关。
    """
    stmt = (
        select(
            Bundle.color_code,
            Bundle.size_code,
            func.count(),
            func.min(Bundle.hands),
            func.max(Bundle.hands),
            func.count(func.distinct(Bundle.hands)),
        )
        .where(Bundle.doc_id == doc_id, Bundle.deleted_at.is_(None))
        .group_by(Bundle.color_code, Bundle.size_code)
        .order_by(Bundle.color_code, Bundle.size_code)
    )
    return [
        HandSpan(color_code, size_code, int(count), int(low), int(high), int(distinct))
        for color_code, size_code, count, low, high, distinct in (await session.execute(stmt)).all()
    ]


__all__ = [
    "BundleHand",
    "HandSpan",
    "count_doc_bundles",
    "hand_spans",
    "insert_bundle_series",
    "lock_active_bundle_hands",
    "sum_bundle_qty_by_key",
    "sum_doc_bundle_qty",
    "void_active_bundles",
]
