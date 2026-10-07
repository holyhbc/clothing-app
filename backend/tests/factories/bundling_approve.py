"""打菲**审核 / 反审核**用例共用的测试世界（T-BUND-005b）。

⚠️ 单开一个模块而不是塞进 :mod:`tests.factories.bundling`（已 400 行硬线，ADR-0030）：
审核用例要的东西（把裁剪尺码明细改成指定手数、读 ``bundled_qty``）只有审核路径用，
放进通用工厂等于让每个用例都拖一份用不上的依赖。

⚠️ 这里的 ``_ensure_sizes`` 是审核用例的**地基**：``XXL`` / ``3XL`` / ``Z01`` 这些尺码在
工厂默认世界里**压根没有对应的裁剪尺码明细行**（而打菲行的 ``cutting_size_line_id`` 是必填
外键），少了补齐这一步，用例会报「裁剪明细行不存在」—— 报错指向打菲，根因却在测试世界。
"""

from dataclasses import replace
from decimal import Decimal
from typing import Any, NamedTuple
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import DocumentStatus
from app.common.models import DocumentLog
from app.modules.bundling.models import Bundle, BundlingOrder
from app.modules.bundling.service import BundlingOrderService
from app.modules.cutting.models import CuttingOrderSizeLine, CuttingOutput
from tests.factories.bundling import attach_output, ctx, payload
from tests.factories.user import OPERATOR_ID

COLOR = "WHT"

REVIEWER_ID = UUID("11111111-2222-3333-4444-555555555555")

#: 1:2:2:1 的手数分配（卡面 TC-AP-01）。⚠️ 尺码码用 ``3XL`` 这种**带数字**的：它是
#: ``bundle_no`` 拼接的边界用例（尺码码 1~3 位 × 手序号固定 2 位之间没有分隔符，
#: Q-B18），少了它「尺码码只能字母」的限制会被后来的人悄悄收窄。
SIZE_PLAN: tuple[tuple[str, int], ...] = (("L", 1), ("XL", 2), ("XXL", 2), ("3XL", 1))


def reviewer_ctx() -> Any:
    """审核人上下文（工厂全厂范围，与制单人只差身份）。"""
    return replace(ctx(), user_id=REVIEWER_ID, name="审核人", employee_no="A002")


# ------------------------------------------------------------------ 助手


class CodeRow(NamedTuple):
    """一行 ``bundles`` 的断言视图（见 :func:`order_codes`）。"""

    bundle_no: str
    size_code: str
    hands: int
    bundle_qty: Decimal
    status: str


async def order_status(db_session, order_id: UUID) -> DocumentStatus:
    row = await db_session.scalar(
        select(BundlingOrder.status)
        .where(BundlingOrder.id == order_id)
        .execution_options(populate_existing=True)
    )
    return DocumentStatus(row)


async def order_codes(db_session, order_id: UUID) -> list[CodeRow]:
    """本单的 ``(bundle_no, size_code, hands, bundle_qty, status)``，按尺码+手号排序。

    ⚠️ **``size_code`` 从列上查，不从 ``bundle_no`` 反解**：反解要靠「手序号恒 2 位」倒推
    尺码码长度（Q-B18 允许 1~3 位），一旦码里出现 ``Z199`` 这种「2 位尺码 + 第 99 手」，
    任何按长度切的写法都会把 ``Z1`` 认成 ``Z19``。列是权威的就直接查列。
    """
    rows = (
        await db_session.execute(
            select(
                Bundle.bundle_no, Bundle.size_code, Bundle.hands, Bundle.bundle_qty, Bundle.status
            )
            .where(Bundle.doc_id == order_id, Bundle.deleted_at.is_(None))
            .order_by(Bundle.size_code, Bundle.hands)
            .execution_options(populate_existing=True)
        )
    ).all()
    return [CodeRow(r[0], r[1], r[2], Decimal(r[3]), str(r[4])) for r in rows]


async def order_logs(db_session, order_id: UUID) -> list[DocumentLog]:
    return list(
        (
            await db_session.scalars(
                select(DocumentLog).where(
                    DocumentLog.doc_type == "BundlingOrder", DocumentLog.doc_id == order_id
                )
            )
        ).all()
    )


async def prepare_order(
    db_session,
    world,
    plan: tuple[tuple[str, int], ...] = SIZE_PLAN,
    *,
    qty_per_hand: int = 60,
) -> BundlingOrder:
    """建一张**已提交**的打菲单：裁剪侧手数 = 打菲手数，结转余量充足。

    审核的 ③ 防超打比的是「本单 Σhands ≤ 裁剪 Σhands」，所以默认让两侧相等 ——
    差值是 TC-AP-03 专门要造的。
    """
    await ensure_sizes(db_session, world, plan, qty_per_hand=qty_per_hand)
    lines = [
        {
            "cutting_size_line_id": next(
                row.id for row in world["cutting_size_lines"] if row.size_code == size_code
            ),
            "size_code": size_code,
            "hands": hands,
        }
        for size_code, hands in plan
    ]
    order = await BundlingOrderService(db_session).create(payload(world, lines=lines), OPERATOR_ID)
    await BundlingOrderService(db_session).submit(order.id, OPERATOR_ID, ctx())
    return order


async def ensure_sizes(
    db_session,
    world,
    plan: tuple[tuple[str, int], ...],
    *,
    qty_per_hand: int = 60,
) -> None:
    """把 world 调整成 ``plan`` 要求的样子：裁剪尺码明细行（补齐/改手数）+ 裁剪结转行。

    ⚠️ **补齐与改手数必须都做**：``XXL`` / ``3XL`` / ``Z01``… 这些尺码工厂的 world 里**压根
    没有**对应的裁剪明细行（而打菲行的 ``cutting_size_line_id`` 是必填外键），而 ``XL``
    这类已有尺码又必须把 ``hands`` 从工厂默认的 1 改掉 —— 只做前者会让 ③ 防超打拿
    「裁剪 1 手 vs 打菲 2 手」判成超打，而报错完全看不出根因是测试世界没铺够。
    """
    from app.modules.cutting.models import CuttingOrderLineColor

    color = (
        await db_session.execute(
            select(CuttingOrderLineColor).where(CuttingOrderLineColor.color_code == COLOR).limit(1)
        )
    ).scalar_one()
    known = {row.size_code for row in world["cutting_size_lines"]}
    next_no = max(row.size_line_no for row in world["cutting_size_lines"]) + 1
    for size_code, hands in plan:
        if size_code in known:
            await set_size_hands(
                db_session, world, size_code, hands=hands, qty_per_hand=qty_per_hand
            )
        else:
            row = CuttingOrderSizeLine(
                line_color_id=color.id,
                line_id=color.line_id,
                size_line_no=next_no,
                size_code=size_code,
                hands=hands,
                qty_per_hand=qty_per_hand,
                output_qty=hands * qty_per_hand,
                output_qty_manual=False,
                balance_qty=0,
                created_by=OPERATOR_ID,
                updated_by=OPERATOR_ID,
            )
            db_session.add(row)
            await db_session.flush()
            world["cutting_size_lines"].append(row)
            known.add(size_code)
            next_no += 1
    # 结转行（可打菲余量的唯一来源）：每个尺码一行，余量恰好够打满
    for size_code, hands in plan:
        await attach_output(
            db_session,
            world["style"].id,
            world["style"].style_no,
            world["workshop"].id,
            size_code=size_code,
            output_qty=Decimal(hands * qty_per_hand),
        )


async def approve_order(db_session, order: BundlingOrder) -> BundlingOrder:
    return await BundlingOrderService(db_session).approve(order.id, REVIEWER_ID, reviewer_ctx())


async def read_bundled(session: AsyncSession, style_no: str, *, size_code: str) -> Decimal:
    """重读结转行的 ``bundled_qty``（审核结转 / 反审核还原的断言入口）。

    ⚠️ ``populate_existing`` 必须带：结转走 Core UPDATE，ORM 对象仍是旧值，断言会假绿
    （与 :func:`~tests.factories.bundling.read_reserved` 同一个理由）。
    """
    row = (
        await session.execute(
            select(CuttingOutput)
            .where(
                CuttingOutput.style_no == style_no,
                CuttingOutput.color_code == COLOR,
                CuttingOutput.size_code == size_code,
            )
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    return Decimal(row.bundled_qty)


def _find_size_line(world: dict[str, Any], size_code: str) -> CuttingOrderSizeLine:
    """取 world 里某条尺码明细行；**不存在时明确报错**而不是 ``next()`` 抛 StopIteration。

    ⚠️ 不用 ``next(...)``：PEP 479 把它包成 ``RuntimeError: coroutine raised
    StopIteration``，报错点在工厂深处，与「这个尺码压根没建」之间隔着三层。
    """
    for row in world["cutting_size_lines"]:
        if row.size_code == size_code:
            return row
    raise AssertionError(
        f"裁剪侧没有尺码 {size_code} 的明细行；现有 {[r.size_code for r in world['cutting_size_lines']]}"
    )


async def set_size_hands(
    session: AsyncSession,
    world: dict[str, Any],
    size_code: str,
    *,
    hands: int,
    qty_per_hand: int = 60,
) -> CuttingOrderSizeLine:
    """把 world 里某条裁剪尺码明细行的手数 / 每手件数改成指定值（审核用例的前置）。

    ⚠️ 改完必须**同步 ``output_qty``**：裁剪侧的口径是 ``output_qty = hands × qty_per_hand``
    （ADR-0020 精确整数），而打菲的 ③ 防超打正是拿「本单 Σhands」与「裁剪 Σhands」比 ——
    只改 ``hands`` 不改 ``output_qty`` 会造出「裁剪说 2 手、出数却是 60 件」的坏数据，
    而用例失败时报错指向的是打菲审核，完全看不出根因在这儿。

    :returns: 该尺码明细行（供用例引用它的 ``id``）。
    """
    size_line = _find_size_line(world, size_code)
    size_line.hands = hands
    size_line.qty_per_hand = qty_per_hand
    size_line.output_qty = hands * qty_per_hand
    size_line.output_qty_manual = False
    await session.flush()
    return size_line


__all__ = [
    "COLOR",
    "REVIEWER_ID",
    "SIZE_PLAN",
    "CodeRow",
    "approve_order",
    "ensure_sizes",
    "order_codes",
    "order_logs",
    "order_status",
    "payload",
    "prepare_order",
    "read_bundled",
    "reviewer_ctx",
    "set_size_hands",
]
