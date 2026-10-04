"""字典内置库 seed（docs/04 §7.4 + modules/01 §3.6.4 定稿清单）。

单独成模块而不是塞进 :mod:`seed_baseline`：那里的内容是"权限点 / 角色 /
初始超管"这类**代码级常量**，而这里是**业务数据**，两者的变更频率与评审人都不一样
（业务方改一个色名不需要看权限注册的 diff）。

清单（业务方 2026-10-01 确认，REV-2026-10 定稿）：
    - 16 色基础色卡
    - 8 行尺码（**不是 6 行** —— ``L``/``XL`` 男女款各一行，ADR-0027）
    - 2 个尺码模板 + 8 条成员
    - 6 个商品分类
    - ❌ 不 seed 童装码表与 ``style_color_size_ratios``（§3.6.4 明确）

三条口径：
    1. ``ON CONFLICT DO NOTHING``，**不覆盖**用户改过的名称与 ``is_active``（R26）
    2. 墓碑跳过：被真删的内置行**不复活**（ADR-0025 决策 3）；恢复走
       :mod:`app.cli.restore_builtin`
    3. 码表成员按 ``(size_code, size_class)`` 取尺码，不是只按码取（ADR-0027）
"""

from __future__ import annotations

from sqlalchemy import func, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncConnection

from app.modules.base.models import (
    Color,
    MaterialCategory,
    ProductCategory,
    Size,
    SizeGroup,
    SizeGroupItem,
    UomUnit,
)

# 字典内置库（docs/04 §7.4 + modules/01 §3.6.4 定稿清单）
# ==================================================================

#: 16 色基础色卡（modules/01 §3.6.4，业务方 2026-10-01 确认）
BUILTIN_COLORS: tuple[tuple[str, str], ...] = (
    ("WHT", "本白"),
    ("BLK", "黑"),
    ("NVY", "藏青"),
    ("KHK", "卡其"),
    ("GRN", "军绿"),
    ("BEG", "米色"),
    ("RED", "正红"),
    ("BLU", "蓝"),
    ("GRY", "深灰"),
    ("PNK", "粉"),
    ("PUR", "紫"),
    ("SLT", "浅灰"),
    ("BRN", "棕"),
    ("OLV", "橄榄"),
    ("BEG2", "浅卡其"),
    ("WHT2", "本白(裁剪区分)"),
)

#: 内置尺码 **8 行**（不是 6 行）：``L`` / ``XL`` 在女款与男款各一行。
#: 唯一键含 ``size_class``（ADR-0027），所以同一码可以存两份。
#: ``name`` 用实际围度标注（04 §7.4 把括号内围度列为"可选"，§3.6.4 要求"用实际尺码标注"）。
BUILTIN_SIZES: tuple[tuple[str, str, str, int], ...] = (
    # (size_code, size_class, name, sort_order)
    ("S", "WOMENS", "S(155/80A)", 1),
    ("M", "WOMENS", "M(160/84A)", 2),
    ("L", "WOMENS", "L(165/88A)", 3),
    ("XL", "WOMENS", "XL(170/92A)", 4),
    ("L", "MENS", "L(165/88A)", 1),
    ("XL", "MENS", "XL(170/92A)", 2),
    ("XXL", "MENS", "XXL(175/96A)", 3),
    ("3XL", "MENS", "3XL(180/100A)", 4),
)

#: 只内置 2 个码表（业务方明确）。成员**各指向本尺码类的那一行**（ADR-0027）
BUILTIN_SIZE_GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("女款模板 S-M / L-XL", "WOMENS", ("S", "M", "L", "XL")),
    ("男款模板 L XL XXL 3XL", "MENS", ("L", "XL", "XXL", "3XL")),
)

#: 6 个内置商品分类（04 §7.11 / ADR-0020）
BUILTIN_PRODUCT_CATEGORIES: tuple[tuple[str, str, int], ...] = (
    ("SET", "套装", 1),
    ("DRESS", "单衣", 2),
    ("TROUSERS", "单裤", 3),
    ("UNDERWEAR", "棉毛", 4),
    ("VEST", "背心", 5),
    ("THERMAL", "打底裤", 6),
)

#: 计量单位（``04 §7.14.1`` 的建表注释里明确列了这三个）。
#:
#: ⚠️ **只搬规范里已写明的，不自行扩充**：``04 §7.14.1`` 的注释给的是
#: ``'PCS' 件 / 'M' 米 / 'KG' 千克``，所以就种这三个。面料行业常用的 ``YD``（英码）
#: **故意不加** —— 它没在任何规范里出现过，而「度量衡码表该有哪些」是业务方的口径
#: （有的厂按米、有的按码、有的按公斤报价）。用户可以在界面上自建。
#:
#: ⚠️ 这三行是 T-BASE-005 顺带发现的缺口：**表建了但一直是空的**，而
#: ``materials.uom_unit_id`` 是必填外键 —— 也就是说物料档案**一条都建不了**。
#: 空表 + 必填外键的组合，症状是「页面打开就报外键违反」，而报错完全看不出
#: 「根因是码表没有初始数据」。
BUILTIN_UOM_UNITS: tuple[tuple[str, str, int], ...] = (
    # code, name, decimal_places（04 §7.14.1 无 sort 列）
    ("M", "米", 3),
    ("KG", "千克", 3),
    ("PCS", "件", 0),
)

#: 物料类目（09 §2.3 物料编码 ``F-{类目码}-{6 位}`` 的第二段；DDL 见 04 §7.15.1）。
#:
#: ⚠️ 这是**种子候选值**而不是封闭枚举 —— 用户可以按自己的料号体系加 ``ZZ-1``。
#:    封闭枚举会逼着用户改编码规则，而 09 §2.2 明写物料编码「不做格式正则校验」。
#:    真正必须在代码里分支的是 ``materials.material_type``（那个是 PG 枚举）。
BUILTIN_MATERIAL_CATEGORIES: tuple[tuple[str, str, int], ...] = (
    ("CT", "纯棉布", 1),
    ("TW", "混纺布", 2),
    ("KN", "针织布", 3),
    ("FL", "毛料", 4),
    ("DM", "牛仔布", 5),
    ("ZL", "拉链", 6),
    ("NX", "纽扣", 7),
    ("WD", "织带", 8),
    ("TH", "缝纫线", 9),
    ("LB", "唛头", 10),
)

#: 操作人（seed 是运维动作，不存在真实登录用户）
_SEED_OPERATOR = "00000000-0000-0000-0000-000000000001"


#: 字典表 → ``document_logs.doc_type``。
#:
#: ⚠️ 必须与 ``DictResource.doc_type`` 逐字一致 —— 墓碑判定靠这个值匹配
#: ``document_logs``，写成表名会导致"服务写了 DELETE 墓碑、seed 认不出来"，
#: 于是被真删的内置项复活（TC-B21）。
DICT_DOC_TYPES: dict[str, str] = {
    "colors": "Color",
    "sizes": "Size",
    "size_groups": "SizeGroup",
    "product_categories": "ProductCategory",
    # ⚠️ 类目码表**当前不在 ADR-0025 的硬删白名单里**（04 §7.15.1），所以理论上
    #    不会有真删墓碑。仍然登记在这里的原因：**白名单以后放开时不需要改两处**
    #    —— 墓碑跳过逻辑先摆好，别等放开那天才想起来（那时数据已经写坏了）。
    "material_categories": "MaterialCategory",
}


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
