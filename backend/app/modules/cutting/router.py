"""裁剪单接口（docs/05 §6 + [modules/02-裁剪.md §6](../docs/modules/02-裁剪.md)）。

## 权限声明

每个端点都带 ``x-permission`` **且**在函数体里显式 ``_require``
（``docs/03 §2.1`` 第 8 条「前端隐藏不是安全」）。``openapi_extra`` 只是文档。

## 三条硬约束

1. **Router 里不许查库**（``docs/03 §1.1`` 第 6 条）—— 列表也必须经 service，
   否则数据范围过滤能被绕过（``INV-8``）。
2. **响应一律走** :func:`app.core.responses.ok` / :func:`page_ok`（``docs/05 §3``）——
   统一 ``{code, message, data}`` 包装，数量与金额在 ``data`` 里是**字符串**。
3. **状态机端点本卡不做**（``/submissions`` ``/approvals`` …）：service 里还没有
   （T-CUT-001b-3）。⚠️ **刻意不���「501 未实现」占位路由** ——
   占位的危害是前端会以为那个接口存在、OpenAPI 里列了它、E2E 里看到 501 而不是 404，
   而「这个功能还没做」的正确表达是**路由不存在**。
"""

from datetime import date
from enum import Enum
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.errors import BusinessError, ErrorCode
from app.core.permissions import AuthContext, get_auth_context
from app.core.responses import ApiResponse, PageData, ok, page_ok
from app.modules.cutting.repository import OrderListQuery
from app.modules.cutting.schemas import (
    CuttingOrderCreateIn,
    CuttingOrderListOut,
    CuttingOrderOut,
    CuttingOrderPatchIn,
    EntryModeSwitchIn,
    PutColorsIn,
    PutLinesIn,
    PutSizeLinesIn,
    SuggestLinesOut,
)
from app.modules.cutting.service import CuttingOrderService

router = APIRouter(prefix="/cutting-orders", tags=["裁剪"])

SessionDep = Annotated[AsyncSession, Depends(get_db)]
ContextDep = Annotated[AuthContext, Depends(get_auth_context)]

#: 与 base / system 的 router 用同一个类型标注（``list[str | Enum]``）——
#: 标成 ``list[str]`` 会因 list 的不变性而让 ``tags=`` 报 arg-type
CUTTING_TAGS: list[str | Enum] = ["裁剪"]

#: 单据 id 路径参数。``UUID`` 类型本身就让 FastAPI 返回 422 —— 不必自己解析，
#: 而自己解析的话错误信息会变成 500（``docs/05 §2`` 要求格式错误是 422）
OrderId = Annotated[UUID, Path(description="裁剪单 id")]


def _require(ctx: AuthContext, permission: str, action: str) -> None:
    """显式权限校验（docs/05 §6）。

    不用 ``Depends(require_permission(...))``：那个依赖在 OpenAPI 里只体现为声明，
    错误文案是通用的「无操作权限」。这里能给出**具体动作**，用户报错时知道该找谁。
    """
    if not ctx.has(permission):
        raise BusinessError(ErrorCode.PERMISSION_DENIED, f"你没有{action}的权限，请联系管理员开通")


def _service(session: AsyncSession) -> CuttingOrderService:
    return CuttingOrderService(session)


# ====================================================================== 建单与读


@router.post(
    "",
    response_model=ApiResponse[CuttingOrderOut],
    status_code=201,
    summary="新建裁剪单（草稿态，表头 + 三层明细一次提交）",
    openapi_extra={"x-permission": "cutting:create"},
    tags=CUTTING_TAGS,
)
async def create_cutting_order(
    payload: CuttingOrderCreateIn,
    ctx: ContextDep,
    session: SessionDep,
) -> dict[str, object]:
    """建单。响应含**三层结构**（`lines[].colors[].size_lines[]`）。

    ⚠️ **表头汇总不接受传入**（C6）：``fabric_qty`` / ``output_qty`` / ``cut_waste_qty`` /
    ``balance_qty`` / ``hands_total`` 在入参里**连字段都没有**，传了会得到 ``10001`` ——
    而不是「悄悄被忽略」。**能被忽略的入参是最坏的一种**：前端以为设的值生效了。
    """
    _require(ctx, "cutting:create", "新建裁剪单")
    order = await _service(session).create(payload, ctx.user_id, ctx)
    return ok(CuttingOrderOut.model_validate(order).model_dump(mode="json"))


@router.get(
    "/{order_id}",
    response_model=ApiResponse[CuttingOrderOut],
    summary="裁剪单详情（三层结构）",
    openapi_extra={"x-permission": "cutting:read"},
    tags=CUTTING_TAGS,
)
async def get_cutting_order(
    order_id: OrderId,
    ctx: ContextDep,
    session: SessionDep,
) -> dict[str, object]:
    """详情。数据范围在 service 的 ``assert_in_scope`` 里强制（``07 §3.2`` 铁律 2）。"""
    _require(ctx, "cutting:read", "查看裁剪单")
    order = await _service(session).get(order_id, ctx)
    return ok(CuttingOrderOut.model_validate(order).model_dump(mode="json"))


@router.get(
    "",
    response_model=ApiResponse[PageData[CuttingOrderListOut]],
    summary="裁剪单列表（省掉三层明细）",
    openapi_extra={"x-permission": "cutting:read"},
    tags=CUTTING_TAGS,
)
async def list_cutting_orders(
    ctx: ContextDep,
    session: SessionDep,
    status: Annotated[str | None, Query(max_length=16, description="DRAFT/SUBMITTED/…")] = None,
    style_no: Annotated[str | None, Query(max_length=32)] = None,
    workshop_id: UUID | None = None,
    doc_date_from: Annotated[date | None, Query(description="起始日期（含）")] = None,
    doc_date_to: Annotated[date | None, Query(description="结束日期（含）")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=200)] = 20,
    sort_by: Annotated[str | None, Query(max_length=32)] = None,
    sort_order: Annotated[str, Query(pattern="^(asc|desc)$")] = "desc",
) -> dict[str, object]:
    """单据列表。

    ⚠️ ``workshop_id`` 传了也只是**再过滤一次**，**不能放大范围** —— 车间主管传一个
    别的车间 id 依然查不到（``docs/07 §3.2`` 铁律 1）。这由 service 里的
    ``apply_data_scope`` 保证，不在本层。

    ⚠️ **默认排序**是「单据日期倒序」（modules/02 §6 指定的 ``-doc_date,doc_no``）。
    """
    _require(ctx, "cutting:read", "查看裁剪单")
    query = OrderListQuery(
        status=status,
        style_no=style_no,
        workshop_id=workshop_id,
        doc_date_from=doc_date_from,
        doc_date_to=doc_date_to,
        page=page,
        size=size,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    rows, total = await _service(session).list_orders(query, ctx)
    items: list[object] = [
        CuttingOrderListOut.model_validate(row).model_dump(mode="json") for row in rows
    ]
    return page_ok(items, total, page, size)


# ====================================================================== 改与删


@router.patch(
    "/{order_id}",
    response_model=ApiResponse[CuttingOrderOut],
    summary="改表头（仅 DRAFT / REJECTED）",
    openapi_extra={"x-permission": "cutting:update"},
    tags=CUTTING_TAGS,
)
async def patch_cutting_order(
    order_id: OrderId,
    payload: CuttingOrderPatchIn,
    ctx: ContextDep,
    session: SessionDep,
) -> dict[str, object]:
    """只改表头。三层明细要走三条 PUT（各自带锁、各自重算）。

    ⚠️ ``version`` **必传**（``docs/05 §4``）：缺失 → ``10001``，不匹配 → ``10003``。
    前端必须把读到的版本号带回来，否则两个人同时改会互相覆盖。
    """
    _require(ctx, "cutting:update", "修改裁剪单")
    order = await _service(session).patch(order_id, payload, ctx.user_id, ctx)
    return ok(CuttingOrderOut.model_validate(order).model_dump(mode="json"))


@router.delete(
    "/{order_id}",
    response_model=ApiResponse[dict[str, str]],
    summary="软删裁剪单（三层级联软删）",
    openapi_extra={"x-permission": "cutting:update"},
    tags=CUTTING_TAGS,
)
async def delete_cutting_order(
    order_id: OrderId,
    ctx: ContextDep,
    session: SessionDep,
    version: Annotated[int, Query(ge=1, description="表头 version（乐观锁）")],
) -> dict[str, object]:
    """软删，**三层级联**（modules/02 §6 DELETE 行）。

    ⚠️ **只有 ``DRAFT`` 能删**。``SUBMITTED`` 及以后是只读的（``08 §1.1``），
    已审核的必须走 ``reverse`` / ``cancel``（T-CUT-001b-3）。

    ⚠️ 应用账号对四张裁剪表**无 DELETE 权限**（``04 §6.2.1``），所以只能软删；
    而只软删表头是不够的 —— 子表若不软删，详情页仍会显示已删单据的尺码明细。
    """
    _require(ctx, "cutting:update", "删除裁剪单")
    await _service(session).delete(order_id, version, ctx.user_id, ctx)
    return ok({"doc_id": str(order_id), "deleted": "true"})


# ====================================================================== 三层明细


@router.put(
    "/{order_id}/lines",
    response_model=ApiResponse[CuttingOrderOut],
    summary="布批行全量替换（行 = 布批，★ 耗料记在行）",
    openapi_extra={"x-permission": "cutting:update"},
    tags=CUTTING_TAGS,
)
async def put_cutting_order_lines(
    order_id: OrderId,
    payload: PutLinesIn,
    ctx: ContextDep,
    session: SessionDep,
) -> dict[str, object]:
    """布批行**全量替换**（≤ 200 行）。

    ⚠️ 全量替换 = 软删不在 ``items`` 里的旧行（连同其颜色与尺码明细）。
    「删掉某一行」与「改某一行」因此走同一个接口 —— 前端只需把页面上现有的行
    原样带上再改要改的那几个。

    ⚠️ 缸号 / 匹号 / 物料 / 供应商**不接受传入**，一律从 ``stock_id`` 反查
    （ADR-0022 级联选料）—— 所以「缸号与匹号对不上」这类错误**结构上不可能发生**。
    """
    _require(ctx, "cutting:update", "修改裁剪单")
    order = await _service(session).put_lines(order_id, payload, ctx.user_id, ctx)
    return ok(CuttingOrderOut.model_validate(order).model_dump(mode="json"))


@router.put(
    "/{order_id}/lines/{line_id}/colors",
    response_model=ApiResponse[CuttingOrderOut],
    summary="行内颜色全量替换（一床可多个颜色，ADR-0017）",
    openapi_extra={"x-permission": "cutting:update"},
    tags=CUTTING_TAGS,
)
async def put_line_colors(
    order_id: OrderId,
    line_id: Annotated[UUID, Path(description="布批行 id")],
    payload: PutColorsIn,
    ctx: ContextDep,
    session: SessionDep,
) -> dict[str, object]:
    """行内颜色**全量替换**（≤ 10 色）。

    ⚠️ ``UNIQUE (line_id, color_code)``（部分唯一索引）—— 同一匹布上同一颜色只能一行，
    但**一行可以 N 个颜色**（C32，一床多色省布 5~10%）。
    """
    _require(ctx, "cutting:update", "修改裁剪单")
    order = await _service(session).put_colors(order_id, line_id, payload, ctx.user_id, ctx)
    return ok(CuttingOrderOut.model_validate(order).model_dump(mode="json"))


@router.put(
    "/{order_id}/size-lines",
    response_model=ApiResponse[CuttingOrderOut],
    summary="尺码明细全量保存（出数的权威来源）",
    openapi_extra={"x-permission": "cutting:update"},
    tags=CUTTING_TAGS,
)
async def put_size_lines(
    order_id: OrderId,
    payload: PutSizeLinesIn,
    ctx: ContextDep,
    session: SessionDep,
) -> dict[str, object]:
    """尺码明细**全量替换**（≤ 50 行 / 颜色）。

    ⚠️ 定位靠 ``line_color_id``（不是 ``line_id + color_code``）：颜色是**全量替换**的，
    本次请求里的颜色行是**新建**的，用 ``color_code`` 定位会指向刚被软删的旧行。

    ⚠️ ``size_line_no`` **省略时由服务端分配**（该颜色现存最大行号 + 1）——
    「先 select 后插」在并发下会发两次号（modules/02 §7）。
    """
    _require(ctx, "cutting:update", "修改裁剪单")
    order = await _service(session).put_size_lines(order_id, payload, ctx.user_id, ctx)
    return ok(CuttingOrderOut.model_validate(order).model_dump(mode="json"))


# ====================================================================== 比例与录入模式


@router.get(
    "/{order_id}/suggest-lines",
    response_model=ApiResponse[SuggestLinesOut],
    summary="按尺码比例带出手数建议（并写比例快照）",
    openapi_extra={"x-permission": "cutting:read"},
    tags=CUTTING_TAGS,
)
async def suggest_size_lines(
    order_id: OrderId,
    ctx: ContextDep,
    session: SessionDep,
    style_no: Annotated[str, Query(min_length=1, max_length=32, description="款号（必填）")],
    color_code: Annotated[str, Query(min_length=1, max_length=32, description="色码（必填）")],
) -> dict[str, object]:
    """按 ``style_color_size_ratios`` 带出手数建议（模式 A / C18）。

    C19 三条口径：
    - 该 ``(style_no, color_code)`` **完全没有**比例 → ``20006``
    - **部分**尺码缺配 → **不拦**，返回 ``missing_size_codes`` 仅提示
    - 比例里出现款号**未定义**的尺码 → ``20007``（主数据脏数据）

    ⚠️ 比例快照在**同一事务内**落库（``modules/02 §7``）：主数据随时可能被改，
    隔一个请求再快照，拍下来的就可能是**另一份**比例 —— 而快照的全部意义就是
    「事后能回答当时为什么这么裁」（C29）。
    """
    _require(ctx, "cutting:read", "查看裁剪单")
    out = await _service(session).suggest_lines(order_id, style_no, color_code, ctx.user_id, ctx)
    return ok(out.model_dump(mode="json"))


@router.post(
    "/{order_id}/entry-mode",
    response_model=ApiResponse[dict[str, object]],
    summary="切换颜色级录入模式（C26 / C27）",
    openapi_extra={"x-permission": "cutting:update"},
    tags=CUTTING_TAGS,
)
async def switch_entry_mode(
    order_id: OrderId,
    payload: EntryModeSwitchIn,
    ctx: ContextDep,
    session: SessionDep,
) -> dict[str, object]:
    """切换**颜色级**录入模式。

    ⚠️ 从 ``MASTER`` 切走会**清掉该颜色现有的尺码明细** —— 所以 ``confirm`` 必须为
    ``true``，前端必须弹二次确认并明示「已有 N 行手数将被清空」。服务端不接受
    ``false``：那个「用户可能没看见弹窗」的场景，代价是精心填的 N 行手数被静默清空。
    """
    _require(ctx, "cutting:update", "修改裁剪单")
    color = await _service(session).switch_entry_mode(order_id, payload, ctx.user_id, ctx)
    return ok(
        {
            "line_color_id": str(color.id),
            "line_id": str(color.line_id),
            "color_code": color.color_code,
            "entry_mode": color.entry_mode.value,
        }
    )


__all__ = ["router"]
