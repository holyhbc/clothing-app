"""款号工序与单价模板复制（原 service.py 1876-2167）。"""

from collections.abc import Sequence
from typing import TYPE_CHECKING

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import ConflictPolicy, RateSource, TemplateCopyMode
from app.core.db import unit_of_work
from app.core.errors import BusinessError, ErrorCode
from app.core.numbering import business_today, normalize_style_no, quantize_price
from app.core.permissions import AuthContext
from app.modules.base.models import OperationRate, Style, StyleOperation
from app.modules.base.schemas import (
    CopiedPriceOut,
    OperationRateOut,
    StyleOperationOut,
    TemplateCopyIn,
    TemplateCopyOut,
)

from .common import (
    ACTION_CREATE,
    DOC_STYLE_OPERATION_COPY,
    RATIO_NOT_COPIED_MESSAGE,
    SCALE_UNIT_PRICE,
    SKIP_CATEGORY_RATE,
    SKIP_TARGET_HAS_OPERATION,
    SKIP_TARGET_HAS_RATE,
    numeric_str,
    write_document_log,
)
from .rate_helpers import rate_source_of


class StyleTemplateMixin:
    session: AsyncSession
    ctx: AuthContext

    if TYPE_CHECKING:

        async def get_required(self, style_no: str, *, for_update: bool = False) -> Style: ...

        async def _bump_aggregate(self, style: Style) -> None: ...

        async def list_style_operations(
            self,
            style_no: str,
            *,
            is_piecework: bool | None = None,
            include_inactive: bool = False,
        ) -> list[StyleOperationOut]: ...

        async def _current_rates(self, style_no: str) -> list[OperationRateOut]: ...

    # -------------------------------------------------------- 模板复制

    async def copy_template(
        self, target_style_no: str, source_style_no: str, payload: TemplateCopyIn
    ) -> TemplateCopyOut:
        """工序与单价模板复制（modules/01 §5.1 / ADR-0009 §3 / ADR-0026 §4）。

        整批**单事务**：先校验源与目标，再写工序结构，最后写单价；任一环节失败整单
        回滚 —— "复制了一半"比完全没复制更坑（用户会以为已经好了）。

        ⚠️ **档位 2（分类价）不复制**（ADR-0026 §4）：分类价是全厂口径，复制一款
        顺手把它改掉属于静默改写全厂工资口径。跳过的行进 ``skipped[]``，并固定附上
        :data:`RATIO_NOT_COPIED_MESSAGE`。
        """
        target_code = normalize_style_no(target_style_no)
        source_code = normalize_style_no(source_style_no)
        if target_code == source_code:
            raise BusinessError(
                ErrorCode.PARAM_INVALID,
                "源款号与目标款号不能是同一个",
                details={"style_no": target_code},
            )
        target = await self.get_required(target_code, for_update=True)
        source = await self.get_required(source_code)
        if not source.is_active:
            raise BusinessError(
                ErrorCode.BASE_DATA_NOT_FOUND,
                f"源款号 {source_code} 已停用，不能作为复制来源",
                details={"source_style_no": source_code},
            )

        async with unit_of_work(self.session):
            source_operations = list(
                (
                    await self.session.execute(
                        select(StyleOperation)
                        .where(
                            StyleOperation.style_no == source_code,
                            StyleOperation.deleted_at.is_(None),
                        )
                        .order_by(StyleOperation.sequence, StyleOperation.operation_no)
                    )
                )
                .scalars()
                .all()
            )
            source_rates = list(
                (
                    await self.session.execute(
                        select(OperationRate).where(
                            # 档位 1（源款号专用价）+ 档位 2 / 3（``style_no`` 为空）。
                            # 档位 2 也要读进来 —— 只有读进来才能在 ``skipped[]``
                            # 里告诉用户"分类价没复制"（ADR-0026 §4 强制要求可解释）
                            or_(
                                OperationRate.style_no == source_code,
                                OperationRate.style_no.is_(None),
                            ),
                            OperationRate.effective_to.is_(None),
                            OperationRate.deleted_at.is_(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
            result = await self._copy_structure(target, source_operations, payload)
            await self._copy_prices(target, source_rates, payload, result)
            await self._bump_aggregate(target)
            log_id = await write_document_log(
                self.session,
                self.ctx,
                doc_type=DOC_STYLE_OPERATION_COPY,
                doc_id=target.id,
                doc_no=target_code,
                action=ACTION_CREATE,
                reason="工序与单价模板复制",
                changed_fields={
                    "source_style_no": source_code,
                    "target_style_no": target_code,
                    "copy_mode": payload.copy_mode.value,
                    "conflict_policy": payload.conflict_policy.value,
                    "price_ratio": str(payload.price_ratio or ""),
                    "structure_written": result.structure_written,
                    "structure_overwritten": result.structure_overwritten,
                    "price_written": result.price_written,
                    "price_overwritten": result.price_overwritten,
                    "skipped": result.skipped,
                },
            )
        messages = [RATIO_NOT_COPIED_MESSAGE]
        if result.skipped:
            messages.append(f"已跳过 {len(result.skipped)} 项，原因见 skipped 明细")
        return TemplateCopyOut(
            target_style_no=target_code,
            source_style_no=source_code,
            copy_mode=payload.copy_mode,
            conflict_policy=payload.conflict_policy,
            structure_written=result.structure_written,
            structure_overwritten=result.structure_overwritten,
            price_written=result.price_written,
            price_overwritten=result.price_overwritten,
            skipped=result.skipped,
            prices=result.prices,
            messages=messages,
            document_log_id=log_id,
            operations=await self.list_style_operations(target_code),
            rates=await self._current_rates(target_code),
        )

    async def _copy_structure(
        self,
        target: Style,
        source_operations: Sequence[StyleOperation],
        payload: TemplateCopyIn,
    ) -> TemplateCopyOut:
        existing = {
            row.operation_no: row
            for row in (
                await self.session.execute(
                    select(StyleOperation)
                    .where(StyleOperation.style_no == target.style_no)
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        }
        conflicts = [
            {"target": item.operation_no, "reason": "目标款号已配置该工序"}
            for item in source_operations
            if item.operation_no in existing
        ]
        if conflicts and payload.conflict_policy == ConflictPolicy.ABORT:
            raise BusinessError(
                ErrorCode.PARAM_INVALID,
                "目标款号已配置部分工序，已按 ABORT 整体回滚；请改用 SKIP 或 OVERWRITE",
                details={"conflicts": conflicts},
            )
        out = TemplateCopyOut(
            target_style_no=target.style_no,
            source_style_no="",
            copy_mode=payload.copy_mode,
            conflict_policy=payload.conflict_policy,
            structure_written=0,
        )
        for item in source_operations:
            conflict = existing.get(item.operation_no)
            # MERGE 与 SKIP 对工序结构同义：保留目标已有的，只补缺的。
            # 两者只在**单价**上不同（MERGE 一律不覆盖已有价）
            if conflict is not None and payload.conflict_policy in (
                ConflictPolicy.SKIP,
                ConflictPolicy.MERGE,
            ):
                out.skipped.append(
                    {"target": item.operation_no, "reason": SKIP_TARGET_HAS_OPERATION}
                )
                continue
            if conflict is None:
                self.session.add(
                    StyleOperation(
                        style_id=target.id,
                        style_no=target.style_no,
                        operation_no=item.operation_no,
                        sequence=item.sequence,
                        bundle_qty=item.bundle_qty,
                        is_piecework=item.is_piecework,
                        is_final_operation=item.is_final_operation,
                        remark=item.remark,
                        created_by=self.ctx.user_id,
                        updated_by=self.ctx.user_id,
                    )
                )
                out.structure_written += 1
                continue
            await self.session.execute(
                update(StyleOperation)
                .where(StyleOperation.id == conflict.id)
                .values(
                    sequence=item.sequence,
                    bundle_qty=item.bundle_qty,
                    is_piecework=item.is_piecework,
                    is_final_operation=item.is_final_operation,
                    remark=item.remark,
                    version=StyleOperation.version + 1,
                    updated_by=self.ctx.user_id,
                )
                .execution_options(synchronize_session=False)
            )
            out.structure_overwritten += 1
        await self.session.flush()
        return out

    async def _copy_prices(
        self,
        target: Style,
        source_rates: Sequence[OperationRate],
        payload: TemplateCopyIn,
        out: TemplateCopyOut,
    ) -> None:
        """复制单价。**必须在事务内调用**（写 ``operation_rates``）。"""
        if payload.copy_mode == TemplateCopyMode.COPY_STRUCTURE_ONLY:
            return
        copy_date = business_today()
        ratio = payload.price_ratio
        target_open = {
            row.operation_no: row
            for row in (
                await self.session.execute(
                    select(OperationRate)
                    .where(
                        OperationRate.style_no == target.style_no,
                        OperationRate.effective_to.is_(None),
                        OperationRate.deleted_at.is_(None),
                    )
                    .order_by(OperationRate.effective_from)
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        }
        target_dates = set(
            (
                await self.session.execute(
                    select(OperationRate.effective_from).where(
                        OperationRate.style_no == target.style_no,
                        OperationRate.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in source_rates:
            source_rate = rate_source_of(row)
            if source_rate == RateSource.CATEGORY:
                # ADR-0026 §4：分类价属全厂口径，**不复制**
                out.skipped.append({"target": row.operation_no, "reason": SKIP_CATEGORY_RATE})
                continue
            conflict = target_open.get(row.operation_no)
            if conflict is not None:
                if payload.conflict_policy in (ConflictPolicy.SKIP, ConflictPolicy.MERGE):
                    out.skipped.append({"target": row.operation_no, "reason": SKIP_TARGET_HAS_RATE})
                    continue
                if copy_date in target_dates:
                    # modules/01 §7：复制与调价并发，同生效日必撞唯一索引 →
                    # 后到者拿 20002，**不允许静默覆盖**
                    raise BusinessError(
                        ErrorCode.STYLE_ALREADY_EXISTS,
                        f"款号 {target.style_no} 的工序 {row.operation_no} "
                        f"在 {copy_date.isoformat()} 已有单价记录，无法覆盖",
                        details={
                            "style_no": target.style_no,
                            "operation_no": row.operation_no,
                            "effective_from": copy_date.isoformat(),
                        },
                    )
                await self.session.execute(
                    update(OperationRate)
                    .where(OperationRate.id == conflict.id, OperationRate.effective_to.is_(None))
                    .values(
                        effective_to=copy_date,
                        version=OperationRate.version + 1,
                        updated_by=self.ctx.user_id,
                    )
                    .execution_options(synchronize_session=False)
                )
                out.price_overwritten += 1
            new_price = row.unit_price if ratio is None else quantize_price(row.unit_price * ratio)
            self.session.add(
                OperationRate(
                    operation_no=row.operation_no,
                    style_id=target.id,
                    style_no=target.style_no,
                    product_category_id=None,
                    effective_from=copy_date,
                    effective_to=None,
                    unit_price=new_price,
                    reason=f"模板复制自 {row.style_no or '全厂通用价'}（{copy_date.isoformat()}）",
                    created_by=self.ctx.user_id,
                    updated_by=self.ctx.user_id,
                )
            )
            out.price_written += 1
            out.prices.append(
                CopiedPriceOut(
                    operation_no=row.operation_no,
                    source_unit_price=numeric_str(row.unit_price, SCALE_UNIT_PRICE),
                    target_unit_price=numeric_str(new_price, SCALE_UNIT_PRICE),
                    rate_source=source_rate,
                )
            )
