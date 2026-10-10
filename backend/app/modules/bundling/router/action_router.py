"""打菲单的**六个状态动作**（08 §1.1 通用动作表 + 08 §2.2 打菲）。

| 动作 | 端点 | 迁移 | 权限点 |
| --- | --- | --- | --- |
| submit | ``/submissions`` | DRAFT/REJECTED → SUBMITTED | ``bundling:submit`` |
| approve | ``/approvals`` | SUBMITTED → APPROVED | ``bundling:approve`` |
| reject | ``/rejections`` | SUBMITTED → REJECTED | ``bundling:reject`` |
| withdraw | ``/withdrawals`` | SUBMITTED → DRAFT | ``bundling:withdraw`` |
| reverse | ``/reversals`` | APPROVED → SUBMITTED | ``bundling:reverse`` |
| cancel | ``/cancellations`` | DRAFT/REJECTED → CANCELLED | ``bundling:cancel`` |

外加一条**不改状态**的写动作（ADR-0033 / 03 §4.1）：``hand-increment``
（``/hand-increments``，``APPROVED`` → ``APPROVED``，``bundling:update``）。

⚠️ **这里只做「HTTP → service」的一层壳**：前置校验、副作用、乐观锁、写日志全在 service 的
状态迁移方法里（``_assert_transition`` / ``_apply_status``）。Router 里再写一遍「状态机」，
就会出现两套判定，而两套判定迟早对不上（T-CUT-001b-3 的教训）。

⚠️ **六个动作的响应都带新的 ``version``**：前端连点两个动作时，第二个必须带第一个返回的
版本号，否则会撞 ``10003``（这个错误是**保护**不是麻烦 —— 它挡住的正是「用旧版本号
把别人刚做的迁移覆盖掉」）。

⚠️ **``withdraw`` 用 ``bundling:withdraw``**（07 §2.2 已登记的权限点，docs/12 L-092 ② 在
本卡定案）：08 §1.1 动作表那一列写的是 ``<mod>:update``，与 07 §2.2 矛盾。取「权限点
总表」的口径 —— 否则 ``bundling:withdraw`` 是个**永不生效**的点（角色被授了它也不会
多出任何能力，而审计时看到它会以为撤回有独立权限）。
"""

from fastapi import APIRouter, Request

from app.core.idempotency import load_idempotent, mark_idempotent_failed, store_idempotent
from app.core.responses import ApiResponse
from app.modules.bundling.schemas import (
    ApproveIn,
    BundlingOrderOut,
    CancelIn,
    HandIncrementIn,
    RejectIn,
    ReverseIn,
)

from .deps import (
    BUNDLING_TAGS,
    ContextDep,
    OrderId,
    SessionDep,
    _order_payload,
    _require,
    _service,
)

router = APIRouter(tags=BUNDLING_TAGS)


# ====================================================================== 提交 / 审核


@router.post(
    "/bundling-orders/{order_id}/submissions",
    response_model=ApiResponse[BundlingOrderOut],
    summary="提交（DRAFT/REJECTED → SUBMITTED，预占裁剪可用量）",
    openapi_extra={"x-permission": "bundling:submit"},
)
async def submit_bundling_order(
    order_id: OrderId,
    ctx: ContextDep,
    session: SessionDep,
) -> dict[str, object]:
    """提交。**无请求体**。

    ⚠️ 提交会**预占** ``cutting_outputs.reserved_qty``（条件 UPDATE 保证不超发），驳回 /
    撤回 / 作废各自释放 —— 成对副作用，漏一条就永久占着裁剪余量。
    ⚠️ **重复提交不幂等**（报 ``30001``）：``SUBMITTED → SUBMITTED`` 不在迁移表里，
    而本动作**不生成码**，所以没有「重试会多生成一批码」的风险，不需要幂等键。
    """
    _require(ctx, "bundling:submit", "提交打菲单")
    return _order_payload(await _service(session).submit(order_id, ctx.user_id, ctx))


@router.post(
    "/bundling-orders/{order_id}/approvals",
    response_model=ApiResponse[BundlingOrderOut],
    summary="审核（SUBMITTED → APPROVED，★ 按手批量生成打菲码；支持 Idempotency-Key）",
    openapi_extra={"x-permission": "bundling:approve", "x-idempotency-key": True},
)
async def approve_bundling_order(
    order_id: OrderId,
    request: Request,
    ctx: ContextDep,
    session: SessionDep,
    payload: ApproveIn | None = None,
) -> dict[str, object]:
    """审核（``SUBMITTED`` → ``APPROVED``）。**03 §4.1 六步，顺序不可调换**：

    ```
    ① 按手生成 bundle_no（一码一手，手序号内嵌到码里）—— 展开在 service 的纯函数里
    ② 每手件数 = 裁剪尺码明细的 qty_per_hand（直接取，不 floor；Q-B15）
    ③ 打菲手数不得超打裁剪手数，否则 31004（少打允许，B21）
    ④ 手序号重复 → 31005（预检 + DB 唯一索引双层）
    ⑤ UNIQUE(doc_id, color_code, size_code, hands) 兜底
    ⑥ 更新裁剪结转：bundled_qty += 本单件数，reserved_qty 释放
    ```

    ⚠️ **「码数 = 手数」断言（§5.3 四条之一）**：审核事务内比对
    ``count(bundles WHERE doc_id=?) == Σ明细 hands``，不等即整事务回滚 ——
    「单据写 2000 手、码只有 1998 个」这种半成品**不许出现**。

    ⚠️ **批量生成用 ``INSERT ... SELECT ... generate_series()``，禁止逐条 INSERT**：
    语句数 = 明细行数，与手数无关；2000 手逐条插就是 2000 次往返，行锁在 2C VPS 上会到秒级。

    ⚠️ **制单人 ≠ 审核人**（``10005``），且不接受前端传入 ``hands`` / ``bundle_qty``。

    ⚠️ **``Idempotency-Key``**（``05 §5``）：同键同 body → HTTP 200 且返回**首次结果**
    （不报错）；同键不同 body → ``10002``。不传也能安全重入 —— 状态机保证第二次审核报
    ``30001``，不会生成第二批码。
    """
    _require(ctx, "bundling:approve", "审核打菲单")
    idempotent = await load_idempotent(request)
    if idempotent is not None and idempotent.cached is not None:
        # ⚠️ 取的是 ``cached["response"]``，**不是** ``cached`` 本身：缓存里存的是
        # ``{"body_hash":…, "response":…}``，直接把整个缓存当响应体返回的话，
        # FastAPI 会把它按 ApiResponse 校验 → ``data`` 变成 null，客户端拿到
        # 「审核成功但没有数据」，而码已经生成了（照 auth/router.py 的写法）。
        cached_response = idempotent.cached.get("response")
        if isinstance(cached_response, dict):
            return dict(cached_response)
    try:
        order = await _service(session).approve(
            order_id,
            ctx.user_id,
            ctx,
            remark=payload.remark if payload is not None else None,
            idempotency_key=idempotent.key if idempotent is not None else None,
        )
        response = _order_payload(order)
        if idempotent is not None:
            await store_idempotent(idempotent, response)
        return response
    except Exception:
        if idempotent is not None:
            await mark_idempotent_failed(idempotent)
        raise


# ====================================================================== 增手（不改状态）


@router.post(
    "/bundling-orders/{order_id}/hand-increments",
    response_model=ApiResponse[BundlingOrderOut],
    summary="审核后增手（APPROVED → 同，★ 只补生成新码；**减手请走 /reversals**）",
    openapi_extra={"x-permission": "bundling:update"},
)
async def increment_bundling_order_hands(
    order_id: OrderId,
    payload: HandIncrementIn,
    ctx: ContextDep,
    session: SessionDep,
) -> dict[str, object]:
    """审核后**增手**（落 ADR-0033）。**只加不减**：``delta_hands`` 必须 ``> 0``。

    副作用（与状态变更同事务，08 R4）：① ``hands_total += delta``（**只加，不重算**）；
    ② **只生成新增那几手的码**（手号从 ``N+1`` 起，复用审核的 ``generate_series``
    批量路径）；③ ``cutting_outputs.bundled_qty += delta × qty_per_hand``（**不碰**
    ``reserved_qty``）；④ 写日志（改前改后 + **新增手号区间**）。

    ⚠️ **入参结构上只容得下这一个动作**：``{size_code | line_id, delta_hands, version}``，
    不接受 ``hands`` 全量替换 / ``color_code`` / ``cutting_size_line_id`` 或任何其它字段
    （``extra="forbid"``）—— 「借增手之名改别的」不是靠校验拦住的，是类型上表达不出来。
    ⚠️ **减手一律走 ``reversals``**：减手必然要废掉超出新 N 的那几手码，而码**不可恢复**
    （B12）；``reverse`` 已经把「未打印软删重建 / 已打印置 ``VOIDED``（``31007``）」做对了。
    ⚠️ **重跑防超打**：``hands ≤ 裁剪可打手数`` 是审核时验的，裁剪侧可能在这中间改了产量，
    所以增手必须再验一遍 → ``31004``（**不是**自动成立）。

    ⚠️ **响应带新的 ``version``**：并发增手时后一个请求必须带前一个返回的版本号，否则撞
    ``10003``（刷新后重试即可 —— 增手是纯增量，重试不会多打一手）。
    """
    _require(ctx, "bundling:update", "给已审核的打菲单增手")
    return _order_payload(
        await _service(session).increment_hands(order_id, payload, ctx.user_id, ctx)
    )


# ====================================================================== 驳回 / 撤回


@router.post(
    "/bundling-orders/{order_id}/rejections",
    response_model=ApiResponse[BundlingOrderOut],
    summary="驳回（SUBMITTED → REJECTED，★ 必填原因 + 释放预占）",
    openapi_extra={"x-permission": "bundling:reject"},
)
async def reject_bundling_order(
    order_id: OrderId,
    payload: RejectIn,
    ctx: ContextDep,
    session: SessionDep,
) -> dict[str, object]:
    """驳回。**``reason`` 必填**（缺失 → ``10001``，全空白 → ``10002``，``03 §9``）。

    ⚠️ 释放预占与状态变更**同事务**：只改状态不释放，这张单会永久占着裁剪余量，
    而界面上它已经是「被打回的草稿」，谁都看不出余量去哪了。
    """
    _require(ctx, "bundling:reject", "驳回打菲单")
    return _order_payload(await _service(session).reject(order_id, payload, ctx.user_id, ctx))


@router.post(
    "/bundling-orders/{order_id}/withdrawals",
    response_model=ApiResponse[BundlingOrderOut],
    summary="撤回（SUBMITTED → DRAFT，★ 撤回人 = 制单人 + 释放预占）",
    openapi_extra={"x-permission": "bundling:withdraw"},
)
async def withdraw_bundling_order(
    order_id: OrderId,
    ctx: ContextDep,
    session: SessionDep,
) -> dict[str, object]:
    """撤回。**无请求体**。

    ⚠️ **只有制单人本人能撤回**（08 §1.1）：service 比 ``created_by``，不在本层判。
    ⚠️ 撤回是「我把刚才提交的单收回来」，不是「拒绝」—— 状态回到 ``DRAFT``，可以继续改。
    """
    _require(ctx, "bundling:withdraw", "撤回打菲单")
    return _order_payload(await _service(session).withdraw(order_id, ctx.user_id, ctx))


# ====================================================================== 反审核 / 作废


@router.post(
    "/bundling-orders/{order_id}/reversals",
    response_model=ApiResponse[BundlingOrderOut],
    summary="反审核（APPROVED → SUBMITTED，★ 必填原因 + 全链路反向）",
    openapi_extra={"x-permission": "bundling:reverse"},
)
async def reverse_bundling_order(
    order_id: OrderId,
    payload: ReverseIn,
    ctx: ContextDep,
    session: SessionDep,
) -> dict[str, object]:
    """反审核。**``reason`` 必填**（缺失 → ``10001``，全空白 → ``10002``）。

    ⚠️ 与审核**严格对称**：本单全部 ACTIVE 码置 ``VOIDED``（**行保留**，B12 不可恢复）、
    ``bundled_qty`` 减回、``reserved_qty`` 重新预占。漏掉任何一半，结转再也回不到原点。
    ⚠️ 已有计件的码 → ``32003``（先在计件模块红冲 + 补录）。
    """
    _require(ctx, "bundling:reverse", "反审核打菲单")
    return _order_payload(
        await _service(session).reverse(order_id, payload.reason, ctx.user_id, ctx)
    )


@router.post(
    "/bundling-orders/{order_id}/cancellations",
    response_model=ApiResponse[BundlingOrderOut],
    summary="作废（DRAFT/REJECTED → CANCELLED，终态 + 必填原因）",
    openapi_extra={"x-permission": "bundling:cancel"},
)
async def cancel_bundling_order(
    order_id: OrderId,
    payload: CancelIn,
    ctx: ContextDep,
    session: SessionDep,
) -> dict[str, object]:
    """作废。**``cancelled_reason`` 必填**（缺失 → ``10001``，全空白 → ``10002``）。

    ⚠️ **无库存副作用**：只能从 ``DRAFT`` / ``REJECTED`` 进，这两个状态下本单手里没有任何
    预占（``REJECTED`` 的预占已在驳回时释放）。已审核的单要走 ``reverse``。
    """
    _require(ctx, "bundling:cancel", "作废打菲单")
    return _order_payload(await _service(session).cancel(order_id, payload, ctx.user_id, ctx))
