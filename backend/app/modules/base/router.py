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
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Path, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.errors import BusinessError, ErrorCode
from app.core.excel import Column, stream_xlsx
from app.core.permissions import AuthContext, get_auth_context
from app.core.responses import ApiResponse, ok, page_ok
from app.modules.base.repository import ListQuery
from app.modules.base.resources import RESOURCES, DictResource
from app.modules.base.schemas import (
    WRITE_MODELS,
    DeleteOut,
    DictOut,
    DictRow,
    DisableIn,
    DisableOut,
    OptionOut,
)
from app.modules.base.service import DictService

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


# 导入即注册：资源路由在模块加载时挂上具体路径
register_resource_routes(router)

__all__ = ["RESOURCES", "DictResource", "register_resource_routes", "router"]
