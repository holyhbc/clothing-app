"""角色管理路由（设计稿 §2.5，原 286-417 行）：6 个端点。

⚠️ **端点相对顺序必须保持源码顺序**：``/roles`` (GET) → ``/roles/options`` →
``/roles`` (POST) → ``/roles/{role_id}`` → ...。``/roles/options`` **必须**排在
``/roles/{role_id}`` 之前，否则 ``options`` 会被当成 UUID 路径参数匹配。
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Path, Query

from app.core.responses import ApiResponse, ok
from app.modules.system.schemas import (
    ReplacePermissionsRequest,
    RoleCreate,
    RoleDisable,
    RoleOptionOut,
    RoleOut,
    RolePatch,
)

from .deps import SYSTEM_TAGS, ContextDep, SessionDep, _require, _roles

router = APIRouter()


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
