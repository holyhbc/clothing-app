"""扫码计件的**并发**用例（``04-计件 §7.1`` C1 / C3 / C4 + 部分生产竞速）。

⚠️ 必须**独立引擎真提交**（docs/10 §5.4）：并发任务用自己的连接，外层事务没提交的话
它们根本看不见这张码（症状是全部报「打菲号不存在」，而根因与被测的规则毫无关系）。

⚠️ 单开文件是因为它用**另一套夹具**（``app_database_url`` + ``piecework_world_persisted``），
而规则用例走 ``db_session``；混在一起读的人会以为并发也共享事务。

======================================  ==========================================
TC-PW-C1                             ★ 20 并发**同员工**扫同码 → 只落 1 条
TC-PW-C3                             ★ 20 并发**不同员工**扫同码 → 先到者成功，其余 32001
TC-PW-C4                             离线重传（20 条无幂等键）→ 逐条幂等
TC-PW-R1                             部分生产 A(28) vs 他人 B 同扫 → 只有一个成功
TC-PW-R2                             A(28) 与 B(32) 叠加 → **不许凑成 60**
======================================  ==========================================

## C3 是本卡唯一「只靠业务层检查就必然失败」的用例

``uq_piecework_logs_idem`` 含 ``employee_id``，两名员工扫同一个码**各自都能插入** ——
唯一索引挡不住「一码一员工」。唯一能挡的是 ① 的 ``SELECT … FOR UPDATE`` 锁码行 +
锁内判 ``counted_at``（04-计件 §7.1 原文：「只靠业务层检查 = 10 §5.4 判**未通过**」）。

⚠️ 所以这条用例的**价值全在「真并发」**上：把它改成顺序跑两次，
20 并发退化成 1 次成功 + 1 次 32001，而**删掉 ``FOR UPDATE`` 的实现同样能过** ——
那就等于什么都没验。
"""

import asyncio
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.errors import BusinessError, ErrorCode
from app.modules.bundling.models import Bundle
from app.modules.piecework.models import PieceworkLog
from app.modules.piecework.service import PieceworkService
from tests.factories.piecework_world import BUNDLE_QTY, ctx_for, scanned_at

CONCURRENCY = 20


async def _count_once(
    factory: Any,
    *,
    bundle_no: str,
    employee_id: Any,
    operation_no: str,
    qty: Decimal | None = None,
) -> tuple[str, str]:
    """一次扫码，返回 ``(结果, log_id 或错误码)``。

    ⚠️ **每个任务一个独立 session**：共用 session 的话 20 个协程根本不是并发 ——
    它们排队用同一条连接，``FOR UPDATE`` 也就不构成竞争（症状是测试永远绿）。
    """
    async with factory() as session:
        try:
            out = await PieceworkService(session).count_piecework(
                bundle_no=bundle_no,
                employee_id=employee_id,
                scanned_at=scanned_at(),
                qty=qty,
                operator_id=ctx_for().user_id,
                ctx=ctx_for(),
            )
        except BusinessError as exc:
            return "error", str(int(exc.code))
        return "ok", str(out.log_id)


async def _log_rows(factory: Any, bundle_no: str) -> list[PieceworkLog]:
    async with factory() as session:
        return list(
            (
                await session.execute(
                    select(PieceworkLog).where(
                        PieceworkLog.bundle_no == bundle_no, PieceworkLog.deleted_at.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        )


async def _bundle_row(factory: Any, bundle_id: Any) -> Bundle:
    async with factory() as session:
        row = await session.get(Bundle, bundle_id)
        assert row is not None
        return row


async def _fire(
    factory: Any, world: dict[str, Any], *, employees: list[Any], qty: Decimal | None = None
) -> list[tuple[str, str]]:
    """并发发起 :data:`CONCURRENCY` 次扫码，员工按 ``employees`` 轮流。"""
    return await asyncio.gather(
        *(
            _count_once(
                factory,
                bundle_no=world["bundle_no"],
                employee_id=employees[i % len(employees)],
                operation_no=world["operation_no"],
                qty=qty,
            )
            for i in range(CONCURRENCY)
        )
    )


# ============================================================== C1


async def test_tc_pw_c1_same_employee_concurrent(
    app_database_url, piecework_world_persisted
) -> None:
    """同码同员工 20 并发 → **只落 1 条**；所有成功的 ``log_id`` 相同；
    ``bundles.counted_qty`` 只被写一次。

    ⚠️ 20 个请求里 19 个要拿到 ``idempotent=true`` 而不是报错：扫码枪会连扫、
    网络会重试，「第一次成功、其余静默幂等」才是工位机该有的行为。
    """
    engine = create_async_engine(app_database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    world = piecework_world_persisted
    try:
        results = await _fire(factory, world, employees=[world["employee_ids"][0]])
        rows = await _log_rows(factory, world["bundle_no"])
        bundle = await _bundle_row(factory, world["bundle_id"])

        assert len(rows) == 1, f"同员工同码只应有 1 条流水，实际 {len(rows)}"
        assert [r.id for r in rows] == [uuid.UUID(results[0][1])]
        assert bundle.counted_qty == BUNDLE_QTY
        assert bundle.version == world["bundle_version"] + 1, "counted_* 只被写一次"
        assert all(kind == "ok" for kind, _ in results), f"不该有失败：{results}"
        assert len({payload for _, payload in results}) == 1, "幂等命中必须回同一个 log_id"
    finally:
        await engine.dispose()


# ============================================================== C3


async def test_tc_pw_c3_different_employees_concurrent(
    app_database_url, piecework_world_persisted
) -> None:
    """同码不同员工 20 并发 → **先到者成功**，其余 :data:`CONCURRENCY`-1 个得 ``32001``。

    ⚠️ **这条用例专门验「唯一索引挡不住」**：``uq_piecework_logs_idem`` 含
    ``employee_id``，20 个不同员工各自都「合法」，删掉 ① 的 ``FOR UPDATE``
    这条用例会变成 20 条流水 + 20 次回写 —— 而它仍能通过 C1（同员工那条，
    唯一键恰好能兜住）。两条一起看才完整。
    """
    engine = create_async_engine(app_database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    world = piecework_world_persisted
    try:
        results = await _fire(factory, world, employees=world["employee_ids"])
        rows = await _log_rows(factory, world["bundle_no"])
        bundle = await _bundle_row(factory, world["bundle_id"])

        assert len(rows) == 1, f"一码一员工，实际落了 {len(rows)} 条"
        assert bundle.counted_qty == BUNDLE_QTY, "counted_qty 只被写一次，不能叠加"
        assert bundle.counted_by == rows[0].employee_id, "counted_by 必须是**胜出那个员工**"
        # ⚠️ **不能断言「只有 1 个请求成功」**：胜出者的重试走幂等返回 200，
        #   数「ok」的个数只测到「并发度 ÷ 员工数」。真正的判据是**成功的请求
        #   全都回同一条流水**，且失败的**全是** 32001 —— 少了「全都回同一条」这条，
        #   「20 个人各插一条」的缺陷也能蒙混过关（那也是 20 个 ok）。
        ok_ids = {payload for kind, payload in results if kind == "ok"}
        assert ok_ids == {str(rows[0].id)}, f"成功的请求必须回同一条流水，实际 {len(ok_ids)} 条"
        losers = {payload for kind, payload in results if kind == "error"}
        assert losers == {str(int(ErrorCode.PIECEWORK_COUNTED_BY_OTHER))}, (
            f"其余必须都是 32001：{losers}"
        )
    finally:
        await engine.dispose()


# ============================================================== C4


async def test_tc_pw_c4_offline_replay(app_database_url, piecework_world_persisted) -> None:
    """离线重传：同一员工把**同一批**缓存连发 20 次（**不带幂等键**）→ 仍只落 1 条。

    ⚠️ 刻意**不走** ``Idempotency-Key``：那一层是 Redis 请求级缓存，而离线队列
    重传时每条缓存的键可能各不相同（甚至没有）—— 能兜住的只有数据层的
    ``uq_piecework_logs_idem``。只测 ``Idempotency-Key`` 会漏掉整条 C4 路径。
    """
    engine = create_async_engine(app_database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    world = piecework_world_persisted
    try:
        results = await _fire(factory, world, employees=[world["employee_ids"][0]])
        rows = await _log_rows(factory, world["bundle_no"])
        assert len(rows) == 1
        assert all(kind == "ok" for kind, _ in results)
    finally:
        await engine.dispose()


# ============================================================== 部分生产竞速


async def test_tc_pw_r1_partial_vs_other_employee(
    app_database_url, piecework_world_persisted
) -> None:
    """A 手写 28 件 与 B 整手 60 件 同扫 → **只有一个成功**。

    ⚠️ ADR-0016 风险 ③ 的并发版：部分生产与他人同时扫，谁先谁后都不能产生
    「两个部分生产叠加成 60」（见下一条）。
    """
    engine = create_async_engine(app_database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    world = piecework_world_persisted
    try:
        results = await asyncio.gather(
            _count_once(
                factory,
                bundle_no=world["bundle_no"],
                employee_id=world["employee_ids"][0],
                operation_no=world["operation_no"],
                qty=Decimal(28),
            ),
            *(
                _count_once(
                    factory,
                    bundle_no=world["bundle_no"],
                    employee_id=world["employee_ids"][1],
                    operation_no=world["operation_no"],
                )
                for _ in range(5)
            ),
        )
        rows = await _log_rows(factory, world["bundle_no"])
        bundle = await _bundle_row(factory, world["bundle_id"])
        assert len(rows) == 1, f"A(28) 与 B(60) 只该一条落地，实际 {len(rows)}"
        ok_ids = {payload for kind, payload in results if kind == "ok"}
        assert len(ok_ids) == 1
        assert bundle.counted_qty == rows[0].qty, "counted_qty 只等于那一条流水"
    finally:
        await engine.dispose()


async def test_tc_pw_r2_partials_never_stack(app_database_url, piecework_world_persisted) -> None:
    """两名员工各手写 28 / 32 件同扫 → **不许凑成 60**。

    ⚠️ 「28 + 32 = 60」是这里最危险的结果：``ck_bundles_cnt (counted_qty ≤ bundle_qty)``
    **拦不住它**（60 ≤ 60），而 ``counted_qty`` 一旦变成 60，工位机上就会显示
    「这手做满了」，而实际两人都只做了一半。所以这里断言的是**条数**而不是数量和：
    只有一条流水能落，``counted_qty`` 只等于那一条的 ``qty``。
    """
    engine = create_async_engine(app_database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    world = piecework_world_persisted
    try:
        results = await asyncio.gather(
            *(
                _count_once(
                    factory,
                    bundle_no=world["bundle_no"],
                    employee_id=world["employee_ids"][i],
                    operation_no=world["operation_no"],
                    qty=qty,
                )
                for i, qty in enumerate([Decimal(28), Decimal(32)])
            ),
        )
        rows = await _log_rows(factory, world["bundle_no"])
        bundle = await _bundle_row(factory, world["bundle_id"])
        assert len(rows) == 1, f"两个部分生产不许都落，实际 {len(rows)} 条"
        assert sum(1 for kind, _ in results if kind == "ok") == 1
        assert bundle.counted_qty == rows[0].qty != BUNDLE_QTY
    finally:
        await engine.dispose()


# ============================================================== TC-50 红冲与他人扫同码并发


async def test_tc_pw_50_reverse_vs_other_scan_concurrent(
    app_database_url, piecework_world_persisted
) -> None:
    """§7.3 / TC-50：A 的流水被红冲的**同时**，B 扫同码 → 必须串行，终值唯一。

    ⚠️ 这是「红冲也锁码」这条规则唯一的真并发验证：不锁码的话，
    红冲清空 ``counted_*`` 的那一瞬，B 的扫码读到的要么是「未计件」要么是「已计」，
    而两者与红冲交错会造出「B 计了、红冲又清了」或「红冲清了、B 也计了」
    的中间态 —— ``counted_qty`` 终值无法保证唯一。

    ⚠️ 结果只能两种（都合法）：
    - 红冲先 → B 重扫整手成功（``counted_qty = 60``）
    - B 先 → 红冲校验 ``counted_qty(60) != 原流水 qty(28)`` → 拒绝（``10008``）

    断言的是「**恰好一种结局**、表里 REVERSAL ≤ 1、B 的扫码要么成要么是被红冲清空后成」，
    而不是某一边必须赢 —— 谁先赢由数据库调度决定，写死一边就是看运气。
    """
    from decimal import Decimal

    engine = create_async_engine(app_database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    world = piecework_world_persisted
    try:
        async with factory() as session:
            # A 先计 28 件（部分生产），落库并回写 counted_*
            a = await PieceworkService(session).count_piecework(
                bundle_no=world["bundle_no"],
                employee_id=world["employee_ids"][0],
                scanned_at=scanned_at(),
                qty=Decimal(28),
                operator_id=ctx_for().user_id,
                ctx=ctx_for(),
            )
        # 并发：A 红冲这条流水 vs B 扫同码整手
        stranger = uuid.uuid4()
        results = await asyncio.gather(
            _reverse_once(factory, a.log_id, stranger),
            *(
                _count_once(
                    factory,
                    bundle_no=world["bundle_no"],
                    employee_id=world["employee_ids"][1],
                    operation_no=world["operation_no"],
                )
                for _ in range(3)
            ),
        )
        rows = await _log_rows(factory, world["bundle_no"])
        reversals = [r for r in rows if r.log_type == "REVERSAL"]
        scans = [r for r in rows if r.log_type == "SCAN"]
        assert len(reversals) <= 1, f"红冲最多 1 条，实际 {len(reversals)}"
        assert len(scans) >= 1, "B 扫码要么成功、要么红冲先清空后被重扫"
        # 终值唯一：counted_qty 只能是 0（红冲后未计）或 60（B 重扫整手），不可能出现中间态
        bundle = await _bundle_row(factory, world["bundle_id"])
        assert bundle.counted_qty in (Decimal(0), Decimal(60)), (
            f"counted_qty 终值非法：{bundle.counted_qty}"
        )
        # ⚠️ 红冲要么成功、要么因 B 先占了码而拒绝；B 的扫码要么 32001、要么幂等重扫
        assert any(kind == "ok" for kind, _ in results), "红冲或 B 扫码至少有一个成功"
    finally:
        await engine.dispose()


async def _reverse_once(factory: Any, log_id: Any, operator_id: Any) -> tuple[str, str]:
    """红冲一次（**operator 必须是陌生人**，否则自审 10005 永远不会执行红冲）。"""
    from app.modules.piecework.schemas import PieceworkReverseIn

    async with factory() as session:
        try:
            out = await PieceworkService(session).reverse_log(
                log_id,
                PieceworkReverseIn(reason="TC-50 红冲 vs 他人扫并发"),
                operator_id,
                ctx_for(user_id=operator_id),
            )
            return "ok", str(out.reversal_log_id)
        except Exception as exc:  # noqa: BLE001 —— 并发竞态下错误码即结果
            return (
                "error",
                f"{type(exc).__name__}:{getattr(exc, 'orig', None) or getattr(exc, 'code', None)}",
            )
