"""请求上下文与权限依赖（docs/07-认证与权限规范.md §3.1）。

包含三部分：
    1. :class:`AuthContext` —— 请求上下文（T-AUTH-001 落地）
    2. :func:`get_auth_context` —— 每次请求**查库**装配权限（docs/07 §1.1：
       "Token 载荷不放权限明细，权限查库，避免权限变更不生效"）
    3. :func:`require_permission` —— 接口级权限声明（docs/05 §6：每个接口必填）
"""

from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from uuid import UUID

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import DataScope
from app.core.db import get_db
from app.core.errors import BusinessError, ErrorCode
from app.core.security import TOKEN_TYPE_ACCESS, decode_token
from app.modules.auth.models import Permission, RolePermission, UserRole


@dataclass(frozen=True, slots=True)
class AuthContext:
    """一次请求的权限上下文。

    :param permissions: 该用户**当前**拥有的权限点集合。

        ⚠️ 每次请求从库里查，**不从 token 里读**（docs/07 §1.1：Token 载荷不放权限
        明细，避免权限变更不生效）。token 只带身份（sub / role_ids / data_scope）。
    :param data_scope: 数据范围。``SELF`` 的具体含义由资源决定：计件/工资是"本人"，
        款号是"我负责的款号"，由 service 层用显式列映射声明，不靠列名推断。
    :param allowed_workshop_ids: 可见车间集合 = 本人所属车间 + 各角色授予的车间。
        仅 ``data_scope=WORKSHOP`` 时有意义；为空且非 FACTORY 时查不到任何数据
        （见 ``app/core/scope.py``，T-AUTH-002 落地）。
    """

    user_id: UUID
    name: str
    employee_no: str
    workshop_id: UUID | None
    group_no: str | None
    permissions: frozenset[str]
    data_scope: DataScope
    allowed_workshop_ids: frozenset[UUID] = field(default_factory=frozenset)

    def has(self, permission: str) -> bool:
        """是否拥有某权限点。``*`` 为通配（系统超管）。"""
        return "*" in self.permissions or permission in self.permissions

    def has_any(self, permissions: frozenset[str] | set[str]) -> bool:
        """是否拥有其中任一权限点（前端 ``v-can="['a','b']"`` 的后端对应）。"""
        if "*" in self.permissions:
            return True
        return bool(self.permissions & permissions)

    @property
    def is_factory_scoped(self) -> bool:
        """是否全厂范围。用于跳过数据范围过滤（少一次查询）。"""
        return self.data_scope == DataScope.FACTORY


#: Bearer 认证。auto_error=False：自己抛 11001 以便带上 request_id 与统一文案
bearer_scheme = HTTPBearer(auto_error=False, description="Bearer <access_token>")


async def load_permissions(
    session: AsyncSession, user_id: UUID, role_ids: list[str] | None = None
) -> frozenset[str]:
    """查库取该用户的权限点集合。

    **每次请求都查**：docs/07 §1.1 明确要求"权限查库，避免权限变更不生效"。
    走 ``user_roles`` / ``role_permissions`` 两个复合唯一索引，QPS 与权限点规模
    都不构成瓶颈（实测见 docs/12 §5 备注：若超阈值再评估缓存，且需写 ADR）。
    """
    stmt = select(Permission.code).join(
        RolePermission, RolePermission.permission_id == Permission.id
    )
    if role_ids:
        stmt = stmt.where(RolePermission.role_id.in_(role_ids))
    else:
        stmt = stmt.join(UserRole, UserRole.role_id == RolePermission.role_id).where(
            UserRole.user_id == user_id
        )
    rows = await session.execute(stmt.distinct())
    return frozenset(rows.scalars().all())


async def load_allowed_workshops(
    session: AsyncSession, user_id: UUID, role_ids: list[str] | None
) -> frozenset[UUID]:
    """可见车间集合 = 各角色授予的车间与本人所属车间的并集。

    返回空集时 ``WORKSHOP`` 范围查不到任何数据（这是**正确**行为，
    不是 bug —— docs/07 §3.2 明确要求）。
    """
    from app.modules.auth.models import RoleWorkshop

    result: set[UUID] = set()
    if role_ids:
        stmt = select(RoleWorkshop.workshop_id).where(RoleWorkshop.role_id.in_(role_ids))
        rows = await session.execute(stmt)
        result.update(rows.scalars().all())
    return frozenset(result)


async def get_auth_context(
    session: AsyncSession = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> AuthContext:
    """装配请求上下文。

    :raises BusinessError: ``11001`` 未带 token、``11002`` token 非法/过期、
        ``11004`` 账号已停用
    """
    from app.modules.auth.service import AuthService

    if credentials is None:
        raise BusinessError(ErrorCode.UNAUTHORIZED, "未登录，请先登录")
    payload = decode_token(credentials.credentials, expected_type=TOKEN_TYPE_ACCESS)

    service = AuthService(session)
    user = await service.get_active_user(UUID(str(payload["sub"])))
    role_ids = [str(item) for item in payload.get("role_ids", [])]
    permissions = await load_permissions(session, user.id, role_ids or None)
    allowed = await load_allowed_workshops(session, user.id, role_ids or None)
    return user.to_auth_context(permissions=permissions, allowed_workshop_ids=allowed)


def require_permission(
    permission: str, *, scope: str | None = None
) -> Callable[..., Coroutine[None, None, AuthContext]]:
    """接口级权限声明（docs/05 §6：每个接口必填）。

    :param permission: 权限点 code，如 ``"base:update"``
    :param scope: 数据范围字段名（表名）。**仅作文档用途** ——
        真正的范围过滤在 service 层用 :func:`app.core.scope.apply_data_scope` 强制，
        因为 Router 里过滤会被 service 的其他调用路径绕过（docs/07 §3.2 铁律 1）。
    """
    del scope

    async def dependency(ctx: AuthContext = Depends(get_auth_context)) -> AuthContext:
        if not ctx.has(permission):
            raise BusinessError(
                ErrorCode.PERMISSION_DENIED,
                f"无权限执行该操作：{permission}",
                details={"required_permission": permission},
            )
        return ctx

    return dependency
