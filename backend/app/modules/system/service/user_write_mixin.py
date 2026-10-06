"""用户写路径：建 / 改 / 停用 / 重置口令 / 分配角色（原 service.py 183-455、517-521）。

:class:`UserWriteMixin` 是用户侧**唯一的事务边界集合**；读路径在
:mod:`user_query_mixin`，最后管理员守卫在 :mod:`user_guard_mixin`。交叉调用用
``TYPE_CHECKING`` 前置声明（照抄 base ``style_child_mixin.py``），运行期不 import
兄弟 Mixin，避免环。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import DataScope
from app.core.db import unit_of_work
from app.core.errors import BusinessError, ErrorCode
from app.core.permissions import AuthContext
from app.core.security import hash_password, validate_password_strength
from app.modules.auth.models import AuthRefreshToken, Role, User, UserRole
from app.modules.base.service import write_document_log
from app.modules.system.schemas import UserCreate, UserOut, UserPatch

from .common import ACTION_CREATE, ACTION_UPDATE, DOC_TYPE_USER


class UserWriteMixin:
    """用户建 / 改 / 停用 / 重置口令 / 分配角色。"""

    session: AsyncSession
    ctx: AuthContext

    if TYPE_CHECKING:

        async def _role_codes(self, user_ids: list[UUID]) -> dict[UUID, list[str]]: ...

        async def get_user(self, user_id: UUID) -> UserOut: ...

        async def _guard_has_operator(self, *, target_id: UUID) -> None: ...

    async def create_user(self, payload: UserCreate) -> UserOut:
        validate_password_strength(payload.password)
        await self._require_roles_exist(payload.role_codes)
        role_ids = await self._role_ids_by_code(payload.role_codes)

        async with unit_of_work(self.session):
            user = User(
                employee_no=payload.employee_no.strip(),
                name=payload.name.strip(),
                password_hash=hash_password(payload.password),
                data_scope=payload.data_scope.value,
                workshop_id=payload.workshop_id,
                group_no=payload.group_no,
                is_active=True,
                # ⚠️ 默认**强制改密**：新建的账号其口令是管理员设的，
                # 用户必须自己换一遍才算真正持有。docs/07 §1.1 同口径。
                must_change_password=payload.must_change_password,
                remark=payload.remark,
                created_by=self.ctx.user_id,
                updated_by=self.ctx.user_id,
            )
            self.session.add(user)
            try:
                await self.session.flush()
            except IntegrityError as exc:
                await self.session.rollback()
                raise self._duplicate_user(exc) from exc
            await self._replace_user_roles(user.id, role_ids, reason=f"新建用户 {user.employee_no}")
            await write_document_log(
                self.session,
                self.ctx,
                doc_type=DOC_TYPE_USER,
                doc_id=user.id,
                doc_no=user.employee_no,
                action=ACTION_CREATE,
                reason=f"新建用户 {user.employee_no}",
                changed_fields={
                    "employee_no": user.employee_no,
                    "name": user.name,
                    "data_scope": user.data_scope,
                    "role_codes": payload.role_codes,
                },
            )
        return await self.get_user(user.id)

    async def patch_user(self, user_id: UUID, payload: UserPatch) -> UserOut:
        user = await self._required(user_id)
        if payload.version != user.version:
            raise BusinessError(
                ErrorCode.OPTIMISTIC_LOCK_CONFLICT,
                f"用户已被他人修改（当前版本 {user.version}，请刷新后重试）",
            )
        # ⚠️ **不允许改工号**：它被 document_logs.doc_no 引用，也是登录凭据。
        changes: dict[str, Any] = {}
        for field in (
            "name",
            "data_scope",
            "workshop_id",
            "group_no",
            "must_change_password",
            "remark",
        ):
            value = getattr(payload, field)
            if value is None:
                continue
            stored = value.value if isinstance(value, DataScope) else value
            if getattr(user, field) != stored:
                changes[field] = {"from": getattr(user, field), "to": stored}
        if not changes:
            return await self.get_user(user_id)

        async with unit_of_work(self.session):
            await self.session.execute(
                update(User)
                .where(User.id == user_id)
                .values(**changes, version=User.version + 1, updated_by=self.ctx.user_id)
            )
            await write_document_log(
                self.session,
                self.ctx,
                doc_type=DOC_TYPE_USER,
                doc_id=user_id,
                doc_no=user.employee_no,
                action=ACTION_UPDATE,
                reason=f"修改用户 {user.employee_no}",
                changed_fields=changes,
            )
        return await self.get_user(user_id)

    async def disable_user(self, user_id: UUID, reason: str, *, enable: bool) -> UserOut:
        """停用 / 启用用户。

        ⚠️ **停用最后一个管理员会被拒绝**（``_guard_last_admin``）。现场往往只有一两个
        能改权限的人，误停用会把系统锁死 —— 没有任何界面能解开。
        先例是 ``cli/seed_baseline.py::ensure_initial_admin``：它要求"至少存在一个
        超管账号"，这里把同一条不变量延伸到停用动作上。
        """
        user = await self._required(user_id)
        # ⚠️ 判据是「已经处于**目标**状态」而不是「与目标不同」。
        #    写成 `user.is_active is not enable` 时，停用一个启用中的账号会命中
        #    `True is not False` → 抛「已经是停用状态」，于是**停用功能从来没成功过**，
        #    而"启用一个已停用的"反而会报错。两个方向都反了。
        if user.is_active == enable:
            raise BusinessError(
                ErrorCode.ILLEGAL_OPERATION,
                f"账号已经是{'启用' if enable else '停用'}状态，无需重复操作",
            )
        if not enable:
            await self._guard_has_operator(target_id=user.id)

        async with unit_of_work(self.session):
            await self.session.execute(
                update(User)
                .where(User.id == user_id)
                .values(
                    is_active=enable,
                    remark=reason[:500],
                    version=User.version + 1,
                    updated_by=self.ctx.user_id,
                )
            )
            # 停用要**立即吊销该用户全部 refresh token** —— 否则他手里的 refresh
            # 还能换出新 access token（auth/service.py::refresh 会查 is_active，
            # 但吊销能让审计日志里留下"谁在什么时候停用了谁"）。
            await self._revoke_refresh_tokens(user_id)
            await write_document_log(
                self.session,
                self.ctx,
                doc_type=DOC_TYPE_USER,
                doc_id=user_id,
                doc_no=user.employee_no,
                action=ACTION_UPDATE,
                reason=reason,
                changed_fields={"is_active": enable},
            )
        return await self.get_user(user_id)

    async def reset_password(self, user_id: UUID, new_password: str, reason: str) -> None:
        """管理员重置口令。新口令由**管理员填写**（见 schema 的说明）。"""
        validate_password_strength(new_password)
        user = await self._required(user_id)

        async with unit_of_work(self.session):
            await self.session.execute(
                update(User)
                .where(User.id == user_id)
                .values(
                    password_hash=hash_password(new_password),
                    # ⚠️ 必须置 true：不这样用户拿初始口令就能一直用下去
                    must_change_password=True,
                    version=User.version + 1,
                    updated_by=self.ctx.user_id,
                )
            )
            await self._revoke_refresh_tokens(user_id)
            await write_document_log(
                self.session,
                self.ctx,
                doc_type=DOC_TYPE_USER,
                doc_id=user_id,
                doc_no=user.employee_no,
                action=ACTION_UPDATE,
                reason=reason,
                changed_fields={"password": "***", "must_change_password": True},
            )

    async def assign_roles(self, user_id: UUID, role_codes: list[str], version: int) -> UserOut:
        """整体替换用户的角色。"""
        user = await self._required(user_id)
        if version != user.version:
            raise BusinessError(
                ErrorCode.OPTIMISTIC_LOCK_CONFLICT, f"用户已被他人修改（当前版本 {user.version}）"
            )
        await self._require_roles_exist(role_codes)
        before = await self._role_codes([user_id])
        role_ids = await self._role_ids_by_code(role_codes)
        async with unit_of_work(self.session):
            await self._replace_user_roles(
                user_id, role_ids, reason=f"调整 {user.employee_no} 的角色"
            )
            await self.session.execute(
                update(User)
                .where(User.id == user_id)
                .values(version=User.version + 1, updated_by=self.ctx.user_id)
            )
            await write_document_log(
                self.session,
                self.ctx,
                doc_type=DOC_TYPE_USER,
                doc_id=user_id,
                doc_no=user.employee_no,
                action=ACTION_UPDATE,
                reason=f"调整 {user.employee_no} 的角色",
                changed_fields={"role_codes": {"from": before.get(user_id, []), "to": role_codes}},
            )
        return await self.get_user(user_id)

    # ---------------------------------------------------------------- 内部

    async def _revoke_refresh_tokens(self, user_id: UUID) -> None:
        """吊销该用户全部未吊销的 refresh token。

        ⚠️ 停用 / 重置口令后**必须**立刻吊销：不吊的话他手里的 refresh 还能换出
        新 access token，虽然 ``auth/service.py::refresh`` 会查 ``is_active`` 而拒绝，
        但"能不能进"和"审计日志里发生了什么"是两件事 —— 停用的那一刻起，
        这个账号不该再产生任何新的 token。
        """
        await self.session.execute(
            update(AuthRefreshToken)
            .where(AuthRefreshToken.user_id == user_id, AuthRefreshToken.revoked_at.is_(None))
            .values(revoked_at=func.now())
        )

    async def _required(self, user_id: UUID) -> User:
        # ⚠️ `populate_existing=True` 不能省。写操作走的是 `update(User).values(...)`，
        #    它会把被更新列在 identity map 里标记为 expired；紧接着 `session.get()`
        #    命中缓存返回那个对象，于是读 `created_at` / `version` 时触发**隐式懒加载**
        #    → AsyncSession 下抛 `MissingGreenlet`。
        #    base 模块用「写完 `_reload()`」解决（见 `DictService._reload`），
        #    这里改成读时强制刷新，一次性覆盖所有读路径。
        user = await self.session.get(User, user_id, populate_existing=True)
        if user is None or user.deleted_at is not None:
            raise BusinessError(ErrorCode.PARAM_INVALID, f"用户 {user_id} 不存在")
        return user

    async def _require_roles_exist(self, role_codes: list[str]) -> None:
        if not role_codes:
            return
        found = set(
            (
                await self.session.execute(
                    select(Role.code).where(Role.code.in_(role_codes), Role.deleted_at.is_(None))
                )
            )
            .scalars()
            .all()
        )
        missing = sorted(set(role_codes) - found)
        if missing:
            raise BusinessError(ErrorCode.PARAM_INVALID, f"角色不存在：{missing}")

    async def _role_ids_by_code(self, role_codes: list[str]) -> list[UUID]:
        if not role_codes:
            return []
        return list(
            (await self.session.execute(select(Role.id).where(Role.code.in_(role_codes))))
            .scalars()
            .all()
        )

    async def _replace_user_roles(
        self, user_id: UUID, role_ids: list[UUID], *, reason: str
    ) -> None:
        await self.session.execute(delete(UserRole).where(UserRole.user_id == user_id))
        for role_id in role_ids:
            self.session.add(UserRole(user_id=user_id, role_id=role_id))
        await self.session.flush()

    def _duplicate_user(self, exc: IntegrityError) -> BusinessError:
        text = str(exc.orig) if exc.orig is not None else str(exc)
        if "uq_users_employee_no" in text or "employee_no" in text:
            return BusinessError(ErrorCode.PARAM_INVALID, "该工号已存在，请换一个")
        return BusinessError(ErrorCode.PARAM_INVALID, "创建用户失败，请检查工号是否重复")
