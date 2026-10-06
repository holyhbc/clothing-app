"""用户读路径：列表 / 候选 / 详情 / 角色码 / 出参映射（原 service.py 125-181、408-537）。

:class:`UserQueryMixin` 只承载**读**；写路径在 :mod:`user_write_mixin`，最后管理员
守卫在 :mod:`user_guard_mixin`。交叉调用用 ``TYPE_CHECKING`` 前置声明（照抄 base
``style_child_mixin.py``），运行期不 import 兄弟 Mixin，避免环。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import DataScope
from app.core.errors import BusinessError, ErrorCode
from app.core.permissions import AuthContext
from app.core.scope import apply_data_scope
from app.modules.auth.models import Role, User, UserRole
from app.modules.system.schemas import UserOptionOut, UserOut

from .common import MAX_OFFSET


class UserQueryMixin:
    """用户读：列表 / 候选 / 详情 / 角色码 / 出参映射。"""

    session: AsyncSession
    ctx: AuthContext

    if TYPE_CHECKING:

        async def _required(self, user_id: UUID) -> User: ...

    def _base_stmt(self) -> Select[Any]:
        stmt = apply_data_scope(select(User), User, self.ctx)
        return stmt.where(User.deleted_at.is_(None))

    async def list_users(
        self,
        *,
        keyword: str | None,
        workshop_id: UUID | None,
        is_active: bool | None,
        page: int,
        size: int,
    ) -> tuple[list[UserOut], int]:
        stmt = self._base_stmt()
        if keyword:
            like = f"%{keyword.strip()}%"
            # 只搜「工号 + 姓名」两列（docs/05 §9.5.1 的 q 语义）——
            # **不搜 password_hash**，虽然它有索引。
            stmt = stmt.where(User.employee_no.ilike(like) | User.name.ilike(like))
        if workshop_id is not None:
            stmt = stmt.where(User.workshop_id == workshop_id)
        if is_active is not None:
            stmt = stmt.where(User.is_active.is_(is_active))
        stmt = stmt.order_by(User.employee_no.asc())

        total = await self.session.scalar(
            select(func.count()).select_from(stmt.order_by(None).subquery())
        )
        rows = list(
            (await self.session.execute(stmt.offset((page - 1) * size).limit(size))).scalars().all()
        )
        codes_by_user = await self._role_codes([row.id for row in rows])
        return [self._out(row, codes_by_user.get(row.id, [])) for row in rows], int(total or 0)

    async def options(self, keyword: str | None, size: int, offset: int) -> list[UserOptionOut]:
        """用户候选（Combo 用，docs/05 §9.5.2）。``label`` 是「工号 姓名」。"""
        if offset > MAX_OFFSET:
            raise BusinessError(ErrorCode.PARAM_INVALID, "offset 过大，请改用关键字搜索")
        stmt = self._base_stmt().order_by(User.employee_no.asc())
        if keyword:
            like = f"%{keyword.strip()}%"
            stmt = stmt.where(User.employee_no.ilike(like) | User.name.ilike(like))
        rows = list((await self.session.execute(stmt.offset(offset).limit(size))).scalars().all())
        return [
            UserOptionOut(
                value=row.id,
                label=f"{row.employee_no} {row.name}",
                sub=None if row.is_active else "已停用",
                disabled=not row.is_active,
            )
            for row in rows
        ]

    async def get_user(self, user_id: UUID) -> UserOut:
        user = await self._required(user_id)
        codes = await self._role_codes([user.id])
        return self._out(user, codes.get(user.id, []))

    async def _role_codes(self, user_ids: list[UUID]) -> dict[UUID, list[str]]:
        if not user_ids:
            return {}
        rows = (
            await self.session.execute(
                select(UserRole.user_id, Role.code)
                .join(Role, Role.id == UserRole.role_id)
                .where(UserRole.user_id.in_(user_ids))
                .order_by(Role.code.asc())
            )
        ).all()
        result: dict[UUID, list[str]] = {}
        for user_id, code in rows:
            result.setdefault(user_id, []).append(code)
        return result

    def _out(self, row: User, role_codes: list[str]) -> UserOut:
        return UserOut(
            id=row.id,
            employee_no=row.employee_no,
            name=row.name,
            workshop_id=row.workshop_id,
            group_no=row.group_no,
            data_scope=DataScope(row.data_scope),
            is_active=row.is_active,
            must_change_password=row.must_change_password,
            role_codes=role_codes,
            version=row.version,
            created_at=row.created_at.isoformat(),
            updated_at=row.updated_at.isoformat(),
        )
