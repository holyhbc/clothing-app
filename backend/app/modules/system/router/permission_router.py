"""权限点路由（设计稿 §2.5，原 492-508 行）：1 个端点。"""

from fastapi import APIRouter

from app.core.responses import ApiResponse, ok
from app.modules.system.schemas import PermissionGroupOut
from app.modules.system.service import permission_groups

from .deps import SYSTEM_TAGS, ContextDep, _require

router = APIRouter()


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
