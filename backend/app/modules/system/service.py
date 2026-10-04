"""用户与角色管理服务层（docs/07 §2.3 内置角色、§5 审计与合规）。

三条贯穿全文件的硬规则：

1. **权限查库，不看 token**（docs/07 §1.1）。所以改权限**立刻生效**，不需重新登录 ——
   `get_auth_context` 每次请求都从库里重建 `AuthContext`。
2. **每次变更写 `document_logs`**（docs/07 §5「权限变更留痕」），日志与数据同事务。
3. **不做物理删除**（AGENTS §2.1；`erp_app` 被 REVOKE DELETE，docs/04 §6.2.1 /
   ADR-0025）。用户与角色都是 `is_active=false` + 软删。
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import Select, delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import DataScope
from app.common.permissions_registry import PERMISSIONS, permission_codes
from app.core.db import unit_of_work
from app.core.errors import BusinessError, ErrorCode
from app.core.permissions import AuthContext
from app.core.scope import apply_data_scope
from app.core.security import hash_password, validate_password_strength
from app.modules.auth.models import (
    AuthRefreshToken,
    Permission,
    Role,
    RolePermission,
    User,
    UserRole,
)
from app.modules.base.service import write_document_log
from app.modules.system.schemas import (
    PermissionGroupOut,
    PermissionOut,
    RoleCreate,
    RoleOut,
    RolePatch,
    UserCreate,
    UserOptionOut,
    UserOut,
    UserPatch,
)
from app.modules.system.scopes import scope_text

logger = logging.getLogger(__name__)

#: ``document_logs.doc_type``（docs/08 §1.1）
DOC_TYPE_USER = "User"
DOC_TYPE_ROLE = "Role"

ACTION_CREATE = "CREATE"
ACTION_UPDATE = "UPDATE"

#: 权限点 code → 中文名（registry 是单一来源；docs/07 §6 第 5 条要求前端常量同源）
PERMISSION_NAMES: dict[str, str] = {item.code: item.name for item in PERMISSIONS}

#: 模块 code → 中文名。docs/07 §2.2 的表格顺序即此顺序。
MODULE_NAMES: dict[str, str] = {
    "base": "基础资料",
    "cutting": "裁剪",
    "bundling": "打菲",
    "piecework": "计件",
    "payroll": "工资",
    "stock": "库存",
    "sales": "销售",
    "purchase": "采购",
    "finance": "财务",
    "system": "系统",
}

MAX_OFFSET = 10000


def _permission_module(code: str) -> str:
    """``base:rate_template:manage`` → ``base``。"""
    return code.split(":", 1)[0]


def permission_groups() -> list[PermissionGroupOut]:
    """按模块分组的权限点（角色表单的勾选树用）。

    ⚠️ 数据来自 **registry** 而不是 ``permissions`` 表 —— registry 是单一来源
    （docs/07 §2.2），而表里可能还留着已作废的码。表与 registry 不一致时
    ``cli/seed_baseline.py --check`` 会报出来。

    做成模块级函数而不是 service 方法：**它完全不碰数据库**（registry 在内存里），
    挂成方法会让人以为要用实例调，于是写出 ``__new__`` 那种绕路代码。
    """
    # ⚠️ **必须按 registry 的声明顺序遍历**，不能用 `permission_codes()` ——
    #    它返回 `frozenset`，迭代顺序由哈希决定，**每次请求都可能不同**。
    #    症状是角色表单里的权限树每次打开顺序都在变，用户找不到上次勾的那一项。
    grouped: dict[str, list[PermissionOut]] = {}
    for seed in PERMISSIONS:
        grouped.setdefault(_permission_module(seed.code), []).append(
            PermissionOut(code=seed.code, name=seed.name)
        )
    # ⚠️ 模块顺序也取自 registry 的声明顺序，**不按 MODULE_NAMES 的字典顺序**。
    #    实测踩过：registry 里 `bundling` 排在 `cutting` 前面，而 MODULE_NAMES
    #    按业务分组把 cutting 写在前面 —— 于是接口输出的顺序与 registry 不一致，
    #    前端拿 registry 生成的单点常量与后端返回的树对不上（TC 对不上就报错）。
    order = list(dict.fromkeys(_permission_module(seed.code) for seed in PERMISSIONS))
    return [
        PermissionGroupOut(
            module=module,
            module_name=MODULE_NAMES.get(module, module),
            permissions=grouped[module],
        )
        for module in order
    ]


class SystemUserService:
    """用户管理。数据范围在 ``apply_data_scope`` 层强制（docs/07 §3.2）。"""

    def __init__(self, session: AsyncSession, ctx: AuthContext) -> None:
        self.session = session
        self.ctx = ctx

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
        for field in ("name", "data_scope", "workshop_id", "group_no", "must_change_password", "remark"):
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

    async def _require_roles_exist(self, role_codes: list[str]) -> None:
        if not role_codes:
            return
        found = set(
            (
                await self.session.execute(
                    select(Role.code).where(
                        Role.code.in_(role_codes), Role.deleted_at.is_(None)
                    )
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
            (
                await self.session.execute(select(Role.id).where(Role.code.in_(role_codes)))
            )
            .scalars()
            .all()
        )

    async def _replace_user_roles(self, user_id: UUID, role_ids: list[UUID], *, reason: str) -> None:
        await self.session.execute(delete(UserRole).where(UserRole.user_id == user_id))
        for role_id in role_ids:
            self.session.add(UserRole(user_id=user_id, role_id=role_id))
        await self.session.flush()

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



    def _duplicate_user(self, exc: IntegrityError) -> BusinessError:
        text = str(exc.orig) if exc.orig is not None else str(exc)
        if "uq_users_employee_no" in text or "employee_no" in text:
            return BusinessError(ErrorCode.PARAM_INVALID, "该工号已存在，请换一个")
        return BusinessError(ErrorCode.PARAM_INVALID, "创建用户失败，请检查工号是否重复")

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
        return [
            self._out(row, perms.get(row.id, []), counts.get(row.id, 0)) for row in rows
        ]

    async def role_options(self, keyword: str | None, size: int, offset: int) -> list[dict[str, Any]]:
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
            (
                await self.session.execute(select(Permission.id).where(Permission.code.in_(codes)))
            )
            .scalars()
            .all()
        )

    async def _replace_permissions(
        self, role_id: UUID, permission_ids: list[UUID], *, reason: str
    ) -> None:
        await self.session.execute(
            delete(RolePermission).where(RolePermission.role_id == role_id)
        )
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
            select(func.count())
            .select_from(
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


__all__ = [
    "MODULE_NAMES",
    "PERMISSION_NAMES",
    "SystemRoleService",
    "SystemUserService",
    "logger",
    "permission_groups",
]
