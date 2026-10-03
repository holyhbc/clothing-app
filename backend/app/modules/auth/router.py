"""认证接口（docs/05 §6 的接口清单 + docs/07 §1）。

五个端点与它们的权限声明
========================

===================  ==========  ==========================================================
端点                 鉴权        权限声明
===================  ==========  ==========================================================
POST /auth/login     公开        无（docs/07 §2.2 未为登录登记权限点）
POST /auth/refresh   公开        无（凭 refresh token 本身即授权）
GET  /auth/me        登录态      无 —— **任何登录用户都要能查自己**，加权限点会让
                                没角色的人拿不到自己的账号信息而卡死
POST /auth/logout    登录态      无（同上）
PUT  /auth/password  登录态      无 —— 改自己的密码不该由管理员权限把守；
                                真正的安全性来自"必须提供原口令"
===================  ==========  ==========================================================

⚠️ 上面 5 个端点**没有一个**挂了 :func:`app.core.permissions.require_permission`，
而 docs/05 §6 要求"每个接口必填权限声明"。这不是遗漏：docs/07 §2.2 的 122 个权限点
里确实没有 auth 模块的权限点，而"登录态自服务"类接口挂权限点在业务上说不通。
已登记为 docs/12 §5 的 **L-026**，等规范补齐 auth 权限点后再挂。

⚠️ **范围过滤不在本文件**：本模块是账号级接口，没有车间维度。业务模块的数据范围
过滤一律在 service 层用 ``apply_data_scope`` 强制（docs/07 §3.2 铁律 1）。

⚠️ 本模块**不做 DTO 转换的兜底**：Schema 用 ``from_attributes`` 直接从 ORM 读，
但 ``password_hash`` 之类字段在 Schema 里根本没有声明 —— 新增字段时若忘了排除，
会静默泄露。这是刻意选择"白名单 Schema"而非"黑名单排除"。
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import BusinessError, ErrorCode
from app.core.idempotency import load_idempotent, store_idempotent
from app.core.permissions import AuthContext, get_auth_context
from app.core.responses import ApiResponse, ok
from app.core.scope import visible_workshops
from app.modules.auth.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    LogoutResponse,
    MeResponse,
    RefreshResponse,
    RoleBrief,
    UserBrief,
)
from app.modules.auth.service import AuthService

logger = logging.getLogger("app.auth.router")

router = APIRouter(prefix="/auth", tags=["认证"])

#: refresh token 的 Cookie 名
REFRESH_COOKIE = "refresh_token"

SessionDep = Annotated[AsyncSession, Depends(get_db)]
ContextDep = Annotated[AuthContext, Depends(get_auth_context)]


def _client_ip(request: Request) -> str | None:
    """取客户端 IP。

    只取 ``request.client.host``，**不信任** ``X-Forwarded-For``：该头由客户端
    可伪造，写进 ``auth_login_logs.ip`` 会让审计日志失去价值。要在逆向后取真实 IP，
    那是部署层的事（docs/11 §6 nginx real_ip 配置）。
    """
    client = request.client
    return client.host if client else None


def set_refresh_cookie(response: Response, token: str, max_age: int) -> None:
    """写 HttpOnly Cookie。

    ``samesite="lax"``：员工端与 PC 端同站，lax 足够挡住跨站表单发起的请求，
    又不会像 ``strict`` 那样从外部链接跳转进来时丢 Cookie。
    """
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=token,
        max_age=max_age,
        httponly=True,
        secure=get_settings().app_env == "production",
        samesite="lax",
        path="/api/v1/auth",
    )


def clear_refresh_cookie(response: Response) -> None:
    """删 Cookie。``path`` 必须与写入时一致，否则删不掉。"""
    response.delete_cookie(key=REFRESH_COOKIE, path="/api/v1/auth")


@router.post(
    "/login",
    response_model=ApiResponse[LoginResponse],
    summary="工号口令登录（PC 端 / 员工端通用）",
    description=(
        "成功返回 access token，refresh token 走 HttpOnly Cookie。"
        "连续 5 次失败锁定 15 分钟（`10004`）。工号不存在与口令错误返回**同一个**"
        "错误码 `11003`，防止枚举工号。带 `Idempotency-Key` 时同键重试返回首次结果。"
    ),
    openapi_extra={"x-permission": None, "x-idempotency-key": True},
)
async def login(
    request: Request,
    response: Response,
    payload: LoginRequest,
    session: SessionDep,
) -> dict[str, object]:
    """登录。"""
    idem = await load_idempotent(request)
    if idem is not None and idem.cached is not None:
        # 同键同体重试：原样返回首次结果，**这不是错误**（docs/05 §5）
        cached_response = idem.cached.get("response")
        if isinstance(cached_response, dict):
            return cached_response

    outcome = await AuthService(session).authenticate(
        employee_no=payload.employee_no,
        password=payload.password,
        channel=payload.channel,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        device_id=payload.device_id,
    )
    set_refresh_cookie(response, outcome.tokens.refresh_token, max_age=outcome.tokens.expires_in)
    logger.info(
        "用户登录成功",
        extra={
            "user_id": str(outcome.user.id),
            "employee_no": outcome.user.employee_no,
            "channel": payload.channel.value,
        },
    )
    envelope = ok(
        LoginResponse(
            access_token=outcome.tokens.access_token,
            expires_in=outcome.tokens.expires_in,
            refresh_expires_at=outcome.tokens.refresh_expires_at,
            user=UserBrief.model_validate(outcome.user),
        ).model_dump(mode="json")
    )
    if idem is not None:
        await store_idempotent(idem, envelope)
    return envelope


@router.post(
    "/refresh",
    response_model=ApiResponse[RefreshResponse],
    summary="用 refresh token 换新 access token（并轮换 refresh token）",
    description="refresh token 走 HttpOnly Cookie 传入。**每次刷新都会轮换**，"
    "旧 token 立即失效；重放旧 token 返回 `11002`。",
    openapi_extra={"x-permission": None},
)
async def refresh(
    response: Response,
    session: SessionDep,
    refresh_token: Annotated[str | None, Cookie(alias=REFRESH_COOKIE)] = None,
) -> dict[str, object]:
    """刷新令牌。"""
    if not refresh_token:
        raise BusinessError(ErrorCode.UNAUTHORIZED, "登录已过期，请重新登录")

    tokens = await AuthService(session).refresh(refresh_token)
    set_refresh_cookie(response, tokens.refresh_token, max_age=tokens.expires_in)
    return ok(
        RefreshResponse(
            access_token=tokens.access_token,
            expires_in=tokens.expires_in,
            refresh_expires_at=tokens.refresh_expires_at,
        ).model_dump(mode="json")
    )


@router.post(
    "/logout",
    response_model=ApiResponse[LogoutResponse],
    summary="登出（吊销该用户全部 refresh token）",
    openapi_extra={"x-permission": None},
)
async def logout(response: Response, session: SessionDep, ctx: ContextDep) -> dict[str, object]:
    """登出。

    全部吊销而不是只吊当前这一个：用户点"退出登录"的语义是"把这个人踢下线"。
    多设备同时登录时只踢一个，用户会认为登出没生效。
    """
    revoked = await AuthService(session).logout(ctx.user_id)
    clear_refresh_cookie(response)
    logger.info("用户登出", extra={"user_id": str(ctx.user_id), "revoked": revoked})
    return ok({"revoked_tokens": revoked})


@router.get(
    "/me",
    response_model=ApiResponse[MeResponse],
    summary="当前登录用户的自描述（角色 / 权限 / 数据范围）",
    description="前端据此控制菜单与按钮显隐；**但服务端每个接口仍独立校验权限**"
    "（docs/07 §1.1：前端隐藏按钮不是安全边界）。",
    openapi_extra={"x-permission": None},
)
async def me(session: SessionDep, ctx: ContextDep) -> dict[str, object]:
    """返回当前用户的角色、权限点与数据范围。

    权限点来自 :func:`get_auth_context`（已查库）；角色列表要单独查。
    """
    service = AuthService(session)
    user = await service.get_active_user(ctx.user_id)
    roles = await service.get_roles(ctx.user_id)
    return ok(
        MeResponse(
            user=UserBrief.model_validate(user),
            roles=[RoleBrief.model_validate(role) for role in roles],
            permissions=sorted(ctx.permissions),
            data_scope=ctx.data_scope,
            allowed_workshop_ids=sorted(str(item) for item in visible_workshops(ctx)),
        ).model_dump(mode="json")
    )


@router.put(
    "/password",
    response_model=ApiResponse[None],
    summary="修改本人口令",
    description="改密后吊销该用户全部 refresh token（强制重新登录）。"
    "若账号带 `must_change_password` 标记，这里就是唯一的解除入口。",
    openapi_extra={"x-permission": None},
)
async def change_password(
    session: SessionDep,
    ctx: ContextDep,
    payload: ChangePasswordRequest,
) -> dict[str, object]:
    """修改口令。"""
    await AuthService(session).change_password(
        user_id=ctx.user_id,
        old_password=payload.old_password,
        new_password=payload.new_password,
    )
    return ok()


@router.post(
    "/sms/send-code",
    response_model=ApiResponse[None],
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
    summary="发送短信验证码（P2 员工端登录，当前未启用）",
    openapi_extra={"x-permission": None},
)
async def send_sms_code() -> dict[str, object]:
    """占位端点：明确告知未实现而不是静默 404（docs/05 §1）。"""
    raise BusinessError(
        ErrorCode.ILLEGAL_OPERATION,
        "短信登录通道尚未启用，计划在 P2 交付；当前请用 PC 端工号口令登录",
    )


__all__ = ["REFRESH_COOKIE", "clear_refresh_cookie", "router", "set_refresh_cookie"]
