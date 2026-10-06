"""用户管理路由（设计稿 §2.5，原 84-280 行）：9 个端点。

⚠️ **端点相对顺序必须保持源码顺序**：``/users`` (GET) → ``/users/options`` →
``/users`` (POST) → ``/users/{user_id}`` → ...。``/users/options`` **必须**排在
``/users/{user_id}`` 之前，否则 ``options`` 会被当成 UUID 路径参数匹配。
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Path, Query

from app.core.responses import ApiResponse, PageData, ok, page_ok
from app.modules.system.schemas import (
    DisableUserRequest,
    EnableUserRequest,
    PasswordResetRequest,
    UserCreate,
    UserOptionOut,
    UserOut,
    UserPatch,
    UserRoleAssign,
)

from .deps import SYSTEM_TAGS, ContextDep, SessionDep, _require, _users

router = APIRouter()


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
