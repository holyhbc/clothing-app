"""最后管理员守卫：停用前确认不会剩下「零个能改权限的账号」（原 service.py 457-515）。

只承载这一个不变量的检查；用户读 / 写分别在 :mod:`user_query_mixin` /
:mod:`user_write_mixin`。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessError, ErrorCode
from app.core.permissions import AuthContext
from app.modules.auth.models import Permission, Role, RolePermission, User, UserRole


class UserGuardMixin:
    """停用用户前的最后管理员守卫。"""

    session: AsyncSession
    ctx: AuthContext

    async def _guard_has_operator(self, *, target_id: UUID) -> None:
        """停用前确认不会剩下「零个能改权限的账号」。

        口径：剩下的人里只要有一个满足下面**任一**条件就放行 ——
        ① 拥有内置角色 ``super_admin``；
        ② 拥有权限点 ``system:role:manage``。

        为什么两个条件都查：``system:role:manage`` 可以被单独授给非超管，
        那时"没有超管"并不等于"没人能改权限"；反过来只看权限点的话，
        超管被误降权之后系统同样会锁死。先例是
        ``cli/seed_baseline.py::ensure_initial_admin`` 要求"至少存在一个超管账号"，
        这里把同一条不变量延伸到停用动作。

        ⚠️ 候选集**不带任何 join**（只有 ``users`` 一张表），再拿它去 ``IN``
        角色/权限子查询。早先把 ``UserRole`` join 进了候选集，于是外层的
        ``IN (select user_roles …)`` 又引入一次 ``user_roles`` —— 同一个 FROM 项
        出现两次，SQLAlchemy 报 ``cartesian product`` 警告，而本项目
        ``filterwarnings=error`` 直接把它变成失败：症状是「停用接口 500，报错却说
        cartesian product」，与停用毫无关系。
        """
        candidates = select(User.id).where(
            User.is_active.is_(True), User.deleted_at.is_(None), User.id != target_id
        )
        candidates_sq = candidates.subquery()

        by_role = await self.session.scalar(
            select(func.count())
            .select_from(candidates_sq)
            .where(
                candidates_sq.c.id.in_(
                    select(UserRole.user_id)
                    .join(Role, Role.id == UserRole.role_id)
                    .where(Role.code == "super_admin", Role.deleted_at.is_(None))
                )
            )
        )
        if int(by_role or 0) > 0:
            return

        by_permission = await self.session.scalar(
            select(func.count())
            .select_from(candidates_sq)
            .where(
                candidates_sq.c.id.in_(
                    select(UserRole.user_id)
                    .join(RolePermission, RolePermission.role_id == UserRole.role_id)
                    .join(Permission, Permission.id == RolePermission.permission_id)
                    .where(Permission.code == "system:role:manage")
                )
            )
        )
        if int(by_permission or 0) > 0:
            return

        raise BusinessError(
            ErrorCode.ILLEGAL_OPERATION,
            "这是最后一个能改权限的账号，停用后没有人能再改权限。"
            "请先建另一个管理员并授予 system:role:manage",
        )
