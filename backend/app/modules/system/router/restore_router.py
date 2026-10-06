"""内置库恢复路由（设计稿 §2.5，原 423-486 行）：2 个端点。

⚠️ **端点相对顺序必须保持源码顺序**：``/dicts/builtin-missing`` (GET) →
``/dicts/builtin-restores`` (POST)。
"""

from typing import Annotated

from fastapi import APIRouter, Body

from app.core.responses import ApiResponse, ok
from app.modules.system.restore import BuiltinRestoreService
from app.modules.system.schemas import (
    BuiltinMissingOut,
    BuiltinRestoreOut,
    BuiltinRestoreRequest,
)

from .deps import SYSTEM_TAGS, ContextDep, SessionDep, _require

router = APIRouter()


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
