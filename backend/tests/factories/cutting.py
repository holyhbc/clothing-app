"""裁剪模块的测试夹具与数据工厂（docs/10 §7）。

## 为什么单独一个文件

``tests/factories/user.py`` 的文件头已经写明理由：「``User.password_hash`` 必须是真
argon2id 哈希，``data_scope`` 组合直接决定测试覆盖哪条分支」。裁剪这边同理：
**「一个车间 + 一个款号 + 一个布批」这个最小世界**要用到十几个用例，而每处
重建一遍会漂移 —— 有一次改夹具只改了一处，另一个模块就红，报错还指向业务代码。

⚠️ **夹具函数要 import 到测试模块的命名空间里**（pytest 只在**本模块的命名空间**
里找夹具）。所以这里定义、两个测试文件各自 import，这是 pytest 的规矩而不是偷懒。

⚠️ **不复用别的模块的私有 helper**：``tests/modules/test_*`` 里的 ``_style()``
是给那些用例专用的 —— 改它们会连带弄坏那边，从这边 import 则那边一改名字这边就红。
需要什么就在这里建什么。
"""

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import DataScope
from app.core.permissions import AuthContext
from app.modules.base.models import (
    MaterialStock,
    ProductCategory,
    Style,
    StyleColorSizeRatio,
    StyleSize,
    Warehouse,
)
from app.modules.cutting.schemas import (
    CuttingOrderCreateIn,
    LineColorIn,
    OrderLineIn,
    SizeLineIn,
)
from tests.factories.user import OPERATOR_ID, WorkshopFactory

#: 固定单据日期。⚠️ **必须显式传入**（docs/10 §10「时间相关测试必须可注入时间」）：
#: 取号按天分组、用 ``date.today()`` 的话跨零点跑就会挂，而那种失败极难复现。
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


async def make_style(db_session: AsyncSession, style_no: str = "CT-TEST-1") -> Style:
    """建一个款号。

    ⚠️ 分类取 ``ProductCategory``（内置 ``SET``）而不是 ``MaterialCategory`` ——
    两张表都叫「分类」，而 ``styles.category_id`` 外键指向的是**前者**。
    传错时报 ``fk_styles_product_categories`` 违规：能看出是外键，
    但看不出「你把物料类目当成了商品分类」。
    """
    category = (
        await db_session.execute(select(ProductCategory).where(ProductCategory.code == "SET"))
    ).scalar_one()
    # ⚠️ **幂等**（先查后建），与 `_material()` 同款。踩过一次的坑：调试脚本里调了
    #    `create()`，而它内部走 `unit_of_work` → `session.commit()` —— 于是这个夹具
    #    真的提交了一行 `CT-TEST-1`，之后每个用例都撞 `uq_styles_no`，
    #    报错指向款号表，与「谁提交了它」毫无关系。
    existing = (
        await db_session.execute(select(Style).where(Style.style_no == style_no))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    style = Style(
        style_no=style_no,
        name="裁剪测试款",
        category_id=category.id,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    db_session.add(style)
    await db_session.flush()
    return style


async def _pick_warehouse(db_session: AsyncSession) -> UUID:
    """取面料仓库 id，**没有就建**（幂等）。

    ⚠️ 为什么不用 ``.scalar_one()`` 硬查：``FAB`` 仓库原先只由
    ``test_stock_basis.py`` 造，本工厂硬查等于**跨文件顺序依赖**
    （docs/10 §2.2 明令禁止）—— 该文件没先跑、或库被清空重建时，
    这里直接 ``NoResultFound``，而报错点离真因十万八千里。
    """
    existing = (
        await db_session.execute(select(Warehouse).where(Warehouse.code == "FAB"))
    ).scalar_one_or_none()
    if existing is not None:
        return existing.id
    warehouse = Warehouse(
        code="FAB",
        name="面料库",
        warehouse_type="FABRIC",
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    db_session.add(warehouse)
    await db_session.flush()
    return warehouse.id


async def make_material(db_session: AsyncSession) -> Any:
    """建一条物料 + 它的类目 / 单位 / 仓库，返回 ``material``。

    ⚠️ 用固定 code（``F-CT-888801``）：每个用例独立事务、跑完回滚，所以不会撞。
    写成随机的话，失败信息里是一串没法检索的十六进制。

    ⚠️ 本函数原先住在 ``tests/modules/test_stock_basis.py`` 里，而**工厂反过来
    import 测试模块**（``tests/factories/bundling.py`` 曾经这么干）——那是跨文件
    顺序依赖（docs/10 §2.2 禁止）：那个测试文件没先跑、或库被清空时就失败。
    所以下沉到工厂，两边共用。
    """
    from app.modules.base.models import (
        Material,
        MaterialCategory,
        UomUnit,
    )

    async def pick(model: Any, column: Any, value: Any, factory: Any) -> Any:
        row = (await db_session.execute(select(model).where(column == value))).scalar_one_or_none()
        if row is None:
            row = factory()
            db_session.add(row)
        return row

    category = await pick(
        MaterialCategory,
        MaterialCategory.code,
        "CT",
        lambda: MaterialCategory(
            code="CT",
            name="纯棉布",
            is_builtin=True,
            created_by=OPERATOR_ID,
            updated_by=OPERATOR_ID,
        ),
    )
    uom = await pick(
        UomUnit,
        UomUnit.code,
        "M",
        lambda: UomUnit(
            code="M",
            name="米",
            decimal_places=3,
            created_by=OPERATOR_ID,
            updated_by=OPERATOR_ID,
        ),
    )
    # 仓库由 make_stock 侧的 _pick_warehouse 负责建（幂等），这里只保证类目/单位就位
    await _pick_warehouse(db_session)
    material = await pick(
        Material,
        Material.code,
        "F-CT-888801",
        lambda: Material(
            code="F-CT-888801",
            name="测试全棉布",
            material_type="FABRIC",
            category_id=category.id,
            uom_unit_id=uom.id,
            created_by=OPERATOR_ID,
            updated_by=OPERATOR_ID,
        ),
    )
    await db_session.flush()
    return material


async def make_stock(
    db_session: AsyncSession, material: Any, lot: str = "DY-CUT-TEST"
) -> MaterialStock:
    """建一个布批（``material_stocks`` 行）。

    ⚠️ 必须**自己**建：库里没有现成的布批行，而 ``cutting_order_lines.stock_id``
    是必填外键（ADR-0022 级联选料 —— 不允许自由输入缸号）。

    :param lot: 缸号。⚠️ **真提交的并发用例必须传唯一值** ——
        ``uq_material_stocks_lot`` 是 ``(仓库, 物料, 缸号, 匹号, 色)`` 上的
        **部分**唯一索引，真提交的行会一直占着号，下一个用例同号就撞。
        和款号同一个道理：真提交不在回滚范围内。
    """
    # ⚠️ **幂等**，理由同 `make_style`：缸号在部分唯一索引上，真提交过一次就长期占号
    existing = (
        await db_session.execute(
            select(MaterialStock).where(
                MaterialStock.dye_lot_no == lot, MaterialStock.bolt_no == "B-1"
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    # ⚠️ 仓库用 `pick`（先查后建）而不是 `.scalar_one()` 硬查单行：
    # `FAB` 原本只由 test_stock_basis.py 造，本工厂 .scalar_one() 等于**跨文件
    # 顺序依赖**（docs/10 §2.2 禁止）——该文件没先跑或库被清空时，这里直接
    # NoResultFound，而报错点离真因十万八千里。
    warehouse_id = await _pick_warehouse(db_session)
    stock = MaterialStock(
        warehouse_id=warehouse_id,
        material_id=material.id,
        supplier_id=None,
        dye_lot_no=lot,
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
    return stock


async def build_world(db_session: AsyncSession, style_no: str = "CT-TEST-1") -> dict[str, Any]:
    """造「一个车间 + 一个款号 + 一个布批 + 它的物料」这套最小世界。

    :param style_no: 款号。⚠️ **必须真提交到库里的并发用例要传唯一值** ——
        真提交不在任何回滚范围内，每次跑都会留下一行；款号撞
        ``uq_styles_no`` 时第二次跑就红，而报错完全看不出根因是「上次跑剩的」

    ⚠️ 刻意**不是** ``@pytest.fixture`` —— 夹具放 :mod:`tests.conftest`，
    这里只留建造逻辑。原因：夹具一旦定义在工厂模块里，测试文件必须
    ``from tests.factories.cutting import world`` 才能用，而那个 ``world``
    与测试函数签名里的同名参数**冲突**（ruff F811 逐个函数报错，
    加 ``noqa`` 就是几十处噪声）。夹具在 conftest 里，参数同名是 pytest 的
    正常写法，不需要任何豁免。
    """
    from tests.modules.test_stock_basis import _material

    material = await _material(db_session)
    workshop = await WorkshopFactory.create(db_session)
    style = await make_style(db_session, style_no=style_no)
    stock = await make_stock(db_session, material, lot=f"DY-{style_no}")
    await db_session.flush()
    return {
        "workshop": workshop,
        "style": style,
        "stock": stock,
        "material": material,
    }


# ------------------------------------------------------------------ 入参快捷方式


def size_line(
    no: int | None, code: str, hands: int, per_hand: int, output: int | None = None
) -> SizeLineIn:
    """一条尺码明细入参。``no=None`` → 服务端分配行号。"""
    return SizeLineIn(
        size_line_no=no, size_code=code, hands=hands, qty_per_hand=per_hand, output_qty=output
    )


def wid(world: dict[str, Any], key: str) -> UUID:
    """取 world 里某个对象的 id，**实体或纯 id 都行**。

    ⚠️ 为什么两种都要支持：常规用例的 world 是 ORM 实体（同 session，identity map
    里就那一份），而并发用例的 world 必须来自**真提交**的独立引擎 ——
    那边的实体绑在一个已经 dispose 掉的 session 上，带出测试模块就会在 GC 时
    抛 ``ResourceWarning: unclosed socket``，而 pytest 会把 unraisable warning
    变成**用例失败**。所以那种 world 只带纯 id（见 conftest 的
    ``cutting_world_persisted``）。
    """
    value = world[key]
    return value.id if hasattr(value, "id") else UUID(str(value))


def payload(world: dict[str, Any], **overrides: Any) -> CuttingOrderCreateIn:
    """一行一色一尺码的最小 payload（**行可出件数 120 > 明细 60** → 余量 60）。"""
    base: dict[str, Any] = {
        "workshop_id": wid(world, "workshop"),
        "style_id": wid(world, "style"),
        "doc_date": DOC_DATE,
        "lines": [
            OrderLineIn(
                line_no=1,
                stock_id=wid(world, "stock"),
                fabric_qty=Decimal("96.000"),
                waste_qty=Decimal("3.000"),
                output_qty=Decimal("120.000"),
                colors=[LineColorIn(color_code="WHT", size_lines=[size_line(1, "L", 1, 60)])],
            )
        ],
    }
    return CuttingOrderCreateIn(**{**base, **overrides})


def line_in(
    world: dict[str, Any],
    *,
    line_no: int = 1,
    fabric_qty: str = "96.000",
    output_qty: str = "120.000",
    colors: list[LineColorIn] | None = None,
    stock: Any = None,
) -> OrderLineIn:
    """一条布批行入参（默认一行一色一尺码）。"""
    return OrderLineIn(
        line_no=line_no,
        stock_id=wid({"stock": stock} if stock is not None else world, "stock"),
        fabric_qty=Decimal(fabric_qty),
        waste_qty=Decimal("0.000"),
        output_qty=Decimal(output_qty),
        colors=colors
        if colors is not None
        else [LineColorIn(color_code="WHT", size_lines=[size_line(1, "L", 1, 60)])],
    )


# ------------------------------------------------------------------ 主数据辅助


async def add_ratio(
    db_session: AsyncSession, style: Style, color_code: str, pairs: dict[str, str]
) -> None:
    """给某款某色配比例（``pairs`` 是 ``{尺码: 手数}``）。

    :param style: 款号实体。⚠️ **传实体而不是 style_no**：``style_color_size_ratios.style_id``
        是必填外键 —— ``styles.style_no`` 是部分唯一索引，PG 不允许外键引用
        （04 §7.7.1 的 REV 说明），所以外键在 ``style_id`` 上、``style_no`` 只做冗余。
    """
    for size_code, ratio in pairs.items():
        db_session.add(
            StyleColorSizeRatio(
                style_id=style.id,
                style_no=style.style_no,
                color_code=color_code,
                size_code=size_code,
                ratio=Decimal(ratio),
                created_by=OPERATOR_ID,
                updated_by=OPERATOR_ID,
            )
        )
    await db_session.flush()


async def add_style_sizes(db_session: AsyncSession, style: Style, codes: list[str]) -> None:
    """声明该款的尺码集合（``style_sizes``）—— C19③ 的判据。

    :param style: 款号实体。⚠️ **传实体而不是 style_no**：``style_sizes.style_id``
        是必填外键（与 ``style_color_size_ratios`` 同一口径 —— ``styles.style_no``
        是部分唯一索引，PG 不允许外键引用，见 04 §7.7.1 的 REV 说明）。
    """
    for index, code in enumerate(codes, start=1):
        db_session.add(
            StyleSize(
                style_id=style.id,
                style_no=style.style_no,
                size_code=code,
                size_name=code,
                sort_no=index,
                created_by=OPERATOR_ID,
                updated_by=OPERATOR_ID,
            )
        )
    await db_session.flush()


__all__ = [
    "DOC_DATE",
    "add_ratio",
    "add_style_sizes",
    "build_world",
    "ctx",
    "line_in",
    "make_stock",
    "make_style",
    "payload",
    "size_line",
    "wid",
]
