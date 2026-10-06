"""工序单价路由（设计稿 §2.2，原 1200-1346 行）。

⚠️ **端点相对顺序必须保持源码顺序**：
    ``/operation-rates`` (GET) → ``/operation-rates/resolve`` → ``/operation-rates/exports``
    → ``/operation-rates`` (POST)。

    ``/operation-rates/resolve`` 与 ``/operation-rates/exports`` **必须**排在
    任何 ``/operation-rates/{x}`` 之前：FastAPI 按注册顺序匹配（与字典路由同一坑，
    见 :func:`_register_one` 的说明）。
"""

from datetime import UTC, date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from app.core.errors import BusinessError, ErrorCode
from app.core.excel import stream_xlsx
from app.core.numbering import business_today
from app.core.responses import ApiResponse, PageData, ok, page_ok
from app.modules.base.schemas import (
    OperationRateCreate,
    OperationRateOut,
    OperationRateSetOut,
    RateResolveOut,
)
from app.modules.base.service import RateQuery

from .deps import (
    EXPORT_GLOBAL_PERMISSION,
    RATE_EXPORT_COLUMNS,
    STYLE_TAGS,
    ContextDep,
    SessionDep,
    _rate_service,
    _require_base,
)

router = APIRouter()


@router.get(
    "/operation-rates",
    response_model=ApiResponse[PageData[OperationRateOut]],
    summary="工序单价区间列表（含历史区间 + 派生 is_current / rate_source）",
    openapi_extra={"x-permission": "base:read"},
    tags=STYLE_TAGS,
)
async def list_operation_rates(
    ctx: ContextDep,
    session: SessionDep,
    style_no: Annotated[
        str | None,
        Query(max_length=32, description="款号；不传 = 全部档位（含分类价 / 全厂统一价）"),
    ] = None,
    operation_no: Annotated[str | None, Query(max_length=16)] = None,
    product_category_id: UUID | None = None,
    effective_from: date | None = None,
    effective_to: date | None = None,
    sort_by: Annotated[str | None, Query(description="可排序字段")] = "effective_from",
    sort_order: Annotated[str, Query(pattern="^(asc|desc)$")] = "desc",
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=200)] = 20,
) -> dict[str, object]:
    """单价历史区间列表。⚠️ ``style_no`` 可选 —— 档位 2 / 3 的行它就是 NULL。"""
    _require_base(ctx, "base:read", "查看工序单价")
    items, total = await _rate_service(session, ctx).list_rates(
        RateQuery(
            style_no=style_no,
            operation_no=operation_no,
            product_category_id=product_category_id,
            effective_from=effective_from,
            effective_to=effective_to,
            sort_by=sort_by,
            sort_order=sort_order,
            page=page,
            size=size,
        )
    )
    return page_ok([item.model_dump(mode="json") for item in items], total, page, size)


# ⚠️ **必须注册在 ``/operation-rates`` 之后、任何 ``/operation-rates/{x}`` 之前**：
# FastAPI 按注册顺序匹配（与字典路由同一坑，见 :func:`_register_one` 的说明）。
@router.get(
    "/operation-rates/resolve",
    response_model=ApiResponse[RateResolveOut],
    summary="按 work_date 预演取价（三档优先，返回 rate_source；只读不写库）",
    openapi_extra={"x-permission": "base:read"},
    tags=STYLE_TAGS,
)
async def resolve_operation_rate(
    ctx: ContextDep,
    session: SessionDep,
    style_no: Annotated[str, Query(min_length=1, max_length=32, description="款号（必填）")],
    operation_no: Annotated[str, Query(min_length=1, max_length=16, description="工序号（必填）")],
    work_date: date | None = Query(default=None, description="计件日期；缺省 = 今天"),
) -> dict[str, object]:
    """取价预演。三档优先级见 ADR-0026 §2；未命中 → ``20004`` + ``details``。"""
    _require_base(ctx, "base:read", "查看工序单价")
    result = await _rate_service(session, ctx).resolve(
        style_no, operation_no, work_date or business_today()
    )
    return ok(result.model_dump(mode="json"))


@router.get(
    "/operation-rates/exports",
    response_class=StreamingResponse,
    summary="导出工序单价 xlsx（与列表同一套筛选）",
    openapi_extra={
        "x-permission": f"base:export 且 {EXPORT_GLOBAL_PERMISSION}",
    },
    tags=STYLE_TAGS,
)
async def export_operation_rates(
    ctx: ContextDep,
    session: SessionDep,
    style_no: Annotated[str | None, Query(max_length=32)] = None,
    operation_no: Annotated[str | None, Query(max_length=16)] = None,
    product_category_id: UUID | None = None,
    effective_from: date | None = None,
    effective_to: date | None = None,
    sort_by: Annotated[str | None, Query()] = "effective_from",
    sort_order: Annotated[str, Query(pattern="^(asc|desc)$")] = "desc",
) -> StreamingResponse:
    """导出单价历史区间。

    ⚠️ **必须与列表共用同一个 service 方法**（docs/07 §3.2 铁律 3）：另写一条导出
    路径的话，"列表看到的"与"导出的"会不一致，而那只有在对账时才发现。

    ⚠️ 需要**两个**权限点同时具备：``base:export`` + ``system:export:manage``。
    """
    for required in ("base:export", EXPORT_GLOBAL_PERMISSION):
        if not ctx.has(required):
            raise BusinessError(ErrorCode.PERMISSION_DENIED, f"无导出权限：缺少 {required}")
    rows = await _rate_service(session, ctx).export_rates(
        RateQuery(
            style_no=style_no,
            operation_no=operation_no,
            product_category_id=product_category_id,
            effective_from=effective_from,
            effective_to=effective_to,
            sort_by=sort_by,
            sort_order=sort_order,
        )
    )
    payload = [row.model_dump(mode="json") for row in rows]
    headers = {
        "Content-Disposition": (
            f'attachment; filename="operation-rates-'
            f'{datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")}.xlsx"'
        ),
        "X-Row-Count": str(len(payload)),
    }
    return StreamingResponse(
        stream_xlsx(RATE_EXPORT_COLUMNS, iter(payload)),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


@router.post(
    "/operation-rates",
    status_code=201,
    response_model=ApiResponse[OperationRateSetOut],
    summary="设价 / 调价（只追加：旧行只关 effective_to，禁 UPDATE unit_price）",
    openapi_extra={
        "x-permission": "piecework:rate:manage",
        "x-request-schema": OperationRateCreate.model_json_schema(),
    },
    tags=STYLE_TAGS,
)
async def create_operation_rate(
    payload: OperationRateCreate, ctx: ContextDep, session: SessionDep
) -> dict[str, object]:
    """设价或调价。

    - 区间重叠 → ``20005`` + ``details``（R18）
    - 调价未填 ``reason`` → ``10006``（R20）
    - 同一生效日已有行 → ``20002``（R11：只追加不修改）
    """
    _require_base(ctx, "piecework:rate:manage", "维护工序单价")
    result = await _rate_service(session, ctx).set_rate(payload)
    return ok(result.model_dump(mode="json"))
