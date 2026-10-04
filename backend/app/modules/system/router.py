"""用户 / 角色 / 权限点管理接口（docs/07 §2、§5）。

## 权限声明

每个端点都带 ``x-permission`` 且在函数体里显式校验（docs/05 §6「每个接口必填」）。
``openapi_extra`` 只是**文档**，真正的判定在这里 —— 前端据此控制按钮显隐，
后端据此兜底（docs/03 §2.1 第 8 条「前端隐藏不是安全」）。

## 路径前缀 `/system`

而不是挂到 `/users`、`/roles` —— 后者是**全局资源**，会和将来的业务资源混淆；
`/system/*` 一眼看出是后台管理面，也给以后加 `/system/logs` 留了位置。
"""

from enum import Enum
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.errors import BusinessError, ErrorCode
from app.core.permissions import AuthContext, get_auth_context
from app.core.responses import ApiResponse, PageData, ok, page_ok
from app.modules.system.restore import BuiltinRestoreService
from app.modules.system.schemas import (
    BuiltinMissingOut,
    BuiltinRestoreOut,
    BuiltinRestoreRequest,
    DisableUserRequest,
    EnableUserRequest,
    PasswordResetRequest,
    PermissionGroupOut,
    ReplacePermissionsRequest,
    RoleCreate,
    RoleDisable,
    RoleOptionOut,
    RoleOut,
    RolePatch,
    UserCreate,
    UserOptionOut,
    UserOut,
    UserPatch,
    UserRoleAssign,
)
from app.modules.system.service import (
    SystemRoleService,
    SystemUserService,
    permission_groups,
)

router = APIRouter(prefix="/system", tags=["系统管理"])

SessionDep = Annotated[AsyncSession, Depends(get_db)]
ContextDep = Annotated[AuthContext, Depends(get_auth_context)]

# 与 base/router.py 的 STYLE_TAGS 同一个类型标注（list[str | Enum]）——
# 标成 list[str] 会因 list 的不变性而让每个 tags= 都报 arg-type。
SYSTEM_TAGS: list[str | Enum] = ["系统管理"]


def _require(ctx: AuthContext, permission: str, action: str) -> None:
    """显式权限校验（docs/05 §6）。

    不用 ``Depends(require_permission(...))``：那个依赖在 OpenAPI 里只体现为声明，
    而错误文案是通用的"无操作权限"。这里能给出**具体动作**，用户报错时知道该找谁。
    """
    if not ctx.has(permission):
        raise BusinessError(ErrorCode.PERMISSION_DENIED, f"你没有{action}的权限，请联系管理员开通")


def _users(session: AsyncSession, ctx: AuthContext) -> SystemUserService:
    return SystemUserService(session, ctx)


def _roles(session: AsyncSession, ctx: AuthContext) -> SystemRoleService:
    return SystemRoleService(session, ctx)


# ====================================================================== 用户


@router.get(
    "/users",
    response_model=ApiResponse[PageData[UserOut]],
    summary="用户列表（响应不含 password_hash 与 phone）",
    openapi_extra={"x-permission": "system:user:manage"},
    tags=SYSTEM_TAGS,
)
async def list_users(
    ctx: ContextDep,
    session: SessionDep,
    q: Annotated[str | None, Query(max_length=64, description="工号或姓名模糊搜索")] = None,
    workshop_id: UUID | None = None,
    is_active: Annotated[bool | None, Query(description="不传 = 全部")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=200)] = 20,
) -> dict[str, object]:
    """用户分页列表。数据范围在 service 层用 ``apply_data_scope`` 强制。

    ⚠️ ``workshop_id`` 传了也只是**再过滤一次**，**不能放大范围** ——
    车间主管传一个别的车间 id 依然查不到人（docs/07 §3.2 铁律 1）。
    """
    _require(ctx, "system:user:manage", "查看用户")
    items, total = await _users(session, ctx).list_users(
        keyword=q, workshop_id=workshop_id, is_active=is_active, page=page, size=size
    )
    return page_ok([item.model_dump(mode="json") for item in items], total, page, size)


@router.get(
    "/users/options",
    response_model=ApiResponse[list[UserOptionOut]],
    summary="用户候选（Combo 用，size ≤ 20）",
    openapi_extra={"x-permission": "system:user:manage"},
    tags=SYSTEM_TAGS,
)
async def list_user_options(
    ctx: ContextDep,
    session: SessionDep,
    q: Annotated[str | None, Query(max_length=64)] = None,
    size: Annotated[int, Query(ge=1, le=20)] = 20,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
) -> dict[str, object]:
    """用户候选。``label`` 是「工号 姓名」（docs/05 §9.5.2 禁止只显示编码）。"""
    _require(ctx, "system:user:manage", "查看用户")
    items = await _users(session, ctx).options(q, size, offset)
    return ok([item.model_dump(mode="json") for item in items])


@router.post(
    "/users",
    response_model=ApiResponse[UserOut],
    status_code=201,
    summary="新建用户",
    openapi_extra={"x-permission": "system:user:manage"},
    tags=SYSTEM_TAGS,
)
async def create_user(
    ctx: ContextDep,
    session: SessionDep,
    payload: Annotated[UserCreate, Body()],
) -> dict[str, object]:
    """新建用户。默认 ``must_change_password=true``。

    ⚠️ 工号重复返回 ``10001``（不是 409 也不是静默失败）—— 工号是登录凭据，
    重复会导致"改了别人的口令还能登进去"这类极难排查的问题。
    """
    _require(ctx, "system:user:manage", "新建用户")
    user = await _users(session, ctx).create_user(payload)
    return ok(user.model_dump(mode="json"))


@router.get(
    "/users/{user_id}",
    response_model=ApiResponse[UserOut],
    summary="用户详情",
    openapi_extra={"x-permission": "system:user:manage"},
    tags=SYSTEM_TAGS,
)
async def get_user(
    ctx: ContextDep,
    session: SessionDep,
    user_id: Annotated[UUID, Path()],
) -> dict[str, object]:
    _require(ctx, "system:user:manage", "查看用户")
    user = await _users(session, ctx).get_user(user_id)
    return ok(user.model_dump(mode="json"))


@router.patch(
    "/users/{user_id}",
    response_model=ApiResponse[UserOut],
    summary="局部更新用户（工号不可改）",
    openapi_extra={"x-permission": "system:user:manage"},
    tags=SYSTEM_TAGS,
)
async def patch_user(
    ctx: ContextDep,
    session: SessionDep,
    user_id: Annotated[UUID, Path()],
    payload: Annotated[UserPatch, Body()],
) -> dict[str, object]:
    """局部更新。⚠️ **工号不可改** —— 它被 ``document_logs.doc_no`` 引用，也是登录凭据。

    ``version`` 不匹配返回 ``10003``（docs/05 §2 乐观锁）。
    """
    _require(ctx, "system:user:manage", "修改用户")
    user = await _users(session, ctx).patch_user(user_id, payload)
    return ok(user.model_dump(mode="json"))


@router.put(
    "/users/{user_id}/roles",
    response_model=ApiResponse[UserOut],
    summary="整体替换用户的角色",
    openapi_extra={"x-permission": "system:user:manage"},
    tags=SYSTEM_TAGS,
)
async def assign_user_roles(
    ctx: ContextDep,
    session: SessionDep,
    user_id: Annotated[UUID, Path()],
    payload: Annotated[UserRoleAssign, Body()],
) -> dict[str, object]:
    """整体替换角色集合（不是增删）。

    ⚠️ 整体替换而不是 PATCH 追加：授权界面天然是"勾选哪些"的全量语义，
    增删式接口会让"取消勾选"这个动作无法表达。
    """
    _require(ctx, "system:user:manage", "分配角色")
    user = await _users(session, ctx).assign_roles(user_id, payload.role_codes, payload.version)
    return ok(user.model_dump(mode="json"))


@router.post(
    "/users/{user_id}/disables",
    response_model=ApiResponse[UserOut],
    summary="停用用户（必填原因）",
    openapi_extra={"x-permission": "system:user:manage"},
    tags=SYSTEM_TAGS,
)
async def disable_user(
    ctx: ContextDep,
    session: SessionDep,
    user_id: Annotated[UUID, Path()],
    payload: Annotated[DisableUserRequest, Body()],
) -> dict[str, object]:
    """停用账号：立即无法登录（``11004``）并吊销其全部 refresh token。

    ⚠️ **最后一个能改权限的账号不能停用**（``10008``）。现场往往只有一两个
    能改权限的人，误停用会把系统锁死，且没有任何界面能解开。
    """
    _require(ctx, "system:user:manage", "停用用户")
    user = await _users(session, ctx).disable_user(user_id, payload.reason, enable=False)
    return ok(user.model_dump(mode="json"))


@router.post(
    "/users/{user_id}/enables",
    response_model=ApiResponse[UserOut],
    summary="启用用户（必填原因）",
    openapi_extra={"x-permission": "system:user:manage"},
    tags=SYSTEM_TAGS,
)
async def enable_user(
    ctx: ContextDep,
    session: SessionDep,
    user_id: Annotated[UUID, Path()],
    payload: Annotated[EnableUserRequest, Body()],
) -> dict[str, object]:
    """解除停用。原因同样必填 —— 启用同样是敏感动作（谁把离职员工放回来了要能查）。"""
    _require(ctx, "system:user:manage", "启用用户")
    user = await _users(session, ctx).disable_user(user_id, payload.reason, enable=True)
    return ok(user.model_dump(mode="json"))


@router.post(
    "/users/{user_id}/password-resets",
    response_model=ApiResponse[None],
    summary="管理员重置口令（必填原因）",
    openapi_extra={"x-permission": "system:user:manage"},
    tags=SYSTEM_TAGS,
)
async def reset_user_password(
    ctx: ContextDep,
    session: SessionDep,
    user_id: Annotated[UUID, Path()],
    payload: Annotated[PasswordResetRequest, Body()],
) -> dict[str, object]:
    """重置口令，并把 ``must_change_password`` 置 true + 吊销其全部 refresh token。

    ⚠️ **新口令由管理员填写，不是应用生成**（口径见 schema 说明）：
    应用生成随机口令就得有送达通道，而本项目没有（``auth/sms/send-code`` 是 P2 未启用）；
    `cli/seed_baseline.py` 也是从环境变量取初始口令、**绝不生成弱口令**。
    """
    _require(ctx, "system:user:manage", "重置口令")
    await _users(session, ctx).reset_password(user_id, payload.new_password, payload.reason)
    return ok(None)


# ====================================================================== 角色


@router.get(
    "/roles",
    response_model=ApiResponse[list[RoleOut]],
    summary="角色列表（含权限点与已授予用户数）",
    openapi_extra={"x-permission": "system:role:manage"},
    tags=SYSTEM_TAGS,
)
async def list_roles(
    ctx: ContextDep,
    session: SessionDep,
    q: Annotated[str | None, Query(max_length=64)] = None,
) -> dict[str, object]:
    """角色列表。

    ⚠️ 角色**不进数据范围过滤**（见 ``core/scope.py::SCOPE_EXEMPT_TABLES``）：
    它是"谁能看哪些车间"的定义本身，再按车间过滤它，管理员就没法给自己配车间了。
    """
    _require(ctx, "system:role:manage", "查看角色")
    items = await _roles(session, ctx).list_roles(q)
    return ok([item.model_dump(mode="json") for item in items])


@router.get(
    "/roles/options",
    response_model=ApiResponse[list[RoleOptionOut]],
    summary="角色候选（Combo 用）",
    openapi_extra={"x-permission": "system:user:manage"},
    tags=SYSTEM_TAGS,
)
async def list_role_options(
    ctx: ContextDep,
    session: SessionDep,
    q: Annotated[str | None, Query(max_length=64)] = None,
    size: Annotated[int, Query(ge=1, le=20)] = 20,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
) -> dict[str, object]:
    """角色候选。

    ⚠️ 权限点写的是 ``system:user:manage`` 而不是 ``system:role:manage``：
    **建号时要选角色**，而大部分车间主管没有角色管理权。
    挂到 ``system:role:manage`` 的话，他们连新建用户都做不了。
    """
    _require(ctx, "system:user:manage", "选择角色")
    items = await _roles(session, ctx).role_options(q, size, offset)
    return ok(items)


@router.post(
    "/roles",
    response_model=ApiResponse[RoleOut],
    status_code=201,
    summary="新建角色",
    openapi_extra={"x-permission": "system:role:manage"},
    tags=SYSTEM_TAGS,
)
async def create_role(
    ctx: ContextDep,
    session: SessionDep,
    payload: Annotated[RoleCreate, Body()],
) -> dict[str, object]:
    """新建角色。⚠️ ``is_system`` 恒为 false —— 内置角色只能由 seed 建，不给接口留口子。"""
    _require(ctx, "system:role:manage", "新建角色")
    role = await _roles(session, ctx).create_role(payload)
    return ok(role.model_dump(mode="json"))


@router.patch(
    "/roles/{role_id}",
    response_model=ApiResponse[RoleOut],
    summary="局部更新角色（code 与 is_system 不可改）",
    openapi_extra={"x-permission": "system:role:manage"},
    tags=SYSTEM_TAGS,
)
async def patch_role(
    ctx: ContextDep,
    session: SessionDep,
    role_id: Annotated[UUID, Path()],
    payload: Annotated[RolePatch, Body()],
) -> dict[str, object]:
    """局部更新角色。⚠️ **``code` 不可改** —— 它被 ``user_roles`` 与审计日志引用。"""
    _require(ctx, "system:role:manage", "修改角色")
    role = await _roles(session, ctx).patch_role(role_id, payload)
    return ok(role.model_dump(mode="json"))


@router.put(
    "/roles/{role_id}/permissions",
    response_model=ApiResponse[RoleOut],
    summary="整体替换角色权限点",
    openapi_extra={"x-permission": "system:role:manage"},
    tags=SYSTEM_TAGS,
)
async def replace_role_permissions(
    ctx: ContextDep,
    session: SessionDep,
    role_id: Annotated[UUID, Path()],
    payload: Annotated[ReplacePermissionsRequest, Body()],
) -> dict[str, object]:
    """整体替换权限点集合。

    ⚠️ **内置角色的权限可以收放**（超管必须能收权），只是 ``code`` / ``is_system``
    不可改（docs/07 §2.3）。变更会写 ``document_logs``（docs/07 §5「权限变更留痕」）
    且**立刻生效** —— 权限每次请求查库，被授予的用户无需重新登录（docs/07 §1.1）。
    """
    _require(ctx, "system:role:manage", "修改角色权限")
    role = await _roles(session, ctx).replace_permissions(
        role_id, payload.permission_codes, payload.version
    )
    return ok(role.model_dump(mode="json"))


@router.post(
    "/roles/{role_id}/disables",
    response_model=ApiResponse[RoleOut],
    summary="停用角色（软删，必填原因）",
    openapi_extra={"x-permission": "system:role:manage"},
    tags=SYSTEM_TAGS,
)
async def disable_role(
    ctx: ContextDep,
    session: SessionDep,
    role_id: Annotated[UUID, Path()],
    payload: Annotated[RoleDisable, Body()],
) -> dict[str, object]:
    """停用角色（软删）。

    ⚠️ **内置角色禁止停用**（docs/07 §2.3）。⚠️ 软删会**真的收权** ——
    ``load_permissions`` 已过滤 ``roles.deleted_at IS NULL``。
    """
    _require(ctx, "system:role:manage", "停用角色")
    role = await _roles(session, ctx).disable_role(role_id, payload.reason)
    return ok(role.model_dump(mode="json"))


# ============================================================== 恢复内置库


@router.get(
    "/dicts/builtin-missing",
    response_model=ApiResponse[BuiltinMissingOut],
    summary="列出被真删且未恢复的内置项",
    openapi_extra={"x-permission": "system:config:manage"},
    tags=SYSTEM_TAGS,
)
async def list_builtin_missing(ctx: ContextDep, session: SessionDep) -> dict[str, object]:
    """缺失清单 —— 「恢复内置库」按钮的**前置提示**。

    ⚠️ 没这一步的话用户点了按钮才知道"原来少了 3 个颜色"，而恢复是不可逆的
    （会把用户自己改过的同编码行按 seed 值覆盖回去的场景需要人工确认）。
    """
    _require(ctx, "system:config:manage", "恢复内置库")
    missing = await BuiltinRestoreService(session, ctx).list_missing()
    return ok(
        BuiltinMissingOut(
            permissions=missing.permissions,
            roles=missing.roles,
            dicts=missing.dicts,
            total=missing.total(),
        ).model_dump(mode="json")
    )


@router.post(
    "/dicts/builtin-restores",
    response_model=ApiResponse[BuiltinRestoreOut],
    summary="恢复被真删的内置项（权限点 / 角色 / 字典）",
    openapi_extra={"x-permission": "system:config:manage"},
    tags=SYSTEM_TAGS,
)
async def restore_builtin(
    ctx: ContextDep,
    session: SessionDep,
    payload: Annotated[BuiltinRestoreRequest, Body()],
) -> dict[str, object]:
    """恢复内置数据（docs/04 §7.4 规则第 4 条 / ADR-0025 §决策 3）。

    ⚠️ **不是**重新跑 ``seed_baseline``：那个命令只补"键不存在"的行，
    被真删的（有墓碑）不补 —— 所以用户删掉的颜色不会自己长回来，但点本接口会。

    墓碑的 DELETE/RESTORE 日志由 ``restore_builtin.clear_tombstones`` 写，
    所以"谁在什么时候恢复了哪一批"可审计（docs/07 §5）。
    """
    _require(ctx, "system:config:manage", "恢复内置库")
    result = await BuiltinRestoreService(session, ctx).restore(
        permissions=payload.permissions, roles=payload.roles, dicts=payload.dicts
    )
    message = (
        f"已恢复：权限点 {result.restored_permissions} 个、"
        f"角色 {result.restored_roles} 个、权限绑定 {result.restored_role_bindings} 条、"
        f"字典 {result.restored_dicts} 条"
    )
    return ok(
        BuiltinRestoreOut(
            restored_permissions=result.restored_permissions,
            restored_roles=result.restored_roles,
            restored_role_bindings=result.restored_role_bindings,
            restored_dicts=result.restored_dicts,
            dict_detail=result.dict_detail,
            message=message,
        ).model_dump(mode="json")
    )


# ================================================================== 权限点


@router.get(
    "/permissions",
    response_model=ApiResponse[list[PermissionGroupOut]],
    summary="权限点（按模块分组，供角色勾选树）",
    openapi_extra={"x-permission": "system:role:manage"},
    tags=SYSTEM_TAGS,
)
async def list_permissions(ctx: ContextDep) -> dict[str, object]:
    """按模块分组的权限点。

    ⚠️ 数据来自 **registry**（``app/common/permissions_registry.py``）而不是
    ``permissions`` 表 —— registry 是单一来源（docs/07 §2.2），而表里可能还留着
    已作废的码。两边不一致时 ``cli/seed_baseline.py --check`` 会报出来。
    """
    _require(ctx, "system:role:manage", "查看权限点")
    groups = permission_groups()
    return ok([group.model_dump(mode="json") for group in groups])


__all__ = ["router"]
