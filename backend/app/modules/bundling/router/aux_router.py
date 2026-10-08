"""打菲的**辅助能力**端点（T-BUND-007b / modules/03 §6）：拆分预演、标签两接口、
码查询、单码作废、统计、导出。

## 端点与权限点（**本卡不新增任何权限点**，12 个 ``bundling:*`` 全部复用）

| 端点 | 权限点 | 备注 |
| --- | --- | --- |
| ``POST /bundling-orders/{id}/split`` | ``bundling:read`` | 只读预演，不写库 → 不需要 approve |
| ``GET  /bundling-orders/{id}/labels`` | ``bundling:print`` | 标签数据导出（``format=data\\|csv``） |
| ``POST /bundling-orders/{id}/label-prints`` | ``bundling:print`` | 批量打印登记，带 ``Idempotency-Key`` |
| ``GET  /bundles`` | ``bundling:read`` | 码列表（数据范围经父单回查） |
| ``GET  /bundles/{bundle_no}`` | ``bundling:read`` | 单码详情（含裁剪来源缸号匹号 + 打印记录） |
| ``POST /bundles/{bundle_no}/voids`` | ``bundling:code:void`` | 单码作废，**行保留** |
| ``GET  /bundling-orders/statistics`` | ``bundling:read`` | 按款号 × 尺码聚合 |
| ``GET  /bundling-orders/exports`` | ``bundling:export`` | 打菲单 CSV，**复用列表 service** |

⚠️ **本包必须整体 ``include`` 在 ``order_router`` 之前**（见 :mod:`.__init__` 的
docstring）：``/bundling-orders/statistics`` 与 ``/exports`` 是**顶层静态段**，
而 ``/bundling-orders/{order_id}`` 是 UUID 路径参数 —— 它一旦先注册，这两个段会被
抢先匹配成 UUID 解析失败（422），而 OpenAPI 里**照样列着它们**（契约说有、实际调不到，
T-CUT-001c-1 的教训）。本文件里的 ``/{order_id}/xxx`` 是**两段**路径，与那条不冲突。

⚠️ **统计与导出都是只读**：不开事务、不写库、不写日志（与 ``/split`` 同款）。
"""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query, Request, Response

from app.common.enums import LabelExportFormat
from app.core.idempotency import load_idempotent, store_idempotent
from app.core.responses import ApiResponse, PageData, ok, page_ok
from app.modules.bundling.code_repository import BundleListQuery, StatQuery
from app.modules.bundling.repository import BundlingOrderListQuery
from app.modules.bundling.schemas import (
    BundleDetailOut,
    BundleListOut,
    LabelExportOut,
    LabelPrintIn,
    LabelPrintOut,
    SplitPreviewIn,
    SplitPreviewOut,
    StatisticsOut,
    VoidCodeIn,
    VoidCodeOut,
)

from .deps import (
    BUNDLING_TAGS,
    ContextDep,
    OrderId,
    SessionDep,
    _csv_response,
    _require,
    _service,
)

router = APIRouter(tags=BUNDLING_TAGS)


# ====================================================================== 统计 / 导出（顶层静态段）


@router.get(
    "/bundling-orders/statistics",
    response_model=ApiResponse[StatisticsOut],
    summary="打菲统计（按款号 × 尺码：手数 / 件数 / 已计手数 / 未计件手数）",
    openapi_extra={"x-permission": "bundling:read"},
)
async def bundling_statistics(
    ctx: ContextDep,
    session: SessionDep,
    date_from: Annotated[date | None, Query(description="起始单据日期（含）")] = None,
    date_to: Annotated[date | None, Query(description="结束单据日期（含）")] = None,
    style_no: Annotated[str | None, Query(max_length=32)] = None,
) -> dict[str, object]:
    """统计。**维度只有款号 × 尺码**（modules/03 §6），不自行发明别的分组。

    ⚠️ **只数 ACTIVE 码**：``VOIDED`` 既不是「已计件」也不是「未计件」（03 §3.3），
    算进任何一边都会让「手数 = 已计 + 未计」对不上。
    ⚠️ **日期按单据日期**：码本身没有日期；按入库时间算，审核分批会把同一个月裂成两段。
    ⚠️ 数据范围在 service 强制：车间主管只统计得到本车间（``workshop_id`` 传了也不能放大）。
    """
    _require(ctx, "bundling:read", "查看打菲统计")
    data = await _service(session).statistics(
        StatQuery(date_from=date_from, date_to=date_to, style_no=style_no), ctx
    )
    return ok(StatisticsOut.model_validate(data, from_attributes=True).model_dump(mode="json"))


@router.get(
    "/bundling-orders/exports",
    response_class=Response,
    summary="导出打菲单 CSV（与单据列表同一套筛选与数据范围）",
    openapi_extra={"x-permission": "bundling:export"},
)
async def export_bundling_orders(
    ctx: ContextDep,
    session: SessionDep,
    status: Annotated[str | None, Query(max_length=16)] = None,
    style_no: Annotated[str | None, Query(max_length=32)] = None,
    operation_no: Annotated[str | None, Query(max_length=16)] = None,
    color_code: Annotated[str | None, Query(max_length=16)] = None,
    doc_date_from: Annotated[date | None, Query(description="起始日期（含）")] = None,
    doc_date_to: Annotated[date | None, Query(description="结束日期（含）")] = None,
) -> Response:
    """导出打菲单（CSV，``text/csv; charset=utf-8-sig``）。

    ⚠️ **必须要求 ``bundling:export``**：导出是绕过界面直接拿数据的地方，比界面更容易
    泄露（07 §3.2 铁律 3）。数据范围与列表完全一致，**不导出范围之外的单据**。
    ⚠️ **复用列表 service**（铁律 3）：另写一条查询的话，「列表看到的」与「导出的」会不一致，
    而那只有对账时才发现。
    ⚠️ 超过单次上限 → ``11011``，**不截断**（截断出来的 CSV 会被当完整数据入账）。
    """
    _require(ctx, "bundling:export", "导出打菲单")
    query = BundlingOrderListQuery(
        status=status,
        style_no=style_no,
        operation_no=operation_no,
        color_code=color_code,
        doc_date_from=doc_date_from,
        doc_date_to=doc_date_to,
    )
    return _csv_response(await _service(session).export_orders(query, ctx), "bundling-orders")


# ====================================================================== 拆分预演


@router.post(
    "/bundling-orders/{order_id}/split",
    response_model=ApiResponse[SplitPreviewOut],
    summary="拆分预演（★ 只读不落库：这张单会生成哪些码、各几件、余数多少）",
    openapi_extra={"x-permission": "bundling:read"},
)
async def preview_bundling_split(
    order_id: OrderId,
    ctx: ContextDep,
    session: SessionDep,
    payload: SplitPreviewIn | None = None,
) -> dict[str, object]:
    """预演。**不传 body = 用当前单据的行**（modules/03 §5.5）。

    ⚠️ **只读**：不写库、不预占、不写日志。所以权限点是 ``bundling:read`` 而不是
    ``bundling:approve`` —— 它不改任何东西。
    ⚠️ **预演结果不作为审核依据**：审核事务内**重算一遍**并跑 §5.3 的四条断言（TOCTOU）。
    ⚠️ ``conflicts[]`` **返回而不报错**：那是给主管看的清单（手序号与库内已有 ACTIVE 码冲突），
    真正拦截发生在 submit / approve（那里有唯一索引兜底 → ``31005``）。
    """
    _require(ctx, "bundling:read", "预演打菲拆分")
    plan = await _service(session).preview_split(
        order_id, ctx, lines=payload.lines if payload is not None else None
    )
    # ⚠️ 逐列显式映射：``OrderSplitPreview.lines`` ↔ 出参 ``previews``（03 §5.5 的字段名），
    #    用**同一个名字**才是让后来的人以为「对不上是 bug」的那种不一致。
    return ok(
        SplitPreviewOut(
            previews=list(plan.lines),
            hands_total=plan.hands_total,
            planned_qty=plan.planned_qty,
            balance_qty=plan.balance_qty,
            conflicts=list(plan.conflicts),
        ).model_dump(mode="json")
    )


# ====================================================================== 标签


@router.get(
    "/bundling-orders/{order_id}/labels",
    response_model=ApiResponse[LabelExportOut],
    summary="标签导出（一手一张：款号/色/码/工序/第 N 手 / 共 M 手/该手件数/二维码）",
    openapi_extra={"x-permission": "bundling:print"},
)
async def export_labels(
    order_id: OrderId,
    ctx: ContextDep,
    session: SessionDep,
    from_hands: Annotated[int | None, Query(ge=1, description="起始手号（含，每尺码各自）")] = None,
    to_hands: Annotated[int | None, Query(ge=1, description="结束手号（含，每尺码各自）")] = None,
    size_code: Annotated[str | None, Query(max_length=16)] = None,
    export_format: Annotated[
        LabelExportFormat, Query(alias="format", description="data = 数据数组；csv = 带 csv_text")
    ] = LabelExportFormat.DATA,
) -> dict[str, object]:
    """标签导出。

    ⚠️ **必须单已 ``APPROVED``**（``30001``）：没码就没标签；反审核后码全 ``VOIDED``，
    拿不到码时返回空数组会让人误判「这单没有手」（03 §6 第五批修订第 ② 条）。
    ⚠️ **手号区间在每个尺码内各自成立**（Q-B13）：``from_hands=1&to_hands=2`` 命中的是
    每个尺码的 1、2 手，不是全单的前两手。
    ⚠️ ``format=csv`` 时**不返回文件**而是把 ``csv_text`` 放在响应里：标签软件导入走前端
    一次请求，而「下载文件」会把浏览器的中文文件名与 BOM 处理引进来。
    ⚠️ 单次手数上限 1 万，超限 ``10001``。
    """
    _require(ctx, "bundling:print", "导出打菲标签")
    export = await _service(session).export_labels(
        order_id,
        ctx,
        from_hands=from_hands,
        to_hands=to_hands,
        size_code=size_code,
        export_format=export_format,
    )
    return ok(LabelExportOut.model_validate(export).model_dump(mode="json"))


@router.post(
    "/bundling-orders/{order_id}/label-prints",
    response_model=ApiResponse[LabelPrintOut],
    summary="批量打印登记（★ 一手一行留痕；重打必带 print_seq；支持 Idempotency-Key）",
    openapi_extra={"x-permission": "bundling:print", "x-idempotency-key": True},
)
async def register_label_prints(
    order_id: OrderId,
    request: Request,
    ctx: ContextDep,
    session: SessionDep,
    payload: LabelPrintIn,
) -> dict[str, object]:
    """打印登记。**只写痕迹**，不生成码、不改单据状态。

    ⚠️ 与「标签导出」**不合并**（03 §6）：实物标签是**先印后核**的 —— 导出 5 手可能只印了
    3 手（卡纸 / 只补两手），合并之后「导出了 5 手」会被记成「打印了 5 手」，
    而车间实物只有 3 张：留痕一旦与实物不符，它就不再是对账证据。
    ⚠️ ``hands_seq`` 必传（缺失 → ``10002``）、``size_code`` 必传口径是「每尺码各自编号」，
    重打必带 ``print_seq``（否则与首次打印无从区分 → ``10008``）。
    ⚠️ ``Idempotency-Key``：同键同 body → 200 + 首次结果；同键不同 body → ``10002``（05 §5）。
    """
    _require(ctx, "bundling:print", "登记打菲标签打印")
    idempotent = await load_idempotent(request)
    if idempotent is not None and idempotent.cached is not None:
        cached = idempotent.cached.get("response")
        if isinstance(cached, dict):
            return dict(cached)
    result = await _service(session).register_print(
        order_id,
        ctx.user_id,
        ctx,
        hands_seq=payload.hands_seq,
        size_code=payload.size_code,
        hands_total_of_size=payload.hands_total_of_size,
        from_hands=payload.from_hands,
        to_hands=payload.to_hands,
        printed_qty=payload.printed_qty,
        is_reprint=payload.is_reprint,
        print_seq=payload.print_seq,
        idempotency_key=idempotent.key if idempotent is not None else None,
    )
    response = ok(LabelPrintOut.model_validate(result).model_dump(mode="json"))
    if idempotent is not None:
        await store_idempotent(idempotent, response)
    return response


# ====================================================================== 码查询 / 单码作废


@router.get(
    "/bundles",
    response_model=ApiResponse[PageData[BundleListOut]],
    summary="打菲码列表（一码一手：第 N 手 / 共 M 手 / 件数 / 计件状态）",
    openapi_extra={"x-permission": "bundling:read"},
)
async def list_bundles(
    ctx: ContextDep,
    session: SessionDep,
    bundle_no: Annotated[str | None, Query(max_length=64, description="前缀搜索")] = None,
    style_no: Annotated[str | None, Query(max_length=32)] = None,
    color_code: Annotated[str | None, Query(max_length=16)] = None,
    size_code: Annotated[str | None, Query(max_length=16)] = None,
    operation_no: Annotated[str | None, Query(max_length=16)] = None,
    status: Annotated[str | None, Query(max_length=16, description="ACTIVE / VOIDED")] = None,
    counted: Annotated[bool | None, Query(description="按已计件过滤")] = None,
    hands: Annotated[int | None, Query(ge=1, description="手序号（第 N 手）")] = None,
    doc_id: UUID | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=200)] = 20,
) -> dict[str, object]:
    """码列表。

    ⚠️ **数据范围经 ``bundling_orders.workshop_id`` 回查**（``bundles`` 没有车间列）：
    service 的 ``apply_data_scope`` 带 ``via`` 强制注入，越权的码压根不进结果集 ——
    「返回空列表」而不是报错，正是车间主管看到别人车间时应有的表现。
    ⚠️ 每行带**共 M 手**：列表页要直接显示「第 N 手 / 共 M 手」（03 §11.5.2）。
    ⚠️ ``bundle_no`` 是**前缀**搜索：扫码枪输错一位时给候选，而不是「查无此码」。
    """
    _require(ctx, "bundling:read", "查看打菲码")
    query = BundleListQuery(
        bundle_no=bundle_no,
        style_no=style_no,
        color_code=color_code,
        size_code=size_code,
        operation_no=operation_no,
        status=status,
        counted=counted,
        hands=hands,
        doc_id=doc_id,
        page=page,
        size=size,
    )
    rows, total = await _service(session).list_bundles(query, ctx)
    return page_ok(
        [BundleListOut.model_validate(row).model_dump(mode="json") for row in rows],
        total,
        page,
        size,
    )


@router.get(
    "/bundles/{bundle_no}",
    response_model=ApiResponse[BundleDetailOut],
    summary="单码详情（★ 手号/共 M 手/该手件数 + 来源缸号匹号 + 计件 + 打印记录）",
    openapi_extra={"x-permission": "bundling:read"},
)
async def get_bundle(
    bundle_no: Annotated[str, Path(min_length=1, max_length=64, description="打菲号")],
    ctx: ContextDep,
    session: SessionDep,
) -> dict[str, object]:
    """单码详情。

    ⚠️ **越权 → ``12002``，查无此码 → ``31001``**（两个码不能混）：车间主管拿别人的码
    只会看到「查无此码」的话，会去核对码有没有输错，而真正的原因是权限。
    ⚠️ ``cutting_size_line_id`` + ``dye_lot_no`` / ``bolt_no`` 是**回查裁剪来源**：
    「这手是从哪缸哪匹布裁出来的」是车间与跟单最高频的追溯问句。
    ⚠️ 响应体可直接显示「XL 第 2 手 · 60 件」（ADR-0016 §6 工位机反馈）。
    """
    _require(ctx, "bundling:read", "查看打菲码")
    row = await _service(session).get_bundle(bundle_no, ctx)
    return ok(BundleDetailOut.model_validate(row).model_dump(mode="json"))


@router.post(
    "/bundles/{bundle_no}/voids",
    response_model=ApiResponse[VoidCodeOut],
    summary="作废单码（★ 行保留不可恢复；已计件 → 32003；不动裁剪结转）",
    openapi_extra={"x-permission": "bundling:code:void", "x-idempotency-key": True},
)
async def void_bundle_code(
    bundle_no: Annotated[str, Path(min_length=1, max_length=64)],
    request: Request,
    ctx: ContextDep,
    session: SessionDep,
    payload: VoidCodeIn,
) -> dict[str, object]:
    """作废**一个**码（``void_reason`` 必填）。

    ⚠️ **单码作废 ≠ 整单反审核**：它**不碰** ``cutting_outputs`` 结转，也**不改**单据状态。
    结转只随整单 ``approve`` / ``reverse`` 动（见 service 的对照表）—— 混淆两者的后果是
    「作废 10 手之后结转少 10 件」，而没有任何表能验出这个差。
    ⚠️ **已计件 → ``32003``**：先在计件模块红冲 + 补录（判定复用反审核那一处，P2 建表后只改一处）。
    ⚠️ **行保留**（B12 不可恢复）：码仍在库里、仍占着手号，仍能被扫码枪查到并回报
    「已作废（31002）」。
    ⚠️ ``Idempotency-Key``：重复点击只生效一次，第二次返回**首次结果**（05 §5）——
    没有它，第二次会撞 ``31002``，而连点不该看到错误。
    """
    _require(ctx, "bundling:code:void", "作废打菲码")
    idempotent = await load_idempotent(request)
    if idempotent is not None and idempotent.cached is not None:
        cached = idempotent.cached.get("response")
        if isinstance(cached, dict):
            return dict(cached)
    result = await _service(session).void_code(
        bundle_no,
        payload.void_reason,
        ctx.user_id,
        ctx,
        idempotency_key=idempotent.key if idempotent is not None else None,
    )
    response = ok(VoidCodeOut.model_validate(result).model_dump(mode="json"))
    if idempotent is not None:
        await store_idempotent(idempotent, response)
    return response
