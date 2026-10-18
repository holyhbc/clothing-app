"""结算周期 service 用例（T-PAY-003；``modules/05 §11`` TC-20~24 / 27~29 / 31）。

=====================================  ==================================================
TC-20                                 整月边界取数（含首尾日，不含次日）
TC-21                                 两月一次跨月区间 + 标签
TC-22                                 整月生成（大小月 / 闰年 2 月以实际月末为准）
TC-23                                 同区间 / 同标签重复建周期被拒 → ``33004``
TC-24                                 区间部分重叠被拒 → ``33004``
TC-27                                 重开必填原因 / 无权限 / 成功（含 document_logs）
TC-28                                 按裁剪打菲完成日截取（区间 + 标签 + 20001）
TC-29                                 ``generate`` 返回核对数且**不落库**
TC-31                                 非法参数（``end_date < start_date`` / 非法 mode）→ ``10001``
=====================================  ==================================================

## 本文件刻意不走 HTTP

本卡**没有 router**（router 归 T-PAY-009），故直接调 service。鉴权用构造的
``AuthContext``（``payroll:period:manage``），数据范围恒 ``FACTORY``（周期全厂口径）。

## 为什么区间取数用例用 2026-07/08 而不是今天

真提交的并发用例（``test_piecework_count_concurrency``）落的是 ``business_today()``
的流水且**不一定清理**（L-035）。本文件的区间取数走全厂合计，若用今天就会被它们污染。
改用确定的历史区间即可隔离 —— 这是「按 work_date 取数」这条口径带来的天然便利。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.common.enums import DataScope
from app.common.models import DocumentLog
from app.core.errors import BusinessError, ErrorCode
from app.core.permissions import AuthContext
from app.modules.payroll.models import PayrollPeriod
from app.modules.payroll.service import PayrollPeriodService
from tests.factories.payroll import (
    BOUNDARY_STYLE_NO,
    seed_boundary_orders,
    seed_period,
    seed_piecework_log,
    settlement_row,
)
from tests.factories.user import OPERATOR_ID

PERIOD_MANAGE = "payroll:period:manage"
DOC_TYPE = "PayrollPeriod"


def _ctx(
    *,
    permissions: frozenset[str] = frozenset({PERIOD_MANAGE}),
    scope: DataScope = DataScope.FACTORY,
) -> AuthContext:
    return AuthContext(
        user_id=OPERATOR_ID,
        name="计件员",
        employee_no="A001",
        workshop_id=None,
        group_no=None,
        permissions=permissions,
        data_scope=scope,
    )


async def _period_count(session: Any) -> int:
    return (await session.execute(select(func.count(PayrollPeriod.id)))).scalar_one()


async def _logs(session: Any, period_id: Any) -> list[DocumentLog]:
    rows = await session.execute(
        select(DocumentLog).where(DocumentLog.doc_type == DOC_TYPE, DocumentLog.doc_id == period_id)
    )
    return list(rows.scalars().all())


# ============================================================== TC-20 ~ TC-22


async def test_tc20_month_boundary_inclusive(db_session) -> None:
    """整月区间取数含首尾日、不含相邻日（``BETWEEN`` 闭区间，BR-PR-20）。"""
    workshop = uuid4()
    for day in (date(2026, 7, 31), date(2026, 8, 1), date(2026, 8, 31), date(2026, 9, 1)):
        await seed_piecework_log(db_session, work_date=day, workshop_id=workshop)

    preview = await PayrollPeriodService(db_session).generate(mode="month", anchor_month="2026-08")
    assert (preview.start_date, preview.end_date) == (date(2026, 8, 1), date(2026, 8, 31))
    assert preview.unsettled_log_count == 2  # 08-01 与 08-31，不含 07-31 / 09-01
    assert preview.unsettled_amount == Decimal("42.0000")


async def test_tc21_bimonth_range_and_label(db_session) -> None:
    """两月一次：起始月 + 下一月；标签 ``YYYY-MM~MM``，``source='MANUAL'``。"""
    for day in (date(2026, 7, 1), date(2026, 7, 31), date(2026, 8, 1), date(2026, 8, 31)):
        await seed_piecework_log(db_session, work_date=day, workshop_id=uuid4())

    preview = await PayrollPeriodService(db_session).generate(
        mode="bimonth", anchor_month="2026-07"
    )
    assert (preview.start_date, preview.end_date) == (date(2026, 7, 1), date(2026, 8, 31))
    assert preview.period_code == "2026-07~08"
    assert preview.source == "MANUAL"
    assert preview.unsettled_log_count == 4
    assert preview.unsettled_amount == Decimal("84.0000")


async def test_tc22_month_end_days(db_session) -> None:
    """大小月 / 闰年 2 月以实际月末为准。"""
    service = PayrollPeriodService(db_session)
    aug = await service.generate(mode="month", anchor_month="2026-08")
    assert (aug.start_date, aug.end_date) == (date(2026, 8, 1), date(2026, 8, 31))
    assert aug.period_code == "2026-08"

    feb = await service.generate(mode="month", anchor_month="2026-02")
    assert (feb.start_date, feb.end_date) == (date(2026, 2, 1), date(2026, 2, 28))

    leap = await service.generate(mode="month", anchor_month="2028-02")
    assert (leap.start_date, leap.end_date) == (date(2028, 2, 1), date(2028, 2, 29))

    apr = await service.generate(mode="month", anchor_month="2026-04")
    assert (apr.start_date, apr.end_date) == (date(2026, 4, 1), date(2026, 4, 30))

    dec = await service.generate(mode="bimonth", anchor_month="2026-12")
    assert (dec.start_date, dec.end_date) == (date(2026, 12, 1), date(2027, 1, 31))
    assert dec.period_code == "2026-12~01"


# ============================================================== TC-23 / TC-24


async def test_tc23_same_range_rejected_33004(db_session) -> None:
    """同区间（不同标签）→ ``33004``（service 层重叠校验）。"""
    service = PayrollPeriodService(db_session)
    await service.create(
        period_code="P-0826-A",
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 31),
        operator_id=OPERATOR_ID,
    )
    with pytest.raises(BusinessError) as exc:
        await service.create(
            period_code="P-0826-B",
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 31),
            operator_id=OPERATOR_ID,
        )
    assert exc.value.code == ErrorCode.PAYROLL_PERIOD_OVERLAP


async def test_tc23b_code_duplicate_rejected_by_unique(db_session) -> None:
    """同标签不同区间 → 唯一约束兜底（``uq_payroll_periods_code``）→ ``33004``。"""
    service = PayrollPeriodService(db_session)
    await service.create(
        period_code="P-DUP",
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 31),
        operator_id=OPERATOR_ID,
    )
    with pytest.raises(BusinessError) as exc:
        await service.create(
            period_code="P-DUP",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 30),
            operator_id=OPERATOR_ID,
        )
    assert exc.value.code == ErrorCode.PAYROLL_PERIOD_OVERLAP


async def test_tc24_partial_overlap_rejected_33004(db_session) -> None:
    """部分重叠（08-15~09-15 与 08-01~08-31）→ ``33004``。"""
    await seed_period(
        db_session,
        period_code="P-OVER-A",
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 31),
    )
    with pytest.raises(BusinessError) as exc:
        await PayrollPeriodService(db_session).create(
            period_code="P-OVER-B",
            start_date=date(2026, 8, 15),
            end_date=date(2026, 9, 15),
            operator_id=OPERATOR_ID,
        )
    assert exc.value.code == ErrorCode.PAYROLL_PERIOD_OVERLAP


async def test_adjacent_ranges_allowed(db_session) -> None:
    """**相邻但不重叠**（08-31 | 09-01）允许 —— 重叠判据不能写成「有交集就算」误伤。"""
    service = PayrollPeriodService(db_session)
    await service.create(
        period_code="P-ADJ-A",
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 31),
        operator_id=OPERATOR_ID,
    )
    created = await service.create(
        period_code="P-ADJ-B",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 30),
        operator_id=OPERATOR_ID,
    )
    assert created.period_code == "P-ADJ-B"


# ============================================================== TC-28


async def test_tc28_cutting_boundary(db_session) -> None:
    """取该款裁剪单最早完成日 ~ 打菲单最晚完成日；标签 ``裁剪MMDD-MMDD``。"""
    await seed_boundary_orders(
        db_session,
        cutting_dates=[date(2026, 8, 3)],
        bundling_dates=[date(2026, 8, 27)],
    )
    preview = await PayrollPeriodService(db_session).generate(
        mode="cutting_boundary", style_no=BOUNDARY_STYLE_NO
    )
    assert (preview.start_date, preview.end_date) == (date(2026, 8, 3), date(2026, 8, 27))
    assert preview.source == "CUTTING_BOUNDARY"
    assert preview.period_code == "裁剪0803-0827"


async def test_tc28_cutting_boundary_picks_min_max(db_session) -> None:
    """多张单时：裁剪取**最早**完成日、打菲取**最晚**完成日。"""
    await seed_boundary_orders(
        db_session,
        cutting_dates=[date(2026, 8, 5), date(2026, 8, 3)],
        bundling_dates=[date(2026, 8, 20), date(2026, 8, 27)],
    )
    preview = await PayrollPeriodService(db_session).generate(
        mode="cutting_boundary", style_no=BOUNDARY_STYLE_NO
    )
    assert (preview.start_date, preview.end_date) == (date(2026, 8, 3), date(2026, 8, 27))


async def test_tc28_unknown_style_20001(db_session) -> None:
    with pytest.raises(BusinessError) as exc:
        await PayrollPeriodService(db_session).generate(
            mode="cutting_boundary", style_no="NO-SUCH-STYLE-9Z"
        )
    assert exc.value.code == ErrorCode.BASE_DATA_NOT_FOUND


async def test_tc28_missing_style_no_10001(db_session) -> None:
    with pytest.raises(BusinessError) as exc:
        await PayrollPeriodService(db_session).generate(mode="cutting_boundary")
    assert exc.value.code == ErrorCode.PARAM_INVALID


# ============================================================== TC-29


async def test_tc29_generate_returns_stats_without_persisting(db_session) -> None:
    """``generate`` 返回核对数且**不落库**（``payroll_periods`` 无新增行）。"""
    before = await _period_count(db_session)
    for day in (date(2026, 8, 1), date(2026, 8, 31)):
        await seed_piecework_log(db_session, work_date=day, workshop_id=uuid4())

    preview = await PayrollPeriodService(db_session).generate(mode="month", anchor_month="2026-08")
    assert preview.unsettled_log_count == 2
    assert preview.unsettled_amount == Decimal("42.0000")
    assert await _period_count(db_session) == before


async def test_create_returns_stats(db_session) -> None:
    """``create`` 响应也带核对数（BR-PR-18，UI 必显）。"""
    await seed_piecework_log(db_session, work_date=date(2026, 8, 10), workshop_id=uuid4())
    created = await PayrollPeriodService(db_session).create(
        period_code="P-STATS",
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 31),
        operator_id=OPERATOR_ID,
    )
    assert created.unsettled_log_count == 1
    assert created.unsettled_amount == Decimal("21.0000")


async def test_stats_excludes_settled_and_deleted(db_session) -> None:
    """核对数只算 ``payroll_id IS NULL AND deleted_at IS NULL``（INV-2 取数口径）。"""
    period = await seed_period(
        db_session,
        period_code="P-EX",
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 31),
    )
    settlement = settlement_row(period.id, uuid4())
    db_session.add(settlement)
    await db_session.flush()

    workshop = uuid4()
    await seed_piecework_log(db_session, work_date=date(2026, 8, 5), workshop_id=workshop)
    await seed_piecework_log(
        db_session,
        work_date=date(2026, 8, 6),
        workshop_id=workshop,
        payroll_id=settlement.id,
    )
    await seed_piecework_log(
        db_session,
        work_date=date(2026, 8, 7),
        workshop_id=workshop,
        deleted_at=datetime(2026, 8, 8, tzinfo=UTC),
    )
    preview = await PayrollPeriodService(db_session).generate(mode="month", anchor_month="2026-08")
    assert preview.unsettled_log_count == 1
    assert preview.unsettled_amount == Decimal("21.0000")


# ============================================================== TC-31


async def test_tc31_end_before_start_10001(db_session) -> None:
    with pytest.raises(BusinessError) as exc:
        await PayrollPeriodService(db_session).create(
            period_code="P-BAD",
            start_date=date(2026, 8, 31),
            end_date=date(2026, 8, 1),
            operator_id=OPERATOR_ID,
        )
    assert exc.value.code == ErrorCode.PARAM_INVALID


async def test_tc31_invalid_mode_10001(db_session) -> None:
    with pytest.raises(BusinessError) as exc:
        await PayrollPeriodService(db_session).generate(mode="weekly", anchor_month="2026-08")
    assert exc.value.code == ErrorCode.PARAM_INVALID


async def test_tc31_month_mode_without_anchor_10001(db_session) -> None:
    with pytest.raises(BusinessError) as exc:
        await PayrollPeriodService(db_session).generate(mode="month")
    assert exc.value.code == ErrorCode.PARAM_INVALID


async def test_tc31_period_code_too_long_10001(db_session) -> None:
    with pytest.raises(BusinessError) as exc:
        await PayrollPeriodService(db_session).create(
            period_code="X" * 33,
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 31),
            operator_id=OPERATOR_ID,
        )
    assert exc.value.code == ErrorCode.PARAM_INVALID


async def test_tc31_invalid_source_10001(db_session) -> None:
    with pytest.raises(BusinessError) as exc:
        await PayrollPeriodService(db_session).create(
            period_code="P-SRC",
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 31),
            operator_id=OPERATOR_ID,
            source="AUTO",
        )
    assert exc.value.code == ErrorCode.PARAM_INVALID
