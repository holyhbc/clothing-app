"""E2E 的**已审核来源裁剪单**（打菲 E2E 的前置数据，ADR-0030 拆分）。

## 为什么单独一个文件

`_seed_approved_cutting` 原本挤在 ``tests/e2e_seed.py`` 里，而那文件已经顶到
400 行硬线（ADR-0030）。它也确实该独立：它是**唯一**一处**故意绕过 service
层、直接落库**的建数逻辑，理由写在该函数的文件级注释里 —— 而
「这一段是例外」这件事，必须一眼可见，不能埋在 400 行的中间。

## 它造的到底是什么

一张 `status='APPROVED'` 的裁剪单 + 尺码明细行 + ``cutting_outputs``（余量登记），
供打菲 E2E 当来源单。裁剪侧的几何（XL 2 手 / L 1 手，每手 60 件）与
``frontend/e2e/p1-bundling-flow.spec.ts`` 里的断言一一对应 ——
改这里就得同步改那边的期望值。
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import DocumentStatus
from app.core.permissions import AuthContext
from app.modules.base.models import MaterialStock, Style, Workshop
from app.modules.cutting.models import (
    CuttingOrder,
    CuttingOrderLine,
    CuttingOrderLineColor,
    CuttingOrderSizeLine,
    CuttingOutput,
)

#: 打菲 E2E 的**已审核**来源裁剪单。B8 要求 `APPROVED`，而裁剪模块没有
#: submit/approve 端点（裁剪单走不到 APPROVED），只能由 seed 直接落库。
APPROVED_CUTTING_DOC_NO = "CT-E2E-APPROVED-0001"
CUTTING_COLOR_CODE = "WHT"
CUTTING_COLOR_GROUP = "WHT-GRP"
CUTTING_DOC_DATE = date(2026, 10, 1)
CUTTING_QTY_PER_HAND = 60
#: (尺码, 手数) —— 与 `p1-bundling-flow.spec.ts` 里的断言一一对应
CUTTING_SIZE_PLAN: tuple[tuple[str, int], ...] = (("XL", 2), ("L", 1))


@dataclass(frozen=True)
class SourceKeys:
    """这张来源单挂靠的**上游数据**主键（款号 / 布批），由 ``e2e_seed.py`` 建好后传进来。

    ⚠️ 同 :class:`tests.e2e_seed_accounts.BusinessKeys`：真源在 ``e2e_seed.py``，
      而那个文件 import 本模块，所以只能往这个方向单向依赖。
    """

    style_no: str
    dye_lot_no: str


async def seed_approved_cutting(session: AsyncSession, ctx: AuthContext, keys: SourceKeys) -> None:
    """一张**已审核**的裁剪单 + 尺码明细行 + ``cutting_outputs``（打菲 E2E 的来源单）。

    ⚠️ **为什么必须 seed 而不是走 API**：裁剪模块**没有提交/审核端点** ——
    ``openapi.json`` 里 ``/cutting-orders`` 下只有 ``get/post``，
    ``{order_id}`` 下只有 ``delete/get/patch``，再加 ``lines`` / ``size-lines``
    / ``entry-mode`` / ``suggest-lines``，**没有任何 submit/approve 路径**。
    也就是说裁剪单在系统里**根本走不到 APPROVED**。

    而 B8 硬要求「来源裁剪单必须 ``status='APPROVED'``」，新建打菲单没有它就
    建不出来。所以只能直接落库。

    ⚠️ 这不是「测试偷懒绕过校验」：seed 造的是**一张前置数据**，
    不是「跳过建单校验」。建打菲单时的 APPROVED 校验照样跑、照样会拦。

    ⚠️ **幂等**：E2E 库跨轮次复用，靠 ``doc_no`` 判存在再返回，
    否则第二轮撞 ``uq_cutting_orders_doc_no``，报错指向唯一索引，
    与「谁跑过 E2E」毫无关系。
    """
    existing = (
        await session.execute(
            select(CuttingOrder).where(CuttingOrder.doc_no == APPROVED_CUTTING_DOC_NO)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return

    workshop = (
        (await session.execute(select(Workshop).where(Workshop.deleted_at.is_(None))))
        .scalars()
        .first()
    )
    style = (
        await session.execute(
            select(Style).where(Style.style_no == keys.style_no, Style.deleted_at.is_(None))
        )
    ).scalar_one()
    stock = (
        await session.execute(
            select(MaterialStock).where(MaterialStock.dye_lot_no == keys.dye_lot_no)
        )
    ).scalar_one()

    total_hands = sum(hands for _, hands in CUTTING_SIZE_PLAN)
    session.add(
        CuttingOrder(
            doc_no=APPROVED_CUTTING_DOC_NO,
            workshop_id=workshop.id,
            style_id=style.id,
            style_no=style.style_no,
            color_codes=CUTTING_COLOR_CODE,
            doc_date=CUTTING_DOC_DATE,
            delivery_date=CUTTING_DOC_DATE,
            ply_count=100,
            entry_mode_default="MASTER",
            fabric_qty=Decimal("120.000"),
            output_qty=Decimal(str(total_hands * CUTTING_QTY_PER_HAND)),
            cut_waste_qty=Decimal("0.000"),
            balance_qty=Decimal("0.000"),
            # ★ 必须 APPROVED —— B8：来源裁剪单未审核则建单报 10001
            status=DocumentStatus.APPROVED,
            hands_total=total_hands,
            remark_source="E2E 种子：打菲来源单",
            approved_by=ctx.user_id,
            approved_at=datetime(2026, 10, 1, 8, 0, tzinfo=UTC),
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
        )
    )
    await session.flush()

    order = (
        await session.execute(
            select(CuttingOrder).where(CuttingOrder.doc_no == APPROVED_CUTTING_DOC_NO)
        )
    ).scalar_one()

    session.add(
        CuttingOrderLine(
            doc_id=order.id,
            line_no=1,
            stock_id=stock.id,
            supplier_id=stock.supplier_id,
            material_id=stock.material_id,
            style_no=style.style_no,
            dye_lot_no=stock.dye_lot_no,
            bolt_no=stock.bolt_no,
            color_plan=CUTTING_COLOR_CODE,
            width_cm=stock.width_cm,
            fabric_qty=Decimal("120.000"),
            waste_qty=Decimal("0.000"),
            output_qty=Decimal(str(total_hands * CUTTING_QTY_PER_HAND)),
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
        )
    )
    await session.flush()

    line = (
        await session.execute(select(CuttingOrderLine).where(CuttingOrderLine.doc_id == order.id))
    ).scalar_one()

    session.add(
        CuttingOrderLineColor(
            line_id=line.id,
            color_code=CUTTING_COLOR_CODE,
            entry_mode="MASTER",
            qty_per_hand=CUTTING_QTY_PER_HAND,
            uniform_qty=Decimal("0"),
            hands_total=Decimal(str(total_hands)),
            output_qty_total=Decimal(str(total_hands * CUTTING_QTY_PER_HAND)),
            balance_qty_total=Decimal("0"),
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
        )
    )
    await session.flush()

    color = (
        await session.execute(
            select(CuttingOrderLineColor).where(CuttingOrderLineColor.line_id == line.id)
        )
    ).scalar_one()

    for index, (size_code, hands) in enumerate(CUTTING_SIZE_PLAN, start=1):
        output_qty = hands * CUTTING_QTY_PER_HAND
        session.add(
            CuttingOrderSizeLine(
                line_id=line.id,
                # ⚠️ 尺码明细行挂的是 **line_color_id**（色行），不是 line_id ——
                #    挂错会在 flush 时才炸，报错是外键约束，与「该挂哪一层」毫无关系。
                line_color_id=color.id,
                size_line_no=index,
                size_code=size_code,
                hands=hands,
                qty_per_hand=CUTTING_QTY_PER_HAND,
                output_qty=output_qty,
                output_qty_manual=False,
                balance_qty=0,
                hands_seq=index,
                created_by=ctx.user_id,
                updated_by=ctx.user_id,
            )
        )
        # ⚠️ `cutting_outputs` 是「可打菲余量」的登记处（`v_cutting_output_available`
        #   的数据源）。**不建这一行**，来源明细接口返回的 available_qty 会是 0，
        #   E2E 会失败在「余量为 0」这种看起来像业务规则的地方。
        session.add(
            CuttingOutput(
                style_id=style.id,
                style_no=style.style_no,
                color_code=CUTTING_COLOR_CODE,
                size_code=size_code,
                workshop_id=workshop.id,
                output_qty=str(output_qty),
                balance_qty="0",
                bundled_qty="0",
                reserved_qty="0",
                cut_waste_qty="0",
                created_by=ctx.user_id,
                updated_by=ctx.user_id,
            )
        )
    await session.flush()
