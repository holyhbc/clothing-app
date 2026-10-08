"""打菲单的**读与草稿写**接口（``GET``/``POST`` 列表与详情、``PATCH`` 表头、
``PUT`` 明细全量替换、``available-outputs``、``logs``）。

⚠️ **端点顺序有讲究**：静态段必须声明在 ``/{order_id}`` **之前**（T-CUT-001c-1 的教训）——
FastAPI 按注册顺序匹配，而 ``/{order_id}`` 是 UUID 路径参数，它一旦先注册，
后续的顶层静态段（``/statistics`` ``/exports``，T-BUND-007b）就永远 422。
本文件里两个顶层静态段是**空路径**（``GET/POST /bundling-orders``），它们排在最前。

⚠️ 六个状态动作在 :mod:`.action_router`，不在本文件：审核要接 ``Idempotency-Key``，
和这批「无幂等」的读/草稿写不是一回事。
"""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.core.responses import ApiResponse, PageData, ok
from app.modules.base.schemas import DocumentLogOut
from app.modules.bundling.repository import BundlingOrderListQuery
from app.modules.bundling.schemas import (
    AvailableOutputOut,
    BundlingOrderCreateIn,
    BundlingOrderListOut,
    BundlingOrderOut,
    BundlingOrderPatchIn,
    PutLinesIn,
)

from .deps import (
    BUNDLING_TAGS,
    ContextDep,
    OrderId,
    SessionDep,
    _order_payload,
    _require,
    _service,
)

router = APIRouter(tags=BUNDLING_TAGS)


# ====================================================================== 建单与查询


@router.post(
    "/bundling-orders",
    response_model=ApiResponse[BundlingOrderOut],
    status_code=201,
    summary="新建打菲单（草稿态，表头 + 明细一次提交）",
    openapi_extra={"x-permission": "bundling:create"},
)
async def create_bundling_order(
    payload: BundlingOrderCreateIn,
    ctx: ContextDep,
    session: SessionDep,
) -> dict[str, object]:
    """建单。响应含**表头汇总**（``hands_total`` / ``output_qty`` / ``balance_qty``）。

    ⚠️ **汇总不接受传入**（03 §4）：入参里**连字段都没有**，传了会得到 ``10001`` ——
    而不是「悄悄被忽略」。**能被忽略的入参是最坏的一种**：前端以为设的值生效了。
    ⚠️ **每行必带 ``cutting_size_line_id``**（B26）：件数与手数的权威来源是裁剪尺码明细行，
    由服务端据此重算 ``planned_qty``。
    """
    _require(ctx, "bundling:create", "新建打菲单")
    return _order_payload(await _service(session).create(payload, ctx.user_id, ctx))


@router.get(
    "/bundling-orders",
    response_model=ApiResponse[PageData[BundlingOrderListOut]],
    summary="打菲单列表（省掉明细；默认按单据日期倒序）",
    openapi_extra={"x-permission": "bundling:read"},
)
async def list_bundling_orders(
    ctx: ContextDep,
    session: SessionDep,
    status: Annotated[
        str | None, Query(max_length=16, description="DRAFT/SUBMITTED/APPROVED/…")
    ] = None,
    style_no: Annotated[str | None, Query(max_length=32)] = None,
    operation_no: Annotated[str | None, Query(max_length=16)] = None,
    color_code: Annotated[str | None, Query(max_length=16)] = None,
    workshop_id: UUID | None = None,
    doc_date_from: Annotated[date | None, Query(description="起始日期（含）")] = None,
    doc_date_to: Annotated[date | None, Query(description="结束日期（含）")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=200)] = 20,
    sort_by: Annotated[str | None, Query(max_length=32)] = None,
    sort_order: Annotated[str, Query(pattern="^(asc|desc)$")] = "desc",
) -> dict[str, object]:
    """单据列表。

    ⚠️ ``workshop_id`` 传了也只是**再过滤一次**，**不能放大范围**（``07 §3.2`` 铁律 1）：
    车间主管传别的车间 id 依然查不到，由 service 里的 ``apply_data_scope`` 保证。
    """
    _require(ctx, "bundling:read", "查看打菲单")
    query = BundlingOrderListQuery(
        status=status,
        style_no=style_no,
        operation_no=operation_no,
        color_code=color_code,
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
        BundlingOrderListOut.model_validate(row).model_dump(mode="json") for row in rows
    ]
    return ok({"items": items, "total": total, "page": page, "page_size": size})


@router.get(
    "/bundling-orders/{order_id}",
    response_model=ApiResponse[BundlingOrderOut],
    summary="打菲单详情（表头 + 明细）",
    openapi_extra={"x-permission": "bundling:read"},
)
async def get_bundling_order(
    order_id: OrderId,
    ctx: ContextDep,
    session: SessionDep,
) -> dict[str, object]:
    """详情。数据范围在 service 的 ``assert_in_scope`` 里强制（``07 §3.2`` 铁律 2）。

    ⚠️ 详情按 ID 直查是越权的经典入口：列表有过滤，按 ID 直查没有。
    """
    _require(ctx, "bundling:read", "查看打菲单")
    return _order_payload(await _service(session).get(order_id, ctx))


# ====================================================================== 草稿改


@router.patch(
    "/bundling-orders/{order_id}",
    response_model=ApiResponse[BundlingOrderOut],
    summary="改表头（仅 DRAFT / REJECTED）",
    openapi_extra={"x-permission": "bundling:update"},
)
async def patch_bundling_order(
    order_id: OrderId,
    payload: BundlingOrderPatchIn,
    ctx: ContextDep,
    session: SessionDep,
) -> dict[str, object]:
    """只改表头（``doc_date`` / ``bundle_qty`` / ``remark``）。

    ⚠️ ``version`` **必传**（``docs/05 §4``）：缺失 → ``10001``，不匹配 → ``10003``。
    前端必须把读到的版本号带回来，否则两个人同时改会互相覆盖。
    ⚠️ 响应里的 ``version`` 是**新值** —— 紧接着的动作（提交/审核）要拿它继续传。
    """
    _require(ctx, "bundling:update", "修改打菲单")
    return _order_payload(await _service(session).patch(order_id, payload, ctx.user_id, ctx))


@router.put(
    "/bundling-orders/{order_id}/lines",
    response_model=ApiResponse[BundlingOrderOut],
    summary="明细全量替换（软删旧行 + 插新行 + 重算汇总）",
    openapi_extra={"x-permission": "bundling:update"},
)
async def put_bundling_order_lines(
    order_id: OrderId,
    payload: PutLinesIn,
    ctx: ContextDep,
    session: SessionDep,
) -> dict[str, object]:
    """明细**全量替换**（≤ 500 行，modules/03 §6）。

    ⚠️ 全量替换 = 软删不在 ``items`` 里的旧行。「删掉某一行」与「改某一行」因此走同一个
    接口 —— 前端只需把页面上现有的行原样带上再改要改的那几个。
    ⚠️ 每一行都要重算，包括没被改动的那几行（汇总不是增量）。
    """
    _require(ctx, "bundling:update", "修改打菲单")
    return _order_payload(await _service(session).put_lines(order_id, payload, ctx.user_id, ctx))


# ====================================================================== 录入辅助


@router.get(
    "/bundling-orders/{order_id}/available-outputs",
    response_model=ApiResponse[list[AvailableOutputOut]],
    summary="可打菲来源明细（裁剪侧手数 + 现在还能打多少）",
    openapi_extra={"x-permission": "bundling:read"},
)
async def list_available_outputs(
    order_id: OrderId,
    ctx: ContextDep,
    session: SessionDep,
    color_code: Annotated[
        str | None, Query(max_length=16, description="不传则用本单色码（ADR-0016 一码一色）")
    ] = None,
) -> dict[str, object]:
    """来源裁剪单该色下**每个尺码**的手数与可用量（打菲录入辅助，modules/03 §6）。

    ⚠️ **款号不接受传入**：一律取本单的 ``style_no`` —— 可用量按 (款号 + 色码 + 尺码)
    定位，让前端传款号就等于让它指定口径，而那个口径服务端并不认。
    ⚠️ 余量为 0 的尺码**也在列表里**（不是被过滤掉）：主管要看见「这个尺码一件都打不了」。
    """
    _require(ctx, "bundling:read", "查看可打菲来源")
    rows = await _service(session).available_outputs(order_id, ctx, color_code=color_code)
    return ok([AvailableOutputOut.model_validate(row).model_dump(mode="json") for row in rows])


@router.get(
    "/bundling-orders/{order_id}/logs",
    response_model=ApiResponse[PageData[DocumentLogOut]],
    summary="本单操作日志（变更历史抽屉，08 §1.2 R2 的读取侧）",
    openapi_extra={"x-permission": "bundling:read"},
)
async def list_bundling_order_logs(
    order_id: OrderId,
    ctx: ContextDep,
    session: SessionDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, object]:
    """本单的操作日志（谁、何时、从什么状态到什么状态、为什么）。

    ⚠️ **按 id 查日志同样要过数据范围** —— 日志是单据的一部分，看不到单据的人不该看到
    「这张单被谁驳回过」。范围由 service 的 ``list_logs``（复用详情那条）强制。
    """
    _require(ctx, "bundling:read", "查看打菲单日志")
    data = await _service(session).list_logs(order_id, ctx, page=page, size=size)
    return ok(data.model_dump(mode="json"))
