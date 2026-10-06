"""物料 / 供应商 / 布批候选 + 变更历史（设计稿 §2.2）。

⚠️ **必须由 ``__init__`` 在 ``register_resource_routes(router)`` 之前 include**：
    注册表给每个资源注册了 ``/{key}/{code}`` 形态的路径，而这三个是**手写**端点。
    本来 ``/materials/options`` 与九个资源的路径前缀不冲突，但把「手写端点写在前、
    注册表在最后」这条约定固定下来，就不必每次都重新推一遍路由匹配顺序。
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.core.responses import ApiResponse, PageData, ok
from app.modules.base.document_logs import list_document_logs
from app.modules.base.schemas import DocumentLogOut, OptionOut, StockBatchOptionOut

from .deps import (
    STOCK_TAGS,
    STYLE_TAGS,
    ContextDep,
    SessionDep,
    _material_options_service,
    _require_base,
)

router = APIRouter()


@router.get(
    "/materials/options",
    response_model=ApiResponse[list[OptionOut]],
    summary="物料候选（只返面料/辅料，size ≤ 20）",
    openapi_extra={"x-permission": "base:read"},
    tags=STYLE_TAGS,
)
async def list_material_options(
    ctx: ContextDep,
    session: SessionDep,
    q: Annotated[str | None, Query(max_length=64)] = None,
    size: Annotated[int, Query(ge=1, le=20)] = 20,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
) -> dict[str, object]:
    """物料候选。⚠️ 只返 ``FABRIC`` / ``TRIMMING``（成衣不该出现在「选布料」里）。"""
    _require_base(ctx, "base:read", "查看物料")
    options = await _material_options_service(session, ctx).list_material_options(q, size, offset)
    return ok([item.model_dump(mode="json") for item in options])


@router.get(
    "/suppliers/options",
    response_model=ApiResponse[list[OptionOut]],
    summary="供应商候选（size ≤ 20）",
    openapi_extra={"x-permission": "base:read"},
    tags=STYLE_TAGS,
)
async def list_supplier_options(
    ctx: ContextDep,
    session: SessionDep,
    q: Annotated[str | None, Query(max_length=64)] = None,
    size: Annotated[int, Query(ge=1, le=20)] = 20,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
) -> dict[str, object]:
    """供应商候选（05 §9.5.2：``code`` / ``name`` / ``contact``）。"""
    _require_base(ctx, "base:read", "查看供应商")
    options = await _material_options_service(session, ctx).list_supplier_options(q, size, offset)
    return ok([item.model_dump(mode="json") for item in options])


@router.get(
    "/material-stocks/options",
    response_model=ApiResponse[list[StockBatchOptionOut]],
    summary="布批候选（缸号/匹号；**只列 available_qty > 0**；按入库日 FIFO 升序；不过滤 purpose）",
    openapi_extra={"x-permission": "stock:read"},
    tags=STOCK_TAGS,
)
async def list_material_stock_options(
    ctx: ContextDep,
    session: SessionDep,
    q: Annotated[str | None, Query(max_length=64)] = None,
    supplier_id: UUID | None = None,
    material_id: UUID | None = None,
    dye_lot_no: Annotated[str | None, Query(max_length=64)] = None,
    size: Annotated[int, Query(ge=1, le=20)] = 20,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
) -> dict[str, object]:
    """布批候选（05 §9.5.2 末行；C38「不允许自由输入缸号」；BR-ST-17 ③ FIFO）。

    ⚠️ ``q`` 模糊匹配**缸号与匹号**；``dye_lot_no`` 是**精确**匹配缸号 ——
        缸号模糊会把 ``H2408`` 与 ``H24080`` 同时列出来，而它们是不同的布。
    ⚠️ 与九个基础资料的 ``/options`` 不同，这里**不返回** ``unit_cost``（批次成本
        不进选择器），而**返回** ``purpose``（BR-ST-25：返修布可正常领用但成本走
        5403 单独口径，录入员该在选批时就看见）。
    ⚠️ **不过滤 ``purpose``**：BR-ST-25 要求 ``REWORK_RECEIPT`` 也能被裁剪单选到；
        「RETURN / SAMPLE 能不能被裁」规范未写，登记在 docs/12 待决问题里。
    """
    _require_base(ctx, "stock:read", "查看布批")
    options = await _material_options_service(session, ctx).list_stock_batch_options(
        keyword=q,
        supplier_id=supplier_id,
        material_id=material_id,
        dye_lot_no=dye_lot_no,
        size=size,
        offset=offset,
    )
    return ok([item.model_dump(mode="json") for item in options])


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
