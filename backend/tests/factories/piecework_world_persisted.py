"""计件**并发**用例的「真提交」世界（``docs/10 §5.4``）。

⚠️ **与 :mod:`.piecework_world`（走 ``db_session`` 回滚）分开**：这里的行会**真提交**
到共享库里，清理必须显式做（``_purge``）。

⚠️ **两个必须记住的坑**（都在下面各自的 docstring 里展开）：

1. 码必须挂在**真的 ``bundling_orders``** 行上（``bundles.doc_id`` 的外键指向它，
   不是 ``cutting_orders``）。
2. 清理必须**物理删且按依赖序**：``piecework_logs.bundle_id`` 是 ``ON DELETE
   RESTRICT``，软删流水不行 —— 外键还在，删码照样撞，而报错点离真因隔了两条语句。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.modules.bundling.models import Bundle, BundleStatus, BundlingOrderLine
from tests.factories.piecework_world import (
    BUNDLE_QTY,
    EMPLOYEE_NO_PREFIX,
    bind,
    ensure_piecework_operation,
    make_employee,
)
from tests.factories.user import OPERATOR_ID


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
