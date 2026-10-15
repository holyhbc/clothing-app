"""计件**写**端点：红冲 / 补录 / 手工补录（``docs/modules/04-计件.md`` §6.1 中间两行）。

与 :mod:`.router`（扫码）分开：这两条是**纠错路径**（INV-2），权限点是
``piecework:reverse`` / ``piecework:manual``，与扫码的 ``piecework:count`` 不是一回事，
硬塞进一个文件只会让「扫码相关」的 review 被「红冲」的校验逻辑分心。

## 三条硬约束（与 :mod:`.router` 同款，不重复解释）

1. Router 里不查库、不做数据范围过滤（03 §1.1 第 6 条）。
2. 响应走 ``ok``（05 §3）。
3. 每个端点都带 ``x-permission`` 且函数体里显式 ``_require``（03 §2.1 第 8 条）。
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.permissions import AuthContext, get_auth_context
from app.core.responses import ApiResponse, ok
from app.modules.piecework.schemas import (
    PieceworkLogIn,
    PieceworkLogWriteOut,
    PieceworkReverseIn,
    ReverseOut,
)
from app.modules.piecework.service import PieceworkService

from .router import _require

SessionDep = Annotated[AsyncSession, Depends(get_db)]
ContextDep = Annotated[AuthContext, Depends(get_auth_context)]

PIECEWORK_WRITE_TAGS: list[str | Enum] = ["计件"]

router = APIRouter(tags=PIECEWORK_WRITE_TAGS)


@router.post(
    "/piecework-logs/{log_id}/reverse",
    response_model=ApiResponse[ReverseOut],
    summary="红冲流水（REVERSAL + 同事务清空 bundles.counted_*；幂等）",
    openapi_extra={"x-permission": "piecework:reverse"},
)
async def reverse_piecework_log(
    log_id: Annotated[UUID, Path(description="被红冲的原流水 id")],
    payload: PieceworkReverseIn,
    ctx: ContextDep,
    session: SessionDep,
) -> dict[str, object]:
    """§5.3 步骤 3。

    ⚠️ **``reason`` 必填**（schema 层已挡，service 层 `10002` 兜底）：红冲是纠错，
    不记为什么，月底对账时「谁红冲了、为什么」就无从查起。

    ⚠️ **幂等**：同 ``reversal_of_id`` 已有 ``REVERSAL`` → 返回既有红冲流水（§6.3），
    不重复生成。网络重试再点一次不会把同一笔红冲执行两遍。
    """
    _require(ctx, "piecework:reverse", "红冲计件流水")
    out = await PieceworkService(session).reverse_log(log_id, payload, ctx.user_id, ctx)
    return ok(ReverseOut.model_validate(out).model_dump(mode="json"))


@router.post(
    "/piecework-logs",
    response_model=ApiResponse[PieceworkLogWriteOut],
    summary="手工补录 / 返工补录（MANUAL 漏扫码 / SUPPLEMENT 红冲后重录；幂等）",
    openapi_extra={"x-permission": "piecework:manual"},
)
async def create_piecework_log(
    payload: PieceworkLogIn,
    ctx: ContextDep,
    session: SessionDep,
) -> dict[str, object]:
    """§5.3 步骤 4（SUPPLEMENT）+ §5.4 流程 D（MANUAL）。

    ⚠️ ``SUPPLEMENT`` 必须 ``reversal_of_id``；``MANUAL`` 不许带。两者 ``remark``
    必填（``10002`` 兜底）。``qty`` 默认整手、``≤ bundle_qty``（``32006``）。

    ⚠️ 幂等键：唯一键 ``(bundle_no, operation_no, employee_id, work_date, log_type)``
    命中 → 返回既有（§6.3）。离线补录重传不会重复计数。
    """
    _require(ctx, "piecework:manual", "补录计件流水")
    out = await PieceworkService(session).write_log(payload, ctx.user_id, ctx)
    return ok(PieceworkLogWriteOut.model_validate(out).model_dump(mode="json"))
