"""计件只读端点（``docs/modules/04-计件.md`` §6.1 的 6 个 GET）。

与 :mod:`.router`（写路径）分开是 ADR-0031 的延续：写路径只有 1 个端点、
读路径有 5 个，硬塞进一个文件会顶破 400 行，而且「读写混在一个 router 里」时，
只想 review 查数逻辑的人得先翻过计件的写入分支。

## ⚠️ **路由声明顺序：``/piecework-logs/export`` 必须排在 ``/{log_id}`` 前面**

FastAPI 按**声明顺序**匹配。同级的 ``/piecework-logs/export`` 会先撞上
``/piecework-logs/{log_id}``，而 ``log_id`` 声明成 ``UUID`` —— 于是 ``export``
解析失败，整个导出端点恒回 422。症状是「导出接口报参数校验失败」，
而打开 OpenAPI 看路径列表完全正常（那里的顺序是字典序，与注册顺序无关），
排查时会一路走到「是不是 log_id 的类型写错了」上去。

## 三条硬约束（与 :mod:`.router` 同款，不重复解释）

1. Router 里不查库、不做数据范围过滤（03 §1.1 第 6 条）。
2. 响应走 ``ok`` / ``page_ok``（05 §3）。
3. 每个端点都带 ``x-permission`` 且函数体里显式 ``_require``（03 §2.1 第 8 条）。
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.errors import BusinessError, ErrorCode
from app.core.numbering import BUSINESS_TZ, business_today
from app.core.permissions import AuthContext, get_auth_context
from app.core.responses import ApiResponse, PageData, ok, page_ok
from app.modules.piecework import query_service
from app.modules.piecework.models import PieceworkLogType
from app.modules.piecework.query_service import PieceworkQueryService
from app.modules.piecework.rates import list_effective_rates
from app.modules.piecework.schemas import (
    LogDetailOut,
    LogQuery,
    LogRowOut,
    OperatorOptionOut,
    PieceworkRateOut,
    StationTodayOut,
    SummaryRowOut,
)

from .router import _require

SessionDep = Annotated[AsyncSession, Depends(get_db)]
ContextDep = Annotated[AuthContext, Depends(get_auth_context)]

PIECEWORK_QUERY_TAGS: list[str | Enum] = ["计件"]

router = APIRouter(tags=PIECEWORK_QUERY_TAGS)


def _query(
    session: SessionDep,
    ctx: ContextDep,
    work_date_from: date | None = None,
    work_date_to: date | None = None,
    style_no: Annotated[str | None, Query(max_length=32)] = None,
    operation_no: Annotated[str | None, Query(max_length=16)] = None,
    workshop_id: UUID | None = None,
    group_no: Annotated[str | None, Query(max_length=32)] = None,
    employee_id: UUID | None = None,
    log_type: PieceworkLogType | None = None,
    bundle_no: Annotated[str | None, Query(max_length=64)] = None,
    is_settled: bool | None = None,
    sort: Annotated[str, Query(max_length=32)] = "-work_date",
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=200)] = 20,
) -> LogQuery:
    """把 12 个 query 参数收成 :class:`LogQuery`。

    ⚠️ **列表 / 导出 / 汇总三个端点都调它**：07 §3.2 铁律 3 要求导出与列表共用
    同一套筛选，而「共用」最省事的做法就是连 query 参数的收法也共用 ——
    少一个端点就少一处可能写歪的地方。
    """
    return LogQuery(
        work_date_from=work_date_from,
        work_date_to=work_date_to,
        style_no=style_no,
        operation_no=operation_no,
        workshop_id=workshop_id,
        group_no=group_no,
        employee_id=employee_id,
        log_type=log_type,
        bundle_no=bundle_no,
        is_settled=is_settled,
        sort=sort,
        page=page,
        size=size,
    )


@router.get(
    "/piecework-logs",
    response_model=ApiResponse[PageData[LogRowOut]],
    summary="计件流水分页列表（★ 手数与件数并列；数据范围由 service 注入）",
    openapi_extra={"x-permission": "piecework:read"},
)
async def list_piecework_logs(
    session: SessionDep,
    ctx: ContextDep,
    query: Annotated[LogQuery, Depends(_query)],
) -> dict[str, object]:
    """流水列表。⚠️ **筛选与导出、汇总完全同源**（见 :func:`_query` 的注）。

    ⚠️ 默认排序 ``-work_date`` 且**带 ``employee_id`` 兜底**：排序键不唯一时，
    「同一行在第 1 页和第 2 页各出现一次」看起来就像分页坏了。
    """
    _require(ctx, "piecework:read", "查看计件流水")
    items, total = await PieceworkQueryService(session, ctx).list_logs(query)
    return page_ok([item.model_dump(mode="json") for item in items], total, query.page, query.size)


@router.get(
    "/piecework-logs/export",
    response_class=Response,
    summary="导出租水 CSV（UTF-8 BOM；与列表同一套筛选；上限 500 行）",
    openapi_extra={
        "x-permission": "piecework:export 且 system:export:manage",
    },
)
async def export_piecework_logs(
    session: SessionDep,
    ctx: ContextDep,
    query: Annotated[LogQuery, Depends(_query)],
) -> Response:
    """导出 CSV。

    ⚠️ 需要**两个**权限点：``piecework:export``（07 §2.2 已登记并授予计件员）
    + ``system:export:manage``（导出总开关，防拖库）。
    ⚠️ ``04-计件 §6.1`` 那一格原写 ``piecework:read`` —— 与
    「权限点已登记却没人用」矛盾，本次按 ``07 §2.2`` 修正并同步文档。

    ⚠️ **BOM 必须真写进字节里**：Excel 不看 charset 参数去猜编码，没 BOM 就按
    GBK 打开 → 中文全是乱码，而「生成成功、下载打不开」排查起来极贵。
    ⚠️ 文件名带时间戳（05 §9.1）：同一份下载两次会命中浏览器缓存，于是
    「重导了一次」看到的还是旧文件。
    """
    for required in ("piecework:export", "system:export:manage"):
        if not ctx.has(required):
            raise BusinessError(
                ErrorCode.PERMISSION_DENIED,
                f"你没有导出计件流水的权限：缺少 {required}",
                details={"required_permission": required},
            )
    rows = await PieceworkQueryService(session, ctx).export_logs(query)
    text = PieceworkQueryService.to_csv(rows)
    stamp = datetime.now(tz=BUSINESS_TZ).strftime("%Y%m%d_%H%M")
    return Response(
        content=text.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8-sig",
        headers={
            "Content-Disposition": f'attachment; filename="piecework-logs-{stamp}.csv"',
            "X-Row-Count": str(len(rows)),
            "X-Row-Limit": str(query_service.EXPORT_LIMIT),
        },
    )


@router.get(
    "/piecework-logs/{log_id}",
    response_model=ApiResponse[LogDetailOut],
    summary="流水详情（+ 码信息 + 关联红冲流水；查不到与越权都回 12002）",
    openapi_extra={"x-permission": "piecework:read"},
)
async def get_piecework_log(
    log_id: Annotated[UUID, Path(description="计件流水 id")],
    session: SessionDep,
    ctx: ContextDep,
) -> dict[str, object]:
    """⚠️ **按 ID 直查也要过数据范围**（07 §3.2 铁律 2）—— 列表过滤了不代表
    「把 id 换一下就能看到别人的」，那是最常见的一处越权。"""
    _require(ctx, "piecework:read", "查看计件流水")
    out = await PieceworkQueryService(session, ctx).get_log(log_id)
    return ok(LogDetailOut.model_validate(out).model_dump(mode="json"))


@router.get(
    "/piecework/summary",
    response_model=ApiResponse[list[SummaryRowOut]],
    summary="计件汇总（★ 手数与件数并列；按 employee / style / operation 分组）",
    openapi_extra={"x-permission": "piecework:read"},
)
async def piecework_summary(
    session: SessionDep,
    ctx: ContextDep,
    query: Annotated[LogQuery, Depends(_query)],
    group_by: Annotated[str, Query(pattern="^(employee|style|operation)$")] = "employee",
) -> dict[str, object]:
    """汇总。⚠️ BR-PW-31：**手数与件数并列**，车间的主计数是「手」。

    ⚠️ ``total_amount`` 是**净额**（红冲取负值，``ck_piecework_logs_sign`` 钉死），
    所以「汇总 ≠ 明细相加」在有红冲时不是 bug。
    """
    _require(ctx, "piecework:read", "查看计件汇总")
    rows = await PieceworkQueryService(session, ctx).summary(query, group_by)
    return ok([row.model_dump(mode="json") for row in rows])


@router.get(
    "/piecework/stations/{station_no}/today",
    response_model=ApiResponse[StationTodayOut],
    summary="工位机今日大字卡（手数为主计数）",
    openapi_extra={"x-permission": "piecework:read"},
)
async def piecework_station_today(
    station_no: Annotated[str, Path(min_length=1, max_length=32)],
    session: SessionDep,
    ctx: ContextDep,
    work_date: date | None = None,
) -> dict[str, object]:
    """工位机今日统计。

    ⚠️ **不套车间数据范围**：这张卡查的是「**本工位**今日」，而工位机的 token
    数据范围往往就是 SELF —— 套上车间过滤会让组别主管在工位机上看到 0，
    那不是「更安全」，是「坏了」。
    """
    _require(ctx, "piecework:read", "查看工位机统计")
    out = await PieceworkQueryService(session, ctx).station_today(station_no, work_date)
    return ok(StationTodayOut.model_validate(out).model_dump(mode="json"))


@router.get(
    "/piecework/operators",
    response_model=ApiResponse[list[OperatorOptionOut]],
    summary="操作员候选（工位机 / 补录用；数据范围内，label=「工号 姓名」）",
    openapi_extra={"x-permission": "piecework:read"},
)
async def piecework_operators(
    session: SessionDep,
    ctx: ContextDep,
    q: Annotated[str | None, Query(max_length=64)] = None,
    workshop_id: UUID | None = None,
    size: Annotated[int, Query(ge=1, le=20)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, object]:
    """工位机 / 补录页的「操作员」下拉（T-PIECE-006，04 §5.1 步骤 1）。

    ⚠️ 权限是 ``piecework:read`` 而**不是** ``system:user:manage``：组长要能选本组
    的工人来扫码计件，但绝不能因此拿到「系统用户管理」。数据范围在
    :meth:`PieceworkQueryService.operators` 里用 ``apply_data_scope`` 强制
    （07 §3.2 铁律 1）—— 组长只看到本组、主管本车间、计件员全厂。
    """
    _require(ctx, "piecework:read", "查看操作员候选")
    rows = await PieceworkQueryService(session, ctx).operators(
        keyword=q, workshop_id=workshop_id, size=size, offset=offset
    )
    return ok([row.model_dump(mode="json") for row in rows])


@router.get(
    "/piecework/rates",
    response_model=ApiResponse[list[PieceworkRateOut]],
    summary="计件单价查询（按 work_date 生效的适用行；T-PIECE-009）",
    openapi_extra={"x-permission": "piecework:rate:manage"},
)
async def piecework_rates(
    session: SessionDep,
    ctx: ContextDep,
    style_no: Annotated[str, Query(min_length=1, max_length=32, description="款号（必填）")],
    operation_no: Annotated[
        str | None, Query(max_length=16, description="工序号；缺省=跨工序")
    ] = None,
    work_date: date | None = None,
) -> dict[str, object]:
    """`04 §6.1` 的 ``GET /piecework/rates``（T-PIECE-009）。

    ⚠️ **权限是 ``piecework:rate:manage``**（与维护单价同一权限点）：单价是敏感
    金额口径，能查生效价的就该是有权设价的人。**数据范围 ``FACTORY``**，故不套
    ``apply_data_scope``（与 ``/piecework/summary`` 那种按车间过滤的读不同）。

    ⚠️ ``work_date`` 缺省**今天**（工厂当地日历 ``business_today``，不是宿主机本地日）
    —— 与取价 ``resolve`` 的口径一致，否则「当天的价」在跨时区的 VPS 上会差一天。

    ⚠️ 语义见 :func:`app.modules.piecework.rates.list_effective_rates`：按 ``work_date``
    当天生效过滤（**不是** ``list_rates`` 的「生效起始日落在区间」）；无命中回 ``20004``。
    """
    _require(ctx, "piecework:rate:manage", "查看计件单价")
    rows = await list_effective_rates(
        session,
        style_no=style_no,
        operation_no=operation_no,
        work_date=work_date or business_today(),
    )
    return ok([row.model_dump(mode="json") for row in rows])
