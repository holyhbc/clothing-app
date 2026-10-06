"""system router 的共享依赖、权限助手与 Service 构造（设计稿 §2.5，原 53-78 行）。

本模块只放**被多个子 router 共用**的依赖与常量，不含任何端点，避免
``user_router`` / ``role_router`` / ``restore_router`` / ``permission_router``
之间互相 import 形成环。分层：``deps`` ← 各内容 router ← ``__init__``。
"""

from enum import Enum
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.errors import BusinessError, ErrorCode
from app.core.permissions import AuthContext, get_auth_context
from app.modules.system.service import SystemRoleService, SystemUserService

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
