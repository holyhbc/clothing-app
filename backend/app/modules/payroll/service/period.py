"""工资结算周期 service（T-PAY-003）。

设计权威：``docs/modules/05-工资.md`` §2（BR-PR-16~22）、§3.1、§4.3、§5.1、§6.1.1、
§8（S10/S11）、§9；决策依据 [ADR-0010](../../../../../docs/adr/0010-工资结算周期自定义.md)。

## 本文件负责什么

- ``generate``：**预演、不落库**，只算区间与核对数（三种 ``mode``）。
- ``create``：落库周期（标签 + 显式区间 + ``source``），区间重叠 → ``33004``。
- ``_unsettled_stats``：§8 S10 的「区间内未结算流水条数与金额合计」（BR-PR-18）。
- 状态机（``close`` / ``reopen``）在 :class:`~app.modules.payroll.service.state_mixin.PeriodStateMixin`，
  常量 / DTO / 纯助手在 :mod:`~app.modules.payroll.service.common`（ADR-0031 拆包）。

## 设计口径

三条贯穿本卡的口径（日期只在 ``generate`` 算、标签不解析、``cutting_boundary`` 的
「完成日」取 ``approved_at::date``）见 :mod:`app.modules.payroll.service.common`
的模块 docstring；本文件只保留实现。
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import DocumentStatus
from app.core.db import unit_of_work
from app.core.errors import BusinessError, ErrorCode
from app.modules.bundling.models import BundlingOrder
from app.modules.cutting.models import CuttingOrder
from app.modules.payroll.models import PayrollPeriod, PayrollPeriodStatus
from app.modules.payroll.service.common import (
    PERIOD_CODE_MAX_LENGTH,
    SOURCE_CUTTING_BOUNDARY,
    SOURCE_MANUAL,
    SOURCES,
    PeriodCreated,
    PeriodGenerateMode,
    PeriodPreview,
    month_bounds,
    parse_anchor_month,
)
from app.modules.payroll.service.state_mixin import PeriodStateMixin
from app.modules.piecework.models import PieceworkLog

logger = logging.getLogger("app.payroll")


class PayrollPeriodService(PeriodStateMixin):
    """结算周期 service。写方法的事务入口统一是 :func:`unit_of_work`（03 §1.1 第 5 条）。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ================================================================== 预演

    async def generate(
        self,
        *,
        mode: PeriodGenerateMode | str,
        anchor_month: str | None = None,
        style_no: str | None = None,
    ) -> PeriodPreview:
        """按 ``mode`` 算区间 + 核对数，**不落库**（05 §6.1.1）。

        :raises BusinessError: ``10001`` 非法 ``mode`` / 缺参 / 格式错；
            ``20001`` ``cutting_boundary`` 的款号没有已审核且有完成日的裁剪/打菲单。
        """
        mode_value = mode.value if isinstance(mode, PeriodGenerateMode) else str(mode)
        if mode_value not in {item.value for item in PeriodGenerateMode}:
            raise BusinessError(
                ErrorCode.PARAM_INVALID,
                f"不支持的周期生成模式：{mode_value}",
                details={"mode": mode_value, "allowed": [i.value for i in PeriodGenerateMode]},
            )

        if mode_value == PeriodGenerateMode.MONTH.value:
            year, month = parse_anchor_month(anchor_month)
            start_date, end_date = month_bounds(year, month)
            period_code = f"{year:04d}-{month:02d}"
            source = SOURCE_MANUAL
        elif mode_value == PeriodGenerateMode.BIMONTH.value:
            year, month = parse_anchor_month(anchor_month)
            next_month = month % 12 + 1
            next_year = year + 1 if month == 12 else year
            start_date = date(year, month, 1)
            _, end_date = month_bounds(next_year, next_month)
            period_code = f"{year:04d}-{month:02d}~{next_month:02d}"
            source = SOURCE_MANUAL
        else:  # cutting_boundary
            start_date, end_date = await self._cutting_boundary_range(style_no)
            period_code = f"裁剪{start_date:%m%d}-{end_date:%m%d}"
            source = SOURCE_CUTTING_BOUNDARY

        count, amount = await self._unsettled_stats(start_date, end_date)
        return PeriodPreview(
            period_code=period_code,
            start_date=start_date,
            end_date=end_date,
            source=source,
            unsettled_log_count=count,
            unsettled_amount=amount,
        )

    async def _cutting_boundary_range(self, style_no: str | None) -> tuple[date, date]:
        """取该款**裁剪单最早完成日 ~ 打菲单最晚完成日**（见 common docstring ③ / Q-PL11）。

        「完成日」= ``approved_at::date``，且仅取 ``status='APPROVED'`` 的行；任一空 → ``20001``。
        """
        if not style_no or not style_no.strip():
            raise BusinessError(ErrorCode.PARAM_INVALID, "该模式必须提供 style_no（款号）")
        code = style_no.strip()
        cutting_first: Any = (
            await self.session.execute(
                select(func.min(CuttingOrder.approved_at)).where(
                    CuttingOrder.style_no == code,
                    CuttingOrder.status == DocumentStatus.APPROVED,
                    CuttingOrder.approved_at.is_not(None),
                    CuttingOrder.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        bundling_last: Any = (
            await self.session.execute(
                select(func.max(BundlingOrder.approved_at)).where(
                    BundlingOrder.style_no == code,
                    BundlingOrder.status == DocumentStatus.APPROVED,
                    BundlingOrder.approved_at.is_not(None),
                    BundlingOrder.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if cutting_first is None or bundling_last is None:
            raise BusinessError(
                ErrorCode.BASE_DATA_NOT_FOUND,
                f"款号 {code} 没有已审核且有完成日期的裁剪单或打菲单，无法按完成日截取周期",
                details={"style_no": code},
            )
        start_date = cutting_first.date()
        end_date = bundling_last.date()
        if end_date < start_date:
            raise BusinessError(
                ErrorCode.PARAM_INVALID,
                f"款号 {code} 的打菲完成日早于裁剪完成日，无法构成合法区间",
                details={"style_no": code},
            )
        return start_date, end_date

    # ================================================================== 落库

    async def create(
        self,
        *,
        period_code: str,
        start_date: date,
        end_date: date,
        operator_id: UUID,
        source: str = SOURCE_MANUAL,
    ) -> PeriodCreated:
        """落库周期（05 §6.1.1）。区间与既有周期**部分重叠**（含完全相同）→ ``33004``。

        ``period_code`` 重复由 ``uq_payroll_periods_code`` 从库层面兜底，同样翻译为
        ``33004``（05 §9 该码的触发场景含「``period_code`` 重复」）。

        :raises BusinessError: ``10001`` 参数非法；``33004`` 区间重叠 / 标签重复。
        """
        code = (period_code or "").strip()
        if not code:
            raise BusinessError(ErrorCode.PARAM_INVALID, "period_code 不能为空")
        if len(code) > PERIOD_CODE_MAX_LENGTH:
            raise BusinessError(
                ErrorCode.PARAM_INVALID,
                f"period_code 最长 {PERIOD_CODE_MAX_LENGTH} 个字符",
                details={"length": len(code)},
            )
        if end_date < start_date:
            raise BusinessError(
                ErrorCode.PARAM_INVALID,
                "end_date 不能早于 start_date",
                details={"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
            )
        if source not in SOURCES:
            raise BusinessError(
                ErrorCode.PARAM_INVALID,
                f"source 取值非法：{source}",
                details={"source": source, "allowed": sorted(SOURCES)},
            )

        async with unit_of_work(self.session):
            await self._assert_no_overlap(start_date, end_date)
            period = PayrollPeriod(
                period_code=code,
                start_date=start_date,
                end_date=end_date,
                status=PayrollPeriodStatus.OPEN,
                source=source,
                created_by=operator_id,
                updated_by=operator_id,
            )
            self.session.add(period)
            try:
                await self.session.flush()
            except IntegrityError:
                # 并发下的最后一道库级防线（P5）：区间 / 标签唯一约束（ADR-0010 §1）。
                raise BusinessError(
                    ErrorCode.PAYROLL_PERIOD_OVERLAP,
                    "该标签或区间与既有结算周期重复，请调整后重试",
                    details={
                        "period_code": code,
                        "start_date": start_date.isoformat(),
                        "end_date": end_date.isoformat(),
                    },
                ) from None
            count, amount = await self._unsettled_stats(start_date, end_date)
            logger.info(
                "工资结算周期已创建",
                extra={"period_code": code, "unsettled_log_count": count},
            )
            return PeriodCreated(
                id=period.id,
                period_code=period.period_code,
                start_date=period.start_date,
                end_date=period.end_date,
                status=period.status,
                source=period.source,
                unsettled_log_count=count,
                unsettled_amount=amount,
            )

    async def _assert_no_overlap(
        self, start_date: date, end_date: date, *, exclude_id: UUID | None = None
    ) -> None:
        """区间重叠校验（ADR-0010 未用 PG daterange 排除约束，故在 service 层判）。

        两区间 ``[a1,b1]`` / ``[a2,b2]`` 重叠 ⟺ ``a1 <= b2`` 且 ``a2 <= b1``；
        「完全相同」自然被它覆盖，故同区间重复也报 ``33004``。
        """
        stmt = select(PayrollPeriod.id).where(
            PayrollPeriod.deleted_at.is_(None),
            PayrollPeriod.start_date <= end_date,
            PayrollPeriod.end_date >= start_date,
        )
        if exclude_id is not None:
            stmt = stmt.where(PayrollPeriod.id != exclude_id)
        hit = (await self.session.execute(stmt.limit(1))).scalar_one_or_none()
        if hit is not None:
            raise BusinessError(
                ErrorCode.PAYROLL_PERIOD_OVERLAP,
                "该区间与既有结算周期重叠（含完全相同），请调整起止日期",
                details={"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
            )

    # ================================================================== 取数核对

    async def _unsettled_stats(
        self, start_date: date, end_date: date, workshop_id: UUID | None = None
    ) -> tuple[int, Decimal]:
        """05 §8 S10：区间内**未结算**流水条数与金额合计（BR-PR-18）。

        ``workshop_id`` 可选：周期是全厂口径，建周期时不传（跨车间合计）；传了则
        等价 S10 原文的 ``workshop_id = :w``。``work_date`` 用 ``>= start AND <= end``
        （闭区间）。金额用 ``Decimal``；无行时 ``coalesce`` 回 0。
        """
        conditions: list[Any] = [
            PieceworkLog.work_date >= start_date,
            PieceworkLog.work_date <= end_date,
            PieceworkLog.payroll_id.is_(None),
            PieceworkLog.deleted_at.is_(None),
        ]
        if workshop_id is not None:
            conditions.append(PieceworkLog.workshop_id == workshop_id)
        row = (
            await self.session.execute(
                select(
                    func.count(PieceworkLog.id),
                    func.coalesce(func.sum(PieceworkLog.qty), 0),
                    func.coalesce(func.sum(PieceworkLog.amount), 0),
                ).where(*conditions)
            )
        ).one()
        return int(row[0]), Decimal(row[2])


__all__ = ["PayrollPeriodService"]
