"""打菲**轻状态迁移**测试的共享助手（T-BUND-005a）。

⚠️ 单独成模块而不是留在某个 ``test_*.py`` 里：``test_bundling_state.py`` 曾涨到 429 行
（超 ADR-0030 的 400 行硬线），拆出「提交预检」那组用例后两个测试文件都要用到这些助手
—— 助手留在任一测试文件里，另一个就得跨测试模块 import（测试之间互相 import 是坏味道，
pytest 的导入顺序也会跟着变得不可预测）。
"""

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import DocumentStatus
from app.common.models import DocumentLog
from app.modules.bundling.models import BundlingOrder
from app.modules.bundling.service import BundlingOrderService
from tests.factories.bundling import attach_output, ctx, payload
from tests.factories.user import OPERATOR_ID

ZERO = Decimal("0")
SIZE = "S"  # factory.build_world 的第一条尺码明细行（hands=1, qty_per_hand=60）
COLOR = "WHT"


async def status(db_session: AsyncSession, order_id: UUID) -> DocumentStatus:
    row = await db_session.scalar(
        select(BundlingOrder.status)
        .where(BundlingOrder.id == order_id)
        .execution_options(populate_existing=True)
    )
    return DocumentStatus(row)


async def logs(db_session: AsyncSession, order_id: UUID) -> list[DocumentLog]:
    return list(
        (
            await db_session.scalars(
                select(DocumentLog).where(
                    DocumentLog.doc_type == "BundlingOrder", DocumentLog.doc_id == order_id
                )
            )
        ).all()
    )


async def make_output(db_session: AsyncSession, world: dict[str, Any], qty: Decimal) -> None:
    await attach_output(
        db_session,
        world["style"].id,
        world["style"].style_no,
        world["workshop"].id,
        size_code=SIZE,
        output_qty=qty,
    )


def line(world: dict[str, Any], *, size_code: str = SIZE, **extra: Any) -> dict[str, Any]:
    size_line = next(sl for sl in world["cutting_size_lines"] if sl.size_code == size_code)
    return {"cutting_size_line_id": size_line.id, "size_code": size_code, **extra}


async def create(
    db_session: AsyncSession, world: dict[str, Any], **overrides: Any
) -> BundlingOrder:
    return await BundlingOrderService(db_session).create(payload(world, **overrides), OPERATOR_ID)


async def submitted(
    db_session: AsyncSession, world: dict[str, Any]
) -> tuple[BundlingOrder, BundlingOrderService]:
    await make_output(db_session, world, Decimal("60"))
    order = await create(db_session, world)
    service = BundlingOrderService(db_session)
    await service.submit(order.id, OPERATOR_ID, ctx())
    return order, service
