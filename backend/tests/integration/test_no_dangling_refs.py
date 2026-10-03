"""并发与数据完整性测试（docs/10 §5.4「必须真并发」+ 设计稿 §9 CC-2/CC-3）。

⚠️ **必须真并发**：``asyncio.gather`` + 每个任务**独立 session/连接**。
用同一个 session 并发跑得到的结果是假的 —— 同一个连接上的语句会被 PostgreSQL
串行化，测出来的"并发"其实是串行，而串行永远不出问题。

三条不变量：
    - **CC-2**：20 并发建同色码 → 恰好 1 行（唯一索引兜底，不是应用层判重）
    - **CC-3**：删字典项 vs 建引用**真并发** → 绝不出现悬空引用（INV-P0-1）
    - **CC-6**：20 并发 PATCH 同一 ``version`` → 恰好 1 个成功，其余 10003

CC-3 的引用方用 ``size_group_items``（码表成员）代替 ``style_sizes`` ——
``styles`` 表 T-BASE-002 才建，而两者的并发形态完全一样：A 删被引用的字典项、
B 插入一条指向它的引用。
"""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy import text as text_of
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.common.enums import DataScope, SizeClass
from app.common.models import DocumentLog
from app.core.errors import BusinessError, ErrorCode
from app.core.permissions import AuthContext
from app.modules.base.models import Color, Size, SizeGroup, SizeGroupItem
from app.modules.base.resources import RESOURCES_BY_KEY
from app.modules.base.service import DictService
from tests.factories.user import OPERATOR_ID

CONCURRENCY = 20


def _ctx() -> AuthContext:
    return AuthContext(
        user_id=OPERATOR_ID,
        name="并发测试",
        employee_no="CONC",
        workshop_id=None,
        group_no=None,
        permissions=frozenset({"*"}),
        data_scope=DataScope.FACTORY,
    )


@pytest.fixture
async def concurrent_sessions(app_database_url: str):
    """每个并发任务一条**独立连接**。

    ⚠️ 不能共用 session：同一连接上的语句会被 PostgreSQL 串行化，
    那样测出来的是"顺序执行"，而顺序执行永远不会有并发问题。
    """
    engine = create_async_engine(app_database_url, pool_pre_ping=True, pool_size=CONCURRENCY + 2)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    sessions = [factory() for _ in range(CONCURRENCY + 4)]
    try:
        yield sessions
    finally:
        for session in sessions:
            await session.close()
        await engine.dispose()


# ------------------------------------------------------------------- CC-2


async def test_cc2_twenty_concurrent_creates_yield_exactly_one_row(
    concurrent_sessions,
):
    """CC-2：20 并发建同色码 → 恰好 1 行，其余报错。

    唯一索引是**唯一**的兜底手段（docs/03 §1.5 禁止"先查后插"）；
    应用层的 ``exists_by_code`` 只是为了给出友好文案，不承担正确性。
    """
    code = f"CC{uuid4().hex[:6].upper()}"
    resource = RESOURCES_BY_KEY["colors"]
    barrier = asyncio.Barrier(CONCURRENCY)

    async def _create(index: int) -> str:
        session = concurrent_sessions[index]
        await barrier.wait()
        service = DictService(session, resource, _ctx())
        try:
            await service.create({"color_code": code, "name": f"色{index}"})
            return "created"
        except BusinessError as exc:
            return f"business:{exc.code}"
        except IntegrityError:  # pragma: no cover —— 唯一索引冲突应已被翻译
            return "integrity"

    results = await asyncio.gather(*(_create(i) for i in range(CONCURRENCY)))
    created = [item for item in results if item == "created"]
    assert len(created) == 1, f"应恰好 1 个成功，实际 {len(created)}：{results}"

    async with concurrent_sessions[0] as session:
        rows = int(
            await session.scalar(
                select(func.count()).select_from(Color).where(Color.color_code == code)
            )
        )
    assert rows == 1, f"库里应恰好 1 行，实际 {rows} 行"


# ------------------------------------------------------------------- CC-3


async def test_cc3_reference_committed_before_delete_blocks_the_delete(ddl_session):
    """CC-3 方向一：引用**先落地**，删除必须被拒（20003）。

    这一半是确定性的，不需要靠运气：先提交引用，再删。
    """
    resource = RESOURCES_BY_KEY["sizes"]
    code = f"CC{uuid4().hex[:6].upper()}"

    size = Size(
        size_code=code,
        name=code,
        size_class=SizeClass.MENS,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    group = SizeGroup(
        name=f"先引用-{uuid4().hex[:6]}",
        size_class=SizeClass.MENS,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    ddl_session.add_all([size, group])
    await ddl_session.flush()
    ddl_session.add(SizeGroupItem(size_group_id=group.id, size_id=size.id, sort_order=1))
    await ddl_session.commit()

    with pytest.raises(BusinessError) as excinfo:
        await DictService(ddl_session, resource, _ctx()).delete(code)
    assert excinfo.value.code is ErrorCode.BASE_DATA_REFERENCED
    assert excinfo.value.details["ref_count"] == 1

    # 不变量：行还在，成员行也还在
    still_there = await ddl_session.scalar(
        select(func.count()).select_from(Size).where(Size.size_code == code)
    )
    assert still_there == 1


async def test_cc3_delete_first_makes_later_reference_fail(ddl_session):
    """CC-3 方向二：删除**先落地**，随后的引用必须被拒。

    这条断言的是「删了就建不到了」—— 即真正的"绝不出现悬空引用"。
    靠 ``size_group_items.size_id`` 上的真实外键兜底。
    """
    resource = RESOURCES_BY_KEY["sizes"]
    code = f"CC{uuid4().hex[:6].upper()}"

    size = Size(
        size_code=code,
        name=code,
        size_class=SizeClass.MENS,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    group = SizeGroup(
        name=f"后引用-{uuid4().hex[:6]}",
        size_class=SizeClass.MENS,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    ddl_session.add_all([size, group])
    await ddl_session.flush()
    size_id, group_id = size.id, group.id
    await ddl_session.commit()

    await DictService(ddl_session, resource, _ctx()).delete(code)

    # 引用必须失败 —— 指向已删除的尺码
    ddl_session.add(SizeGroupItem(size_group_id=group_id, size_id=size_id, sort_order=1))
    with pytest.raises(IntegrityError):
        await ddl_session.commit()
    await ddl_session.rollback()

    # 不变量：绝无悬空成员行
    dangling = await ddl_session.scalar(
        text_of(
            "SELECT count(*) FROM size_group_items item "
            "LEFT JOIN sizes s ON s.id = item.size_id WHERE s.id IS NULL"
        )
    )
    assert dangling == 0


async def test_cc3_true_concurrency_never_yields_both_success(concurrent_sessions):
    """CC-3 真并发：引用与删除同时进行，**绝不允许两者都成功**。

    ⚠️ 早先这条只用一个 barrier 让两边"同时起跑"，实测把条件 DELETE 退化成
    "先查后删"它**照样通过** —— 因为 PostgreSQL 的锁顺序让删除总是先拿到，
    竞态根本没被制造出来。真正能暴露问题的是：让引用事务**先握住行锁**
    （外键插入会拿 ``FOR KEY SHARE``），删除就会真的被挡住。
    """
    resource = RESOURCES_BY_KEY["sizes"]
    code = f"CC{uuid4().hex[:6].upper()}"

    setup = concurrent_sessions[0]
    size = Size(
        size_code=code,
        name=code,
        size_class=SizeClass.MENS,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    group = SizeGroup(
        name=f"真并发-{uuid4().hex[:6]}",
        size_class=SizeClass.MENS,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    setup.add_all([size, group])
    await setup.commit()
    size_id, group_id = size.id, group.id

    referencing = concurrent_sessions[1]
    deleting = concurrent_sessions[2]

    # 引用方先插入但**不提交** —— 持有 FOR KEY SHARE，删除方会被挡住
    referencing.add(SizeGroupItem(size_group_id=group_id, size_id=size_id, sort_order=1))
    await referencing.flush()

    async def _delete() -> str:
        try:
            await DictService(deleting, resource, _ctx()).delete(code)
            return "deleted"
        except BusinessError as exc:
            return f"rejected:{int(exc.code)}"

    delete_task = asyncio.create_task(_delete())
    # 给删除方足够时间去撞上那把行锁（否则它可能在引用提交前就把行删掉）
    await asyncio.sleep(0.2)
    assert not delete_task.done(), "删除应当被引用方的行锁挡住；若已完成说明没有并发"

    try:
        await referencing.commit()
        reference_result = "referenced"
    except IntegrityError:  # pragma: no cover —— 行还在时不该失败
        await referencing.rollback()
        reference_result = "rejected:fk"

    delete_result = await delete_task

    assert reference_result == "referenced"
    assert delete_result.startswith("rejected:"), f"引用已落地时删除必须被拒，实际 {delete_result}"

    # 不变量：绝无悬空引用
    async with concurrent_sessions[3] as verify:
        dangling = int(
            await verify.scalar(
                text_of(
                    "SELECT count(*) FROM size_group_items item "
                    "LEFT JOIN sizes s ON s.id = item.size_id WHERE s.id IS NULL"
                )
            )
        )
        size_exists = int(
            await verify.scalar(select(func.count()).select_from(Size).where(Size.id == size_id))
        )
    assert dangling == 0, "出现悬空引用"
    assert size_exists == 1, "尺码被删了但成员行还在"


async def test_cc3_concurrent_delete_of_same_row_deletes_exactly_once(
    concurrent_sessions,
):
    """两人同时删同一条：一次成功、一次"不存在"，不能出现两次 20003 或半删。"""
    resource = RESOURCES_BY_KEY["colors"]
    code = f"CC{uuid4().hex[:6].upper()}"

    setup = concurrent_sessions[0]
    setup.add(
        Color(color_code=code, name="并发删除", created_by=OPERATOR_ID, updated_by=OPERATOR_ID)
    )
    await setup.commit()

    async def _delete(index: int) -> str:
        session = concurrent_sessions[index]
        try:
            await DictService(session, resource, _ctx()).delete(code)
            return "deleted"
        except BusinessError as exc:
            return f"rejected:{int(exc.code)}"

    results = await asyncio.gather(_delete(1), _delete(2))
    assert results.count("deleted") == 1, results
    assert results.count("rejected:20001") == 1, (
        f"第二个并发删除应报 20001（行已不存在），实际 {results}"
    )


# ------------------------------------------------------------------- CC-6


async def test_cc6_twenty_concurrent_patches_on_same_version(concurrent_sessions):
    """CC-6：20 并发 PATCH 同一 ``version`` → 恰好 1 个成功，其余 ``10003``。

    靠条件 UPDATE（``WHERE id=? AND version=?``）+ ``rowcount=0`` 判定，
    而不是"先读 version 再写" —— 后者两个请求会读到同一个 version 并双双成功。
    """
    resource = RESOURCES_BY_KEY["colors"]
    code = f"CC{uuid4().hex[:6].upper()}"

    setup = concurrent_sessions[0]
    color = Color(color_code=code, name="原名", created_by=OPERATOR_ID, updated_by=OPERATOR_ID)
    setup.add(color)
    await setup.commit()
    color_id, version = color.id, color.version

    barrier = asyncio.Barrier(CONCURRENCY)

    async def _patch(index: int) -> str:
        session = concurrent_sessions[index]
        await barrier.wait()
        try:
            await DictService(session, resource, _ctx()).patch(
                code, {"name": f"改名{index}", "version": version}
            )
            return "ok"
        except BusinessError as exc:
            return f"rejected:{int(exc.code)}"

    results = await asyncio.gather(*(_patch(i) for i in range(CONCURRENCY)))
    assert results.count("ok") == 1, results
    conflict = int(ErrorCode.OPTIMISTIC_LOCK_CONFLICT)
    assert results.count(f"rejected:{conflict}") == CONCURRENCY - 1, results

    async with concurrent_sessions[CONCURRENCY + 1] as verify:
        final = (await verify.execute(select(Color).where(Color.id == color_id))).scalar_one()
    assert final.version == version + 1, "乐观锁每次成功只 +1"


# ------------------------------------------------------- 悬空引用巡检


@pytest.mark.parametrize("table", ["colors", "sizes", "size_groups", "operations"])
async def test_no_dangling_references_anywhere(table: str, db_session):
    """全库巡检：该表的每一行都不会被"已删除"的方式破坏引用（ADR-0025）。

    这是 INV-P0-1 的巡检版本：不构造场景，直接在**当前库状态**上断言不存在悬空。
    """
    from sqlalchemy import text as sa_text

    dangling = await db_session.scalar(
        sa_text(
            "SELECT count(*) FROM size_group_items item "
            "LEFT JOIN sizes s ON s.id = item.size_id "
            "WHERE s.id IS NULL"
        )
    )
    assert dangling == 0, f"size_group_items 存在悬空引用（{table} 检查时发现）"


async def test_foreign_key_constraints_cover_all_real_relationships(db_session):
    """每个"逻辑上必须存在"的引用关系都要有**真实外键**兜底。

    没有 FK 时"应用层保证"只是约定：有人直接写 SQL 或换个代码路径，悬空引用立刻
    产生，而且**没有任何报错**。这条测试把"靠自觉"变成"改代码就会红"。
    """
    from sqlalchemy import text as sa_text

    rows = (
        (
            await db_session.execute(
                sa_text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE contype = 'f' AND connamespace = 'public'::regnamespace "
                    "ORDER BY conname"
                )
            )
        )
        .scalars()
        .all()
    )
    required = {
        "fk_users_workshops",
        "fk_role_workshops_workshops",
        "fk_size_group_items_sizes",
        "fk_size_group_items_groups",
        "fk_workshop_groups_workshops",
        "fk_operations_workshops",
    }
    assert required <= set(rows), f"缺外键：{sorted(required - set(rows))}"


async def test_document_logs_reference_existing_rows_for_soft_delete_resources(ddl_session):
    """软删资源的 ``document_logs.doc_id`` 必须指向真实存在的对象。

    ⚠️ **只对软删表成立**。字典表（颜色/尺码/码表）是**物理删除**（ADR-0025），
    删完之后 ``doc_id`` 指向的行本来就不存在了 —— 而这正是想要的：审计日志必须
    留住"曾经有过、现在被删了"这个事实（docs/07 §5）。早先不区分这两类，
    巡检一直报 2 条悬空，看着像 bug，其实是审计在正常工作。
    """
    from sqlalchemy import text as sa_text

    dangling = await ddl_session.scalar(
        sa_text(
            "SELECT count(*) FROM document_logs l "
            "WHERE l.doc_type = 'Workshop' AND NOT EXISTS ("
            "  SELECT 1 FROM workshops w WHERE w.id = l.doc_id)"
        )
    )
    assert dangling == 0


async def test_physical_delete_keeps_log_for_the_deleted_row(ddl_session):
    """反向断言：真删的行**必须**留下一条指向已不存在 id 的 DELETE 日志。

    这是上面那条的正反面 —— 如果哪天有人把审计日志也"清理"掉让巡检变绿，
    这条会失败。
    """
    from sqlalchemy import text as sa_text

    code = f"AUD{uuid4().hex[:6].upper()}"
    color = Color(color_code=code, name="审计", created_by=OPERATOR_ID, updated_by=OPERATOR_ID)
    ddl_session.add(color)
    await ddl_session.flush()
    color_id = color.id

    await DictService(ddl_session, RESOURCES_BY_KEY["colors"], _ctx()).delete(code)

    kept = await ddl_session.scalar(
        sa_text(
            "SELECT count(*) FROM document_logs "
            "WHERE doc_type = 'Color' AND action = 'DELETE' AND doc_id = :doc_id"
        ),
        {"doc_id": color_id},
    )
    assert kept == 1, "物理删除必须留下一条指向被删 id 的 DELETE 日志"


async def test_delete_writes_audit_log_with_soft_delete_or_physical(ddl_session):
    """删除必须留痕，且记录的 ``doc_id`` 与被删对象一致。"""
    resource = RESOURCES_BY_KEY["colors"]
    code = f"LG{uuid4().hex[:6].upper()}"

    setup = ddl_session
    color = Color(color_code=code, name="留痕", created_by=OPERATOR_ID, updated_by=OPERATOR_ID)
    setup.add(color)
    await setup.commit()
    color_id = color.id

    await DictService(setup, resource, _ctx()).delete(code)

    logs = (
        (
            await setup.execute(
                select(DocumentLog).where(
                    DocumentLog.doc_type == "Color", DocumentLog.doc_no == code
                )
            )
        )
        .scalars()
        .all()
    )
    delete_logs = [item for item in logs if item.action == "DELETE"]
    assert len(delete_logs) == 1, "物理删除必须留一条 DELETE 留痕"
    assert delete_logs[0].doc_id == color_id
    assert delete_logs[0].operator_name == "并发测试"


async def test_cascade_delete_of_size_group_leaves_no_orphan_members(ddl_session):
    """码表删成员级联：删完之后不能有指向不存在码表的成员行。"""
    from sqlalchemy import text as sa_text

    code = f"CS{uuid4().hex[:6].upper()}"
    size = Size(
        size_code=code,
        name=code,
        size_class=SizeClass.MENS,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    group = SizeGroup(
        name=f"级联码表-{uuid4().hex[:6]}",
        size_class=SizeClass.MENS,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    ddl_session.add_all([size, group])
    await ddl_session.flush()
    ddl_session.add(SizeGroupItem(size_group_id=group.id, size_id=size.id, sort_order=1))
    await ddl_session.flush()

    await DictService(ddl_session, RESOURCES_BY_KEY["size-groups"], _ctx()).delete(group.name)

    orphan = await ddl_session.scalar(
        sa_text(
            "SELECT count(*) FROM size_group_items item "
            "LEFT JOIN size_groups g ON g.id = item.size_group_id "
            "WHERE g.id IS NULL"
        )
    )
    assert orphan == 0, "码表删除后仍有孤儿成员行"


async def test_size_group_items_keep_fk_when_member_size_soft_deleted(ddl_session):
    """尺码被停用不影响码表成员（停用 ≠ 删除，历史照常可查，R17）。"""
    resource = RESOURCES_BY_KEY["sizes"]
    code = f"DS{uuid4().hex[:6].upper()}"

    size = Size(
        size_code=code,
        name=code,
        size_class=SizeClass.MENS,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    group = SizeGroup(
        name=f"停用测试码表-{uuid4().hex[:6]}",
        size_class=SizeClass.MENS,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    ddl_session.add_all([size, group])
    await ddl_session.flush()
    ddl_session.add(SizeGroupItem(size_group_id=group.id, size_id=size.id, sort_order=1))
    await ddl_session.flush()

    await DictService(ddl_session, resource, _ctx()).disable(code, "停产")

    still_there = await ddl_session.scalar(
        select(func.count()).select_from(SizeGroupItem).where(SizeGroupItem.size_id == size.id)
    )
    assert still_there == 1, "停用不应删掉码表成员"


async def test_foreign_key_declares_restrict_not_cascade(db_session):
    """**哪一层真正在保证 INV-P0-1 ——** 是外键，不是应用层的条件 DELETE。

    实测把条件 DELETE 里的 ``NOT EXISTS`` 去掉后，CC-3 的并发用例**照样通过**，
    因为真正挡住悬空引用的是外键。应用层的引用判定价值在于
    ① 提前给出可读的 ``20003``，而不是让用户看到数据库错误
    ② 覆盖**还没有外键**的引用关系（例如 colors 的引用方要等 T-BASE-002）

    知道这一点很重要：将来若有人想"简化"掉看起来冗余的引用判定，要先确认对应
    外键还在；反过来若有人删了外键想靠应用层兜底，这条测试会失败并指出缺口。

    所以这里**声明式**断言外键的删除规则，而不是去执行一次注定失败的 DELETE ——
    FK 违例会让事务进入 aborted 状态，在 ORM session 里后续查询会变成
    MissingGreenlet，测起来又脆又难读。
    """
    rows = (
        await db_session.execute(
            text_of(
                "SELECT conname, confdeltype::text FROM pg_constraint "
                "WHERE contype = 'f' AND connamespace = 'public'::regnamespace"
            )
        )
    ).all()
    # PG 的 confdeltype：a=NO ACTION / r=RESTRICT / c=CASCADE / n=SET NULL ...
    by_name = {row[0]: row[1] for row in rows}
    assert len(by_name) >= 6, f"外键数量异常：{by_name}"

    # 尺码被码表成员引用 → 删除必须被 RESTRICT 挡住（不能 CASCADE，那会静默删成员）
    assert by_name["fk_size_group_items_sizes"] == "r"
    # 码表被成员引用 → 允许 CASCADE（删码表就该连成员一起没，否则留残渣）
    assert by_name["fk_size_group_items_groups"] == "c"
    # 组织类同理：组别/工序随车间走，车间不能被 CASCADE 掉
    assert by_name["fk_workshop_groups_workshops"] in ("a", "r")
    assert by_name["fk_operations_workshops"] in ("a", "r")


async def test_every_allow_physical_delete_resource_has_a_real_guard():
    """每个允许真删的资源，要么有引用检查器，要么其引用关系有真实外键。

    两者都没有 = 删掉还在被引用的行，且**数据库不会拦**。这是 ADR-0025
    「未被引用可真删」能成立的前提。
    """
    from app.modules.base.resources import RESOURCES

    # 尺码 ← 码表成员：有引用检查器 **且** 有 ON DELETE RESTRICT 外键
    assert RESOURCES_BY_KEY["sizes"].ref_checkers
    # 码表：成员随码表级联删除，由外键保证（fk_size_group_items_groups ON DELETE CASCADE）
    assert "size-groups" in {item.key for item in RESOURCES if item.allow_physical_delete}
    # 颜色：引用方（styles/style_colors/materials）T-BASE-002 才建，
    # 届时必须同时补 RefChecker **和外键**，否则就是裸奔
    assert RESOURCES_BY_KEY["colors"].ref_checkers == ()


#: 本模块用**独立引擎真提交**（并发测试必须如此，否则同一连接上的语句会被
#: PostgreSQL 串行化，测出来的是"顺序执行"）。代价是这些数据**不在**测试夹具的
#: 回滚范围内，会留在共享测试库里。
#:
#: 实测踩过：CC-2 插入的 ``CCxxxxxx`` 色码让后续
#: ``test_list_returns_page_envelope`` / ``test_list_includes_ref_count`` 全部失败
#: —— 典型的"测试顺序依赖"，而且报错点离原因很远。
#:
#: 所以按**前缀**清理：本模块建的行都用 ``CC`` / ``FK`` / ``LG`` / ``CS`` / ``DS`` /
#: ``AUD`` 前缀，与 seed 的内置码（WHT、XL、L…）不会冲突。
_TEST_CODE_PREFIXES: tuple[str, ...] = ("CC", "FK", "LG", "CS", "DS", "AUD")


@pytest.fixture(autouse=True)
async def _purge_concurrency_rows(app_database_url: str):
    """清掉本模块真提交留下的行（见上方说明）。"""
    yield
    engine = create_async_engine(app_database_url, pool_pre_ping=True)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        # 顺序要紧：先删关联行，再删被引用方，否则外键挡住
        await session.execute(
            text_of(
                "DELETE FROM size_group_items item WHERE EXISTS ("
                "  SELECT 1 FROM sizes s WHERE s.id = item.size_id "
                "  AND s.size_code LIKE ANY(:prefixes))"
            ),
            {"prefixes": [f"{p}%" for p in _TEST_CODE_PREFIXES]},
        )
        await session.execute(
            text_of(
                "DELETE FROM size_group_items item WHERE EXISTS ("
                "  SELECT 1 FROM size_groups g WHERE g.id = item.size_group_id "
                "  AND g.name LIKE ANY(:prefixes))"
            ),
            {"prefixes": [f"{p}%" for p in _TEST_CODE_PREFIXES]},
        )
        await session.execute(
            text_of("DELETE FROM size_groups WHERE name LIKE ANY(:prefixes)"),
            {"prefixes": [f"{p}%" for p in _TEST_CODE_PREFIXES]},
        )
        await session.execute(
            text_of("DELETE FROM sizes WHERE size_code LIKE ANY(:prefixes)"),
            {"prefixes": [f"{p}%" for p in _TEST_CODE_PREFIXES]},
        )
        await session.execute(
            text_of("DELETE FROM colors WHERE color_code LIKE ANY(:prefixes)"),
            {"prefixes": [f"{p}%" for p in _TEST_CODE_PREFIXES]},
        )
        await session.commit()
    await engine.dispose()
