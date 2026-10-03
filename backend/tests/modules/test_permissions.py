"""鉴权链路测试（docs/07 §3.1、§3.2）。

覆盖三层，从外到内：
    1. ``get_auth_context`` —— bearer 解析、令牌类型、账号停用、**权限查库**
    2. ``require_permission`` —— 12001、``*`` 通配
    3. 数据范围来源 —— 角色授权的车间

⚠️ 关键用例是"改权限立即生效"：JWT 载荷里没有权限明细（docs/07 §1.1），
如果实现改成从令牌读权限，这个用例会失败。
"""

from dataclasses import fields
from uuid import uuid4

import pytest
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select

from app.common.enums import DataScope
from app.core.errors import BusinessError, ErrorCode
from app.core.permissions import AuthContext, get_auth_context, require_permission
from app.core.security import (
    TOKEN_TYPE_ACCESS,
    TOKEN_TYPE_REFRESH,
    create_access_token,
    decode_token,
)
from app.modules.auth.models import Permission, Role, User
from tests.factories.user import (
    RoleFactory,
    UserFactory,
    UserRoleFactory,
    grant_permissions,
)

#: registry 里真实存在的权限点（手写字面量会在权限点改名后静默失效）
PERM_A = "base:read"
PERM_B = "base:update"


def _bearer(
    user: User, *, role_ids: list[str] | None = None, scope: DataScope | None = None
) -> dict[str, str]:
    token, _ = create_access_token(
        user_id=str(user.id),
        role_ids=role_ids or [],
        data_scope=(scope or DataScope(user.data_scope)).value,
    )
    return {"Authorization": f"Bearer {token}"}


# -------------------------------------------------------- get_auth_context


async def test_missing_bearer_raises_11001():
    with pytest.raises(BusinessError) as excinfo:
        await get_auth_context(session=None, credentials=None)  # type: ignore[arg-type]
    assert excinfo.value.code is ErrorCode.UNAUTHORIZED


async def test_garbage_token_raises_11002(db_session):
    with pytest.raises(BusinessError) as excinfo:
        await get_auth_context(session=db_session, credentials=_creds("not-a-jwt"))
    assert excinfo.value.code is ErrorCode.TOKEN_INVALID


def test_token_type_cannot_be_swapped():
    """access 与 refresh 的 type 必须互斥：refresh 有效期 7-30 天，
    把它当 access 用等于开了一个长期后门。"""
    token, _ = create_access_token(user_id=str(uuid4()), role_ids=[], data_scope="SELF")
    assert decode_token(token, expected_type=TOKEN_TYPE_ACCESS)["type"] == TOKEN_TYPE_ACCESS
    with pytest.raises(BusinessError) as excinfo:
        decode_token(token, expected_type=TOKEN_TYPE_REFRESH)
    assert excinfo.value.code is ErrorCode.TOKEN_INVALID


async def test_unknown_user_raises_11001(db_session):
    token, _ = create_access_token(
        user_id=str(uuid4()), role_ids=[], data_scope=DataScope.SELF.value
    )
    with pytest.raises(BusinessError) as excinfo:
        await get_auth_context(session=db_session, credentials=_creds(token))
    assert excinfo.value.code is ErrorCode.UNAUTHORIZED


async def test_disabled_user_raises_11004(db_session):
    user = await UserFactory.create(db_session, employee_no="OFF001", is_active=False)
    await db_session.flush()
    with pytest.raises(BusinessError) as excinfo:
        await get_auth_context(session=db_session, credentials=_creds(_token_of(user)))
    assert excinfo.value.code is ErrorCode.ACCOUNT_DISABLED


async def test_permissions_come_from_db_not_from_token(db_session):
    """docs/07 §1.1：令牌里不放权限明细。

    用例先生成令牌（此刻用户还没有角色），再给角色授权 —— 若实现从令牌读权限，
    这里就拿不到权限，测试会失败。
    """
    user = await UserFactory.create(db_session, employee_no="P001")
    role = await RoleFactory.create(db_session, code="r1", name="R1")
    await UserRoleFactory.create(db_session, user_id=user.id, role_id=role.id)
    await db_session.flush()

    token = _token_of(user, role_ids=[str(role.id)])
    ctx = await get_auth_context(session=db_session, credentials=_creds(token))
    assert ctx.permissions == frozenset()

    await grant_permissions(db_session, role=role, codes=(PERM_A, PERM_B))
    ctx2 = await get_auth_context(session=db_session, credentials=_creds(token))
    assert ctx2.permissions == frozenset({PERM_A, PERM_B})


async def test_allowed_workshops_come_from_role_grants(db_session):
    user = await UserFactory.create(db_session, employee_no="W001")
    role = await RoleFactory.create(db_session, code="r2", name="R2")
    await UserRoleFactory.create(db_session, user_id=user.id, role_id=role.id)
    w1, w2 = uuid4(), uuid4()
    from tests.factories.user import RoleWorkshopFactory

    await RoleWorkshopFactory.create(db_session, role_id=role.id, workshop_id=w1)
    await RoleWorkshopFactory.create(db_session, role_id=role.id, workshop_id=w2)
    await db_session.flush()

    ctx = await get_auth_context(
        session=db_session, credentials=_creds(_token_of(user, role_ids=[str(role.id)]))
    )
    assert ctx.allowed_workshop_ids == frozenset({w1, w2})


async def test_context_does_not_leak_password_hash(db_session):
    user = await UserFactory.create(db_session, employee_no="L001")
    await UserRoleFactory.create(
        db_session, user_id=user.id, role_id=(await _super_role(db_session)).id
    )
    await db_session.flush()
    ctx = await get_auth_context(session=db_session, credentials=_creds(_token_of(user)))
    # AuthContext 是 slots dataclass，没有 __dict__，只能按声明字段核对
    assert {item.name for item in fields(ctx)} == {
        "user_id",
        "name",
        "employee_no",
        "workshop_id",
        "group_no",
        "permissions",
        "data_scope",
        "allowed_workshop_ids",
    }
    assert "password_hash" not in {item.name for item in fields(ctx)}
    assert "phone" not in {item.name for item in fields(ctx)}


# ------------------------------------------------------ require_permission


async def _super_role(session) -> Role:
    found = await session.execute(select(Role).where(Role.code == "super_admin"))
    role = found.scalar_one_or_none()
    if role is not None:
        return role
    return await RoleFactory.create(
        session, code="super_admin", name="超级管理员", data_scope=DataScope.FACTORY
    )


def _token_of(user: User, *, role_ids: list[str] | None = None) -> str:
    token, _ = create_access_token(
        user_id=str(user.id),
        role_ids=role_ids or [],
        data_scope=DataScope(user.data_scope).value,
    )
    return token


def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="bearer", credentials=token)


async def test_require_permission_grants_when_present():
    ctx = AuthContext(
        user_id=uuid4(),
        name="n",
        employee_no="E1",
        workshop_id=None,
        group_no=None,
        permissions=frozenset({PERM_A}),
        data_scope=DataScope.SELF,
    )
    dep = require_permission(PERM_A)
    assert await dep(ctx) is ctx


async def test_require_permission_denies_raises_12001():
    ctx = AuthContext(
        user_id=uuid4(),
        name="n",
        employee_no="E1",
        workshop_id=None,
        group_no=None,
        permissions=frozenset({PERM_A}),
        data_scope=DataScope.SELF,
    )
    dep = require_permission(PERM_B)
    with pytest.raises(BusinessError) as excinfo:
        await dep(ctx)
    assert excinfo.value.code is ErrorCode.PERMISSION_DENIED
    assert PERM_B in excinfo.value.message


async def test_global_star_grants_everything():
    """``*`` 是全局通配。registry 实际不给任何角色发 ``*``（super_admin 拿的是
    全部 122 个显式 code），这里只锁住"万一有人手工发了 ``*``"的行为。"""
    ctx = AuthContext(
        user_id=uuid4(),
        name="n",
        employee_no="E1",
        workshop_id=None,
        group_no=None,
        permissions=frozenset({"*"}),
        data_scope=DataScope.SELF,
    )
    assert await require_permission(PERM_B)(ctx) is ctx
    assert await require_permission("piecework:count")(ctx) is ctx


async def test_prefix_wildcard_does_not_grant():
    """``base:*`` **不**授予 ``base:update``。

    ⚠️ docs/07 §2.2 的 122 个权限点里没有任何通配写法，§2.3 只说 super_admin
    "所有权限"。所以前缀通配是**未定义行为**，这里按最严处理（精确匹配才放行），
    并已登记为 docs/12 §5 的 L-027 等规范确认。
    """
    ctx = AuthContext(
        user_id=uuid4(),
        name="n",
        employee_no="E1",
        workshop_id=None,
        group_no=None,
        permissions=frozenset({"base:*"}),
        data_scope=DataScope.SELF,
    )
    with pytest.raises(BusinessError):
        await require_permission(PERM_B)(ctx)


async def test_permission_registry_rows_are_real(db_session):
    """守卫：测试引用的权限点必须真实存在于库中。"""
    rows = await db_session.execute(
        select(Permission.code).where(Permission.code.in_([PERM_A, PERM_B]))
    )
    assert set(rows.scalars().all()) == {PERM_A, PERM_B}
