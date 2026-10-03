"""认证与权限相关的数据工厂（docs/10-测试规范.md §7）。

为什么单独建 ``user.py``：``User`` 的 ``password_hash`` 必须是真 argon2id 哈希，
``data_scope`` / ``is_active`` / ``must_change_password`` 的组合直接决定测试覆盖
哪条分支。默认值集中在这里，测试里就不再出现"手写一堆字段"的字典。
"""

from typing import Any, ClassVar
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import AuthChannel, DataScope
from app.core.security import hash_password
from app.modules.auth.models import Permission, Role, RolePermission, RoleWorkshop, User, UserRole

#: 全局固定口令。**只用于测试**，强度刚好过策略校验
DEFAULT_PASSWORD = "Test1234"  # noqa: S105 —— 测试夹具常量，非真实凭据

#: 固定的"操作人" uuid。docs/04 §2 要求 ``created_by`` / ``updated_by`` NOT NULL，
#: 由应用层填；测试里用固定值而不是随机值，便于断言与复现（docs/10 §2.1）
OPERATOR_ID = UUID("00000000-0000-0000-0000-000000000001")

__all__ = [
    "DEFAULT_PASSWORD",
    "OPERATOR_ID",
    "PermissionFactory",
    "RoleFactory",
    "RoleWorkshopFactory",
    "UserFactory",
    "UserRoleFactory",
    "grant_permissions",
    "grant_role",
    "make_login_payload",
]


class UserFactory:
    """``users`` 工厂。"""

    defaults: ClassVar[dict[str, Any]] = {
        "employee_no": "A001",
        "name": "张三",
        "data_scope": DataScope.SELF,
        "is_active": True,
        "must_change_password": False,
        "created_by": OPERATOR_ID,
        "updated_by": OPERATOR_ID,
    }

    @classmethod
    async def create(cls, session: AsyncSession, **overrides: Any) -> User:
        """建用户并 flush（拿到 ``id``），返回 ORM 实体。

        默认填真 argon2id 哈希 —— 用假哈希会让 ``verify_password`` 走不到
        真正分支，测试就成了摆设。
        """
        payload: dict[str, Any] = {**cls.defaults, **overrides}
        payload.setdefault("password_hash", hash_password(DEFAULT_PASSWORD))
        user = User(**payload)
        session.add(user)
        await session.flush()
        return user


class RoleFactory:
    """``roles`` 工厂。"""

    defaults: ClassVar[dict[str, Any]] = {
        "code": "test_role",
        "name": "测试角色",
        "data_scope": DataScope.SELF,
        "is_system": False,
        "created_by": OPERATOR_ID,
        "updated_by": OPERATOR_ID,
    }

    @classmethod
    async def create(cls, session: AsyncSession, **overrides: Any) -> Role:
        role = Role(**{**cls.defaults, **overrides})
        session.add(role)
        await session.flush()
        return role


class PermissionFactory:
    """``permissions`` 工厂。"""

    defaults: ClassVar[dict[str, Any]] = {
        "code": "test:read",
        "name": "测试读取",
        "module": "test",
        "action": "read",
        "sort_order": 999,
        "created_by": OPERATOR_ID,
        "updated_by": OPERATOR_ID,
    }

    @classmethod
    async def create(cls, session: AsyncSession, **overrides: Any) -> Permission:
        permission = Permission(**{**cls.defaults, **overrides})
        session.add(permission)
        await session.flush()
        return permission


class UserRoleFactory:
    """``user_roles`` 工厂。"""

    @classmethod
    async def create(cls, session: AsyncSession, *, user_id: UUID, role_id: UUID) -> UserRole:
        link = UserRole(user_id=user_id, role_id=role_id)
        session.add(link)
        await session.flush()
        return link


class RoleWorkshopFactory:
    """``role_workshops`` 工厂。"""

    @classmethod
    async def create(
        cls, session: AsyncSession, *, role_id: UUID, workshop_id: UUID
    ) -> RoleWorkshop:
        link = RoleWorkshop(role_id=role_id, workshop_id=workshop_id)
        session.add(link)
        await session.flush()
        return link


async def grant_role(
    session: AsyncSession, *, user: User, role: Role, workshop_ids: tuple[UUID, ...] = ()
) -> None:
    """给用户授角色，并按需授予可见车间。"""
    await UserRoleFactory.create(session, user_id=user.id, role_id=role.id)
    for workshop_id in workshop_ids:
        await RoleWorkshopFactory.create(session, role_id=role.id, workshop_id=workshop_id)


async def grant_permissions(session: AsyncSession, *, role: Role, codes: tuple[str, ...]) -> None:
    """给角色授权限点。code 不存在时抛错 —— 静默跳过会造出"看起来授权了其实没有"的用例。"""
    if not codes:
        return
    rows = await session.execute(select(Permission).where(Permission.code.in_(codes)))
    found = {item.code for item in rows.scalars().all()}
    missing = set(codes) - found
    if missing:
        raise AssertionError(f"权限点不存在：{sorted(missing)}")
    permissions = await session.execute(select(Permission).where(Permission.code.in_(codes)))
    # 一次批量插入：super_admin 有 122 个权限点，逐条插入会让每个用例多 122 次往返
    session.add_all(
        RolePermission(role_id=role.id, permission_id=permission.id)
        for permission in permissions.scalars().all()
    )
    await session.flush()


def make_login_payload(
    *, employee_no: str = "A001", password: str = DEFAULT_PASSWORD, **extra: Any
) -> dict[str, Any]:
    """登录请求体。与 :data:`DEFAULT_PASSWORD` 保持一致，改一处即可。"""
    payload: dict[str, Any] = {
        "employee_no": employee_no,
        "password": password,
        "channel": AuthChannel.PC.value,
    }
    return {**payload, **extra}
