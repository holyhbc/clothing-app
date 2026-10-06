"""款号子表路由：色码 / 尺码 / 比例 / 款号工序 / 模板复制（设计稿 §2.2，原 1000-1170 行）。"""

from typing import Annotated

from fastapi import APIRouter, Path, Query, Request

from app.core.idempotency import load_idempotent, store_idempotent
from app.core.responses import ApiResponse, ok
from app.modules.base.schemas import (
    RatioListOut,
    RatioReplaceIn,
    StyleColorCreate,
    StyleColorOut,
    StyleOperationOut,
    StyleOperationsListOut,
    StyleOperationsReplaceIn,
    StyleSizeCreate,
    StyleSizeOut,
    TemplateCopyIn,
    TemplateCopyOut,
)

from .deps import STYLE_TAGS, ContextDep, SessionDep, _require_base, _style_service

router = APIRouter()


@router.post(
    "/styles/{style_no}/colors",
    status_code=201,
    response_model=ApiResponse[list[StyleColorOut]],
    summary="新增款号色组",
    openapi_extra={
        "x-permission": "base:create",
        "x-request-schema": StyleColorCreate.model_json_schema(),
    },
    tags=STYLE_TAGS,
)
async def create_style_color(
    style_no: Annotated[str, Path(min_length=1, max_length=32)],
    payload: StyleColorCreate,
    ctx: ContextDep,
    session: SessionDep,
) -> dict[str, object]:
    """新增色组行。色码重复 → ``10001``。"""
    _require_base(ctx, "base:create", "新建款号色组")
    rows = await _style_service(session, ctx).add_colors(style_no, payload)
    return ok([item.model_dump(mode="json") for item in rows])


@router.post(
    "/styles/{style_no}/sizes",
    status_code=201,
    response_model=ApiResponse[list[StyleSizeOut]],
    summary="新增款号尺码（单码，或 size_group_name 一键带出整套）",
    openapi_extra={
        "x-permission": "base:create",
        "x-request-schema": StyleSizeCreate.model_json_schema(),
    },
    tags=STYLE_TAGS,
)
async def create_style_size(
    style_no: Annotated[str, Path(min_length=1, max_length=32)],
    payload: StyleSizeCreate,
    ctx: ContextDep,
    session: SessionDep,
) -> dict[str, object]:
    """新增尺码。``size_group_name`` 与 ``size_code`` **二选一**（Schema 层就拒）。"""
    _require_base(ctx, "base:create", "新建款号尺码")
    rows = await _style_service(session, ctx).add_sizes(style_no, payload)
    return ok([item.model_dump(mode="json") for item in rows])


@router.get(
    "/style-color-size-ratios",
    response_model=ApiResponse[RatioListOut],
    summary="查尺码比例（hands_total + missing_size_codes）",
    openapi_extra={"x-permission": "base:read"},
    tags=STYLE_TAGS,
)
async def list_style_ratios(
    ctx: ContextDep,
    session: SessionDep,
    style_no: Annotated[str, Query(min_length=1, max_length=32, description="款号（必填）")],
    color_code: Annotated[str | None, Query(max_length=32)] = None,
    size_code: Annotated[str | None, Query(max_length=32)] = None,
) -> dict[str, object]:
    """比例查询。**不抛 20006** —— 查询必须永远能返回空集（R24 / ADR-0014）。"""
    _require_base(ctx, "base:read", "查看尺码比例")
    payload = await _style_service(session, ctx).list_ratios(style_no, color_code, size_code)
    return ok(payload.model_dump(mode="json"))


@router.put(
    "/style-color-size-ratios",
    response_model=ApiResponse[RatioListOut],
    summary="按 (款号, 颜色) 全量替换尺码比例（≤100 行，必传 version）",
    openapi_extra={
        "x-permission": "base:update",
        "x-request-schema": RatioReplaceIn.model_json_schema(),
    },
    tags=STYLE_TAGS,
)
async def replace_style_ratios(
    payload: RatioReplaceIn, ctx: ContextDep, session: SessionDep
) -> dict[str, object]:
    """全量替换比例。

    - 比例出现款号没有的尺码 → ``20007``
    - ``version`` 不匹配 → ``10003``（TC-B31：并发一成一败）
    """
    _require_base(ctx, "base:update", "维护尺码比例")
    result = await _style_service(session, ctx).replace_ratios(payload)
    return ok(result.model_dump(mode="json"))


@router.get(
    "/styles/{style_no}/operations",
    response_model=ApiResponse[list[StyleOperationOut]],
    summary="款号工序配置列表（按 sequence 升序）",
    openapi_extra={"x-permission": "base:read"},
    tags=STYLE_TAGS,
)
async def list_style_operations(
    style_no: Annotated[str, Path(min_length=1, max_length=32)],
    ctx: ContextDep,
    session: SessionDep,
    is_piecework: bool | None = None,
    include_inactive: Annotated[bool, Query(description="是否包含工序字典已停用的行")] = False,
) -> dict[str, object]:
    """款号工序配置列表。"""
    _require_base(ctx, "base:read", "查看款号工序")
    rows = await _style_service(session, ctx).list_style_operations(
        style_no, is_piecework=is_piecework, include_inactive=include_inactive
    )
    return ok([item.model_dump(mode="json") for item in rows])


@router.put(
    "/styles/{style_no}/operations",
    response_model=ApiResponse[StyleOperationsListOut],
    summary="全量替换款号工序（≤500 行，必传 version）",
    openapi_extra={
        "x-permission": "base:update",
        "x-request-schema": StyleOperationsReplaceIn.model_json_schema(),
    },
    tags=STYLE_TAGS,
)
async def replace_style_operations(
    style_no: Annotated[str, Path(min_length=1, max_length=32)],
    payload: StyleOperationsReplaceIn,
    ctx: ContextDep,
    session: SessionDep,
) -> dict[str, object]:
    """全量替换款号工序。

    - ``operation_no`` 不存在或已停用 → ``10001``
    - ``is_final_operation`` 超过一道 → ``10001``
    - ``version`` 不匹配 → ``10003``
    """
    _require_base(ctx, "base:update", "维护款号工序")
    result = await _style_service(session, ctx).replace_style_operations(style_no, payload)
    return ok(result.model_dump(mode="json"))


@router.post(
    "/styles/{style_no}/operations/copy-from/{source_style_no}",
    response_model=ApiResponse[TemplateCopyOut],
    summary="工序与单价模板复制（支持 Idempotency-Key；档位2 分类价不复制）",
    openapi_extra={
        "x-permission": "base:rate_template:manage",
        "x-request-schema": TemplateCopyIn.model_json_schema(),
    },
    tags=STYLE_TAGS,
)
async def copy_style_template(
    style_no: Annotated[str, Path(min_length=1, max_length=32, description="目标款号")],
    source_style_no: Annotated[str, Path(min_length=1, max_length=32, description="源款号")],
    request: Request,
    payload: TemplateCopyIn,
    ctx: ContextDep,
    session: SessionDep,
) -> dict[str, object]:
    """把源款号的工序结构与当前有效价整套复制到目标款号（单事务）。

    ⚠️ 支持 ``Idempotency-Key``（docs/05 §5）：同键同 body 返回**首次结果**
    （HTTP 200，不报错）；同键不同 body → ``10002``。前端重复点击靠这个兜底。
    """
    _require_base(ctx, "base:rate_template:manage", "工序单价模板复制")
    idempotent = await load_idempotent(request)
    if idempotent is not None and idempotent.cached is not None:
        return idempotent.cached
    result = await _style_service(session, ctx).copy_template(style_no, source_style_no, payload)
    response = ok(result.model_dump(mode="json"))
    if idempotent is not None:
        await store_idempotent(idempotent, response)
    return response
