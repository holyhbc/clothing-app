"""字典内置库的写入逻辑（幂等 + 不覆盖用户改动 + 墓碑跳过）。

三条口径：
1. ``ON CONFLICT DO NOTHING``，**不覆盖**用户改过的名称与 ``is_active``（R26）
2. 墓碑跳过：被真删的内置行**不复活**（ADR-0025 决策 3）；恢复走
   :mod:`app.cli.restore_builtin`
3. 码表成员按 ``(size_code, size_class)`` 取尺码，不是只按码取（ADR-0027）
"""

from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncConnection

from app.cli.seed_dicts.builtin_data import (
    _SEED_OPERATOR,
    BUILTIN_COLORS,
    BUILTIN_MATERIAL_CATEGORIES,
    BUILTIN_PRODUCT_CATEGORIES,
    BUILTIN_SIZE_GROUPS,
    BUILTIN_SIZES,
    BUILTIN_UOM_UNITS,
    DICT_DOC_TYPES,
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


async def tombstoned_codes(conn: AsyncConnection, doc_type: str) -> set[str]:
    """列出被真删且**尚未恢复**的内置项编码。

    墓碑判定口径与 :func:`app.cli.restore_builtin.clear_tombstones` 一致：
    存在 ``DELETE`` 且**不存在更新的** ``RESTORE``。

    ⚠️ 这是 TC-B21 的关键。``ON CONFLICT DO NOTHING`` 只在唯一键冲突时跳过；
    行被**物理删除**后表里就没有冲突了，seed 会把它重新插回来 —— 也就是
    "用户真删的内置项自动复活"，与 ADR-0025 决策 3 直接冲突。
    """
    rows = (
        (
            await conn.execute(
                text(
                    "SELECT DISTINCT deleted.doc_no FROM document_logs deleted "
                    "WHERE deleted.doc_type = :doc_type AND deleted.action = 'DELETE' "
                    "  AND NOT EXISTS ("
                    "    SELECT 1 FROM document_logs restored "
                    "    WHERE restored.doc_type = deleted.doc_type "
                    "      AND restored.doc_no = deleted.doc_no "
                    "      AND restored.action = 'RESTORE' "
                    "      AND restored.created_at >= deleted.created_at)"
                ),
                {"doc_type": doc_type},
            )
        )
        .scalars()
        .all()
    )
    return {str(item) for item in rows if item}


async def seed_dict_library(conn: AsyncConnection) -> dict[str, int]:
    """写入字典内置库。**幂等且不覆盖用户改动**（04 §7.4 / R26）。

    三条口径：
    1. ``ON CONFLICT DO NOTHING`` —— 冲突即跳过，**不覆盖**用户改过的名称与
       ``is_active``。用户把"藏青"改成"藏青(深)"，重跑 seed 不会改回去。
    2. 码表成员（``size_group_items``）用 ``ON CONFLICT DO NOTHING``，
       但 ``sort_order`` **要更新** —— 它是顺序而不是用户可改的文案。
    3. **墓碑跳过**：被真删的内置行不复活（ADR-0025 决策 3）。恢复走
       ``restore_builtin``。

    :returns: 各表的新增条数
    """
    result = await seed_colors(conn)
    result.update(await seed_sizes(conn))
    result.update(await seed_size_groups(conn))
    result.update(await seed_product_categories(conn))
    result.update(await seed_material_categories(conn))
    result.update(await seed_uom_units(conn))
    return result


async def _skip_tombstoned(conn: AsyncConnection, table: str) -> set[str]:
    """该表里处于墓碑状态、**本次不应重建**的编码。"""
    return await tombstoned_codes(conn, DICT_DOC_TYPES[table])


async def seed_colors(conn: AsyncConnection) -> dict[str, int]:
    count = 0
    skip = await _skip_tombstoned(conn, "colors")
    for code, name in BUILTIN_COLORS:
        if code in skip:
            continue
        stmt = (
            postgresql.insert(Color)
            .values(
                color_code=code,
                name=name,
                is_builtin=True,
                is_active=True,
                created_by=_SEED_OPERATOR,
                updated_by=_SEED_OPERATOR,
            )
            .on_conflict_do_nothing(index_elements=["color_code"])
        )
        result = await conn.execute(stmt)
        count += result.rowcount or 0
    return {"colors": count}


async def seed_sizes(conn: AsyncConnection) -> dict[str, int]:
    count = 0
    # 尺码墓碑记的是 "size_code/size_class"（L 在两个尺码类下各一行，
    # 只按码判定会把女款 L 连男款 L 一起跳过）
    skip_pairs = {
        parts
        for item in await _skip_tombstoned(conn, "sizes")
        if len(parts := item.split("/", 1)) == 2
    }
    for code, size_class, name, sort_order in BUILTIN_SIZES:
        if skip_pairs.intersection({(code, size_class)}):
            continue
        stmt = (
            postgresql.insert(Size)
            .values(
                size_code=code,
                name=name,
                size_class=size_class,
                sort_order=sort_order,
                is_builtin=True,
                is_active=True,
                created_by=_SEED_OPERATOR,
                updated_by=_SEED_OPERATOR,
            )
            # ⚠️ 冲突目标是 (size_code, size_class) —— ADR-0027 的复合唯一键。
            #    写 on_conflict_do_nothing(index_elements=["size_code"]) 会被 PG 拒绝
            #    （没有这个唯一索引），而且 L/XL 两行会互相覆盖
            .on_conflict_do_nothing(index_elements=["size_code", "size_class"])
        )
        result = await conn.execute(stmt)
        count += result.rowcount or 0
    return {"sizes": count}


async def seed_size_groups(conn: AsyncConnection) -> dict[str, int]:
    groups = 0
    items = 0
    skip_groups = await _skip_tombstoned(conn, "size_groups")
    for name, size_class, codes in BUILTIN_SIZE_GROUPS:
        if name in skip_groups:
            continue
        stmt = (
            postgresql.insert(SizeGroup)
            .values(
                name=name,
                size_class=size_class,
                is_builtin=True,
                is_active=True,
                created_by=_SEED_OPERATOR,
                updated_by=_SEED_OPERATOR,
            )
            .on_conflict_do_nothing(index_elements=["name"])
        )
        result = await conn.execute(stmt)
        groups += result.rowcount or 0

        group_id = await conn.scalar(select(SizeGroup.id).where(SizeGroup.name == name))
        if group_id is None:  # pragma: no cover —— 上面的 insert 刚成功
            continue
        for order, code in enumerate(codes, start=1):
            # ⚠️ 按 (size_code, size_class) 取尺码，而不是只按 size_code ——
            #    ADR-0027 之后 L/XL 各有两行，只按码取会拿到女款那一行，
            #    于是男款码表里混进 WOMENS 的尺码
            size_id = await conn.scalar(
                select(Size.id).where(Size.size_code == code, Size.size_class == size_class)
            )
            if size_id is None:  # pragma: no cover —— sizes 先于本函数写入
                continue
            result = await conn.execute(
                postgresql.insert(SizeGroupItem)
                .values(size_group_id=group_id, size_id=size_id, sort_order=order)
                .on_conflict_do_nothing(index_elements=["size_group_id", "size_id"])
            )
            # ⚠️ 必须数 rowcount 而不是"每条 +1"。ON CONFLICT DO NOTHING 跳过时
            # rowcount 是 0；数尝试次数会让第二次运行仍然报"新增 8 条"，
            # 运维看日志会以为 seed 不幂等，而实际数据是对的
            items += result.rowcount or 0
    return {"size_groups": groups, "size_group_items": items}


async def seed_product_categories(conn: AsyncConnection) -> dict[str, int]:
    count = 0
    # 商品分类是纯软删表（allow_physical_delete=False），不会有真删墓碑，
    # 但仍按同一口径跳过，避免以后放开真删时忘了改这里
    skip = await _skip_tombstoned(conn, "product_categories")
    for code, name, order in BUILTIN_PRODUCT_CATEGORIES:
        if code in skip:
            continue
        stmt = (
            postgresql.insert(ProductCategory)
            .values(
                code=code,
                name=name,
                sort=order,
                is_active=True,
                created_by=_SEED_OPERATOR,
                updated_by=_SEED_OPERATOR,
            )
            .on_conflict_do_nothing(index_elements=["code"])
        )
        result = await conn.execute(stmt)
        count += result.rowcount or 0
    return {"product_categories": count}


async def seed_uom_units(conn: AsyncConnection) -> dict[str, int]:
    """计量单位。

    ⚠️ **不是字典表**（没有 ``is_builtin``），所以 ``--check`` 单独数它：
    它与 ``material_categories`` 不同 —— 计量单位是全厂共用的固定集，
    不像物料类目那样允许用户按自己的料号体系扩充。
    """
    count = 0
    for code, name, decimal_places in BUILTIN_UOM_UNITS:
        stmt = (
            postgresql.insert(UomUnit)
            .values(
                code=code,
                name=name,
                decimal_places=decimal_places,
                created_by=_SEED_OPERATOR,
                updated_by=_SEED_OPERATOR,
            )
            .on_conflict_do_nothing(index_elements=["code"])
        )
        result = await conn.execute(stmt)
        count += result.rowcount or 0
    return {"uom_units": count}


async def seed_material_categories(conn: AsyncConnection) -> dict[str, int]:
    """物料类目。⚠️ 与 ``product_categories`` 同口径：软删表 + 墓碑跳过 + 幂等。"""
    count = 0
    skip = await _skip_tombstoned(conn, "material_categories")
    for code, name, order in BUILTIN_MATERIAL_CATEGORIES:
        if code in skip:
            continue
        stmt = (
            postgresql.insert(MaterialCategory)
            .values(
                code=code,
                name=name,
                sort=order,
                is_active=True,
                is_builtin=True,
                created_by=_SEED_OPERATOR,
                updated_by=_SEED_OPERATOR,
            )
            .on_conflict_do_nothing(index_elements=["code"])
        )
        result = await conn.execute(stmt)
        count += result.rowcount or 0
    return {"material_categories": count}
