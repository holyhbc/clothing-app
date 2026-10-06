"""款号主表路由（设计稿 §2.2，原 763-999 行）。

⚠️ **端点相对顺序必须保持源码顺序**：
    ``/styles`` 列表 → ``/styles/exports`` → ``/styles/options`` → ``POST /styles``
    → ``/styles/suggested-no`` → ``/styles/{style_no}`` (GET) → ``.../disables``
    → ``/styles/{style_no}`` (PATCH)。

    其中 ``/styles/suggested-no`` **必须**排在 ``/styles/{style_no}`` 之前：FastAPI
    按注册顺序匹配，排在后面会被当成「款号叫 suggested-no 的那一行」→ 404。
"""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query
from fastapi.responses import StreamingResponse

from app.core.errors import BusinessError, ErrorCode
from app.core.excel import stream_xlsx
from app.core.responses import ApiResponse, PageData, ok, page_ok
from app.modules.base.schemas import (
    OptionOut,
    StyleCreate,
    StyleDetailOut,
    StyleDisableIn,
    StyleListOut,
    StyleOut,
    StylePatch,
    SuggestedStyleNoOut,
)
from app.modules.base.service import StyleQuery

from .deps import (
    EXPORT_GLOBAL_PERMISSION,
    STYLE_EXPORT_COLUMNS,
    STYLE_TAGS,
    ContextDep,
    SessionDep,
    _require_base,
    _style_service,
)

router = APIRouter()


@router.get(
    "/styles",
    response_model=ApiResponse[PageData[StyleListOut]],
    summary="款号列表（跟单只查本人款号）",
    openapi_extra={"x-permission": "base:read"},
    tags=STYLE_TAGS,
)
async def list_styles(
    ctx: ContextDep,
    session: SessionDep,
    q: Annotated[str | None, Query(max_length=64, description="款号或款名模糊搜索")] = None,
    is_active: bool | None = None,
    customer_id: UUID | None = None,
    category_id: UUID | None = None,
    merchandiser_id: UUID | None = None,
    sort_by: Annotated[str | None, Query(description="可排序字段")] = None,
    sort_order: Annotated[str, Query(pattern="^(asc|desc)$")] = "asc",
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=200)] = 20,
) -> dict[str, object]:
    """款号分页列表。数据范围在 service 层过滤（跟单 SELF → 本人款号）。"""
    _require_base(ctx, "base:read", "查看款号")
    items, total = await _style_service(session, ctx).list_styles(
        StyleQuery(
            q=q,
            is_active=is_active,
            customer_id=customer_id,
            category_id=category_id,
            merchandiser_id=merchandiser_id,
            sort_by=sort_by,
            sort_order=sort_order,
            page=page,
            size=size,
        )
    )
    return page_ok([item.model_dump(mode="json") for item in items], total, page, size)


@router.get(
    "/styles/exports",
    response_class=StreamingResponse,
    summary="导出货号 xlsx（与列表同一套筛选）",
    openapi_extra={
        "x-permission": f"base:export 且 {EXPORT_GLOBAL_PERMISSION}",
    },
    tags=STYLE_TAGS,
)
async def export_styles(
    ctx: ContextDep,
    session: SessionDep,
    q: Annotated[str | None, Query(max_length=64)] = None,
    is_active: bool | None = None,
    customer_id: UUID | None = None,
    category_id: UUID | None = None,
    merchandiser_id: UUID | None = None,
    sort_by: Annotated[str | None, Query()] = None,
    sort_order: Annotated[str, Query(pattern="^(asc|desc)$")] = "asc",
) -> StreamingResponse:
    """导出货号。

    ⚠️ **与列表共用同一个 service 筛选路径**（docs/07 §3.2 铁律 3）：另写一条
    导出查询的话，「列表看到的」与「导出的」会不一致，而那只有对账时才发现。

    ⚠️ **不导出数据范围之外的款号**：跟单（SELF）导出的也只有本人款号 ——
    导出是绕过界面直接拿数据的地方，比界面更容易泄露。

    ⚠️ 需要**两个**权限点同时具备：``base:export`` + ``system:export:manage``。
    """
    for required in ("base:export", EXPORT_GLOBAL_PERMISSION):
        if not ctx.has(required):
            raise BusinessError(ErrorCode.PERMISSION_DENIED, f"无导出权限：缺少 {required}")
    rows = await _style_service(session, ctx).export_styles(
        StyleQuery(
            q=q,
            is_active=is_active,
            customer_id=customer_id,
            category_id=category_id,
            merchandiser_id=merchandiser_id,
            sort_by=sort_by,
            sort_order=sort_order,
        )
    )
    payload = [row.model_dump(mode="json") for row in rows]
    headers = {
        "Content-Disposition": (
            f'attachment; filename="styles-{datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")}.xlsx"'
        ),
        "X-Row-Count": str(len(payload)),
    }
    return StreamingResponse(
        stream_xlsx(STYLE_EXPORT_COLUMNS, iter(payload)),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


@router.get(
    "/styles/options",
    response_model=ApiResponse[list[OptionOut]],
    summary="款号候选（默认按最近使用倒序，size ≤ 20）",
    openapi_extra={"x-permission": "base:read"},
    tags=STYLE_TAGS,
)
async def list_style_options(
    ctx: ContextDep,
    session: SessionDep,
    q: Annotated[str | None, Query(max_length=64)] = None,
    size: Annotated[int, Query(ge=1, le=20)] = 20,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
) -> dict[str, object]:
    """款号候选。``q`` 为空时按 ``last_used_at DESC NULLS LAST`` 返回前 N 条。"""
    _require_base(ctx, "base:read", "查看款号")
    options = await _style_service(session, ctx).list_options(q, size, offset)
    return ok([item.model_dump(mode="json") for item in options])


@router.post(
    "/styles",
    status_code=201,
    response_model=ApiResponse[StyleOut],
    summary="新建款号（style_no 必填、用户自定义；可附带建议号）",
    openapi_extra={
        "x-permission": "base:create",
        "x-request-schema": StyleCreate.model_json_schema(),
    },
    tags=STYLE_TAGS,
)
async def create_style(
    payload: StyleCreate, ctx: ContextDep, session: SessionDep
) -> dict[str, object]:
    """新建款号。``suggest_style_no=true`` 时额外返回建议号（用户输入仍然优先）。"""
    _require_base(ctx, "base:create", "新建款号")
    style = await _style_service(session, ctx).create(payload)
    return ok(style.model_dump(mode="json"))


# ⚠️ **必须注册在 `/styles/{style_no}` 之前**：FastAPI 按注册顺序匹配，排在后面的话
#    `/styles/suggested-no` 会被当成「款号叫 suggested-no 的那一行」→ 404，
#    症状是"这个接口明明在 OpenAPI 里却 404"，极难定位（字典路由踩过同一个坑，
#    见 `_register_one` 的注释）。
@router.get(
    "/styles/suggested-no",
    response_model=ApiResponse[SuggestedStyleNoOut],
    summary="取一个建议款号（**只读建议，不建档**；⚠️ 会消耗一个序号）",
    openapi_extra={"x-permission": "base:create"},
    tags=STYLE_TAGS,
)
async def suggest_style_no_endpoint(
    ctx: ContextDep,
    session: SessionDep,
    customer_id: Annotated[UUID | None, Query(description="归属客户；不给 = 全厂序列")] = None,
) -> dict[str, object]:
    """取建议款号。

    ⚠️ **权限点用 `base:create` 而不是 `base:read`**：取号会 `UPDATE
    style_no_sequences.next_no`，是一个**写操作**。给 `base:read` 的话，任何能看款号的
    人都能狂点把某一年的序号消耗光（虽然不影响正确性，但建议号会跳得很难看）。

    为什么不复用 ``POST /styles?suggest_style_no=true``：那个端点会**真的建档**
    （建议号是建档时"额外回一个"）。表单上的「生成建议号」按钮要的是"填进去让我改"，
    复用它等于每点一次按钮就多一个款号。
    """
    _require_base(ctx, "base:create", "生成建议款号")
    return ok(
        SuggestedStyleNoOut(
            style_no=await _style_service(session, ctx).suggest(customer_id),
            customer_id=customer_id,
        ).model_dump(mode="json")
    )


@router.get(
    "/styles/{style_no}",
    response_model=ApiResponse[StyleDetailOut],
    summary="款号详情（款号 + 色组 + 尺码 + 工序 + 现行价）",
    openapi_extra={"x-permission": "base:read"},
    tags=STYLE_TAGS,
)
async def get_style(
    style_no: Annotated[str, Path(min_length=1, max_length=32)],
    ctx: ContextDep,
    session: SessionDep,
) -> dict[str, object]:
    """款号详情。不存在 → ``20001``；不在数据范围 → ``12002``。"""
    _require_base(ctx, "base:read", "查看款号")
    detail = await _style_service(session, ctx).get_detail(style_no)
    return ok(detail.model_dump(mode="json"))


@router.post(
    "/styles/{style_no}/disables",
    response_model=ApiResponse[StyleOut],
    summary="停用款号（必填原因 + 必传 version）",
    openapi_extra={
        "x-permission": "base:disable",
        "x-request-schema": StyleDisableIn.model_json_schema(),
    },
    tags=STYLE_TAGS,
)
async def disable_style(
    style_no: Annotated[str, Path(min_length=1, max_length=32)],
    payload: StyleDisableIn,
    ctx: ContextDep,
    session: SessionDep,
) -> dict[str, object]:
    """停用款号：不允许新建裁剪/打菲单，**历史单据照常**（R2）。

    ⚠️ 恢复走 ``PATCH /styles/{style_no}``（``is_active=true``）—— 不另开 enable 端点，
    理由见 ``StyleService.disable`` 的注释。
    """
    _require_base(ctx, "base:disable", "停用款号")
    style = await _style_service(session, ctx).disable(style_no, payload.reason, payload.version)
    return ok(style.model_dump(mode="json"))


@router.patch(
    "/styles/{style_no}",
    response_model=ApiResponse[StyleOut],
    summary="修改款号（必传 version；style_no 不可改）",
    openapi_extra={
        "x-permission": "base:update",
        "x-request-schema": StylePatch.model_json_schema(),
    },
    tags=STYLE_TAGS,
)
async def patch_style(
    style_no: Annotated[str, Path(min_length=1, max_length=32)],
    payload: StylePatch,
    ctx: ContextDep,
    session: SessionDep,
) -> dict[str, object]:
    """修改款号。``version`` 不匹配 → ``10003``。"""
    _require_base(ctx, "base:update", "修改款号")
    style = await _style_service(session, ctx).patch(style_no, payload)
    return ok(style.model_dump(mode="json"))
