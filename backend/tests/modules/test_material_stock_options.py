"""物料 / 供应商候选端点测试（T-BASE-007a）。

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

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.modules.base.models import Material, Supplier

MATERIALS_API = "/api/v1/materials/options"
SUPPLIERS_API = "/api/v1/suppliers/options"

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
