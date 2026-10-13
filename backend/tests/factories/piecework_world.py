"""计件链路的最小世界（一个可扫的码 + 能计价的单价 + 已绑定的员工）。

⚠️ **为什么不直接复用** ``tests/factories/bundling.py`` 的世界：它铺的是「裁剪 + 打菲单」，
而计件要多三样东西 —— **款号工序配置**（``style_operations.is_piecework``）、
**生效单价**（``operation_rates``）、**员工当日绑定**（``piecework_bindings``）。
少任何一样，扫码都会在 ② / ④ / ③ 上挂掉，而那三个错误码与「规则写错」长得一模一样
（都是「没成功」），报错指向的方向完全不同。

⚠️ **``style_operations.bundle_qty`` 在这里故意设成与码的件数不同的值**（默认 1），
用来守 Q-PW13：权威是 ``bundles.bundle_qty``，配置列**一个字节都不该被读**。
把两者设成不同值，这条不变量才测得出来 —— 都设成 60 的话，取错列与取对列
的结果一模一样，测试会永远绿着。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.common.enums import DataScope
from app.core.numbering import business_today
from app.core.permissions import AuthContext
from app.modules.auth.models import User
from app.modules.base.models import OperationRate, StyleOperation
from app.modules.piecework.models import PieceworkBinding
from tests.factories.user import OPERATOR_ID

#: 计件工序号。⚠️ **必须用码上真实的工序号**，不能自己编一个 ——
#: 打菲码是按打菲单明细行的工序生成的（03 §4.1），世界里的码是 ``OP01``，
#: 而 ② 校验的是「款号配没配**码上那个**工序」。配一个自己造的工序号的话，
#: 每条用例都会挂在 ② 上，而那个错误码看起来与「工序没配」完全一样。
PIECEWORK_OPERATION_NO = "OP01"

#: 并发世界专用员工的工号前缀。⚠️ **必须可识别**：真提交不在回滚范围内，
#: 而 ``test_scope`` / ``test_document_logs`` 有「全库计数」断言 —— 留一个
#: ``PW…`` 的账号在库里，下一个用例就会因为「用户多了几个」而失败，
#: 报错点与真因隔了整张表（这正是 ``bundling_world_persisted`` docstring 记的 L-090）。
EMPLOYEE_NO_PREFIX = "PWCONC"

#: 这手码的件数。60 是「一码一手」的典型值（04-计件 §5.1 的示例文案就是 60 件）。
BUNDLE_QTY = Decimal(60)

#: 单价（元/件）。⚠️ 选一个**不能被二进制浮点整除**的值：``0.42`` × 60 = 25.2 在
#: float 下是 ``25.199999999999996``，用它才能验出「金额全程 Decimal」这条（05 §3）。
UNIT_PRICE = Decimal("0.42")


async def ensure_piecework_operation(
    session: AsyncSession, world: dict[str, Any], operation_no: str = PIECEWORK_OPERATION_NO
) -> str:
    """给世界的款号配上**一个计件工序** + **一条当日生效单价**。

    ⚠️ 单价按 ``STYLE`` 档建（带 ``style_no``、不带 ``product_category_id``）——
    取价优先级是 ``STYLE > CATEGORY > OPERATION``（ADR-0026 §2），建在别的档上
    也能命中，但**一旦以后有人调了优先级**，建在 OPERATION 档的用例就会假失败。
    """
    from app.modules.base.models import Operation

    style_no = world["style"].style_no
    existing_op = (
        await session.execute(select(Operation).where(Operation.operation_no == operation_no))
    ).scalar_one_or_none()
    if existing_op is None:
        session.add(
            Operation(
                operation_no=operation_no,
                name="车缝",
                default_bundle_qty=Decimal(1),
                is_piecework=True,
                created_by=OPERATOR_ID,
                updated_by=OPERATOR_ID,
            )
        )
        await session.flush()
    session.add(
        StyleOperation(
            style_id=world["style"].id,
            style_no=style_no,
            operation_no=operation_no,
            sequence=10,
            # ⚠️ 故意与 BUNDLE_QTY 不同：见模块 docstring 的 Q-PW13 说明
            bundle_qty=Decimal(1),
            is_piecework=True,
            created_by=OPERATOR_ID,
            updated_by=OPERATOR_ID,
        )
    )
    today = business_today()
    session.add(
        OperationRate(
            operation_no=operation_no,
            style_id=world["style"].id,
            style_no=style_no,
            effective_from=today,
            effective_to=None,
            unit_price=UNIT_PRICE,
            reason="计件测试单价",
            created_by=OPERATOR_ID,
            updated_by=OPERATOR_ID,
        )
    )
    await session.flush()
    return operation_no


async def make_employee(
    session: AsyncSession,
    *,
    name: str = "陈美玲",
    employee_no: str | None = None,
    group_no: str | None = "A",
) -> User:
    """建一个**能被计件的员工**（``users`` 行，带车间与组别）。

    ⚠️ ``data_scope`` 给 ``SELF``：员工端只用它自己的 token，而工位机用例里
    操作员另有其人（``ctx.user_id`` ≠ ``employee_id``）。
    """
    user = User(
        employee_no=employee_no or f"{EMPLOYEE_NO_PREFIX}{uuid4().hex[:8].upper()}",
        name=name,
        password_hash="$2b$12$placeholder-not-used-in-service-tests",
        workshop_id=None,
        group_no=group_no,
        data_scope=DataScope.SELF,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    session.add(user)
    await session.flush()
    return user


async def bind(
    session: AsyncSession,
    *,
    user: User,
    bundle: Any,
    operation_no: str,
    workshop_id: UUID,
    work_date: date | None = None,
) -> PieceworkBinding:
    """建一条**当日绑定**（不建的话 ③ 会报 ``32002``）。

    ⚠️ ``work_date`` 默认**今天**而不是 ``scanned_at`` 的日期：调用方若要验
    「昨天的绑定今天失效」，得显式传昨天的日期 —— 默认值给今天，
    「忘了传」就变成在测今天，而用例想测的是昨天。

    ⚠️ ``workshop_id`` **由调用方传**：``bundles`` 自己没有车间列（03 §3.3），
    从码上取要走 ``bundling_orders`` 的关系，而夹具里的码常常还没加载那条关系
    （``lazy="raise_on_sql"`` 会直接抛）—— 让调用方给，比在工厂里偷偷补一次查询
    然后在某条用例里莫名慢 5ms 好。
    """
    binding = PieceworkBinding(
        user_id=user.id,
        style_no=bundle.style_no,
        operation_no=operation_no,
        bundle_no=bundle.bundle_no,
        workshop_id=workshop_id,
        work_date=work_date or business_today(),
        is_active=True,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    session.add(binding)
    await session.flush()
    return binding


def ctx_for(
    *,
    scope: DataScope = DataScope.FACTORY,
    permissions: frozenset[str] | None = None,
    user_id: UUID = OPERATOR_ID,
) -> AuthContext:
    """造一个 ``AuthContext``（service 直调时用，不走 HTTP）。

    ⚠️ 操作员默认用 ``OPERATOR_ID`` 而**不是**员工本人：工位机是组长替员工扫码
    （``employee_id`` 传另一个 UUID），两者必须不同，否则「``document_logs`` 记的是
    操作员、``piecework_logs`` 记的是计件人」这条根本验不出来。
    """
    from app.core.permissions import AuthContext

    return AuthContext(
        user_id=user_id,
        name="工位机组长",
        employee_no="A001",
        workshop_id=None,
        group_no=None,
        permissions=permissions if permissions is not None else frozenset({"piecework:count"}),
        data_scope=scope,
    )


def scanned_at(now: Any = None) -> Any:
    """扫码时刻。带 ``tzinfo`` —— ``work_date`` 由它按 ``Asia/Shanghai`` 推导（BR-PW-11），
    裸 ``datetime`` 在 astimezone 时会被当成本地时间，跨零点用例立刻假失败。
    """
    from datetime import UTC, datetime

    return now or datetime.now(tz=UTC)


__all__ = [
    "BUNDLE_QTY",
    "EMPLOYEE_NO_PREFIX",
    "PIECEWORK_OPERATION_NO",
    "UNIT_PRICE",
    "bind",
    "build_persisted_world",
    "build_world",
    "ctx_for",
    "ensure_piecework_operation",
    "make_employee",
    "scanned_at",
]


async def build_world(
    client: AsyncClient,
    session: AsyncSession,
    auth_headers: Any,
    bundling_world: dict[str, Any],
    *,
    hands: int = 2,
) -> dict[str, Any]:
    """铺一个**能成功计一件**的世界：已审核的单（有码）+ 工序 + 单价 + 员工 + 绑定。

    ⚠️ 走 HTTP 建单（``approved_order``）而不是直接插 ``bundles``：打菲码是**审核
    时按手批量生成**的（03 §4.1），直接插会造出「码存在但单据没审核」的形状，
    而那形状在计件侧全都该走另一条分支。
    """
    from tests.factories.bundling_http import approved_order

    order = await approved_order(client, session, auth_headers, bundling_world, hands=hands)
    codes = await _codes(session, order["id"])
    bundle = codes[0]
    # ⚠️ 先取码、再配工序：工序号必须跟着**码**走（见 ``PIECEWORK_OPERATION_NO`` 的注）
    operation_no = await ensure_piecework_operation(
        session, bundling_world, operation_no=bundle.operation_no
    )
    employee = await make_employee(session)
    workshop_id = bundling_world["workshop"].id
    await bind(
        session,
        user=employee,
        bundle=bundle,
        operation_no=operation_no,
        workshop_id=workshop_id,
    )
    return {
        "order": order,
        "operation_no": operation_no,
        "bundle": bundle,
        "employee": employee,
        "workshop_id": workshop_id,
        "style_no": bundle.style_no,
        "bundle_no": bundle.bundle_no,
        "session": session,
    }


async def _codes(session: AsyncSession, doc_id: UUID) -> list[Any]:
    """按手序号取这一单的码（0 起索引）。

    ⚠️ **按 ``hands`` 排序**而不是按 ``id``：生成用的是 ``generate_series``，
    id 的顺序与手序号在历史数据上未必一致，而用例「第 2 手」说的是手序号。
    """
    from app.modules.bundling.models import Bundle

    rows = (
        (
            await session.execute(
                select(Bundle)
                .where(Bundle.doc_id == doc_id, Bundle.deleted_at.is_(None))
                .order_by(Bundle.hands)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def build_persisted_world(app_database_url: str, migration_url: str) -> Any:
    """计件并发的**真提交**世界（``yield`` 出来的 async generator，由夹具驱动）。

    与 ``bundling_world_persisted`` 同款：独立引擎 + 真提交 + 用迁移账号清理。
    本世界的款号每次唯一，否则第二次跑撞 ``uq_styles_no``。
    """
    from tests.factories.bundling import build_world as build_bundling_world
    from tests.factories.user import OPERATOR_ID

    engine = create_async_engine(app_database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    style_no = f"PW-CONC-{uuid4().hex[:10].upper()}"
    try:
        async with factory() as session:
            world = await build_bundling_world(session, style_no=style_no)
            bundle = await _seed_code(session, world)
            operation_no = await ensure_piecework_operation(session, world, bundle.operation_no)
            employees = [await make_employee(session) for _ in range(2)]
            for employee in employees:
                await bind(
                    session,
                    user=employee,
                    bundle=bundle,
                    operation_no=operation_no,
                    workshop_id=world["workshop"].id,
                )
            await session.commit()
            yield {
                "style_no": style_no,
                "operation_no": operation_no,
                "bundle_no": bundle.bundle_no,
                "bundle_id": bundle.id,
                "bundle_version": bundle.version,
                "employee_ids": [e.id for e in employees],
                "workshop_id": world["workshop"].id,
                "operator_id": OPERATOR_ID,
            }
    finally:
        await engine.dispose()
        await _purge(migration_url, style_no)


async def _seed_code(session: AsyncSession, world: dict[str, Any]) -> Any:
    """建一张**已提交的打菲单 + 1 个码**（不铺裁剪/打菲的完整审批往返）。

    ⚠️ **码必须挂在真的 ``bundling_orders`` 行上**（``bundles.doc_id`` 的外键指向它，
    不是 ``cutting_orders``）。早期版本图省事把 ``doc_id`` 指向裁剪单，
    外键当场就该报错 —— 但症状是「并发用例 setup 失败」，离真因很远。

    ⚠️ 与规则用例的 ``build_world`` 不同，这里**不走 HTTP 审批**：并发用例验的是
    「同码抢不抢得到」，铺全链路只会让 20 个任务前面多出几次无关往返，
    把真正的竞争窗口冲淡。审核的其它不变量（码数 = 手数等）在 T-BUND-005b 验过。
    """
    from app.modules.bundling.models import Bundle, BundleStatus, BundlingOrderLine
    from tests.factories.bundling_approve import prepare_order

    size_line = world["cutting_size_lines"][0]
    order = await prepare_order(session, world, plan=((size_line.size_code, 1),))
    line = (
        (
            await session.execute(
                select(BundlingOrderLine).where(BundlingOrderLine.doc_id == order.id)
            )
        )
        .scalars()
        .first()
    )
    assert line is not None
    code = Bundle(
        doc_id=order.id,
        line_id=line.id,
        bundle_no=f"BD-PWCONC-{uuid4().hex[:10].upper()}",
        hands=1,
        style_no=world["style"].style_no,
        color_code="WHT",
        size_code=size_line.size_code,
        operation_no="OP01",
        cutting_size_line_id=size_line.id,
        bundle_qty=BUNDLE_QTY,
        counted_qty=Decimal(0),
        status=BundleStatus.ACTIVE,
        qr_content="",
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    code.qr_content = code.bundle_no
    session.add(code)
    await session.flush()
    return code


async def _purge(migration_url: str, style_no: str) -> None:
    """按款号清掉真提交的行。⚠️ **物理删，且必须用迁移账号**（``erp_app`` 无 DELETE）。

    ⚠️ **顺序不能颠倒**：``piecework_logs.bundle_id`` 与 ``reversal_of_id`` 都是
    ``ON DELETE RESTRICT``，所以流水必须**在** ``bundles`` **之前**物理删 ——
    软删流水（``UPDATE deleted_at``）不行，外键还在，删码照样撞
    「update or delete on table violates foreign key constraint」，而报错点
    （``DELETE FROM bundles``）离真因（没删流水）隔了两条语句。
    """
    from sqlalchemy import text

    engine = create_async_engine(migration_url, pool_pre_ping=True)
    try:
        async with engine.begin() as conn:
            for stmt in (
                "DELETE FROM document_logs WHERE doc_type = 'PIECEWORK' AND doc_id IN "
                "(SELECT id FROM piecework_logs WHERE style_no = :sn)",
                "DELETE FROM piecework_logs WHERE style_no = :sn",
                "DELETE FROM piecework_bindings WHERE style_no = :sn",
                "DELETE FROM bundles WHERE style_no = :sn",
                "DELETE FROM operation_rates WHERE style_no = :sn",
                # ⚠️ style_operations 必须在 styles **之前**（fk_style_operations_styles）
                "DELETE FROM style_operations WHERE style_no = :sn",
            ):
                await conn.execute(text(stmt), {"sn": style_no})
            # ⚠️ 员工账号排在最后：``users`` 被 ``piecework_logs.employee_id`` /
            #    ``piecework_bindings.user_id`` 引用（虽然没建 FK，但 ``role_users`` 有），
            #    必须在上面几张表清完之后才删。
            await conn.execute(
                text(
                    "DELETE FROM user_roles WHERE user_id IN (SELECT id FROM users "
                    "WHERE employee_no LIKE :pfx)"
                ),
                {"pfx": f"{EMPLOYEE_NO_PREFIX}%"},
            )
            await conn.execute(
                text("DELETE FROM users WHERE employee_no LIKE :pfx"),
                {"pfx": f"{EMPLOYEE_NO_PREFIX}%"},
            )
    finally:
        await engine.dispose()
        from tests.plugins.worlds import _purge_persisted_world

        await _purge_persisted_world(migration_url, style_no)
