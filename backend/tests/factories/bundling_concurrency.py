"""打菲**真并发**用例的共用助手（T-BUND-008）。

⚠️ **独立引擎真提交**（docs/10 §5.4）：并发任务各用自己的连接 —— 同一连接上的语句会被
PostgreSQL 串行化，测出来的是「顺序执行」，而顺序执行永远不会有并发问题。所以本卡
**不用** ``db_session``（它把一个 session 绑在一个回滚事务上），而是自建引擎真提交。

⚠️ **自带精确清理**（L-094 / L-099）：真提交不在任何回滚范围内，这些行会长期留在
共享测试库里，而 ``test_document_logs`` / ``test_stock_views_and_grants`` 有「全库计数」
断言 —— 不清理的表现是「下一个用例失败，而失败原因与本用例毫无关系」。
本模块因此提供两段清理：:func:`purge_side_effects` 删**本卡自己**造出来、而夹具按款号
清不到的两类行（``document_logs`` / ``users``），其余（裁剪、打菲、款号、布批）由
``conftest.bundling_world_persisted`` 按**精确款号**清 —— 它刻意不接受前缀，
因为前缀会把别的用例仍在用的持久化 world 一起删掉。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.common.enums import DataScope
from app.core.db import get_db
from app.core.security import create_access_token
from app.main import create_app
from app.modules.auth.models import UserRole
from app.modules.bundling.service import BundlingOrderService
from app.modules.cutting.models import CuttingOrderSizeLine
from tests.factories.bundling import attach_output, ctx, payload
from tests.factories.bundling_approve import REVIEWER_ID, reviewer_ctx
from tests.factories.user import OPERATOR_ID, UserFactory

#: 并发任务数（docs/10 §5.1 的「20 并发」口径）。
CONCURRENCY = 20

#: 审核人用的内置角色：``seed_baseline`` 已 seed，带全部 12 个 ``bundling:*`` 权限点。
#: ⚠️ **复用它而不新建角色**：新建角色要多清 ``role_permissions``，漏一条就留给下一张卡
#: 一个脏角色，而脏角色会让「按角色授权」的用例静默拿到额外权限。
REVIEWER_ROLE = "super_admin"

#: 本卡造出来的单据日志类型（``document_logs.doc_type``）。
DOC_TYPE = "BundlingOrder"


@asynccontextmanager
async def concurrency_engine(app_database_url: str) -> AsyncIterator[async_sessionmaker]:
    """并发专用引擎，**``NullPool``**（每条 session 一条独立物理连接），退出时 dispose。

    ⚠️ **不用默认的 QueuePool**，两个理由：① 池上限小于并发任务数时，第 N+1 个任务要等
    前面的任务 ``close()`` 才拿到连接 —— 「20 并发」悄悄退化成「分批串行」而用例照样绿；
    ② QueuePool 把用完的连接**留在池里**，只要有任何一条 session 在 ``dispose()`` 之后才开
    （断言阶段常犯），它归池的 socket 就再没人关 —— ``filterwarnings=error`` 会把那个
    ``ResourceWarning`` 变成**下一个用例的失败**，报错点是 ``socket.__del__``，
    与真因隔了整整三层。
    """
    engine = create_async_engine(app_database_url, poolclass=NullPool)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    finally:
        await engine.dispose()


def world_refs(world: dict[str, Any]) -> dict[str, Any]:
    """把持久化夹具（**纯 id**）接成 :func:`tests.factories.bundling.payload` 认得的形状。

    ⚠️ 持久化夹具返回纯 id 是刻意的：ORM 实体绑在一个即将 dispose 的 session 上，
    带出夹具会在 GC 时抛 ``ResourceWarning``，而 pytest 把它变成**用例失败**。
    """
    ref = type("Ref", (), {"id": world["workshop"], "style_no": world["style_no"]})()
    return {
        "workshop": ref,
        "style": ref,
        "cutting_order": type("C", (), {"id": world["cutting_order"]})(),
        "cutting_size_lines": [],
    }


async def persisted_size_line(
    session: AsyncSession, world: dict[str, Any], size_code: str = "XL"
) -> CuttingOrderSizeLine:
    """取持久化世界里的某条裁剪尺码明细行。

    ⚠️ 按 ``size_code`` 定位而不是按下标：夹具返回的列表按 ``size_line_no`` 排序，
    下标会随裁剪单结构变化漂移，而漂移后的报错是「尺码不匹配」，与真因隔了三层。
    """
    for line_id in world["cutting_size_lines"]:
        row = await session.get(CuttingOrderSizeLine, line_id)
        if row is not None and row.size_code == size_code:
            return row
    raise AssertionError(f"持久化世界里没有尺码 {size_code} 的裁剪明细行")


async def set_persisted_size_hands(
    session: AsyncSession, size_line: CuttingOrderSizeLine, *, hands: int, qty_per_hand: int = 60
) -> None:
    """把裁剪尺码明细行的手数 / 每手件数改成指定值（并发用例的前置）。

    ⚠️ 三列必须**同步改**：``output_qty = hands × qty_per_hand``（ADR-0020 精确整数）。
    只改 ``hands`` 不改 ``output_qty``，③ 防超打会比出「裁剪说 2 手、出数却是 60 件」
    的坏数据，而用例失败时报错指向的是打菲审核、完全看不出根因在这里。
    """
    size_line.hands = hands
    size_line.qty_per_hand = qty_per_hand
    size_line.output_qty = hands * qty_per_hand
    await session.flush()


async def make_persisted_order(
    factory: async_sessionmaker[AsyncSession],
    world: dict[str, Any],
    *,
    size_code: str = "XL",
    hands: int = 1,
    qty_per_hand: int = 60,
    output_qty: int | None = None,
    submit: bool = False,
) -> UUID:
    """真提交一张打菲单（可选已提交），返回 ``order_id``。

    :param output_qty: 裁剪结转行的可用量，默认恰好等于本单要打的件数。
        TC-13 要的是「两张单各要 120，而结转只给 120」—— 那种场景必须显式传。
    """
    async with factory() as session:
        size_line = await persisted_size_line(session, world, size_code)
        await set_persisted_size_hands(session, size_line, hands=hands, qty_per_hand=qty_per_hand)
        await attach_output(
            session,
            world["style"],
            world["style_no"],
            world["workshop"],
            size_code=size_code,
            output_qty=Decimal(output_qty if output_qty is not None else hands * qty_per_hand),
        )
        refs = world_refs(world)
        refs["cutting_size_lines"] = [size_line]
        order = await BundlingOrderService(session).create(
            payload(
                refs,
                lines=[
                    {"cutting_size_line_id": size_line.id, "size_code": size_code, "hands": hands}
                ],
            ),
            OPERATOR_ID,
        )
        await session.commit()
        if submit:
            await BundlingOrderService(session).submit(order.id, OPERATOR_ID, ctx())
            await session.commit()
        return order.id


async def approve_persisted_order(
    factory: async_sessionmaker[AsyncSession], order_id: UUID
) -> None:
    """以**审核人**身份真提交一次审核（并发用例铺场景用）。"""
    async with factory() as session:
        await BundlingOrderService(session).approve(order_id, REVIEWER_ID, reviewer_ctx())
        await session.commit()


async def reverse_persisted_order(
    factory: async_sessionmaker[AsyncSession], order_id: UUID, reason: str
) -> None:
    """真提交一次反审核：码全部置 ``VOIDED``，**手号仍占着**（B12，TC-25 的前置）。"""
    async with factory() as session:
        await BundlingOrderService(session).reverse(order_id, reason, REVIEWER_ID, reviewer_ctx())
        await session.commit()


async def reviewer_headers(
    factory: async_sessionmaker[AsyncSession],
) -> tuple[dict[str, str], str]:
    """在真提交的世界里建一个审核人，返回 ``(请求头, employee_no)``。

    ⚠️ **必须真提交**：``get_auth_context`` 每次请求都**查库**取用户与权限点
    （07 §1.1「权限查库，避免权限变更不生效」），回滚事务里的用户对别的连接压根不存在
    → 11004，报错完全看不出根因是「夹具没提交」。
    ⚠️ ``data_scope`` 落在 **User 行**上（``User.to_auth_context``）。审核 / 码查询都是
    「别人建的单」，给 ``WORKSHOP`` 会让每个用例都撞 ``12002`` —— 与本卡要验的东西无关。
    """
    async with factory() as session:
        role_id = (
            await session.execute(
                text("SELECT id FROM roles WHERE code = :code"), {"code": REVIEWER_ROLE}
            )
        ).scalar_one()
        employee_no = f"BDC{uuid4().hex[:6].upper()}"
        user = await UserFactory.create(
            session,
            employee_no=employee_no,
            name="并发审核人",
            data_scope=DataScope.FACTORY,
        )
        session.add(UserRole(user_id=user.id, role_id=role_id))
        await session.commit()
        user_id = user.id
    token, _expires = create_access_token(
        user_id=str(user_id), role_ids=[str(role_id)], data_scope=DataScope.FACTORY.value
    )
    return {"Authorization": f"Bearer {token}", "X-Test-User-Id": str(user_id)}, employee_no


@asynccontextmanager
async def pooled_client(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncClient]:
    """**每个请求一条独立 session** 的 ASGI 客户端（真并发的另一半）。

    ⚠️ 不能复用 ``conftest.client``：那个把 ``get_db`` 覆盖成 ``db_session``（单 session），
    20 个并发请求共用一条连接 —— 于是测出来的是「顺序执行」。这里每次依赖解析开一条
    新 session（独立连接），20 个请求才真的同时压在数据库上。
    """
    app = create_app()

    async def _get_db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = _get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()


async def purge_side_effects(
    migration_url: str, *, doc_ids: tuple[UUID, ...] = (), employee_nos: tuple[str, ...] = ()
) -> None:
    """删掉**本卡自己**造出来、而夹具按款号清不到的两类行。

    ⚠️ 用**迁移账号**（``erp_ddl``）：``erp_app`` 被 REVOKE 了全部 DELETE（04 §6.2.1）。
    ⚠️ 按 ``doc_id`` / ``employee_no`` **精确定位**，不按前缀：前缀会把别的用例仍在用的
    行一起删（L-099 的教训）。
    ⚠️ ``document_logs`` 必须清：``conftest`` 那段清的是 ``doc_no = '{款号}'``，
    而打菲日志的 ``doc_no`` 是 ``BD-...``，那个条件**永远匹配不上** —— 这正是
    「并发用例留下的日志进不去全库计数断言」的来源。
    """
    if not doc_ids and not employee_nos:
        return
    engine = create_async_engine(migration_url, pool_pre_ping=True)
    try:
        async with engine.begin() as conn:
            if doc_ids:
                await conn.execute(
                    text("DELETE FROM document_logs WHERE doc_type = :t AND doc_id = ANY(:ids)"),
                    {"t": DOC_TYPE, "ids": [str(item) for item in doc_ids]},
                )
            if employee_nos:
                await conn.execute(
                    text(
                        "DELETE FROM user_roles WHERE user_id IN "
                        "(SELECT id FROM users WHERE employee_no = ANY(:nos))"
                    ),
                    {"nos": list(employee_nos)},
                )
                await conn.execute(
                    text("DELETE FROM users WHERE employee_no = ANY(:nos)"),
                    {"nos": list(employee_nos)},
                )
    finally:
        await engine.dispose()


__all__ = [
    "CONCURRENCY",
    "DOC_TYPE",
    "approve_persisted_order",
    "concurrency_engine",
    "make_persisted_order",
    "persisted_size_line",
    "pooled_client",
    "purge_side_effects",
    "reverse_persisted_order",
    "reviewer_headers",
    "set_persisted_size_hands",
    "world_refs",
]
