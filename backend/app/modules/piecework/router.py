"""计件 router（``docs/modules/04-计件.md`` §6）。

## 三条硬约束（与打菲 router 同一个理由，见 ``bundling/router/deps.py``）

1. **Router 里不查库、不做数据范围过滤**（03 §1.1 第 6 条）：全部经 service。
2. **响应走** :func:`app.core.responses.ok`（05 §3）：数量与金额在 ``data`` 里是字符串。
3. **``x-permission`` + 函数体里显式 ``_require``**（03 §2.1 第 8 条「前端隐藏不是安全」）。

## ``Idempotency-Key`` 为什么在这一层而不在 service

05 §5 的幂等是**请求级**的（同一 HTTP 请求的重试），它的存储是 Redis，生命周期跟着
请求走 —— 放进 service 就等于把 HTTP 的概念漏进业务层。而 service 里的第二层幂等
（``uq_piecework_logs_idem``）是**数据级**的，管的是「离线重传」与「重复扫码」。
两层必须都在，少哪层都会在某个场景下重复计数。
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.errors import BusinessError, ErrorCode
from app.core.idempotency import load_idempotent, mark_idempotent_failed, store_idempotent
from app.core.permissions import AuthContext, get_auth_context
from app.core.responses import ApiResponse, ok
from app.modules.piecework.events import publish_counted_event
from app.modules.piecework.schemas import CountPieceworkIn, CountPieceworkOut
from app.modules.piecework.service import PieceworkService

SessionDep = Annotated[AsyncSession, Depends(get_db)]
ContextDep = Annotated[AuthContext, Depends(get_auth_context)]

#: OpenAPI 标签（05 §9.5.2：``tags="计件"`` + 每个端点 ``summary``）。
#: 标成 ``list[str | Enum]`` 是为了与 base / cutting / bundling 的 router 同一类型
#: —— 标 ``list[str]`` 会因 list 的不变性让 ``tags=`` 报 arg-type。
PIECEWORK_TAGS: list[str | Enum] = ["计件"]

router = APIRouter(tags=PIECEWORK_TAGS)


def _require(ctx: AuthContext, permission: str, action: str) -> None:
    """显式权限校验（05 §6）。不用 ``Depends(require_permission(...))``：
    那个依赖的错误文案是通用的「无操作权限」，这里能给出**具体动作**。
    """
    if not ctx.has(permission):
        raise BusinessError(
            ErrorCode.PERMISSION_DENIED,
            f"你没有{action}的权限，请联系管理员开通",
            details={"required_permission": permission},
        )


@router.post(
    "/piecework/count",
    response_model=ApiResponse[CountPieceworkOut],
    summary="扫码计件（★ 一码一员工；不传 qty = 该手全件，传了 = 部分生产；支持 Idempotency-Key）",
    openapi_extra={"x-permission": "piecework:count", "x-idempotency-key": True},
)
async def count_piecework(
    payload: CountPieceworkIn,
    request: Request,
    ctx: ContextDep,
    session: SessionDep,
) -> dict[str, object]:
    """工位机扫码计件（``04-计件 §5.1`` ①~⑪）。

    ⚠️ **管理端带 ``employee_id``**：工位机是**组长替员工扫码**，请求体必须有操作员
    （``04-计件 §6.1``）。员工端那个端点**不接受** ``employee_id``（从 token 取），
    两者共用同一个 service 方法 —— 规则完全一致，入口形态不同。

    ⚠️ **不传 ``qty`` 就是这一手全件**（``bundles.bundle_qty``，BR-PW-26），
    传了才是部分生产（BR-PW-27，≤ 该手件数）。这里**没有**默认 1 —— 那是
    ADR-0016 之前的「扫码 +1 件」旧口径。

    ⚠️ **同一员工重试永远返回 200 + ``idempotent=true``**，不返回 ``32001``；
    ``32001`` 只给**别人**已计的码（ADR-0016 决策 3，BR-PW-16/24）。
    """
    _require(ctx, "piecework:count", "扫码计件")
    idempotent = await load_idempotent(request)
    if idempotent is not None and idempotent.cached is not None:
        # ⚠️ 取 ``cached["response"]``，**不是** ``cached`` 本身：缓存里存的是
        #   ``{"body_hash":…, "response":…}``，直接把整个缓存当响应体返回的话，
        #   FastAPI 会按 ApiResponse 校验 → ``data`` 变 null，客户端拿到
        #   「计件成功但没有数据」，而码已经被计过了。
        cached_response = idempotent.cached.get("response")
        if isinstance(cached_response, dict):
            return dict(cached_response)
    try:
        out = await PieceworkService(session).count_piecework(
            bundle_no=payload.bundle_no,
            employee_id=payload.employee_id,
            scanned_at=payload.scanned_at,
            qty=payload.qty,
            operator_id=ctx.user_id,
            ctx=ctx,
            station_id=payload.station_id,
            device_id=payload.device_id,
            idempotency_key=idempotent.key if idempotent is not None else None,
        )
        # ⑪ 事件广播（04 §5.1 ⑪）：**提交之后**、**只在真新增时**。
        #    幂等命中（`idempotent=true`）不重复发 —— 否则看板会把一次扫码显示两次。
        #    best-effort：Redis 挂掉不影响这次已经成功的计件（docs/01 §5）。
        if not out.idempotent:
            await publish_counted_event(session, out=out, employee_id=payload.employee_id)
        # ⚠️ ``model_dump(mode="json")`` 是必需的：``work_date`` / ``id`` 是
        #   date / UUID，Pydantic 默认按 python 对象 dump，Redis 缓存会存成不可 JSON
        #   序列化的东西 → 下一个带同键的请求读缓存时炸。
        response: dict[str, object] = ok(
            CountPieceworkOut.model_validate(out).model_dump(mode="json")
        )
        if idempotent is not None:
            await store_idempotent(idempotent, response)
        return response
    except Exception:
        if idempotent is not None:
            await mark_idempotent_failed(idempotent)
        raise


__all__ = ["PIECEWORK_TAGS", "router"]
