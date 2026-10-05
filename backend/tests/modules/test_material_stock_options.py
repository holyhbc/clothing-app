"""物料 / 供应商 / 布批候选端点测试（T-BASE-007）。

TC-B07-01/02/03   物料候选（只返面料辅料 / 模糊 / 停用 disabled）、供应商候选
TC-B07-04         ★ 布批候选**只列** ``available_qty > 0``（05 §9.5.2 加粗 / ADR-0022）
TC-B07-05         ★ 布批默认排序 = **FIFO（入库日升序）**（BR-ST-17 ③）
TC-B07-06         ★ **不过滤** ``purpose``：``REWORK_RECEIPT`` 也能被裁剪单选到
                  （BR-ST-25 / TC-35 ①）
TC-B07-07/08      不返回 ``unit_cost``；``dye_lot_no`` **精确**匹配；``q`` 模糊匹配
TC-B07-09         权限点**不同**：布批要 ``stock:read``，物料/供应商要 ``base:read``
TC-B07-10/11/12   ``size > 20`` → 422；软删不进候选；端点带已登记的 ``x-permission``

⚠️ **TC-B07-04 / TC-B07-06 是本卡最要紧的两条**：

- 04：把 ``available_qty > 0`` 写成「取出来在 Python 里比」会让只剩 0.001 米的
  批次也列出来，用户填完耗料才收 ``40006`` —— 症状是「明明还剩布却说不够」。
- 06：给 ``purpose`` 加一个 ``= 'NORMAL'`` 的过滤看起来很合理，但 **BR-ST-25 明写**
  「``purpose='REWORK_RECEIPT'`` 的批次与正常布料一样**可被裁剪单选批领用**」，
  TC-35 ① 也要求两批都可选。挡住返修布不会报错，只会让返修布永远领不出去。
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.common.permissions_registry import PERMISSIONS
from app.core.errors import ErrorCode
from app.modules.base.models import Material, MaterialStock, Supplier, Warehouse
from app.modules.base.schemas import StockBatchOptionOut

MATERIALS_API = "/api/v1/materials/options"
SUPPLIERS_API = "/api/v1/suppliers/options"
STOCKS_API = "/api/v1/material-stocks/options"

OPERATOR_ID = "00000000-0000-0000-0000-0000000000aa"


async def _ensure_material(
    db_session,
    *,
    code: str,
    name: str,
    material_type: str = "FABRIC",
    is_active: bool = True,
    category_id: Any = None,
    uom_unit_id: Any = None,
) -> Material:
    """建一条物料（幂等）。

    ⚠️ 必须**幂等**：本文件有用例会真的 commit（权限 / 并发那类），
    非幂等的固定 code 第二次跑就撞 ``uq_materials_code``，而报错指向物料表、
    与「谁提交过它」毫无关系。
    """
    if category_id is None or uom_unit_id is None:
        from app.modules.base.models import MaterialCategory, UomUnit

        if category_id is None:
            row = (
                await db_session.execute(
                    select(MaterialCategory).where(MaterialCategory.code == "OPT")
                )
            ).scalar_one_or_none()
            if row is None:
                row = MaterialCategory(
                    code="OPT",
                    name="候选测试类目",
                    is_builtin=True,
                    created_by=OPERATOR_ID,
                    updated_by=OPERATOR_ID,
                )
                db_session.add(row)
                await db_session.flush()
            category_id = row.id
        if uom_unit_id is None:
            row = (
                await db_session.execute(select(UomUnit).where(UomUnit.code == "M"))
            ).scalar_one_or_none()
            if row is None:
                row = UomUnit(
                    code="M",
                    name="米",
                    decimal_places=3,
                    is_builtin=True,
                    created_by=OPERATOR_ID,
                    updated_by=OPERATOR_ID,
                )
                db_session.add(row)
                await db_session.flush()
            uom_unit_id = row.id

    existing = (
        await db_session.execute(select(Material).where(Material.code == code))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    material = Material(
        code=code,
        name=name,
        material_type=material_type,
        category_id=category_id,
        uom_unit_id=uom_unit_id,
        is_active=is_active,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    db_session.add(material)
    await db_session.flush()
    return material


async def _ensure_supplier(db_session, *, code: str, name: str, short_name: str | None) -> Supplier:
    """建一个供应商（幂等，见 :func:`_ensure_material`）。"""
    existing = (
        await db_session.execute(select(Supplier).where(Supplier.code == code))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    supplier = Supplier(
        code=code,
        name=name,
        short_name=short_name,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    db_session.add(supplier)
    await db_session.flush()
    return supplier


async def _ensure_stock(
    db_session,
    material: Material,
    *,
    dye_lot_no: str,
    bolt_no: str = "01",
    stock_qty: str = "100.000",
    locked_qty: str = "0.000",
    width_cm: str = "152.00",
    in_date: date = date(2026, 9, 1),
    purpose: str = "NORMAL",
    supplier_id: Any = None,
    deleted: bool = False,
) -> MaterialStock:
    """建一个布批（幂等，见 :func:`_ensure_material`）。

    ⚠️ ``in_date`` 必须给 ``date`` 对象、``deleted_at`` 给 ``datetime``：asyncpg
    不做字符串转换，报 ``'str' object has no attribute 'toordinal'`` ——
    而这条报错完全看不出是「测试数据该用 date」。"""
    existing = (
        await db_session.execute(
            select(MaterialStock).where(
                MaterialStock.dye_lot_no == dye_lot_no,
                MaterialStock.bolt_no == bolt_no,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    warehouse = (
        await db_session.execute(select(Warehouse).where(Warehouse.code == "FAB"))
    ).scalar_one_or_none()
    assert warehouse is not None, "闸门 4 之后基线 seed 应已建 FAB 仓库"
    stock = MaterialStock(
        warehouse_id=warehouse.id,
        material_id=material.id,
        supplier_id=supplier_id,
        color_code="-",
        dye_lot_no=dye_lot_no,
        bolt_no=bolt_no,
        width_cm=Decimal(width_cm),
        stock_qty=Decimal(stock_qty),
        total_length_m=Decimal(stock_qty),
        locked_qty=Decimal(locked_qty),
        unit_cost=Decimal("12.500000"),
        purpose=purpose,
        in_date=in_date,
        deleted_at=None if not deleted else datetime(2026, 10, 5, tzinfo=UTC),
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    db_session.add(stock)
    await db_session.flush()
    return stock


async def _options(
    client: AsyncClient, url: str, headers: Any, **params: Any
) -> list[dict[str, Any]]:
    """拉候选。⚠️ ``headers`` 必须**单独**传：塞进 ``**params`` 会变成查询参数
    （``?headers={...}``），HTTP 头就丢了 —— 症状是 401，报错指向令牌。"""
    response = await client.get(url, headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()["data"]


@pytest.mark.asyncio
async def test_material_options_exclude_finished_goods(
    client: AsyncClient, db_session: Any, auth_headers: Any
) -> None:
    """TC-B07-01 成衣不该出现在「裁剪要用的布料」选择器里。"""
    await _ensure_material(db_session, code="F-OPT-FAB", name="候选测试面料")
    await _ensure_material(
        db_session, code="F-OPT-FG", name="候选测试成衣", material_type="FINISHED_GOODS"
    )
    headers = await auth_headers(role="super_admin")

    rows = await _options(client, MATERIALS_API, headers)

    codes = {row["value"] for row in rows}
    assert "F-OPT-FAB" in codes
    assert "F-OPT-FG" not in codes, "成衣进了面料候选（用户要在一堆成衣里挑布料）"


@pytest.mark.asyncio
async def test_material_options_fuzzy_and_disabled(
    client: AsyncClient, db_session: Any, auth_headers: Any
) -> None:
    """TC-B07-02 模糊匹配编码/名称/类型；停用项标 ``disabled``。"""
    await _ensure_material(db_session, code="F-OPT-FUZZY", name="弹力府绸")
    await _ensure_material(db_session, code="F-OPT-OFF", name="停用面料", is_active=False)
    headers = await auth_headers(role="super_admin")

    by_code = await _options(client, MATERIALS_API, headers, q="FUZZY")
    assert [row["value"] for row in by_code] == ["F-OPT-FUZZY"]
    assert by_code[0]["label"] == "F-OPT-FUZZY 弹力府绸"

    # ⚠️ 按**名称**也能搜到（05 §9.5.2「对编码 + 名称 + 副标识做模糊匹配」）
    by_name = await _options(client, MATERIALS_API, headers, q="弹力")
    assert "F-OPT-FUZZY" in {row["value"] for row in by_name}

    stopped = await _options(client, MATERIALS_API, headers, q="F-OPT-OFF")
    assert stopped[0]["disabled"] is True, "停用物料必须 disabled（Combo 据此禁止选中）"
    assert stopped[0]["sub"] == "已停用"


@pytest.mark.asyncio
async def test_supplier_options(client: AsyncClient, db_session: Any, auth_headers: Any) -> None:
    """TC-B07-03 供应商候选：模糊匹配 + ``sub`` 是简称。"""
    await _ensure_supplier(db_session, code="SUP-OPT-1", name="广州某某制衣", short_name="广州厂")
    headers = await auth_headers(role="super_admin")

    rows = await _options(client, SUPPLIERS_API, headers, q="广州")
    assert rows, "按名称片段搜不到供应商"
    assert rows[0]["value"] == "SUP-OPT-1"
    assert rows[0]["label"] == "SUP-OPT-1 广州某某制衣"
    assert rows[0]["sub"] == "广州厂"


@pytest.mark.asyncio
async def test_stock_options_only_available(
    client: AsyncClient, db_session: Any, auth_headers: Any
) -> None:
    """TC-B07-04 ★ 只列 ``available_qty > 0``。"""
    material = await _ensure_material(db_session, code="F-OPT-AVAIL", name="可用量测试布")
    await _ensure_stock(db_session, material, dye_lot_no="OPT-AVAIL-1", stock_qty="30.000")
    # 可用量 = 10 - 10 = 0 → **不该出现**（05 §9.5.2 强制 `available_qty > 0`）
    await _ensure_stock(
        db_session, material, dye_lot_no="OPT-AVAIL-2", stock_qty="10.000", locked_qty="10.000"
    )
    headers = await auth_headers(role="super_admin")

    rows = await _options(client, STOCKS_API, headers, material_id=str(material.id))

    lots = {row["dye_lot_no"] for row in rows}
    assert "OPT-AVAIL-1" in lots
    assert "OPT-AVAIL-2" not in lots, "可用量 = 0 的批次进了候选（用户填完耗料才收 40006）"


@pytest.mark.asyncio
async def test_stock_options_fifo_order(
    client: AsyncClient, db_session: Any, auth_headers: Any
) -> None:
    """TC-B07-05 ★ 默认排序 = FIFO（入库日升序，BR-ST-17 ③）。"""
    material = await _ensure_material(db_session, code="F-OPT-FIFO", name="先进先出测试布")
    await _ensure_stock(db_session, material, dye_lot_no="OPT-FIFO-LATE", in_date=date(2026, 9, 30))
    await _ensure_stock(db_session, material, dye_lot_no="OPT-FIFO-EARLY", in_date=date(2026, 9, 1))
    headers = await auth_headers(role="super_admin")

    rows = await _options(client, STOCKS_API, headers, material_id=str(material.id))

    order = [row["dye_lot_no"] for row in rows]
    assert order.index("OPT-FIFO-EARLY") < order.index("OPT-FIFO-LATE"), (
        f"倒序排了（{order}）：先入库的布应该先用，倒序让最陈旧的永远排最后"
    )


@pytest.mark.asyncio
async def test_stock_options_include_rework_purpose(
    client: AsyncClient, db_session: Any, auth_headers: Any
) -> None:
    """TC-B07-06 ★ **不过滤** ``purpose``：返修布可被裁剪单正常选批（BR-ST-25）。"""
    material = await _ensure_material(db_session, code="F-OPT-RW", name="返修布测试面料")
    await _ensure_stock(db_session, material, dye_lot_no="OPT-RW-1", purpose="REWORK_RECEIPT")
    headers = await auth_headers(role="super_admin")

    rows = await _options(client, STOCKS_API, headers, material_id=str(material.id))

    lots = {row["dye_lot_no"] for row in rows}
    assert "OPT-RW-1" in lots, (
        "返修布被挡在候选外 —— 与 BR-ST-25「返修布照常入库、照常领用」直接冲突，而且不会报任何错"
    )
    rework = next(row for row in rows if row["dye_lot_no"] == "OPT-RW-1")
    assert rework["sub"] == "REWORK_RECEIPT", "purpose 没进 sub，录入员看不到成本口径"


@pytest.mark.asyncio
async def test_stock_options_hide_unit_cost(
    client: AsyncClient, db_session: Any, auth_headers: Any
) -> None:
    """TC-B07-07 ★ 候选**不返回** ``unit_cost``。"""
    material = await _ensure_material(db_session, code="F-OPT-COST", name="成本口径测试布")
    await _ensure_stock(db_session, material, dye_lot_no="OPT-COST-1")
    headers = await auth_headers(role="super_admin")

    rows = await _options(client, STOCKS_API, headers, material_id=str(material.id))

    assert rows
    assert "unit_cost" not in rows[0], "批次成本出现在选批下拉里（会被截图发群里）"


@pytest.mark.asyncio
async def test_stock_options_dye_lot_is_exact(
    client: AsyncClient, db_session: Any, auth_headers: Any
) -> None:
    """TC-B07-08 缸号**精确**匹配：``H2408`` 不该带出 ``H24080``。"""
    material = await _ensure_material(db_session, code="F-OPT-EXACT", name="精确匹配测试布")
    await _ensure_stock(db_session, material, dye_lot_no="OPT-EX-2408")
    await _ensure_stock(db_session, material, dye_lot_no="OPT-EX-24080")
    headers = await auth_headers(role="super_admin")

    rows = await _options(client, STOCKS_API, headers, dye_lot_no="OPT-EX-2408")

    lots = {row["dye_lot_no"] for row in rows}
    assert lots == {"OPT-EX-2408"}, f"缸号精确匹配带了别的缸：{lots}"


@pytest.mark.asyncio
async def test_stock_options_fuzzy_by_keyword(
    client: AsyncClient, db_session: Any, auth_headers: Any
) -> None:
    """TC-B07-08b ★ ``q`` 模糊匹配缸号与匹号。

    ⚠️ 这条用例是**补漏**才加的：原先所有布批用例都只传 ``material_id`` / ``dye_lot_no``，
    于是 ``q`` 分支从未被执行 —— 而那个分支里原本写的是 SQLAlchemy 1.x 的
    ``stmt.or_(...)``（2.0 已移除），症状是「用户手输关键字 → 500」，而全套用例全绿。
    真正的暴露者是 mypy，测试只是把这条路径钉住。
    """
    material = await _ensure_material(db_session, code="F-OPT-KW", name="关键字测试布")
    await _ensure_stock(db_session, material, dye_lot_no="OPT-KW-A1", bolt_no="01")
    await _ensure_stock(db_session, material, dye_lot_no="OPT-KW-A2", bolt_no="02")
    headers = await auth_headers(role="super_admin")

    by_lot = await _options(client, STOCKS_API, headers, q="OPT-KW-A")
    assert {row["dye_lot_no"] for row in by_lot} == {"OPT-KW-A1", "OPT-KW-A2"}

    by_bolt = await _options(client, STOCKS_API, headers, q="02", material_id=str(material.id))
    assert {row["bolt_no"] for row in by_bolt} == {"02"}


@pytest.mark.asyncio
async def test_stock_options_expose_available_and_width(
    client: AsyncClient, db_session: Any, auth_headers: Any
) -> None:
    """布批候选必须带门幅与可用量（§11.5.2；C24 门幅校验的输入）。"""
    material = await _ensure_material(db_session, code="F-OPT-INFO", name="门幅可用量测试布")
    await _ensure_stock(
        db_session,
        material,
        dye_lot_no="OPT-INFO-1",
        stock_qty="50.000",
        locked_qty="12.500",
        width_cm="148.00",
    )
    headers = await auth_headers(role="super_admin")

    rows = await _options(client, STOCKS_API, headers, material_id=str(material.id))

    row = next(item for item in rows if item["dye_lot_no"] == "OPT-INFO-1")
    assert row["width_cm"] == "148.00"
    assert row["available_qty"] == "37.500", "可用量 = stock_qty - locked_qty（C16）"
    # ⚠️ value 必须是批次 UUID：裁剪行要的就是 material_stocks.id（ADR-0022），
    #    而九个基础资料的候选给的是**业务编码** —— 前端要区别对待。
    assert row["value"] == str(
        (
            await db_session.execute(
                select(MaterialStock).where(MaterialStock.dye_lot_no == "OPT-INFO-1")
            )
        )
        .scalar_one()
        .id
    )
    # 类型层也守一道：数量 / 门幅必须是 str（05 §3）
    assert isinstance(StockBatchOptionOut.model_validate(row).available_qty, str)


@pytest.mark.asyncio
async def test_stock_options_skip_soft_deleted(
    client: AsyncClient, db_session: Any, auth_headers: Any
) -> None:
    """TC-B07-11 软删布批不进候选（INV-7；过滤由 ``apply_data_scope`` 统一附加）。"""
    material = await _ensure_material(db_session, code="F-OPT-DEL", name="软删测试布")
    await _ensure_stock(db_session, material, dye_lot_no="OPT-DEL-1", deleted=True)
    headers = await auth_headers(role="super_admin")

    rows = await _options(client, STOCKS_API, headers, material_id=str(material.id))

    assert "OPT-DEL-1" not in {row["dye_lot_no"] for row in rows}


@pytest.mark.asyncio
async def test_stock_options_require_stock_read(client: AsyncClient, auth_headers: Any) -> None:
    """TC-B07-09 ★ 只有 ``base:read`` 时看布批候选 → **403**（权限点是 ``stock:read``）。

    ⚠️ **不能**在同一条用例里再验「反向」：``role="custom"`` 复用**同一个角色行**、
    权限是往里追加的，所以第二次建号拿到的是两个权限点的**并集**，「反向」那条
    断言必然假失败 —— 而失败信息是「expected 403, got 200」，完全看不出是角色复用。
    反向那条见 :func:`test_base_options_deny_stock_only_user`（另起一条用例 = 新事务）。
    """
    headers = await auth_headers(role="custom", permissions=("base:read",))
    assert (await client.get(MATERIALS_API, headers=headers)).status_code == 200
    assert (await client.get(SUPPLIERS_API, headers=headers)).status_code == 200

    denied = await client.get(STOCKS_API, headers=headers)
    assert denied.status_code == 403
    body = denied.json()
    assert body["code"] == int(ErrorCode.PERMISSION_DENIED)
    assert body["details"]["required_permission"] == "stock:read"


@pytest.mark.asyncio
async def test_base_options_deny_stock_only_user(client: AsyncClient, auth_headers: Any) -> None:
    """TC-B07-09 反向：只有 ``stock:read`` 时看物料候选 → **403**。

    ⚠️ 布批能看而物料不能：说明两个端点的权限点**真的**不同 ——
    如果有人图省事把三个端点都写成 ``base:read``，这条会红。
    """
    headers = await auth_headers(role="custom", permissions=("stock:read",))
    assert (await client.get(STOCKS_API, headers=headers)).status_code == 200
    assert (await client.get(MATERIALS_API, headers=headers)).status_code == 403


@pytest.mark.asyncio
async def test_options_reject_size_over_20(client: AsyncClient, auth_headers: Any) -> None:
    """TC-B07-10 候选硬上限 20（05 §9.5.2 / 06 §10）。"""
    headers = await auth_headers(role="super_admin")
    for url in (MATERIALS_API, SUPPLIERS_API, STOCKS_API):
        response = await client.get(url, headers=headers, params={"size": 21})
        assert response.status_code == 422, f"{url} 收下了 size=21（候选必须硬上限 20）"


@pytest.mark.asyncio
async def test_options_openapi_contract(auth_headers: Any) -> None:
    """TC-B07-12 端点带 ``x-permission`` 且权限点已在 registry 登记。"""
    from app.main import create_app

    paths = create_app().openapi()["paths"]
    # ⚠️ OpenAPI 的 key 带完整前缀（`/api/v1/...`），别拿它去掉前缀去比对 ——
    #    那会得到「端点没进 OpenAPI」的假报错。
    expected = {
        MATERIALS_API: "base:read",
        SUPPLIERS_API: "base:read",
        STOCKS_API: "stock:read",
    }
    known = {item.code for item in PERMISSIONS}
    for path, permission in expected.items():
        assert path in paths, f"{path} 没进 OpenAPI"
        operation = paths[path]["get"]
        assert operation["x-permission"] == permission
        assert permission in known, f"{permission} 没在 permissions_registry 登记"
