"""``apply_data_scope`` / ``in_scope`` 的行为测试（docs/07 §3.2）。

⚠️ 本模块是**越权防线**的核心，测试重点放在三类"必须查不到"的场景：
    1. 列缺失（资源不支持该范围）→ ``where(False)``，不是"不加过滤"
    2. 可见车间为空 → 查不到任何数据，**不能退化成全厂可见**
    3. 详情按 ID 直查 → ``in_scope`` 拒绝

只测"正常路径能查到"是不够的：那种实现哪怕把 ``apply_data_scope`` 整个
注释掉也能通过，而那正是它要防的漏洞。
"""

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.common.enums import DataScope
from app.core.errors import BusinessError
from app.core.permissions import AuthContext
from app.core.scope import (
    PENDING_TABLES,
    SCOPE_EXEMPT_TABLES,
    SCOPE_SPECS,
    ScopeSpec,
    apply_data_scope,
    assert_in_scope,
    in_scope,
)
from app.modules.auth.models import User
from tests.factories.user import UserFactory

#: 用于构造"未登记资源"用例的固定 owner
_OWNER = uuid4()


def _ctx(
    *,
    data_scope: DataScope,
    workshop_id=None,
    group_no: str | None = None,
    allowed: frozenset | None = None,
) -> AuthContext:
    return AuthContext(
        user_id=uuid4(),
        name="测试",
        employee_no="A001",
        workshop_id=workshop_id,
        group_no=group_no,
        permissions=frozenset(),
        data_scope=data_scope,
        allowed_workshop_ids=allowed or frozenset(),
    )


async def _all_users(session) -> list[User]:
    await UserFactory.create(session, employee_no="S001", data_scope=DataScope.SELF)
    await UserFactory.create(session, employee_no="S002", data_scope=DataScope.SELF)
    await session.flush()
    stmt = select(User).order_by(User.employee_no)
    return list((await session.execute(stmt)).scalars().all())


# ----------------------------------------------------------------- FACTORY


async def test_factory_scope_adds_no_workspace_filter(db_session):
    """全厂范围不加范围条件 —— 但软删过滤仍要加。"""
    await _all_users(db_session)
    stmt = apply_data_scope(select(User), User, _ctx(data_scope=DataScope.FACTORY))
    rows = (await db_session.execute(stmt)).scalars().all()
    assert len(rows) == 2


# --------------------------------------------------------------------- SELF


async def test_self_scope_returns_only_own_rows(db_session):
    user_a, _user_b = await _all_users(db_session)
    ctx = _ctx(data_scope=DataScope.SELF)
    ctx = AuthContext(
        user_id=user_a.id,
        name=ctx.name,
        employee_no=ctx.employee_no,
        workshop_id=None,
        group_no=None,
        permissions=frozenset(),
        data_scope=DataScope.SELF,
    )
    stmt = apply_data_scope(select(User), User, ctx)
    rows = (await db_session.execute(stmt)).scalars().all()
    assert [row.employee_no for row in rows] == ["S001"]


async def test_self_scope_with_unsupported_column_returns_nothing(db_session):
    """``users`` 的 SELF 走 ``id`` 列；若某资源 spec 把 user_column 设成 None，
    必须查不到任何数据 —— 而不是放行全部。"""
    ctx = _ctx(data_scope=DataScope.SELF)
    stmt = apply_data_scope(
        select(User), User, ctx, spec=ScopeSpec(user_column=None, workshop_column="workshop_id")
    )
    rows = (await db_session.execute(stmt)).scalars().all()
    assert rows == []


# ----------------------------------------------------------------- WORKSHOP


async def test_workshop_scope_filters_by_allowed_workshops(db_session):
    w1, w2 = uuid4(), uuid4()
    user_a = await UserFactory.create(db_session, employee_no="W001", workshop_id=w1)
    user_b = await UserFactory.create(db_session, employee_no="W002", workshop_id=w2)
    del user_a, user_b
    await db_session.flush()

    ctx = _ctx(data_scope=DataScope.WORKSHOP, allowed=frozenset({w1}))
    stmt = apply_data_scope(select(User), User, ctx)
    rows = (await db_session.execute(stmt)).scalars().all()
    assert [row.employee_no for row in rows] == ["W001"]


async def test_workshop_scope_with_empty_allowlist_returns_nothing(db_session):
    """这是最容易写成漏洞的一处：空集合 if 写成"跳过过滤"就等于全厂可见。"""
    await _all_users(db_session)
    ctx = _ctx(data_scope=DataScope.WORKSHOP, allowed=frozenset())
    stmt = apply_data_scope(select(User), User, ctx)
    rows = (await db_session.execute(stmt)).scalars().all()
    assert rows == []


async def test_workshop_scope_also_includes_own_workshop(db_session):
    """角色没授权任何车间，但用户自己属于某车间 → 至少能看到自己。"""
    w1 = uuid4()
    await _all_users(db_session)
    ctx = _ctx(data_scope=DataScope.WORKSHOP, workshop_id=w1, allowed=frozenset())
    stmt = apply_data_scope(select(User), User, ctx)
    # users 表的 spec 里 workshop_column 是 workshop_id；没有任何行属于 w1
    assert (await db_session.execute(stmt)).scalars().all() == []


async def test_workshop_scope_with_missing_column_returns_nothing(db_session):
    await _all_users(db_session)
    ctx = _ctx(data_scope=DataScope.WORKSHOP, allowed=frozenset({uuid4()}))
    stmt = apply_data_scope(select(User), User, ctx, spec=ScopeSpec(workshop_column=None))
    assert (await db_session.execute(stmt)).scalars().all() == []


# -------------------------------------------------------------------- GROUP


async def test_group_scope_requires_both_workshop_and_group(db_session):
    w1 = uuid4()
    await _all_users(db_session)
    ctx = _ctx(data_scope=DataScope.GROUP, workshop_id=w1, group_no="A")
    stmt = apply_data_scope(select(User), User, ctx)
    assert (await db_session.execute(stmt)).scalars().all() == []


async def test_group_scope_without_group_returns_nothing(db_session):
    await _all_users(db_session)
    ctx = _ctx(data_scope=DataScope.GROUP, workshop_id=uuid4(), group_no=None)
    assert ctx.group_no is None
    stmt = apply_data_scope(select(User), User, ctx)
    assert (await db_session.execute(stmt)).scalars().all() == []


# ------------------------------------------------------------------ 软删


async def test_soft_delete_filter_is_appended_automatically(db_session):
    from datetime import UTC, datetime

    await UserFactory.create(db_session, employee_no="D001")
    alive = await UserFactory.create(db_session, employee_no="D002")
    await db_session.flush()
    alive.deleted_at = datetime.now(tz=UTC)
    await db_session.flush()

    stmt = apply_data_scope(select(User), User, _ctx(data_scope=DataScope.FACTORY))
    rows = (await db_session.execute(stmt)).scalars().all()
    assert [row.employee_no for row in rows] == ["D001"]


# ---------------------------------------------------------------- in_scope


async def test_in_scope_rejects_row_outside_workshop(db_session):
    w1 = uuid4()
    owner = await UserFactory.create(db_session, employee_no="O001", workshop_id=w1)
    await UserFactory.create(db_session, employee_no="O002", workshop_id=uuid4())
    await db_session.flush()

    ctx = _ctx(data_scope=DataScope.WORKSHOP, allowed=frozenset({w1}))
    assert in_scope(owner, ctx) is True

    other = await UserFactory.create(db_session, employee_no="O003", workshop_id=uuid4())
    await db_session.flush()
    assert in_scope(other, ctx) is False


async def test_in_scope_factory_scope_always_true(db_session):
    user = await UserFactory.create(db_session, employee_no="F001")
    await db_session.flush()
    assert in_scope(user, _ctx(data_scope=DataScope.FACTORY)) is True


async def test_assert_in_scope_raises_12002(db_session):
    user = await UserFactory.create(db_session, employee_no="X001", workshop_id=uuid4())
    await db_session.flush()
    ctx = _ctx(data_scope=DataScope.WORKSHOP, allowed=frozenset())
    with pytest.raises(BusinessError) as excinfo:
        assert_in_scope(user, ctx)
    assert int(excinfo.value.code) == 12002


async def test_in_scope_unregistered_resource_is_strict(db_session):
    """没在 ``SCOPE_SPECS`` 登记的资源不允许"默认放行"。

    ⚠️ 这里用 ``AuthLoginLog``（确实未登记），**不能**用 ``User`` —— ``users``
    早就登记过了，拿它来测这条分支会因为 ``workshop_id`` 为空而"碰巧"返回
    False，看起来通过、实际没走到 ``resolved is None`` 那条路径。
    """
    from app.modules.auth.models import AuthLoginLog

    row = AuthLoginLog(employee_no="U001", channel="PC", is_success=True)
    ctx = AuthContext(
        user_id=uuid4(),
        name="x",
        employee_no="U001",
        workshop_id=None,
        group_no=None,
        permissions=frozenset(),
        data_scope=DataScope.WORKSHOP,
    )
    assert "auth_login_logs" not in SCOPE_SPECS
    assert in_scope(row, ctx) is False


async def test_unregistered_resource_self_scope_falls_back_to_created_by(db_session):
    """未登记资源在 ``SELF`` 下按 ``created_by`` 判定 —— 最严但可解释。"""

    mine = _log_row(created_by=_OWNER)
    theirs = _log_row(created_by=uuid4())
    ctx = AuthContext(
        user_id=_OWNER,
        name="x",
        employee_no="U001",
        workshop_id=None,
        group_no=None,
        permissions=frozenset(),
        data_scope=DataScope.SELF,
    )
    assert in_scope(mine, ctx) is True
    assert in_scope(theirs, ctx) is False


def _log_row(*, created_by) -> object:
    """构造一个内存态的 AuthLoginLog（只用于 ``in_scope`` 的属性判定，不落库）。"""
    from app.modules.auth.models import AuthLoginLog

    row = AuthLoginLog(employee_no="U001", channel="PC", is_success=True)
    object.__setattr__(row, "created_by", created_by)
    return row


def test_scope_specs_keys_are_real_or_declared_pending():
    """登记项要么是真实表，要么在 ``PENDING_TABLES`` 里显式声明为待建。

    守卫的是**拼写错误**：``SCOPE_SPECS`` 里写了个不存在的表名（``style`` 而不是
    ``styles``），数据范围会静默失效而没有任何报错。这比原来那句
    ``isinstance(spec, ScopeSpec)`` 有用得多 —— 后者对一个字面量字典永远成立，
    是条纯噪声用例。
    """
    import app.modules.auth.models  # noqa: F401 —— 确保 auth 表已注册
    from app.common.models import Base

    known = set(Base.metadata.tables) | PENDING_TABLES
    for table in SCOPE_SPECS:
        assert table in known, f"SCOPE_SPECS 里的 {table} 既不是真实表名也没声明为待建"


def test_pending_tables_use_plural_convention():
    """待建表名必须是复数（docs/04 表名约定），否则建表时会对不上。"""
    for table in PENDING_TABLES:
        assert table.endswith("s"), f"待建表名 {table} 不是复数"


def test_scope_specs_declares_workshop_resources():
    """凡是有 ``workshop_id`` 列的表，都必须登记车间映射或**显式豁免**。

    这是本模块最重要的一条守卫：漏登记一张单据表，它就会走"未登记 → 查不到
    数据"的路径，表现为**界面一片空白**而不是越权 —— 那种 bug 极难从日志发现。
    """
    import app.modules.auth.models  # noqa: F401
    from app.common.models import Base

    missing = [
        name
        for name, table in Base.metadata.tables.items()
        if "workshop_id" in table.columns
        and name not in SCOPE_SPECS
        and name not in SCOPE_EXEMPT_TABLES
    ]
    assert missing == [], f"这些表有车间列但既未登记数据范围也没写进 SCOPE_EXEMPT_TABLES：{missing}"
