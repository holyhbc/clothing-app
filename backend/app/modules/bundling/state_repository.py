"""状态迁移与预占的**行锁 / 条件写**查询（T-BUND-005a）。

## 为什么单独一个文件

:mod:`app.modules.bundling.repository` 已有 383 行，把本文件的查询并进去就突破
ADR-0030 的「单文件 ≤400 行手写」硬上限。所以**写路径**单独成文件，
:mod:`repository` 保持只读定位（那里不写库、不碰状态字段，08 R7）。

## 本文件**不判断业务**

- 条件 UPDATE 不满足时**返回 ``False`` 而不是抛异常**：翻译成哪个错误码（``30002``）
  是 service 的决定。repository 抛错会让同一条规则在两条路径上分叉，而分叉的表现是
  「提交拦住了、撤回没拦住」——最难查的那类不一致。
- 所有查询都带 ``deleted_at IS NULL``（INV-7），不做软删豁免。

## 加锁顺序（modules/03 §7）

先 ``cutting_outputs`` → ``bundling_orders`` → ``bundles``。:func:`lock_cutting_outputs`
里那条 ``ORDER BY`` 不是洁癖，而是把顺序**写进 SQL** —— 两张打菲单并发提交、
涉及重叠的尺码时，锁序不一致就是死锁。
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.bundling.models import BundlingOrder, BundlingOrderLine
from app.modules.cutting.models import CuttingOutput

#: 一个 (色码, 尺码) —— 结转行的业务键（``uq_cutting_outputs_style_color_size``）。
type OutputKey = tuple[str, str]


def available_qty_of(output: CuttingOutput) -> Decimal:
    """结转行的**可打菲余量**，口径 = 视图 ``v_cutting_output_available``。

    ⚠️ 必须与视图 / 条件 UPDATE 用**同一个式子**（``output - bundled - reserved``）。
    三处各算一遍的话，某天改了一处就会表现成「列表说够、提交说不够」。
    """
    return Decimal(output.output_qty) - Decimal(output.bundled_qty) - Decimal(output.reserved_qty)


async def lock_order_for_transition(session: AsyncSession, order_id: UUID) -> BundlingOrder | None:
    """状态迁移入口取单并 ``FOR UPDATE``（**带** ``populate_existing``）。

    ⚠️ ``populate_existing=True`` 不是可选项：并发迁移时 identity map 里那个对象是
    上一轮读到的快照，而 SQLAlchemy 默认**不覆盖**已加载对象的属性 —— 于是
    「明明已被别人提交过的单又提交了一次」这种错判会静默通过。

    ⚠️ **刻意不带明细**：子表并发由 :func:`~app.modules.bundling.repository.
    get_lines_for_update` 的行锁 + 乐观锁兜底，而 ``SELECT ... FOR UPDATE`` 配
    ``SELECT`` 子查询时 PG 只锁外层那一张表。
    """
    stmt = (
        select(BundlingOrder)
        .where(BundlingOrder.id == order_id, BundlingOrder.deleted_at.is_(None))
        .execution_options(populate_existing=True, synchronize_session=False)
        .with_for_update()
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def lock_cutting_outputs(
    session: AsyncSession, *, style_no: str, keys: Sequence[OutputKey]
) -> dict[OutputKey, CuttingOutput]:
    """锁本单涉及的结转行，返回 ``{(色码, 尺码): 行}``；**缺失的键不在返回里**。

    :param keys: 本单明细出现的 (色码, 尺码)。逐单去重后成批锁，避免 500 行明细
        发 500 条 ``FOR UPDATE``。
    :returns: 查不到的键**不返回**而不是返回空行 —— 「没有结转行」与
        「结转行可用量为 0」在提交时是同一个错（``30002``），不必让调用方区分。
    """
    if not keys:
        return {}
    stmt = (
        select(CuttingOutput)
        .where(CuttingOutput.style_no == style_no, CuttingOutput.deleted_at.is_(None))
        .where(tuple_(CuttingOutput.color_code, CuttingOutput.size_code).in_(list(keys)))
        .order_by(CuttingOutput.color_code, CuttingOutput.size_code)
        .with_for_update()
    )
    rows = (await session.execute(stmt)).scalars().all()
    return {(row.color_code, row.size_code): row for row in rows}


async def reserve_output_qty(
    session: AsyncSession, *, output_id: UUID, qty: Decimal, operator_id: UUID
) -> bool:
    """条件预占：``available >= :qty`` 才加，否则返回 ``False``（INV-6 不超发）。

    ⚠️ **必须条件 UPDATE，不能「先查再写」**：查完到写之间另一个事务能插进来，
    两张打菲单会同时看到同一份余量并双双预占成功 —— 超发的经典形态。
    service 拿 ``False`` 翻译成 ``30002``。

    ⚠️ 用 ``RETURNING`` 判成败而不是 ``rowcount``：mypy 看不到异步 ``Result`` 上的
    ``rowcount``，而 :meth:`~app.modules.bundling.service.common.CommonMixin._bump_header`
    已经是这个写法（同一份约定，不在这里发明第二种）。
    """
    stmt = (
        update(CuttingOutput)
        .where(CuttingOutput.id == output_id)
        .where(
            CuttingOutput.output_qty - CuttingOutput.bundled_qty - CuttingOutput.reserved_qty >= qty
        )
        .values(
            reserved_qty=CuttingOutput.reserved_qty + qty,
            version=CuttingOutput.version + 1,
            updated_by=operator_id,
        )
        .returning(CuttingOutput.id)
        .execution_options(synchronize_session=False)
    )
    return len((await session.execute(stmt)).all()) == 1


async def release_output_qty(
    session: AsyncSession, *, output_id: UUID, qty: Decimal, operator_id: UUID
) -> bool:
    """条件释放：``reserved_qty >= :qty`` 才减，否则返回 ``False``。

    ⚠️ 这个守卫防的是**减掉别人的预占**：条件不满足说明本单那一份已经不在了
    （数据被改过 / 被别的路径动过），无脑减会让 ``reserved_qty`` 变负 ——
    而 ``reserved_qty`` 变小等于凭空放大可打菲余量，直接破 INV-6。
    """
    stmt = (
        update(CuttingOutput)
        .where(CuttingOutput.id == output_id, CuttingOutput.reserved_qty >= qty)
        .values(
            reserved_qty=CuttingOutput.reserved_qty - qty,
            version=CuttingOutput.version + 1,
            updated_by=operator_id,
        )
        .returning(CuttingOutput.id)
        .execution_options(synchronize_session=False)
    )
    return len((await session.execute(stmt)).all()) == 1


async def snapshot_available_qty_before(
    session: AsyncSession,
    *,
    doc_id: UUID,
    color_code: str,
    size_code: str,
    available_qty: Decimal,
    operator_id: UUID,
) -> None:
    """把「提交时读到的可用量」快照到本单该 (色码, 尺码) 的明细行（§3.2 列语义）。

    ⚠️ **按 (色码, 尺码) 批量 UPDATE**，不是逐行 ORM 写：一张单最多 500 行明细，
    而同一个 (色码, 尺码) 可能有多行（ADR-0017 §4 同尺码多布批），
    它们读到的是**同一个**可用量快照。
    """
    await session.execute(
        update(BundlingOrderLine)
        .where(
            BundlingOrderLine.doc_id == doc_id,
            BundlingOrderLine.deleted_at.is_(None),
            BundlingOrderLine.color_code == color_code,
            BundlingOrderLine.size_code == size_code,
        )
        .values(
            available_qty_before=available_qty,
            version=BundlingOrderLine.version + 1,
            updated_by=operator_id,
        )
        .execution_options(synchronize_session=False)
    )


__all__ = [
    "OutputKey",
    "available_qty_of",
    "lock_cutting_outputs",
    "lock_order_for_transition",
    "release_output_qty",
    "reserve_output_qty",
    "snapshot_available_qty_before",
]
