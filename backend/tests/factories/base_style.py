"""款号 / 模板复制的数据工厂。

⚠️ **为什么单独成模块而不是复用 `test_base_style_service` 里的 `_style` / `_copy_source`**：
测试之间互相 import 是坏味道（pytest 的导入顺序会跟着变得不可预测），
而 `style_child_router` 的幂等回归（``test_style_child_idempotency``）需要**同样的源款数据**。
两边各抄一份则会出现「service 测试种的是 2 道工序、router 测试种的是 3 道」，
而差异的根因要跨两个文件才看得出来。
"""

from datetime import date
from decimal import Decimal

from app.modules.base.models import (
    Operation,
    OperationRate,
    ProductCategory,
    Style,
    StyleOperation,
)
from tests.factories.user import OPERATOR_ID

#: 工序单价的生效日 —— ``_copy_source`` 与断言共用，避免两处日期漂移
D1 = date(2026, 1, 1)


async def _category(session, name: str = "测试分类") -> ProductCategory:
    row = ProductCategory(
        code=f"CAT-{name}",
        name=name,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    session.add(row)
    await session.flush()
    return row


async def make_style(session, style_no: str, *, category: ProductCategory | None = None) -> Style:
    row = Style(
        style_no=style_no,
        name=f"款名 {style_no}",
        category_id=(category or await _category(session)).id,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    session.add(row)
    await session.flush()
    return row


async def make_operation(session, operation_no: str) -> Operation:
    row = Operation(
        operation_no=operation_no,
        name=f"工序{operation_no}",
        is_active=True,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    session.add(row)
    await session.flush()
    return row


async def copy_source(session) -> tuple[Style, Style]:
    """模板复制的源款 + 目标款（与 `test_base_style_service._copy_source` 同款数据）。

    源款：2 道工序（01/02）+ 2 条工序单价（0.350000 / 0.300000）。
    目标款：**空**（复制要写的就是它）。
    """
    category = await _category(session)
    source = await make_style(session, "HB-2026-0001", category=category)
    for no in ("01", "02"):
        await make_operation(session, no)

    session.add_all(
        [
            StyleOperation(
                style_id=source.id,
                style_no=source.style_no,
                operation_no="01",
                sequence=1,
                bundle_qty=Decimal("1"),
                created_by=OPERATOR_ID,
                updated_by=OPERATOR_ID,
            ),
            StyleOperation(
                style_id=source.id,
                style_no=source.style_no,
                operation_no="02",
                sequence=2,
                bundle_qty=Decimal("12"),
                is_final_operation=True,
                created_by=OPERATOR_ID,
                updated_by=OPERATOR_ID,
            ),
        ]
    )
    session.add_all(
        [
            OperationRate(
                operation_no="01",
                style_id=source.id,
                style_no=source.style_no,
                effective_from=D1,
                unit_price=Decimal("0.350000"),
                created_by=OPERATOR_ID,
                updated_by=OPERATOR_ID,
            ),
            OperationRate(
                operation_no="02",
                style_id=source.id,
                style_no=source.style_no,
                effective_from=D1,
                unit_price=Decimal("0.300000"),
                created_by=OPERATOR_ID,
                updated_by=OPERATOR_ID,
            ),
        ]
    )
    await session.flush()
    target = await make_style(session, "HB-2026-0002", category=category)
    return source, target
