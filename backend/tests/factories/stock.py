"""库存台账测试用的工厂（``make_material`` / ``make_ledger``）。

⚠️ **为什么单独一个模块而不是写在测试文件里**：``make_stock`` 早就住在
``tests/factories/cutting.py`` 里，而本卡的两张测试文件（表结构 / 视图与权限）
都要用同一套「一个物料 + 一个布批 + 一条台账」的世界。把它抄两份就会开始漂 ——
而漂的方向是「其中一个文件忘了改，于是它守的东西悄悄不守了」。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.common.models import Base
from app.modules.base.models import Material, MaterialCategory, UomUnit
from app.modules.stock.models import StockLedger, StockLedgerLine
from tests.factories.user import OPERATOR_ID

#: 物料 code 用**固定值**而不是随机：每个用例独立事务、跑完回滚，所以不会撞。
#: 写成随机的话，失败信息里是一串没法检索的十六进制。
_MATERIAL_CODE = "F-CT-990901"


async def make_material(db_session) -> Any:
    """一个物料（+ 它的类目 / 计量单位），走「先查后建」。

    ⚠️ **必须幂等**：本仓的并发用例要用独立引擎**真提交**造数据（``docs/10 §2.3``），
    而真提交不在任何回滚范围内 —— 无条件 INSERT 会撞 ``uq_materials_code``，
    而报错指向 ``materials``、与「台账建不出来」毫无关系。
    """

    async def pick(model: type[Base], column: Any, value: str, factory: Any) -> Any:
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
    material = await pick(
        Material,
        Material.code,
        _MATERIAL_CODE,
        lambda: Material(
            code=_MATERIAL_CODE,
            name="台账测试布",
            material_type="FABRIC",
            category_id=category.id,
            uom_unit_id=uom.id,
            created_by=OPERATOR_ID,
            updated_by=OPERATOR_ID,
        ),
    )
    # ⚠️ 只在**新建**分支 add，所以必须由这里 flush —— 调用方紧接着就用
    #    ``material.id`` 去建台账明细（NOT NULL 外键）。漏掉时报
    #    ``null value in column "material_id"``，指向明细表而根因在物料上
    await db_session.flush()
    return material


async def make_ledger(
    db_session,
    stock: Any,
    *,
    qty: str = "-96.000",
    line_no: int = 1,
    direction: str = "OUT",
    with_line: bool = True,
) -> tuple[StockLedger, StockLedgerLine | None]:
    """写一条台账（可选带一条批次明细），返回 ``(ledger, line)``。

    ⚠️ 数量与金额**都自洽**：``amount = ROUND(qty × unit_cost, 4)``，
    所以 ``v_stock_cost_check`` 不会因为这行而报出来 —— 要测「视图能报出异常」
    必须另造不匹配的那一条（见 ``test_stock_views_and_grants.py``）。

    ⚠️ 金额用 ``Decimal`` 算而不是 ``unit_cost * abs(float(qty))`` ——
    ``Decimal × float`` 直接抛 ``TypeError``，而报错指向测试代码本身，
    读的人会以为业务代码里有 float（``AGENTS §2.2`` 禁金额 float）。

    ⚠️ 符号跟着 ``qty`` 走：CHECK 是 ``ROUND(qty * unit_cost, 4) = amount``，
    出库是负数所以金额也必须是负数 —— 这一条测的就是这个一致性。
    """
    quantity = Decimal(qty)
    ledger = StockLedger(
        stock_type="MATERIAL",
        stock_id=stock.id,
        warehouse_id=stock.warehouse_id,
        direction=direction,
        qty=quantity,
        source_doc_type="CuttingOrder",
        # 任意 uuid 即可：台账**不建**到单据的外键（单据可软删 / 反审核）
        source_doc_id=stock.id,
        source_line_no=line_no,
        created_by=OPERATOR_ID,
    )
    db_session.add(ledger)
    await db_session.flush()
    if not with_line:
        return ledger, None
    line = StockLedgerLine(
        ledger_id=ledger.id,
        material_id=stock.material_id,
        stock_id=stock.id,
        dye_lot_no=stock.dye_lot_no,
        bolt_no=stock.bolt_no,
        qty=quantity,
        unit_cost=stock.unit_cost,
        amount=(quantity * stock.unit_cost).quantize(Decimal("0.0001")),
        created_by=OPERATOR_ID,
    )
    db_session.add(line)
    await db_session.flush()
    return ledger, line
