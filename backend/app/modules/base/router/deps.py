"""基础资料 router 的共享依赖、权限助手与导出常量（设计稿 §2.2）。

本模块只放**被多个子 router 共用**的依赖与常量，不含任何端点，避免
``style_router`` / ``material_router`` / ``rate_router`` 之间互相 import
形成环。分层：``deps`` ← 各内容 router ← ``dict_factory`` ← ``__init__``。
"""

import logging
from datetime import UTC, datetime
from enum import Enum
from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.errors import BusinessError, ErrorCode
from app.core.excel import Column
from app.core.permissions import AuthContext, get_auth_context
from app.modules.base.repository import ListQuery
from app.modules.base.resources import DictResource
from app.modules.base.service import (
    DictService,
    MaterialOptionsService,
    RateService,
    StyleService,
)

logger = logging.getLogger("app.base.router")

#: 端点返回值。FastAPI 靠 ``__signature__`` 反射参数，所以这里统一用别名收口 Any
type EndpointResult = Any

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


def _require_base(ctx: AuthContext, permission: str, action: str) -> None:
    """校验权限点。失败时**同时报出缺哪个权限**，否则前端只能猜。"""
    if not ctx.has(permission):
        raise BusinessError(
            ErrorCode.PERMISSION_DENIED,
            f"无权限{action}",
            details={"required_permission": permission},
        )


# ------------------------------------------------------------------ 组 D 常量


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

#: 布批候选的 OpenAPI 标签。⚠️ 与其余基础资料端点**分开**：布批是库存数据，
#: 它的权限点是 ``stock:read`` 而不是 ``base:read``（05 §9.5.2 + C38）。
STOCK_TAGS: list[str | Enum] = ["库存"]


def _style_service(session: AsyncSession, ctx: AuthContext) -> StyleService:
    return StyleService(session, ctx)


def _rate_service(session: AsyncSession, ctx: AuthContext) -> RateService:
    return RateService(session, ctx)


def _material_options_service(session: AsyncSession, ctx: AuthContext) -> MaterialOptionsService:
    return MaterialOptionsService(session, ctx)
