"""**业务模块的最小世界**夹具（裁剪 / 打菲），从 ``tests/conftest.py`` 拆出。

⚠️ **为什么拆**（ADR-0030 的 400 行硬线）：``conftest.py`` 一路涨到 473 行。
根因不是「有人在里面堆代码」，而是**每个业务模块都把自己的「最小世界」夹具往同一个
conftest 里塞** —— 于是加第 N 个模块的夹具必然顶破硬线，而顶破之后没人愿意动它
（改 conftest 有风险、收益又看不见），于是一个 500 行的 conftest 就长期住了下来。

**为什么用 ``pytest_plugins`` 而不是普通 import**：夹具要被 pytest 发现，
得走插件加载这条路；普通 ``from tests.plugins.worlds import cutting_world`` 也能塞进
conftest 的命名空间让 pytest 注册，但那会让「这个夹具定义在哪」变得看不出来
—— 读 conftest 的人看到的是一个从别处 import 进来的名字。
``pytest_plugins`` 是 pytest 官方给的拆分位置。

⚠️ **夹具必须仍叫 ``cutting_world`` / ``bundling_world``**（不能加前缀改名）：
测试函数签名里就是这两个名字，靠 conftest / 插件按名字注入。
也别为了「避免噪声」把它们改成别的名字再在每个测试里 import ——
那正是这个文件 docstring 里记着的 ruff F811 教训。
"""

from typing import Any
from uuid import uuid4

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ------------------------------------------------------------------ 裁剪模块的最小世界


@pytest_asyncio.fixture
async def cutting_world(db_session: AsyncSession) -> dict[str, Any]:
    """裁剪测试用的「一个车间 + 一个款号 + 一个布批 + 它的物料」。

    ⚠️ **为什么放 conftest 而不是工厂模块**：夹具定义在工厂模块里的话，
    测试文件必须 ``from tests.factories.cutting import world`` 才能拿到它，
    而那个名字与测试函数签名里的同名参数冲突 —— ruff F811 会**逐个函数**报错，
    逐个加 ``noqa`` 就是几十处噪声。夹具在 conftest 里，同名参数是 pytest
    的正常写法，零豁免。建造逻辑在 :func:`tests.factories.cutting.build_world`。
    """
    from tests.factories.cutting import build_world

    return await build_world(db_session)


@pytest_asyncio.fixture
async def cutting_world_persisted(app_database_url: str, migration_url: str) -> dict[str, Any]:
    """``cutting_world`` 的**真提交**版本，供并发用例用（docs/10 §2.3 / §5.4）。

    ⚠️ 为什么需要单独一个：并发用例必须用**独立引擎真提交**，否则
    ``db_session`` 的外层事务没提交、并发任务用自己的连接根本看不见那张单
    （症状是全部报「裁剪单不存在」）。而真提交不在回滚范围内，所以：
    ① 款号必须**每次唯一**（否则第二次跑撞 ``uq_styles_no``，报错看不出根因）
    ② 单据只能**软删**清理（``erp_app`` 对裁剪单无 DELETE 权限）
    """
    from tests.factories.cutting import build_world
    from tests.factories.user import OPERATOR_ID  # noqa: F401 —— 保持导入路径一致

    engine = create_async_engine(app_database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    style_no = f"CT-CONC-{uuid4().hex[:10].upper()}"
    try:
        async with factory() as session:
            world = await build_world(session, style_no=style_no)
            await session.commit()
        # ⚠️ 返回**纯 id**而不是 ORM 实体：这些实体绑在一个即将 dispose 的 session 上，
        #    带出夹具会在 GC 时抛 `ResourceWarning: unclosed socket`，而 pytest 会把
        #    unraisable warning 变成**用例失败** —— 报错完全看不出根因是「夹具泄漏了连接」。
        #    `tests.factories.cutting.wid` 同时支持实体与纯 id，所以调用方不用改。
        # ⚠️ 必须 `yield`：`return` 会在返回值那一刻就执行 `finally`，
        # 清理会跑到测试体之前，被测数据当场消失（症状是 `assert ... is not None`，
        # 而库里确实是空的 —— 报错点离真因十万八千里）
        yield {
            "workshop": world["workshop"].id,
            "style": world["style"].id,
            "style_no": world["style"].style_no,
            "stock": world["stock"].id,
            "material": world["material"].id,
        }
    finally:
        await engine.dispose()
        await _purge_persisted_world(migration_url, style_no, like_prefix="CT-CONC%")


# ------------------------------------------------------------------ 打菲模块的最小世界


@pytest_asyncio.fixture
async def bundling_world(db_session: AsyncSession) -> dict[str, Any]:
    """打菲测试用的「一个车间 + 一个款号 + 一个已审核裁剪单 + 它的尺码明细行」。

    夹具放 conftest 里避免 ruff F811 噪声（同 cutting_world 的理由）。
    建造逻辑在 :func:`tests.factories.bundling.build_world`。
    """
    from tests.factories.bundling import build_world

    return await build_world(db_session)


@pytest_asyncio.fixture
async def bundling_world_persisted(app_database_url: str, migration_url: str) -> dict[str, Any]:
    """``bundling_world`` 的**真提交**版本，供并发用例用（docs/10 §2.3 / §5.4）。

    ⚠️ **必须清理**：真提交不在任何回滚范围内，这些行会长期留在库里 —— 而
    ``test_stock_views_and_grants`` / ``test_document_logs`` / ``test_style_disable_export``
    有「全库计数」断言（视图恒 0 行、取建议号不许留款号行）。不清理的表现是
    「下一个用例失败，而失败原因与本用例毫无关系」——本仓 L-090 已记录同一类问题。
    清理用**迁移账号**：``erp_app`` 被 REVOKE 了 DELETE（docs/04 §6.2.1）。
    """
    from tests.factories.bundling import build_world
    from tests.factories.user import OPERATOR_ID  # noqa: F401

    engine = create_async_engine(app_database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    style_no = f"BD-CONC-{uuid4().hex[:10].upper()}"
    try:
        async with factory() as session:
            world = await build_world(session, style_no=style_no)
            await session.commit()
        # ⚠️ 必须用 `yield` 而不是 `return`：fixture 的 `return` 会在**返回值那一刻**
        # 就执行 `finally`，清理跑到测试体之前，被测数据当场消失（症状是用例报
        # `assert size_line is not None`，而库确实是空的 —— 报错点离真因十万八千里）。
        yield {
            "workshop": world["workshop"].id,
            "style": world["style"].id,
            "style_no": world["style"].style_no,
            "cutting_order": world["cutting_order"].id,
            "cutting_size_lines": [sl.id for sl in world["cutting_size_lines"]],
        }
    finally:
        await engine.dispose()
        await _purge_persisted_world(migration_url, style_no)


async def _purge_persisted_world(
    migration_url: str, style_no: str, like_prefix: str | None = None
) -> None:
    """删掉真提交造出的全部行（按前缀，**依赖序：子表 → 父表**）。

    ⚠️ 用**迁移账号**：``erp_app`` 被 REVOKE 了全部 DELETE（docs/04 §6.2.1）。
    ⚠️ 尺码明细/行内颜色**没有** ``style_no`` 列（靠 ``doc_id`` / ``line_id`` 关联），
    所以只能从 ``cutting_orders.style_no`` 反查，顺序不能颠倒（先子后父）。
    ⚠️ **日志按 ``doc_id`` 精确删，不按 ``doc_no``**（T-BUND-008f 补，闭环 L-111）：
    打菲 ``document_logs`` 的 ``doc_no`` 是 ``BD-…``，与这里传入的款号**永不相等**，
    按 ``doc_no`` 删的条件恒删 0 行 —— 于是每跑一次并发用例就往共享库留一批日志，
    而 ``test_document_logs`` / ``test_stock_views_and_grants`` 有全库计数断言，
    失败点与真因隔三层。口径与 ``tests.factories.bundling_concurrency.purge_side_effects``
    一致（那里就是为了补这个漏才单写的）。
    """
    engine = create_async_engine(migration_url, pool_pre_ping=True)
    # ⚠️ 按**精确** style_no 清理（不按前缀）：前缀会连别的用例仍在用的持久化 world 一起删。
    #    布批/日志按前缀兜底（同一 world 的缸号由 style_no 派生）。
    style_like = f"= '{style_no}'"
    lot_like = f"= 'DY-{style_no}'"
    try:
        async with engine.begin() as conn:
            stmts = [
                # ⓪ 日志（无 FK 指向它，先清最省事）。⚠️ 按 doc_id 定位，见上面 docstring
                "DELETE FROM document_logs WHERE doc_type = 'BundlingOrder' AND doc_id IN "  # noqa: S608
                f"(SELECT id FROM bundling_orders WHERE style_no {style_like})",
                # ① 打菲侧（子 → 父）。⚠️ ``bundles`` 必须在 ``bundling_order_lines``
                #    **之前**删：``fk_bundles_line`` 是 ``ON DELETE RESTRICT``，而审核
                #    （T-BUND-005b）之后每张单都带码行，先删明细会撞外键。
                "DELETE FROM bundles WHERE doc_id IN "  # noqa: S608
                f"(SELECT id FROM bundling_orders WHERE style_no {style_like})",
                "DELETE FROM bundling_order_lines WHERE doc_id IN "  # noqa: S608
                f"(SELECT id FROM bundling_orders WHERE style_no {style_like})",
                f"DELETE FROM cutting_outputs WHERE style_no {style_like}",  # noqa: S608
                f"DELETE FROM bundling_orders WHERE style_no {style_like}",  # noqa: S608
                # ② 裁剪侧（子 → 父）
                "DELETE FROM cutting_order_size_lines WHERE line_color_id IN "  # noqa: S608
                "(SELECT c.id FROM cutting_order_line_colors c "
                "JOIN cutting_order_lines l ON l.id = c.line_id "
                "JOIN cutting_orders o ON o.id = l.doc_id "
                f"WHERE o.style_no {style_like})",
                "DELETE FROM cutting_order_line_colors WHERE line_id IN "  # noqa: S608
                "(SELECT l.id FROM cutting_order_lines l "
                "JOIN cutting_orders o ON o.id = l.doc_id "
                f"WHERE o.style_no {style_like})",
                "DELETE FROM cutting_order_lines WHERE doc_id IN "  # noqa: S608
                f"(SELECT id FROM cutting_orders WHERE style_no {style_like})",
                f"DELETE FROM cutting_orders WHERE style_no {style_like}",  # noqa: S608
                # ③ 布批（裁剪侧已清，`fk_cutting_order_lines_stock` 不再挡）
                f"DELETE FROM material_stocks WHERE dye_lot_no {lot_like}",  # noqa: S608
                # ④ 款号（被 cutting_orders 引用，必须最后）
                f"DELETE FROM styles WHERE style_no {style_like}",  # noqa: S608
            ]
            # 表名与前缀都是模块内字面量，不是输入（docs/03 §1.5 禁的是拼用户输入）
            for stmt in stmts:
                await conn.execute(text(stmt))
    finally:
        await engine.dispose()
