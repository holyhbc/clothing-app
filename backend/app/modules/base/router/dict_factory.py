"""字典资源的路径注册机制（设计稿 §2.2）。

九个资源 × 7 个动作全部**由 :mod:`resources` 注册表生成**。手写九份 handler
意味着「停用必填 reason」这种规则会有九份拷贝，漏一份就是可以随便停用主数据的
漏洞 —— 所以这里一个函数注册多个路径。

⚠️ 权限声明用**运行时按路径参数**（:func:`_permission`）而不是装饰器上的常量，
因为每个端点的权限点取决于命中的资源（``base:create`` vs ``base:operation:manage``）。
OpenAPI 里用 ``x-permission`` 标注九个资源的实际权限点。
"""

import inspect
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import APIRouter, Path
from fastapi.responses import StreamingResponse

from app.core.responses import ApiResponse
from app.modules.base.resources import RESOURCES, DictResource
from app.modules.base.schemas import WRITE_MODELS, DeleteOut, DictOut, DisableOut, OptionOut

from .deps import EXPORT_GLOBAL_PERMISSION, EndpointResult, _permission
from .dict_handlers import (
    create_resource,
    delete_resource,
    disable_resource,
    export_resource,
    get_resource_one,
    list_options,
    list_resource,
    patch_resource,
)


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
