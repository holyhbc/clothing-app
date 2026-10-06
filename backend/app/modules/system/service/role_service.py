"""角色与权限点管理（原 service.py 540-859）。

``SystemRoleService`` 保持单文件（约 320 行，计入自身 import 仍 ≤400）。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import DataScope
from app.common.permissions_registry import permission_codes
from app.core.db import unit_of_work
from app.core.errors import BusinessError, ErrorCode
from app.core.permissions import AuthContext
from app.modules.auth.models import Permission, Role, RolePermission, User, UserRole
from app.modules.base.service import write_document_log
from app.modules.system.schemas import RoleCreate, RoleOut, RolePatch
from app.modules.system.scopes import scope_text

from .common import ACTION_CREATE, ACTION_UPDATE, DOC_TYPE_ROLE, MAX_OFFSET


class SystemRoleService:
    """角色与权限点管理。

    角色表**不进数据范围过滤**（见 ``core/scope.py::SCOPE_EXEMPT_TABLES``）：
    它是"谁能看哪些车间"的定义本身，再按车间过滤它，管理员就没法给自己配车间了。
    """

    def __init__(self, session: AsyncSession, ctx: AuthContext) -> None:
        self.session = session
        self.ctx = ctx

    async def list_roles(self, keyword: str | None) -> list[RoleOut]:
        stmt = select(Role).where(Role.deleted_at.is_(None))
        if keyword:
            like = f"%{keyword.strip()}%"
            stmt = stmt.where(Role.code.ilike(like) | Role.name.ilike(like))
        rows = list((await self.session.execute(stmt.order_by(Role.code.asc()))).scalars().all())
        perms = await self._permissions_by_role([row.id for row in rows])
        counts = await self._user_counts([row.id for row in rows])
        return [self._out(row, perms.get(row.id, []), counts.get(row.id, 0)) for row in rows]

    async def role_options(
        self, keyword: str | None, size: int, offset: int
    ) -> list[dict[str, Any]]:
        if offset > MAX_OFFSET:
            raise BusinessError(ErrorCode.PARAM_INVALID, "offset 过大，请改用关键字搜索")
        stmt = select(Role).where(Role.deleted_at.is_(None)).order_by(Role.code.asc())
        if keyword:
            like = f"%{keyword.strip()}%"
            stmt = stmt.where(Role.code.ilike(like) | Role.name.ilike(like))
        rows = list((await self.session.execute(stmt.offset(offset).limit(size))).scalars().all())
        return [
            {
                "value": row.code,
                "label": row.name,
                "sub": scope_text(DataScope(row.data_scope)),
                # 内置角色**照常可选**（它就是给用户授权用的），只是不可编辑 / 不可停用。
                # 已软删的角色不会出现在这里（上面 where 掉了）。
                "disabled": False,
            }
            for row in rows
        ]

    async def create_role(self, payload: RoleCreate) -> RoleOut:
        unknown = sorted(set(payload.permission_codes) - set(permission_codes()))
        if unknown:
            raise BusinessError(ErrorCode.PARAM_INVALID, f"权限点不存在：{unknown}")
        permission_ids = await self._permission_ids(payload.permission_codes)

        async with unit_of_work(self.session):
            role = Role(
                code=payload.code.strip(),
                name=payload.name.strip(),
                data_scope=payload.data_scope.value,
                # ⚠️ is_system 恒为 false：内置角色只能由 seed 建，
                # 不给接口留"建一个内置角色"的口子。
                is_system=False,
                description=payload.description,
                created_by=self.ctx.user_id,
                updated_by=self.ctx.user_id,
            )
            self.session.add(role)
            try:
                await self.session.flush()
            except IntegrityError as exc:
                await self.session.rollback()
                raise BusinessError(
                    ErrorCode.PARAM_INVALID, f"角色 code 已存在：{payload.code}"
                ) from exc
            await self._replace_permissions(role.id, permission_ids, reason=f"新建角色 {role.code}")
            await write_document_log(
                self.session,
                self.ctx,
                doc_type=DOC_TYPE_ROLE,
                doc_id=role.id,
                doc_no=role.code,
                action=ACTION_CREATE,
                reason=f"新建角色 {role.code}",
                changed_fields={"code": role.code, "permission_codes": payload.permission_codes},
            )
        return await self._one(role.id)

    async def patch_role(self, role_id: UUID, payload: RolePatch) -> RoleOut:
        role = await self._required(role_id)
        if payload.version != role.version:
            raise BusinessError(
                ErrorCode.OPTIMISTIC_LOCK_CONFLICT, f"角色已被他人修改（当前版本 {role.version}）"
            )
        changes: dict[str, Any] = {}
        for field in ("name", "description"):
            value = getattr(payload, field)
            if value is not None and getattr(role, field) != value:
                changes[field] = {"from": getattr(role, field), "to": value}
        if payload.data_scope is not None and role.data_scope != payload.data_scope.value:
            changes["data_scope"] = {"from": role.data_scope, "to": payload.data_scope.value}
        if not changes:
            return await self._one(role_id)

        async with unit_of_work(self.session):
            await self.session.execute(
                update(Role)
                .where(Role.id == role_id)
                .values(**changes, version=Role.version + 1, updated_by=self.ctx.user_id)
            )
            await write_document_log(
                self.session,
                self.ctx,
                doc_type=DOC_TYPE_ROLE,
                doc_id=role_id,
                doc_no=role.code,
                action=ACTION_UPDATE,
                reason=f"修改角色 {role.code}",
                changed_fields=changes,
            )
        return await self._one(role_id)

    async def disable_role(self, role_id: UUID, reason: str) -> RoleOut:
        """停用角色 —— 实现为**软删**（写 ``deleted_at``）。

        ⚠️ **为什么不用 `is_active` 标志**：`roles` 表**根本没有这一列**。
        T-AUTH-001 建表时给的机制就是软删（表有 `deleted_at`、无 `is_active`），
        配合 AGENTS §2.1「禁止物理删除，一律软删」。所以"停用"在这里就是软删。

        ⚠️ 软删要真的**收权**才成立：`core/permissions.py::load_permissions` 原本
        只 join 了 `role_permissions` 而没碰 `roles`，于是软删之后权限照样授予 ——
        界面显示"已停用"而实际一点没少。同批已补上 `deleted_at IS NULL` 过滤。

        ⚠️ **内置角色（``is_system=true``）禁止停用**（docs/07 §2.3）。它们是
        seed 建出来的系统角色，停用会让所有按角色授权的账号突然没有对应权限 ——
        而且没有任何界面能恢复。
        """
        role = await self._required(role_id)
        if role.is_system:
            raise BusinessError(
                ErrorCode.ILLEGAL_OPERATION, f"「{role.name}」是系统内置角色，不能停用"
            )
        await self._guard_role_not_last_admin(role)

        async with unit_of_work(self.session):
            await self.session.execute(
                update(Role)
                .where(Role.id == role_id)
                .values(
                    deleted_at=func.now(),
                    remark=reason[:500],
                    version=Role.version + 1,
                    updated_by=self.ctx.user_id,
                )
            )
            await write_document_log(
                self.session,
                self.ctx,
                doc_type=DOC_TYPE_ROLE,
                doc_id=role_id,
                doc_no=role.code,
                action=ACTION_UPDATE,
                reason=reason,
                changed_fields={"deleted_at": "已停用"},
            )
            # ⚠️ 必须在事务内 refresh：上面的 `update()` 把 `deleted_at` 等列在
            #    identity map 里标记为 expired，而 `role` 是**软删前**取出来的快照。
            #    直接读它的 `created_at` 会触发隐式懒加载 → AsyncSession 下抛
            #    `MissingGreenlet`（症状：停用角色接口 500，报错却是 greenlet）。
            await self.session.refresh(role)
        return await self._one_from_snapshot(role_id, role)

    async def replace_permissions(
        self, role_id: UUID, permission_codes_in: list[str], version: int
    ) -> RoleOut:
        """整体替换角色权限。

        ⚠️ 内置角色的权限**可以被调整**（超管要能收权），但 ``code`` / ``is_system``
        不可改（docs/07 §2.3）。调整会写 ``document_logs``（docs/07 §5「权限变更留痕」）。
        """
        role = await self._required(role_id)
        if version != role.version:
            raise BusinessError(
                ErrorCode.OPTIMISTIC_LOCK_CONFLICT, f"角色已被他人修改（当前版本 {role.version}）"
            )
        unknown = sorted(set(permission_codes_in) - set(permission_codes()))
        if unknown:
            raise BusinessError(ErrorCode.PARAM_INVALID, f"权限点不存在：{unknown}")
        before = (await self._permissions_by_role([role_id])).get(role_id, [])
        added = sorted(set(permission_codes_in) - set(before))
        removed = sorted(set(before) - set(permission_codes_in))
        if not added and not removed:
            return await self._one(role_id)

        permission_ids = await self._permission_ids(permission_codes_in)
        async with unit_of_work(self.session):
            await self._replace_permissions(
                role_id, permission_ids, reason=f"调整角色 {role.code} 的权限"
            )
            await self.session.execute(
                update(Role)
                .where(Role.id == role_id)
                .values(version=Role.version + 1, updated_by=self.ctx.user_id)
            )
            await write_document_log(
                self.session,
                self.ctx,
                doc_type=DOC_TYPE_ROLE,
                doc_id=role_id,
                doc_no=role.code,
                action=ACTION_UPDATE,
                reason=f"调整角色 {role.code} 的权限：+{len(added)} -{len(removed)}",
                changed_fields={"added": added, "removed": removed},
            )
        return await self._one(role_id)

    # ---------------------------------------------------------------- 内部

    async def _required(self, role_id: UUID) -> Role:
        # `populate_existing=True`：与用户侧同一个理由（写后读会撞 expired 属性）
        role = await self.session.get(Role, role_id, populate_existing=True)
        if role is None or role.deleted_at is not None:
            raise BusinessError(ErrorCode.PARAM_INVALID, f"角色 {role_id} 不存在")
        return role

    async def _one(self, role_id: UUID) -> RoleOut:
        role = await self._required(role_id)
        return await self._one_from_snapshot(role_id, role)

    async def _one_from_snapshot(self, role_id: UUID, snapshot: Role) -> RoleOut:
        """用**已取到的行**拼响应，而不是软删之后再查一次。

        不是微优化：软删之后 ``_required`` 会抛「角色不存在」，于是「停用角色」
        这个接口必然 500。所以调用方要保证 ``snapshot`` 已被 ``refresh`` 过
        （见 ``disable_role`` 里那行）。
        """
        perms = (await self._permissions_by_role([role_id])).get(role_id, [])
        counts = await self._user_counts([role_id])
        return self._out(snapshot, perms, counts.get(role_id, 0))

    async def _permissions_by_role(self, role_ids: list[UUID]) -> dict[UUID, list[str]]:
        if not role_ids:
            return {}
        rows = (
            await self.session.execute(
                select(RolePermission.role_id, Permission.code)
                .join(Permission, Permission.id == RolePermission.permission_id)
                .where(RolePermission.role_id.in_(role_ids))
                .order_by(Permission.code.asc())
            )
        ).all()
        result: dict[UUID, list[str]] = {}
        for role_id, code in rows:
            result.setdefault(role_id, []).append(code)
        return result

    async def _user_counts(self, role_ids: list[UUID]) -> dict[UUID, int]:
        if not role_ids:
            return {}
        rows = (
            await self.session.execute(
                select(UserRole.role_id, func.count())
                .where(UserRole.role_id.in_(role_ids))
                .group_by(UserRole.role_id)
            )
        ).all()
        return {role_id: int(count) for role_id, count in rows}

    async def _permission_ids(self, codes: list[str]) -> list[UUID]:
        if not codes:
            return []
        return list(
            (await self.session.execute(select(Permission.id).where(Permission.code.in_(codes))))
            .scalars()
            .all()
        )

    async def _replace_permissions(
        self, role_id: UUID, permission_ids: list[UUID], *, reason: str
    ) -> None:
        await self.session.execute(delete(RolePermission).where(RolePermission.role_id == role_id))
        for permission_id in permission_ids:
            self.session.add(RolePermission(role_id=role_id, permission_id=permission_id))
        await self.session.flush()

    async def _guard_role_not_last_admin(self, role: Role) -> None:
        """停用角色前，确认停用后仍有人能改权限。"""
        if role.code != "super_admin":
            return
        others = select(func.count()).select_from(
            select(UserRole.user_id)
            .join(Role, Role.id == UserRole.role_id)
            .where(Role.code == "super_admin", Role.deleted_at.is_(None))
            .subquery()
        )
        if int(await self.session.scalar(others) or 0) > 1:
            return
        admins = await self.session.scalar(
            select(func.count()).select_from(
                select(UserRole.user_id)
                .join(User, User.id == UserRole.user_id)
                .where(User.is_active.is_(True), User.deleted_at.is_(None))
                .subquery()
            )
        )
        if int(admins or 0) > 0:
            return
        raise BusinessError(
            ErrorCode.ILLEGAL_OPERATION,
            "停用系统管理员角色后没有任何启用中的账号，请先建好另一个管理员",
        )

    def _out(self, row: Role, permission_codes_in: list[str], user_count: int) -> RoleOut:
        return RoleOut(
            id=row.id,
            code=row.code,
            name=row.name,
            data_scope=DataScope(row.data_scope),
            is_system=row.is_system,
            description=row.description,
            permission_codes=permission_codes_in,
            user_count=user_count,
            version=row.version,
            created_at=row.created_at.isoformat(),
            updated_at=row.updated_at.isoformat(),
        )
