"""工序单价设价 / 调价 / 取价 / 历史（原 service.py 2173-2537）。"""

from collections.abc import Sequence
from datetime import date
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import unit_of_work
from app.core.errors import BusinessError, ErrorCode
from app.core.numbering import normalize_style_no
from app.core.permissions import AuthContext
from app.modules.base.models import OperationRate, ProductCategory, Style
from app.modules.base.schemas import (
    OperationRateCreate,
    OperationRateOut,
    OperationRateSetOut,
    RateResolveOut,
)

from .common import (
    ACTION_UPDATE,
    DOC_OPERATION_RATE,
    SCALE_UNIT_PRICE,
    numeric_str,
    write_document_log,
)
from .rate_helpers import build_rate_resolve_stmt, rate_out, rate_source_of
from .rate_query_mixin import RateQueryMixin
from .style_service import StyleService


class RateService(RateQueryMixin):
    """工序单价：设价 / 调价 / 取价 / 历史（ADR-0026）。"""

    def __init__(self, session: AsyncSession, ctx: AuthContext) -> None:
        self.session = session
        self.ctx = ctx

    async def resolve(self, style_no: str, operation_no: str, work_date: date) -> RateResolveOut:
        """取价预演。**只读不写库**（modules/01 §6 末条）。

        SQL 见 :func:`build_rate_resolve_stmt`（ADR-0026 §2，勿改）。
        """
        code = normalize_style_no(style_no)
        style = await self._require_style(code)
        stmt = build_rate_resolve_stmt(
            operation_no=operation_no,
            style_no=code,
            category_id=style.category_id,
            work_date=work_date,
        )
        hit = (await self.session.execute(stmt)).scalar_one_or_none()
        if hit is None:
            raise BusinessError(
                ErrorCode.OPERATION_RATE_NOT_SET,
                f"款号 {code} 的工序 {operation_no} 在 {work_date.isoformat()} 没有生效单价，请先设价",
                details={
                    "style_no": code,
                    "operation_no": operation_no,
                    "work_date": work_date.isoformat(),
                },
            )
        return RateResolveOut(
            style_no=code,
            operation_no=operation_no,
            work_date=work_date,
            unit_price=numeric_str(hit.unit_price, SCALE_UNIT_PRICE),
            effective_from=hit.effective_from,
            effective_to=hit.effective_to,
            rate_source=rate_source_of(hit),
            product_category_id=hit.product_category_id,
        )

    async def _require_style(self, style_no: str) -> Style:
        """款号必须存在**且在数据范围内**。

        走 :meth:`StyleService.get_required`（先查、再 ``assert_in_scope``）而不是
        「带数据范围过滤的查询」：后者会把"款号存在但不属于我"与"款号根本不存在"
        一起变成 ``20001``，前端于是提示"款号不存在"，用户去列表里找不到就更加
        困惑。口径必须与款号详情一致：越权 ``12002``、不存在 ``20001``。
        """
        return await StyleService(self.session, self.ctx).get_required(style_no)

    # -------------------------------------------------------------- 写

    async def set_rate(self, payload: OperationRateCreate) -> OperationRateSetOut:
        """设价（首次）与调价（关旧区间 + 插新区间）。

        完整口径：

        1. **档位互斥**：``style_no`` 与 ``product_category_id`` 不能同时给
           （Schema 已拦，这里再兜一层防绕过直调）。两者都空 = 档位 3 全厂统一价。
        2. **同一生效日已有行 → ``20002``**：追加式模型下"改今天的价"必须换一个
           生效日，禁 ``UPDATE unit_price``（R11 / INV-P0-3）。
        3. **区间重叠 → ``20005`` + ``details``**（R18）。唯一能重叠的形态是
           "旧的当前档 + 新区间从它中间开始"，那就是正常调价 → 关掉旧档。
        4. **调价必填 ``reason`` → ``10006``**（R20）。首次设价可空。
        5. 区间行按 ``effective_from`` 排序 ``FOR UPDATE``，防并发插出两条
           ``effective_to IS NULL``（CC-4）。
        """
        operation_no = payload.operation_no
        style_no = normalize_style_no(payload.style_no) if payload.style_no else None
        if style_no is not None and payload.product_category_id is not None:
            raise BusinessError(
                ErrorCode.PARAM_INVALID,
                "style_no 与 product_category_id 不能同时给：一个价要么限款号、要么限分类",
                details={"style_no": style_no},
            )
        style = await self._resolve_target_style(style_no, payload.product_category_id)

        async with unit_of_work(self.session):
            existing = list(
                (
                    await self.session.execute(
                        select(OperationRate)
                        .where(
                            OperationRate.operation_no == operation_no,
                            OperationRate.style_no.is_(None)
                            if style_no is None
                            else OperationRate.style_no == style_no,
                            OperationRate.product_category_id.is_(None)
                            if payload.product_category_id is None
                            else OperationRate.product_category_id == payload.product_category_id,
                            OperationRate.deleted_at.is_(None),
                        )
                        .order_by(OperationRate.effective_from)
                        .with_for_update()
                    )
                )
                .scalars()
                .all()
            )
            closed = await self._reconcile_intervals(existing, payload)
            if closed and not (payload.reason or "").strip():
                raise BusinessError(
                    ErrorCode.REASON_REQUIRED,
                    "调价必须填写原因（reason），说明为什么调",
                    details={"operation_no": operation_no, "style_no": style_no},
                )
            row = OperationRate(
                operation_no=operation_no,
                style_id=style.id if style else None,
                style_no=style_no,
                product_category_id=payload.product_category_id,
                effective_from=payload.effective_from,
                effective_to=payload.effective_to,
                unit_price=payload.unit_price,
                reason=payload.reason,
                created_by=self.ctx.user_id,
                updated_by=self.ctx.user_id,
            )
            self.session.add(row)
            try:
                await self.session.flush()
            except IntegrityError as exc:
                # 并发下两个请求同生效日插入（CC-4）：``uq_operation_rates`` 兜底
                raise BusinessError(
                    ErrorCode.STYLE_ALREADY_EXISTS,
                    "该生效日已有一条单价记录，可能刚被他人调整，请刷新后重试",
                    details={
                        "style_no": style_no,
                        "operation_no": operation_no,
                        "effective_from": payload.effective_from.isoformat(),
                    },
                ) from exc
            log_id = await write_document_log(
                self.session,
                self.ctx,
                doc_type=DOC_OPERATION_RATE,
                doc_id=row.id,
                doc_no=f"{style_no or 'ALL'}/{operation_no}",
                action=ACTION_UPDATE,
                reason=payload.reason or "首次设价",
                changed_fields={
                    "rate_source": rate_source_of(row).value,
                    "style_no": style_no,
                    "product_category_id": str(payload.product_category_id or ""),
                    "operation_no": operation_no,
                    "effective_from": payload.effective_from.isoformat(),
                    "effective_to": payload.effective_to.isoformat()
                    if payload.effective_to
                    else None,
                    "unit_price": str(payload.unit_price),
                    "closed": [
                        {
                            "effective_from": item.effective_from.isoformat(),
                            "effective_to": payload.effective_from.isoformat(),
                            "unit_price": str(item.unit_price),
                        }
                        for item in closed
                    ],
                },
            )
        # ⚠️ ``closed`` 里的行刚被 UPDATE 过：``updated_at`` 由数据库算出、
        #    被标记为 expired，此时读属性就是隐式懒加载（AsyncSession 下抛
        #    MissingGreenlet）。所以重查一次再拼响应 —— 也顺带让回显的
        #    ``effective_to`` 是真正落库的值
        closed_out: list[OperationRateOut] = []
        if closed:
            rows = (
                (
                    await self.session.execute(
                        select(OperationRate)
                        .where(OperationRate.id.in_([item.id for item in closed]))
                        .order_by(OperationRate.effective_from)
                    )
                )
                .scalars()
                .all()
            )
            closed_out = [rate_out(item) for item in rows]
        return OperationRateSetOut(
            rate=rate_out(row),
            closed_rates=closed_out,
            document_log_id=log_id,
        )

    async def _reconcile_intervals(
        self, existing: Sequence[OperationRate], payload: OperationRateCreate
    ) -> list[OperationRate]:
        """区间重叠判定 + 关旧区间；返回本次被关闭的行。**必须在事务内调用**。

        "先查区间再插入"**不足以防重**（docs/03 §1.5）：加锁由调用方的
        ``SELECT ... FOR UPDATE`` 提供，唯一性最后由 ``uq_operation_rates`` 兜底。
        """
        new_from = payload.effective_from
        new_to = payload.effective_to
        same_day = [row for row in existing if row.effective_from == new_from]
        if same_day:
            raise BusinessError(
                ErrorCode.STYLE_ALREADY_EXISTS,
                f"工序 {payload.operation_no} 在 {new_from.isoformat()} 已有单价记录；"
                "单价只追加不修改，请换一个生效日",
                details={
                    "operation_no": payload.operation_no,
                    "effective_from": new_from.isoformat(),
                    "existing_unit_price": str(same_day[0].unit_price),
                },
            )
        overlapping: list[OperationRate] = []
        for row in existing:
            # 半开区间 ``[f, t)`` 相交判定：新区间完全在既有区间之前，或完全在其后
            before = new_to is not None and row.effective_from >= new_to
            after = row.effective_to is not None and row.effective_to <= new_from
            if before or after:
                continue
            overlapping.append(row)

        # 唯一可关的形态：既有的"当前档"（effective_to IS NULL）且起点早于新区间
        closable = [
            row for row in overlapping if row.effective_to is None and row.effective_from < new_from
        ]
        closable_ids = {row.id for row in closable}
        conflicting = [row for row in overlapping if row.id not in closable_ids]
        if conflicting:
            raise BusinessError(
                ErrorCode.RATE_RANGE_OVERLAP,
                f"工序 {payload.operation_no} 的生效区间与既有区间重叠，请调整生效日",
                details={
                    "operation_no": payload.operation_no,
                    "new_interval": {
                        "effective_from": new_from.isoformat(),
                        "effective_to": new_to.isoformat() if new_to else None,
                    },
                    "conflicts": [
                        {
                            "effective_from": row.effective_from.isoformat(),
                            "effective_to": row.effective_to.isoformat()
                            if row.effective_to
                            else None,
                            "unit_price": str(row.unit_price),
                        }
                        for row in conflicting
                    ],
                },
            )
        for row in closable:
            await self.session.execute(
                update(OperationRate)
                .where(OperationRate.id == row.id, OperationRate.effective_to.is_(None))
                .values(
                    effective_to=new_from,
                    version=OperationRate.version + 1,
                    updated_by=self.ctx.user_id,
                )
                .execution_options(synchronize_session=False)
            )
            # 同步内存态：``synchronize_session=False`` 不会刷新 ORM 对象，
            # 而响应里的 ``closed_rates`` 直接读它 —— 不同步就会回显旧区间
            row.effective_to = new_from
        await self.session.flush()
        return closable

    async def _resolve_target_style(
        self, style_no: str | None, category_id: UUID | None
    ) -> Style | None:
        """校验档位目标存在（款号 / 分类），并校验款号数据范围。"""
        if style_no is not None:
            return await self._require_style(style_no)
        if category_id is not None:
            found = (
                await self.session.execute(
                    select(ProductCategory.id).where(
                        ProductCategory.id == category_id,
                        ProductCategory.deleted_at.is_(None),
                    )
                )
            ).first()
            if found is None:
                raise BusinessError(
                    ErrorCode.BASE_DATA_NOT_FOUND,
                    "商品分类不存在或已删除",
                    details={"product_category_id": str(category_id)},
                )
        return None
