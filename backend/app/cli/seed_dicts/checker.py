"""字典内置库完整性校验（``seed_baseline --check`` 用）。"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from app.cli.seed_dicts.builtin_data import (
    BUILTIN_COLORS,
    BUILTIN_MATERIAL_CATEGORIES,
    BUILTIN_PRODUCT_CATEGORIES,
    BUILTIN_SIZE_GROUPS,
    BUILTIN_SIZES,
    BUILTIN_UOM_UNITS,
)
from app.modules.base.models import (
    Color,
    MaterialCategory,
    ProductCategory,
    Size,
    SizeGroup,
    SizeGroupItem,
    UomUnit,
)


async def check_dict_library(conn: AsyncConnection) -> list[str]:
    """校验字典内置库完整性（``--check`` 用）。

    检查两件事：内置行**存在**，且数量与定稿清单一致。**不检查**名称与
    ``is_active`` 是否被改过 —— 那是用户的合法改动（TC-B01）。
    """
    problems: list[str] = []
    checks = (
        (
            "colors",
            select(func.count()).select_from(Color).where(Color.is_builtin),
            len(BUILTIN_COLORS),
        ),
        (
            "sizes",
            select(func.count()).select_from(Size).where(Size.is_builtin),
            len(BUILTIN_SIZES),
        ),
        (
            "size_groups",
            select(func.count()).select_from(SizeGroup).where(SizeGroup.is_builtin),
            len(BUILTIN_SIZE_GROUPS),
        ),
        (
            "product_categories",
            select(func.count()).select_from(ProductCategory),
            len(BUILTIN_PRODUCT_CATEGORIES),
        ),
        (
            # ⚠️ 计量单位**数全表**：它是固定集（``BUILTIN_UOM_UNITS`` 就三条），
            #    用户自建的不算在内，所以不能按 is_builtin 数
            "uom_units",
            select(func.count()).select_from(UomUnit),
            len(BUILTIN_UOM_UNITS),
        ),
        (
            # ⚠️ 数 **is_builtin** 而不是全表：类目码表是**用户可扩展**的
            #    （09 §2.3「不做格式正则校验」），用户加一个 ``ZZ-1`` 不该让
            #    ``seed_baseline --check`` 报「数量不符」
            "material_categories",
            select(func.count()).select_from(MaterialCategory).where(MaterialCategory.is_builtin),
            len(BUILTIN_MATERIAL_CATEGORIES),
        ),
    )
    for label, stmt, expected in checks:
        actual = int(await conn.scalar(stmt) or 0)
        if actual != expected:
            problems.append(f"内置 {label} 数量 {actual} != 定稿清单 {expected}")

    # 码表成员数：两个模板共 8 条
    # 内置码表的成员数：只数内置码表下的成员，避免用户自建码表把数字冲高
    items = int(
        await conn.scalar(
            select(func.count())
            .select_from(SizeGroupItem)
            .join(SizeGroup, SizeGroup.id == SizeGroupItem.size_group_id)
            .where(SizeGroup.is_builtin)
        )
        or 0
    )
    if items != 8:
        problems.append(f"内置码表成员 {items} 条 != 8 条")
    return problems
