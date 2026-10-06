"""九个字典资源的 7 个 handler + 导出列（设计稿 §2.2）。

handler 本身**由 :mod:`dict_factory` 按资源绑定**成具体路径；这里只负责
「收参数 → 校验权限 → 调 service → 包装响应」。handler 函数体逐字搬运自
原 ``router.py``，不新增/修改任何业务逻辑。
"""

from typing import Annotated, Any

from fastapi import Body, Path, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.errors import BusinessError, ErrorCode
from app.core.excel import Column, stream_xlsx
from app.core.responses import ok, page_ok
from app.modules.base.resources import DictResource
from app.modules.base.schemas import (
    WRITE_MODELS,
    DeleteOut,
    DictOut,
    DictRow,
    DisableIn,
    DisableOut,
)

from .deps import (
    EXPORT_GLOBAL_PERMISSION,
    ContextDep,
    SessionDep,
    _export_filename,
    _list_query,
    _permission,
    _require,
    _service,
)


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
