"""基础资料接口（设计稿 §4.4）。

九个资源 × 7 个动作，全部**由 :mod:`resources` 注册表生成**。手写九份 handler
意味着「停用必填 reason」这种规则会有九份拷贝，漏一份就是可以随便停用主数据的
漏洞 —— 所以这里一个函数注册多个路径。

⚠️ 权限声明用**运行时按路径参数**（:func:`_permission`）而不是装饰器上的常量，
因为每个端点的权限点取决于命中的资源（``base:create`` vs ``base:operation:manage``）。
OpenAPI 里用 ``x-permission`` 标注九个资源的实际权限点。
"""

import inspect
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from enum import Enum
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Path, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.errors import BusinessError, ErrorCode
from app.core.excel import Column, stream_xlsx
from app.core.idempotency import load_idempotent, store_idempotent
from app.core.numbering import business_today
from app.core.permissions import AuthContext, get_auth_context
from app.core.responses import ApiResponse, PageData, ok, page_ok
from app.modules.base.document_logs import list_document_logs
from app.modules.base.repository import ListQuery
from app.modules.base.resources import RESOURCES, DictResource
from app.modules.base.schemas import (
    WRITE_MODELS,
    DeleteOut,
    DictOut,
    DictRow,
    DisableIn,
    DisableOut,
    DocumentLogOut,
    OperationRateCreate,
    OperationRateOut,
    OperationRateSetOut,
    OptionOut,
    RateResolveOut,
    RatioListOut,
    RatioReplaceIn,
    StyleColorCreate,
    StyleColorOut,
    StyleCreate,
    StyleDetailOut,
    StyleDisableIn,
    StyleListOut,
    StyleOperationOut,
    StyleOperationsListOut,
    StyleOperationsReplaceIn,
    StyleOut,
    StylePatch,
    StyleSizeCreate,
    StyleSizeOut,
    SuggestedStyleNoOut,
    TemplateCopyIn,
    TemplateCopyOut,
)
from app.modules.base.service import (
    DictService,
    RateQuery,
    RateService,
    StyleQuery,
    StyleService,
)

logger = logging.getLogger("app.base.router")

#: 端点返回值。FastAPI 靠 ``__signature__`` 反射参数，所以这里统一用别名收口 Any
type EndpointResult = Any

# ⚠️ **不加 /base 前缀**：设计稿 §4.4 与 docs/05 §9.5.2 规定的路径是
# ``/api/v1/colors``、``/api/v1/workshops``，顶层直挂。挂到 ``/api/v1/base/*``
# 会让前端与 docs/05 §9 的示例对不上。
router = APIRouter(tags=["基础资料"])

SessionDep = Annotated[AsyncSession, Depends(get_db)]
ContextDep = Annotated[AuthContext, Depends(get_auth_context)]

#: 导出接口额外的全局权限（§4.4：``base:export`` **且** ``system:export:manage``）
EXPORT_GLOBAL_PERMISSION = "system:export:manage"


def _export_filename(resource: DictResource) -> str:
    """导出文件名带时间戳 —— 否则用户上午导一次、下午再导，浏览器会拿
    ``colors-export(1).xlsx`` 这种名字，或者直接静默覆盖。"""
    return f"{resource.key}-{datetime.now(tz=UTC).strftime('%Y%m%d-%H%M%S')}.xlsx"


def _permission(resource: DictResource, action: str) -> str:
    return resource.permission(action)


def _require(ctx: AuthContext, permission: str, resource: DictResource, action: str) -> None:
    """校验权限点。失败时**同时报出缺哪个权限**，否则前端只能猜。"""
    if not ctx.has(permission):
        raise BusinessError(
            ErrorCode.PERMISSION_DENIED,
            f"无权限{action} {resource.doc_type}",
            details={"required_permission": permission},
        )


def _service(resource: DictResource, session: AsyncSession, ctx: AuthContext) -> DictService:
    return DictService(session, resource, ctx)


# ------------------------------------------------------------------ 查询参数


def _list_query(
    q: str | None,
    is_active: bool | None,
    is_builtin: bool | None,
    sort_by: str | None,
    sort_order: str,
    page: int,
    size: int,
    workshop_id: str | None,
    size_class: str | None,
    warehouse_type: str | None,
    color_family: str | None,
    is_piecework: bool | None,
) -> ListQuery:
    """把查询参数收成 :class:`ListQuery`。

    非法的 filter 名**不在这里拦**，而是交给 :meth:`ListQuery.validate` 按资源白名单
    判定 —— 否则每个资源的参数集不同，FastAPI 的签名就得写九份。
    """
    filters: dict[str, Any] = {
        "workshop_id": workshop_id,
        "size_class": size_class,
        "warehouse_type": warehouse_type,
        "color_family": color_family,
        "is_piecework": is_piecework,
    }
    return ListQuery(
        q=q,
        is_active=is_active,
        is_builtin=is_builtin,
        filters={key: value for key, value in filters.items() if value is not None},
        sort_by=sort_by,
        sort_order=sort_order,
        page=page,
        size=size,
    )


# ------------------------------------------------------------------ 端点


async def list_resource(
    resource: DictResource,
    ctx: ContextDep,
    session: SessionDep,
    q: Annotated[str | None, Query(max_length=64, description="编码或名称模糊搜索")] = None,
    is_active: bool | None = None,
    is_builtin: bool | None = None,
    sort_by: str | None = None,
    sort_order: Annotated[str, Query(pattern="^(asc|desc)$")] = "asc",
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=200)] = 20,
    workshop_id: str | None = None,
    size_class: str | None = None,
    warehouse_type: str | None = None,
    color_family: str | None = None,
    is_piecework: bool | None = None,
) -> dict[str, object]:
    """分页列表，每行带 ``ref_count``。"""
    _require(ctx, "base:read", resource, "查看")
    if not ctx.has("base:read"):
        raise BusinessError(ErrorCode.PERMISSION_DENIED, "无权限查看基础资料")
    service = _service(resource, session, ctx)
    items, total = await service.list_rows(
        _list_query(
            q,
            is_active,
            is_builtin,
            sort_by,
            sort_order,
            page,
            size,
            workshop_id,
            size_class,
            warehouse_type,
            color_family,
            is_piecework,
        )
    )
    return page_ok(
        [DictRow.model_validate(item).model_dump(mode="json") for item in items],
        total,
        page,
        size,
    )


async def list_options(
    resource: DictResource,
    ctx: ContextDep,
    session: SessionDep,
    q: Annotated[str | None, Query(max_length=64)] = None,
    size: Annotated[int, Query(ge=1, le=20)] = 20,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
) -> dict[str, object]:
    """候选搜索。``q`` 为空时按默认排序返回前 N 条。"""
    _require(ctx, "base:read", resource, "查看")
    options = await _service(resource, session, ctx).options(q, size, offset)
    return ok([item.model_dump(mode="json") for item in options])


async def create_resource(
    resource: DictResource,
    ctx: ContextDep,
    session: SessionDep,
    payload: Annotated[dict[str, Any], Body(description="字段随资源而定，见各资源的 Create 模型")],
) -> dict[str, object]:
    """新建。写权限按资源不同（见注册表 ``write_permission``）。"""
    _require(ctx, _permission(resource, "create"), resource, "新建")
    model_cls = WRITE_MODELS[resource.key][0]
    validated = _validate(payload, model_cls, resource)
    obj = await _service(resource, session, ctx).create(validated)
    return ok(DictOut.model_validate(obj).model_dump(mode="json"))


async def get_resource_one(
    resource: DictResource,
    code: str,
    ctx: ContextDep,
    session: SessionDep,
) -> dict[str, object]:
    """取单条。路径参数是**业务编码**（如 ``WHT`` / ``01``），不是 UUID。"""
    _require(ctx, "base:read", resource, "查看")
    obj = await _service(resource, session, ctx).get_one(code)
    return ok(DictOut.model_validate(obj).model_dump(mode="json"))


async def patch_resource(
    resource: DictResource,
    code: Annotated[str, Path(min_length=1, max_length=64)],
    ctx: ContextDep,
    session: SessionDep,
    payload: Annotated[dict[str, Any], Body(description="必含 version；编码字段不可改")],
) -> dict[str, object]:
    """部分更新。``version`` 不匹配 → ``10003``。"""
    _require(ctx, _permission(resource, "update"), resource, "修改")
    model_cls = WRITE_MODELS[resource.key][1]
    validated = _validate(payload, model_cls, resource)
    obj = await _service(resource, session, ctx).patch(code, validated)
    return ok(DictOut.model_validate(obj).model_dump(mode="json"))


async def disable_resource(
    resource: DictResource,
    code: str,
    ctx: ContextDep,
    session: SessionDep,
    payload: DisableIn,
) -> dict[str, object]:
    """停用。``reason`` 缺失 → ``10001``（Schema 拦），空串 → ``10002``。"""
    _require(ctx, _permission(resource, "disable"), resource, "停用")
    obj = await _service(resource, session, ctx).disable(code, payload.reason)
    return ok(
        DisableOut(
            code=resource.path_column_value(obj),
            is_active=False,
            reason=payload.reason,
        ).model_dump(mode="json")
    )


async def delete_resource(
    resource: DictResource,
    code: str,
    ctx: ContextDep,
    session: SessionDep,
) -> dict[str, object]:
    """删除。

    - 纯软删表（车间/组别/仓库/单位/工序/分类）：写 ``deleted_at``
    - 字典表（颜色/尺码/码表）：引用为 0 → **物理删除**；>0 → ``20003``
    """
    _require(ctx, _permission(resource, "delete"), resource, "删除")
    cascaded = await _service(resource, session, ctx).delete(code)
    return ok(DeleteOut(deleted=True, cascaded=cascaded).model_dump(mode="json"))


async def export_resource(
    resource: DictResource,
    ctx: ContextDep,
    session: SessionDep,
    q: Annotated[str | None, Query(max_length=64)] = None,
    is_active: bool | None = None,
    is_builtin: bool | None = None,
    sort_by: str | None = None,
    sort_order: Annotated[str, Query(pattern="^(asc|desc)$")] = "asc",
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=200)] = 20,
    workshop_id: str | None = None,
    size_class: str | None = None,
    warehouse_type: str | None = None,
    color_family: str | None = None,
    is_piecework: bool | None = None,
) -> StreamingResponse:
    """导出。**与列表共用同一个 service 方法**（docs/07 §3.2 铁律 3）。

    ⚠️ 导出需要**两个**权限点同时具备：``base:export``（能导出这份数据）与
    ``system:export:manage``（全局导出总开关，ADR 决议）。少一个都拒绝。
    """
    for required in (resource.permission("export"), EXPORT_GLOBAL_PERMISSION):
        if not ctx.has(required):
            raise BusinessError(ErrorCode.PERMISSION_DENIED, f"无导出权限：缺少 {required}")
    service = _service(resource, session, ctx)
    rows = await service.export_rows(
        _list_query(
            q,
            is_active,
            is_builtin,
            sort_by,
            sort_order,
            page,
            size,
            workshop_id,
            size_class,
            warehouse_type,
            color_family,
            is_piecework,
        )
    )
    columns = _export_columns(resource)
    headers = {
        "Content-Disposition": f'attachment; filename="{_export_filename(resource)}"',
        "X-Row-Count": str(len(rows)),
    }
    return StreamingResponse(
        stream_xlsx(columns, iter(rows)),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


# ------------------------------------------------------------------ 辅助


def _validate(
    payload: dict[str, Any], model_cls: type[BaseModel], resource: DictResource
) -> dict[str, Any]:
    """用该资源的写入模型校验请求体。

    直接收 ``dict`` 再手动校验，是因为九个资源的字段集不同：写成九个不同类型的
    形参虽然 OpenAPI 更精确，但 FastAPI 无法对**运行时决定**的路径参数给出不同的
    body 模型（``Union`` 会让 OpenAPI 显示成一个anyOf，前端拿不到准确契约）。
    这里用 ``extra="forbid"`` 的模型手工校验，效果等价 —— 多余字段照样被拒。
    """
    try:
        return model_cls.model_validate(payload).model_dump()
    except Exception as exc:
        raise BusinessError(
            ErrorCode.PARAM_INVALID,
            "请求参数不合法",
            details={"resource": resource.key, "reason": _short(exc)},
        ) from exc


def _short(exc: Exception) -> str:
    """把 pydantic 的报错压成一行可读文案。"""
    errors = getattr(exc, "errors", None)
    if callable(errors):
        parts = [
            f"{'.'.join(str(item) for item in item.get('loc', ()))}: {item.get('msg', '')}"
            for item in errors()[:5]
        ]
        return "; ".join(parts)
    return str(exc)[:200]


#: 各资源的导出列。顺序即表头顺序
_EXPORT_COLUMN_MAP: dict[str, list[Column]] = {
    "workshops": [
        Column("code", "车间编码"),
        Column("name", "车间名"),
        Column("is_active", "启用"),
    ],
    "workshop-groups": [
        Column("workshop_id", "车间ID"),
        Column("group_no", "组别号"),
        Column("name", "组别名"),
        Column("is_active", "启用"),
    ],
    "warehouses": [
        Column("code", "仓库编码"),
        Column("name", "仓库名"),
        Column("warehouse_type", "类型"),
        Column("is_active", "启用"),
    ],
    "uom-units": [
        Column("code", "单位码"),
        Column("name", "单位名"),
        Column("decimal_places", "小数位"),
        Column("remark", "备注"),
    ],
    "product-categories": [
        Column("code", "分类码"),
        Column("name", "分类名"),
        Column("sort", "排序"),
        Column("is_active", "启用"),
    ],
    "colors": [
        Column("color_code", "色码"),
        Column("name", "色名"),
        Column("color_family", "色卡族"),
        Column("pantone_code", "潘通号"),
        Column("is_builtin", "内置"),
        Column("is_active", "启用"),
    ],
    "sizes": [
        Column("size_code", "尺码码"),
        Column("name", "尺码名"),
        Column("size_class", "尺码类"),
        Column("sort_order", "排序"),
        Column("is_builtin", "内置"),
        Column("is_active", "启用"),
    ],
    "size-groups": [
        Column("name", "码表名"),
        Column("size_class", "尺码类"),
        Column("is_builtin", "内置"),
        Column("is_active", "启用"),
    ],
    "operations": [
        Column("operation_no", "工序号"),
        Column("name", "工序名"),
        Column("workshop_id", "车间ID"),
        Column("is_piecework", "计件"),
        Column("default_bundle_qty", "默认一扎件数"),
        Column("sort_order", "排序"),
        Column("is_active", "启用"),
    ],
    "customers": [
        Column("code", "客户编码"),
        Column("name", "客户全称"),
        Column("short_name", "简称"),
        Column("contact", "联系人"),
        Column("phone", "联系电话"),
        Column("tax_no", "税号"),
        Column("settlement_period_days", "账期天数"),
        Column("remark", "备注"),
        Column("is_active", "启用"),
    ],
}


def _export_columns(resource: DictResource) -> list[Column]:
    try:
        return _EXPORT_COLUMN_MAP[resource.key]
    except KeyError as exc:  # pragma: no cover —— 注册表加了资源但忘了列
        raise BusinessError(
            ErrorCode.INTERNAL,
            f"资源 {resource.key} 尚未配置导出列",
        ) from exc


# ------------------------------------------------------------------ 路径注册


def register_resource_routes(api: APIRouter) -> None:
    """为**每个资源**注册**具体路径**。

    ⚠️ 不用 ``/{resource_key}`` 通配。三条理由，每条都会真的踩到：
        1. 通配会**遮蔽后续所有顶层资源** —— T-BASE-002 加 ``/styles`` 时，
           ``/styles`` 会被 ``/{resource_key}`` 抢走，FastAPI 靠注册顺序决定谁生效，
           这种隐式依赖极难排查
        2. OpenAPI 里只显示 ``/api/v1/{resource_key}``，前端发现不了
           ``/api/v1/colors``，而 docs/05 §9.5.2 明确要求这个路径存在
        3. 路径参数的长度/pattern 校验无法按资源定制
    """
    for resource in RESOURCES:
        _register_one(api, resource)


def _register_one(api: APIRouter, resource: DictResource) -> None:
    """为一个资源注册 7 个端点。

    ⚠️ **注册顺序有讲究**：``/exports`` 与 ``/options`` 必须排在 ``/{code}`` **之前**。
    FastAPI 按注册顺序匹配，先注册 ``/{code}`` 的话，请求 ``/colors/exports``
    会被当成"编码叫 exports 的那一行"，结果是 404 —— 表现为"导出接口 404"，
    而代码里明明有这条路由，极难定位。
    """
    key = resource.key
    create_model, patch_model = WRITE_MODELS[key]

    api.add_api_route(
        f"/{key}",
        _bind(list_resource, resource),
        methods=["GET"],
        response_model=ApiResponse[dict[str, Any]],
        summary=f"{resource.doc_type} 列表",
        openapi_extra={"x-permission": "base:read"},
        tags=["基础资料"],
    )
    api.add_api_route(
        f"/{key}",
        _bind(create_resource, resource),
        methods=["POST"],
        status_code=201,
        response_model=ApiResponse[DictOut],
        summary=f"新建 {resource.doc_type}",
        openapi_extra={
            "x-permission": _permission(resource, "create"),
            "x-request-schema": create_model.model_json_schema(),
        },
        tags=["基础资料"],
    )
    api.add_api_route(
        f"/{key}/options",
        _bind(list_options, resource),
        methods=["GET"],
        response_model=ApiResponse[list[OptionOut]],
        summary=f"{resource.doc_type} 候选（下拉用，size ≤ 20）",
        openapi_extra={"x-permission": "base:read"},
        tags=["基础资料"],
    )
    api.add_api_route(
        f"/{key}/exports",
        _bind(export_resource, resource),
        methods=["GET"],
        response_class=StreamingResponse,
        summary=f"导出 {resource.doc_type} xlsx（与列表同一套筛选）",
        openapi_extra={
            "x-permission": f"{resource.permission('export')} 且 {EXPORT_GLOBAL_PERMISSION}"
        },
        tags=["基础资料"],
    )
    api.add_api_route(
        f"/{key}/{{code}}",
        _bind(get_resource_one, resource),
        methods=["GET"],
        response_model=ApiResponse[DictOut],
        summary=f"按业务编码取 {resource.doc_type}",
        openapi_extra={"x-permission": "base:read"},
        tags=["基础资料"],
    )
    api.add_api_route(
        f"/{key}/{{code}}",
        _bind(patch_resource, resource),
        methods=["PATCH"],
        response_model=ApiResponse[DictOut],
        summary=f"修改 {resource.doc_type}（必传 version）",
        openapi_extra={
            "x-permission": _permission(resource, "update"),
            "x-request-schema": patch_model.model_json_schema(),
        },
        tags=["基础资料"],
    )
    api.add_api_route(
        f"/{key}/{{code}}/disables",
        _bind(disable_resource, resource),
        methods=["POST"],
        response_model=ApiResponse[DisableOut],
        summary=f"停用 {resource.doc_type}（必填 reason）",
        openapi_extra={"x-permission": _permission(resource, "disable")},
        tags=["基础资料"],
    )
    api.add_api_route(
        f"/{key}/{{code}}",
        _bind(delete_resource, resource),
        methods=["DELETE"],
        response_model=ApiResponse[DeleteOut],
        summary=f"删除 {resource.doc_type}（字典表：未被引用真删 / 被引用 20003）",
        openapi_extra={"x-permission": _permission(resource, "delete")},
        tags=["基础资料"],
    )


def _bind(
    handler: Callable[..., Awaitable[EndpointResult]], resource: DictResource
) -> EndpointResult:
    """把资源绑到 handler 的 ``resource`` 参数上，并让它对 FastAPI 不可见。

    ⚠️ 关键是**必须改写 ``__signature__``**：FastAPI 靠它决定哪些参数是路径 /
    查询 / 依赖。不改的话它会把 ``resource: DictResource`` 当成一个要客户端传的
    查询参数，于是所有请求都因为缺参而 422。
    """
    import functools

    @functools.wraps(handler)
    async def _wrapped(**kwargs: EndpointResult) -> EndpointResult:
        return await handler(resource=resource, **kwargs)

    _wrapped.__signature__ = _signature_without_resource(handler)  # type: ignore[attr-defined]
    _wrapped.__annotations__ = {
        name: value for name, value in handler.__annotations__.items() if name != "resource"
    }
    return _wrapped


def _signature_without_resource(handler: Callable[..., EndpointResult]) -> inspect.Signature:
    """去掉 ``resource`` 形参，并给 ``code`` 补上 Path 标注。"""
    signature = inspect.signature(handler)
    parameters = []
    for name, param in signature.parameters.items():
        if name == "resource":
            continue
        if name == "code":
            # ⚠️ ``Parameter.annotation`` 是**只读**属性，直接赋值会抛
            #    AttributeError（Python 3.12）。必须用 ``replace()`` 造一个新的。
            param = param.replace(annotation=Annotated[str, Path(min_length=1, max_length=64)])
        parameters.append(param)
    return signature.replace(parameters=parameters)


# ==================================================================
# 组 D：款号、色码尺码、比例、款号工序、模板复制、工序单价（§4.5）
# ==================================================================

#: 模板复制成功后必须回给前端的那句提示（modules/01 §5.1 前端表现）。
#: 在**服务端**再兜一次：前端漏渲染这句，用户就会以为"比例也配好了"。

STYLE_TAGS: list[str | Enum] = ["基础资料"]

#: 款号导出的列。⚠️ **不含 UUID**：款号导出是给车间/跟单对账用的，
#: 他们要的是「款号 / 款名 / 客户 / 分类 / 大货数量」，
#: 导出 `category_id` 这种 UUID 对他们毫无意义（而且 docs/05 §3 要求带业务名）。
STYLE_EXPORT_COLUMNS: list[Column] = [
    Column("style_no", "款号"),
    Column("name", "款名"),
    Column("customer_name", "归属客户"),
    Column("customer_style_no", "客户货号"),
    Column("category_name", "商品分类"),
    Column("bulk_qty", "大货数量"),
    Column("merchandiser_name", "跟单员"),
    Column("is_active", "启用"),
    Column("last_used_at", "最近使用"),
]

#: 单价导出的列。⚠️ 顺序即表头顺序；金额 / 单价导出为**字符串**（docs/05 §3）
RATE_EXPORT_COLUMNS: list[Column] = [
    Column("style_no", "款号"),
    Column("operation_no", "工序号"),
    Column("product_category_id", "商品分类ID"),
    Column("rate_source", "取价档位"),
    Column("unit_price", "单价"),
    Column("effective_from", "生效起"),
    Column("effective_to", "生效止"),
    Column("is_current", "当前有效"),
    Column("reason", "原因"),
]


def _style_service(session: AsyncSession, ctx: AuthContext) -> StyleService:
    return StyleService(session, ctx)


def _rate_service(session: AsyncSession, ctx: AuthContext) -> RateService:
    return RateService(session, ctx)


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


@router.get(
    "/document-logs",
    response_model=ApiResponse[PageData[DocumentLogOut]],
    summary="变更历史（按 doc_type + doc_no 查审计日志，docs/06 §2.3 的抽屉）",
    openapi_extra={"x-permission": "base:read"},
    tags=["基础资料"],
)
async def list_logs(
    ctx: ContextDep,
    session: SessionDep,
    doc_type: Annotated[str, Query(max_length=32, description="Style / OperationRate / ...")],
    doc_no: Annotated[str, Query(max_length=64, description="单据号 / 业务编码")],
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, object]:
    """某张单据 / 一条主数据的变更历史。

    ⚠️ ``doc_type`` 与 ``doc_no`` **都必填**：审计表没有归属列，"查全部日志"这种用法
    在数据范围（Q-P0-05 跟单只看自己的款号）下根本无法表达 —— 强行支持就只能给一个
    全厂都能看的"操作日志大屏"，那是另一个需求、另一个权限点。
    """
    _require_base(ctx, "base:read", "查看变更历史")
    data = await list_document_logs(
        session, ctx, doc_type=doc_type, doc_no=doc_no, page=page, size=size
    )
    return ok(data.model_dump(mode="json"))


# ------------------------------------------------------------------ 单价


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


def _require_base(ctx: AuthContext, permission: str, action: str) -> None:
    """校验权限点。失败时**同时报出缺哪个权限**，否则前端只能猜。"""
    if not ctx.has(permission):
        raise BusinessError(
            ErrorCode.PERMISSION_DENIED,
            f"无权限{action}",
            details={"required_permission": permission},
        )


# 导入即注册：资源路由在模块加载时挂上具体路径
register_resource_routes(router)

__all__ = ["RESOURCES", "DictResource", "register_resource_routes", "router"]
