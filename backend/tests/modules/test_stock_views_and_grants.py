"""库存台账的**对账视图**与应用账号权限测试（T-BASE-009 第 1 张卡 / 迁移 0012 + 0013）。

覆盖：

======================  =================================================
TC-BL-07a              台账与结存**自洽**时 ``v_stock_reconciliation`` /
                        ``v_stock_cost_check`` 都恒为 0 行
TC-BL-07b              结存**没有对应台账**时 ``v_stock_reconciliation`` 报出来
TC-BL-07c              台账**没有批次明细**时 ``v_stock_cost_check`` 报出来
TC-BL-08               应用账号**不能** UPDATE / DELETE 台账与明细，但
                        **能** UPDATE ``stock_reservations``（释放锁要靠它）
======================  =================================================

⚠️ **「恒为 0 行」与「能报出异常」必须同时测，缺一不可**：

- 只测「自洽时为空」，视图可以是一个 ``WHERE false`` 的空壳 —— 永远绿。
- 只测「异常时报出」，那只能证明它不是空壳，证明不了它在正常状态下不误报。

而这两个方向的失败代价完全不对称：误报是「发不了版」，漏报是「数据已经错了
而没人知道，直到某天算不出成本」。所以两个方向都得有断言。

⚠️ **一个初看像 bug 的事实**：刚 seed 出来、还没走过入库流程的布批，
``v_stock_reconciliation`` **本来就非空**（``stock_qty = 500`` 而台账合计 0）。
那是视图**正确**工作 —— 「结余 ≠ Σ台账」在台账没记全时就是账实不符。
真实流程里 ``stock_qty`` 只由到货登记写，且同一事务必写一条 ``IN`` 台账
（``modules/06 §5.1``），所以生产库不会长期处于这个状态；只有 seed 与测试会。
"""

import pytest
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.modules.stock.models import StockLedger
from tests.factories.cutting import make_stock
from tests.factories.stock import make_ledger, make_material
from tests.factories.user import OPERATOR_ID


async def _stock(db_session):
    return await make_stock(db_session, await make_material(db_session), lot="DY-LEDGER-1")


async def _count(db_session, view: str) -> int:
    # 视图名来自本文件里两个字面量，不是外部输入
    result = await db_session.execute(text(f"SELECT count(*) FROM {view}"))  # noqa: S608
    return int(result.scalar_one())


# --------------------------------------------------------------- 对账视图


async def test_views_are_empty_when_ledger_matches_stock(db_session):
    """TC-BL-07a：台账与结存**自洽**时两个视图都恒为 0 行。"""
    stock = await _stock(db_session)
    # 写一条与 ``stock_qty`` 等值的 IN 台账去「对齐」seed 直接写进去的结存
    await make_ledger(db_session, stock, qty=str(stock.stock_qty), line_no=1, direction="IN")
    assert await _count(db_session, "v_stock_reconciliation") == 0, (
        "v_stock_reconciliation 非空 —— 视图非空的含义是「账实不一致」，发布前必须为 0 行"
    )
    assert await _count(db_session, "v_stock_cost_check") == 0, (
        "v_stock_cost_check 非空 —— 台账与明细的数量对不上"
    )


async def test_reconciliation_flags_qty_without_ledger(db_session):
    """TC-BL-07b：结存**没有对应台账**时视图必须报出来。

    ⚠️ 这是「结余 = 台账汇总」（BR-ST-01）这条不变式的**唯一机器守卫**。
    没有它，一条「扣了料但没写台账」的库存改动会静悄悄通过，直到某天对账视图
    非空、发不了版，而那时**已经没人说得清是哪张单干的**。
    """
    stock = await _stock(db_session)
    # 只写 499，而结存是 500 —— 差额 1 就是账实不符
    await make_ledger(db_session, stock, qty="499.000", line_no=1, direction="IN")
    assert await _count(db_session, "v_stock_reconciliation") == 1, (
        "结存与台账差 1 米而 v_stock_reconciliation 没有报出来 —— "
        "「结余 = Σ台账」这条不变式的守卫失效了"
    )


async def test_cost_check_flags_ledger_without_lines(db_session):
    """TC-BL-07c：台账**没有**批次明细时 ``v_stock_cost_check`` 必须报出来。

    ⚠️ 这是「每条出库都有批次耗用」这条不变式的**唯一机器守卫**
    （``modules/06 §3.6``：台账无明细即非法，BR-ST-19）。没有它，
    一条「记了出库但没记用了哪几缸布」的台账会静悄悄通过，
    直到成衣成本算不出来。
    """
    stock = await _stock(db_session)
    await make_ledger(db_session, stock, qty="-1.000", line_no=777, with_line=False)
    assert await _count(db_session, "v_stock_cost_check") == 1, (
        "台账缺批次明细时 v_stock_cost_check 没有报出来（BR-ST-19 的守卫失效）"
    )


async def test_cost_check_flags_qty_mismatch(db_session):
    """TC-BL-07c（第二向）：台账与明细**数量不等**也必须报出来。

    ⚠️ 这条与 TC-BL-07c 抓的不是同一类缺陷：一个缺明细、一个数量不符。
    而**行级 CHECK 拦不住后者** —— 台账 -95、明细 -96，两边**各自内部**都自洽
    （明细的 ``amount`` 就是按 -96 算的），只有跨表比对才看得见。
    症状是「结余差 1 米」，而 `fabric_qty` 与实际扣料量对不上，
    直到某天算成衣成本时才发现。

    ⚠️ **必须靠 INSERT 造这个不一致，不能靠 UPDATE 改台账**：台账是 append-only，
    应用账号连 UPDATE 都没有（迁移 0012 的 ``REVOKE UPDATE, DELETE``），
    所以「先写对再改错」这条路在权限层就先被挡住了 —— 而守卫要测的
    恰恰是**写入时就对不上**这个情形，那才是真实会发生的错。
    """
    from decimal import Decimal

    from app.modules.stock.models import StockLedgerLine

    stock = await _stock(db_session)
    ledger = StockLedger(
        stock_type="MATERIAL",
        stock_id=stock.id,
        warehouse_id=stock.warehouse_id,
        direction="OUT",
        qty=Decimal("-95.000"),
        source_doc_type="CuttingOrder",
        source_doc_id=stock.id,
        source_line_no=5,
        created_by=OPERATOR_ID,
    )
    db_session.add(ledger)
    await db_session.flush()
    line_qty = Decimal("-96.000")
    db_session.add(
        StockLedgerLine(
            ledger_id=ledger.id,
            material_id=stock.material_id,
            stock_id=stock.id,
            dye_lot_no=stock.dye_lot_no,
            bolt_no=stock.bolt_no,
            qty=line_qty,
            unit_cost=stock.unit_cost,
            amount=(line_qty * stock.unit_cost).quantize(Decimal("0.0001")),
            created_by=OPERATOR_ID,
        )
    )
    await db_session.flush()
    assert await _count(db_session, "v_stock_cost_check") == 1, (
        "台账 -95 与明细 -96 对不上而视图没有报出来"
    )


# --------------------------------------------------------------- 应用账号权限


async def test_app_role_cannot_modify_append_only_tables(app_database_url):
    """TC-BL-08：应用账号**不能** UPDATE / DELETE 两张 append-only 表。

    ⚠️ 权限边界只有真去试才能确认 —— ORM 里看不到任何痕迹。台账尤其需要：
    它的纠错手段**只有**红冲（INV-4），一条被物理删掉或被 UPDATE 过的流水
    会让 ``v_stock_reconciliation`` 永远对不上且**无从追溯**。
    """
    engine = create_async_engine(app_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            for statement in (
                "UPDATE stock_ledgers SET qty = 0",
                "DELETE FROM stock_ledgers",
                "UPDATE stock_ledger_lines SET amount = 0",
                "DELETE FROM stock_ledger_lines",
                "DELETE FROM stock_reservations",
            ):
                with pytest.raises((sa.exc.ProgrammingError, sa.exc.DBAPIError)) as caught:
                    await session.execute(sa.text(statement))
                await session.rollback()
                assert "permission denied" in str(caught.value).lower(), (
                    f"应用账号竟然能对台账执行 {statement!r}（04 §6.2.1）"
                )
    finally:
        await engine.dispose()


async def test_app_role_can_release_reservation(db_session):
    """TC-BL-08（反向）：应用账号**能** UPDATE ``stock_reservations``。

    ⚠️ 这条是上一条的**必要对照**：把 UPDATE 一并收掉会让锁永远释放不掉
    （INV-7「释放用置值不用删行」）。只断言「收紧了」而不断言「该留的还留着」，
    就会有人为了「更安全」把 UPDATE 也 revoke，然后卡死在一批永远解不开的锁上。
    """
    stock = await _stock(db_session)
    from app.modules.stock.models import StockReservation

    db_session.add(
        StockReservation(
            stock_type="MATERIAL",
            stock_id=stock.id,
            doc_type="CuttingOrder",
            doc_id=stock.id,
            qty="10.000",
            created_by=OPERATOR_ID,
            updated_by=OPERATOR_ID,
        )
    )
    await db_session.flush()
    released = await db_session.execute(
        text(
            "UPDATE stock_reservations SET released_at = now() "
            "WHERE stock_type = 'MATERIAL' AND stock_id = :sid AND released_at IS NULL"
        ),
        {"sid": stock.id},
    )
    assert released.rowcount == 1, "应用账号没能释放锁（released_at 写不进去，INV-7 失效）"


async def test_ledger_is_written_without_service(db_session):
    """TC-BL-07a 的前提确认：台账**现在就能写**（service 还没写）。

    ⚠️ 本卡只建表，但 TC-BL-07a/07b 的「视图能报出异常」方向依赖「真的能写台账」。
    所以这里显式确认写入路径通 —— 否则将来有人加了 service 却把这条能力
    改坏了，那两个视图用例仍会因为「压根没数据」而**假绿**。
    """
    stock = await _stock(db_session)
    ledger, line = await make_ledger(db_session, stock)
    assert isinstance(ledger, StockLedger) and line is not None
