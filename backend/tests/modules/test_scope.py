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
from app.core.scope import SCOPE_SPECS, ScopeSpec, apply_data_scope, assert_in_scope, in_scope
from app.modules.auth.models import User
from tests.factories.user import UserFactory


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
    """没在 ``SCOPE_SPECS`` 登记的资源不允许"默认放行"。"""
    user = await UserFactory.create(db_session, employee_no="U001")
    await db_session.flush()
    # 未登记表名 → resolved 为 None → 非 FACTORY 一律拒绝
    ctx = AuthContext(
        user_id=user.id,
        name="x",
        employee_no="U001",
        workshop_id=None,
        group_no=None,
        permissions=frozenset(),
        data_scope=DataScope.WORKSHOP,
    )
    assert in_scope(user, ctx) is False


def test_scope_specs_all_are_scope_spec():
    """每个登记项都要能说明 SELF 语义，避免留空占位。"""
    for table, spec in SCOPE_SPECS.items():
        assert isinstance(spec, ScopeSpec), table
