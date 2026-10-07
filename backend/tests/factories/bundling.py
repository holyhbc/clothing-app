"""打菲模块的测试夹具与数据工厂（docs/10 §7）。

参考 ``tests/factories/cutting.py`` 的结构：
- 一个车间 + 一个款号 + 一个已审核裁剪单 + 它的尺码明细行
"""

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import DataScope, DocumentStatus
from app.core.permissions import AuthContext
from app.modules.base.models import MaterialStock, Operation, ProductCategory, Style
from app.modules.bundling.schemas import BundlingOrderCreateIn, LineIn
from app.modules.cutting.models import (
    CuttingOrder,
    CuttingOrderLine,
    CuttingOrderLineColor,
    CuttingOrderSizeLine,
)
from app.modules.cutting.service.recalc import recalc_color, recalc_line, recalc_order
from tests.factories.user import OPERATOR_ID, WorkshopFactory

#: 固定单据日期（docs/10 §10：时间相关测试必须可注入时间）
DOC_DATE = date(2026, 10, 18)


def ctx(workshop_id: UUID | None = None, scope: DataScope = DataScope.FACTORY) -> AuthContext:
    """造一个 AuthContext。默认 ``FACTORY`` —— 它不加任何范围条件。"""
    return AuthContext(
        user_id=OPERATOR_ID,
        name="测试操作人",
        employee_no="A001",
        workshop_id=workshop_id,
        group_no=None,
        permissions=frozenset(),
        data_scope=scope,
        allowed_workshop_ids=frozenset({workshop_id}) if workshop_id else frozenset(),
    )


async def make_style(db_session: AsyncSession, style_no: str = "BD-TEST-1") -> Style:
    """建一个款号（复用 ProductCategory.SET）。"""
    category = (
        await db_session.execute(select(ProductCategory).where(ProductCategory.code == "SET"))
    ).scalar_one()
    existing = (
        await db_session.execute(select(Style).where(Style.style_no == style_no))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    style = Style(
        style_no=style_no,
        name="打菲测试款",
        category_id=category.id,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    db_session.add(style)
    await db_session.flush()
    return style


async def ensure_operation(db_session: AsyncSession, operation_no: str = "OP01") -> Operation:
    """确保工序存在。"""
    op = (
        await db_session.execute(select(Operation).where(Operation.operation_no == operation_no))
    ).scalar_one_or_none()
    if op is not None:
        return op
    # 建一个车间作为关联
    workshop = await WorkshopFactory.create(db_session)
    op = Operation(
        operation_no=operation_no,
        name="打菲工序",
        workshop_id=workshop.id,
        is_piecework=True,
        default_bundle_qty=60,
        sort_order=1,
        is_active=True,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    db_session.add(op)
    await db_session.flush()
    return op


async def make_approved_cutting_order(
    db_session: AsyncSession,
    *,
    style: Style,
    workshop_id: UUID,
    color_code: str = "WHT",
    size_codes: list[str] | None = None,
) -> CuttingOrder:
    """建一张 **已审核** 的裁剪单，含尺码明细行（打菲的来源单据）。

    返回的 CuttingOrder 已 flush，其 size_lines 已包含 hands / qty_per_hand。
    """
    if size_codes is None:
        size_codes = ["S", "M", "L", "XL"]

    # 先建布批（level=MaterialStock）
    from tests.modules.test_stock_basis import _material

    material = await _material(db_session)
    stock = MaterialStock(
        warehouse_id=(
            await db_session.execute(text("SELECT id FROM warehouses WHERE code = 'FAB'"))
        ).scalar_one(),
        material_id=material.id,
        supplier_id=None,
        dye_lot_no=f"DY-{style.style_no}",
        bolt_no="B-1",
        width_cm=Decimal("152.00"),
        stock_qty=Decimal("500.000"),
        total_length_m=Decimal("500.000"),
        locked_qty=Decimal("0.000"),
        unit_cost=Decimal("12.500000"),
        in_date=DOC_DATE,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    db_session.add(stock)
    await db_session.flush()

    # 建裁剪单（三层结构）
    from app.core.numbering import DOC_PREFIX_CUTTING, take_doc_no

    doc_no = await take_doc_no(db_session, prefix=DOC_PREFIX_CUTTING, doc_date=DOC_DATE)
    cutting = CuttingOrder(
        doc_no=doc_no,
        workshop_id=workshop_id,
        style_id=style.id,
        style_no=style.style_no,
        color_codes=color_code,
        doc_date=DOC_DATE,
        delivery_date=DOC_DATE,
        ply_count=100,
        entry_mode_default="MASTER",
        status=DocumentStatus.APPROVED,  # ★ 必须是 APPROVED
        remark_source="测试",
        remark=None,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    db_session.add(cutting)
    await db_session.flush()

    # 布批行
    line = CuttingOrderLine(
        doc_id=cutting.id,
        line_no=1,
        stock_id=stock.id,
        supplier_id=stock.supplier_id,
        material_id=stock.material_id,
        style_no=cutting.style_no,
        dye_lot_no=stock.dye_lot_no,
        bolt_no=stock.bolt_no,
        color_plan=color_code,
        width_cm=stock.width_cm,
        fabric_qty=Decimal("200.000"),
        waste_qty=Decimal("5.000"),
        output_qty=Decimal("240.000"),
        remark=None,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    db_session.add(line)
    await db_session.flush()

    # 颜色行
    color = CuttingOrderLineColor(
        line_id=line.id,
        color_code=color_code,
        entry_mode="MASTER",
        qty_per_hand=60,
        uniform_qty=Decimal("0"),
        hands_total=Decimal("0"),
        output_qty_total=Decimal("0"),
        balance_qty_total=Decimal("0"),
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    db_session.add(color)
    await db_session.flush()

    # 尺码明细行（每个尺码 hands=1, qty_per_hand=60 -> output_qty=60）
    for idx, size_code in enumerate(size_codes, start=1):
        size_line = CuttingOrderSizeLine(
            line_color_id=color.id,
            line_id=line.id,
            size_line_no=idx,
            size_code=size_code,
            hands=1,
            qty_per_hand=60,
            output_qty=60,
            output_qty_manual=False,
            balance_qty=0,
            hands_seq=idx,
            remark=None,
            created_by=OPERATOR_ID,
            updated_by=OPERATOR_ID,
        )
        db_session.add(size_line)

    await db_session.flush()

    # 重算裁剪单汇总（直接用已 flush 的对象，不走关系懒加载）
    # 查出刚插入的尺码明细行
    size_lines = list(
        (
            await db_session.execute(
                select(CuttingOrderSizeLine)
                .where(CuttingOrderSizeLine.line_color_id == color.id)
                .where(CuttingOrderSizeLine.deleted_at.is_(None))
                .order_by(CuttingOrderSizeLine.size_line_no)
            )
        )
        .scalars()
        .all()
    )

    recalc_color(color, size_lines)
    recalc_line(line, [color])
    recalc_order(cutting, [line], lambda _line: [color])

    await db_session.flush()
    return cutting


async def build_world(db_session: AsyncSession, style_no: str = "BD-TEST-1") -> dict[str, Any]:
    """造「一个车间 + 一个款号 + 一个已审核裁剪单 + 它的尺码明细行」这套最小世界。"""
    workshop = await WorkshopFactory.create(db_session)
    await ensure_operation(db_session, "OP01")
    style = await make_style(db_session, style_no=style_no)
    cutting = await make_approved_cutting_order(
        db_session, style=style, workshop_id=workshop.id, color_code="WHT"
    )
    await db_session.flush()

    # 取出裁剪单的尺码明细行 id
    size_lines = list(
        (
            await db_session.execute(
                select(CuttingOrderSizeLine)
                .join(CuttingOrderLineColor)
                .join(CuttingOrderLine)
                .where(CuttingOrderLine.doc_id == cutting.id)
                .where(CuttingOrderSizeLine.deleted_at.is_(None))
                .order_by(CuttingOrderSizeLine.size_line_no)
            )
        )
        .scalars()
        .all()
    )

    return {
        "workshop": workshop,
        "style": style,
        "cutting_order": cutting,
        "cutting_size_lines": size_lines,
    }


# ------------------------------------------------------------------ 入参快捷方式


def line_in(
    cutting_size_line_id: UUID,
    *,
    line_no: int = 1,
    size_code: str = "L",
    color_code: str = "WHT",
    hands: int = 1,
    operation_no: str = "OP01",
    group_no: str | None = None,
    workstation_no: str | None = None,
) -> LineIn:
    """一条打菲明细入参（必带 cutting_size_line_id）。"""
    return LineIn(
        line_no=line_no,
        color_code=color_code,
        size_code=size_code,
        operation_no=operation_no,
        cutting_size_line_id=cutting_size_line_id,
        hands=hands,
        group_no=group_no,
        workstation_no=workstation_no,
    )


def payload(world: dict[str, Any], **overrides: Any) -> BundlingOrderCreateIn:
    """最小可用 payload（一行，引用第一个裁剪尺码明细行）。"""
    size_line = world["cutting_size_lines"][0]
    base: dict[str, Any] = {
        "workshop_id": world["workshop"].id,
        "style_no": world["style"].style_no,
        "operation_no": "OP01",
        "color_group": "WHT",
        "color_code": "WHT",
        "bundle_qty": 60,
        "doc_date": DOC_DATE,
        "source_cutting_order_id": world["cutting_order"].id,
        "lines": [line_in(size_line.id, size_code=size_line.size_code)],
    }
    # 处理 lines 覆盖：需要保证每行都有完整的必填字段
    if "lines" in overrides:
        # 覆盖时直接用传入的 lines
        base["lines"] = [
            line_in(
                item["cutting_size_line_id"],
                line_no=item.get("line_no", i + 1),
                size_code=item.get("size_code", size_line.size_code),
                color_code=item.get("color_code", "WHT"),
                hands=item.get("hands", 1),
                operation_no=item.get("operation_no", "OP01"),
            )
            for i, item in enumerate(overrides["lines"])
        ]
        # 移除 lines，避免双重处理
        overrides = {k: v for k, v in overrides.items() if k != "lines"}
    return BundlingOrderCreateIn(**{**base, **overrides})


def wid(world: dict[str, Any], key: str) -> UUID:
    """取 world 里某个对象的 id（实体或纯 id 都行）。"""
    value = world[key]
    return value.id if hasattr(value, "id") else UUID(str(value))


__all__ = [
    "DOC_DATE",
    "build_world",
    "ctx",
    "line_in",
    "make_approved_cutting_order",
    "make_style",
    "payload",
    "wid",
]
