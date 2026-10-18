"""结算周期 service 的常量、DTO 与纯助手（ADR-0031：从 ``period.py`` 拆出）。

## 为什么拆

原 ``period.py`` 单文件 641 行，越过 ``ADR-0030`` 的 **单文件 400 行硬线**。
按 ``P2-工资-实施说明 §7``「单文件 400 行硬线优先于文件计数」拆包：

- :mod:`~app.modules.payroll.service.common`（本文件）—— 常量 / 枚举 / DTO / 纯助手
- :mod:`~app.modules.payroll.service.state_mixin` —— 状态机（close / reopen）
- :mod:`~app.modules.payroll.service.period` —— 组合类 + 预演 / 落库

本文件不依赖另外两个（`common` 只在最底层，无环）。

## 三条贯穿本卡的设计口径（原 ``period.py`` 模块 docstring）

### ① 日期只在 ``generate`` 里算；``create`` 收显式区间

``bimonth`` 含起始月 + 下一月；``month`` 的自然月末按 ``calendar.monthrange`` 取
（大小月 / 闰年以实际月末为准）。区间**均含首尾** —— SQL 用
``work_date >= start AND work_date <= end``（等价 ``BETWEEN`` 闭区间，TC-20）。

### ② 标签只给人看，**不解析**（BR-PR-20）

``period_code`` 是自由标签（``'2026-08'`` / ``'7-8月'`` / ``'裁剪0820-0905'``），
唯一性靠 ``(start_date, end_date)``（``uq_payroll_periods_range``）。取数永远走
日期比较，任何 SQL 条件里**禁止**出现标签解析。

### ③ ``cutting_boundary`` 的「完成日」= ``approved_at::date``（⚠️ Q-PL11）

规范（BR-PR-17 / §5.1）只说「裁剪单最早完成日 ~ 打菲单最晚完成日」，**没有定义
「完成日」是哪个列**。本卡选择 **``approved_at::date``，且仅取
``status='APPROVED'`` 的行**，理由：``doc_date`` 是**下单日**（不是完成日）；
``cutting_orders`` / ``bundling_orders`` 都只有 ``approved_at`` 这个「单据实际
生效 / 完成」的时刻列。⚠️ 待决问题 **Q-PL11** 已登记到
``docs/modules/05-工资.md §12``；若业务口径改为别的列（如新增 ``completed_at``），
只改 :meth:`~app.modules.payroll.service.period.PayrollPeriodService._cutting_boundary_range`。
⚠️ ``approved_at`` 是 ``timestamptz``，``.date()`` 取的是 **UTC 日**（等价
``approved_at::date``）；跨零点东八区会差一天 —— 同属 Q-PL11 待确认范围。
"""

from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Final
from uuid import UUID

from pydantic import BaseModel

from app.core.errors import BusinessError, ErrorCode
from app.modules.payroll.models import PayrollPeriodStatus

#: ``document_logs.doc_type``（周期不是单据，但迁移同样写留痕，05 §4.3）。
DOC_TYPE_PAYROLL_PERIOD: Final[str] = "PayrollPeriod"
#: 建 / 关 / 重开周期的权限点（05 §2 BR-PR-19、§10）。
PERIOD_MANAGE_PERMISSION: Final[str] = "payroll:period:manage"
#: ``payroll_periods.source``（04 §7.6.1，varchar(16)，非 PG enum）。
SOURCE_MANUAL: Final[str] = "MANUAL"
SOURCE_CUTTING_BOUNDARY: Final[str] = "CUTTING_BOUNDARY"
SOURCES: Final[frozenset[str]] = frozenset({SOURCE_MANUAL, SOURCE_CUTTING_BOUNDARY})
#: 周期标签 / 数据库列的长度上限（04 §7.6.1：``varchar(32)``）。
PERIOD_CODE_MAX_LENGTH: Final[int] = 32

#: ``document_logs.action`` 的取值。⚠️ 周期动作 ``close`` / ``reopen`` 沿用
#: 08 §1.1「会计期间动作」的命名，用大写（与 ``DocumentAction`` 的既有取值同风格）。
ACTION_CLOSE: Final[str] = "CLOSE"
ACTION_REOPEN: Final[str] = "REOPEN"


class PeriodGenerateMode(StrEnum):
    """``generate`` 的三种建法（BR-PR-17 / 05 §6.1.1）。

    ⚠️ **不是 PG enum**：库里没有这一列，它只是接口入参的取值集合。放在本模块
    而不放 ``app/common/enums.py`` —— 与 ``PayrollPeriodStatus``（模块内 ``models``）
    同一口径：工资域专属、不跨模块。
    """

    MONTH = "month"
    BIMONTH = "bimonth"
    CUTTING_BOUNDARY = "cutting_boundary"


#: 状态迁移表。**按动作登记** ``(允许的起始状态, 目标状态)``（照 08 §1.2 R1）：
#: ``reopen`` 仅 ``CLOSED → OPEN``（否则 ``10007``）。
TRANSITION_BY_ACTION: Final[
    dict[str, tuple[frozenset[PayrollPeriodStatus], PayrollPeriodStatus]]
] = {
    "close": (frozenset({PayrollPeriodStatus.OPEN}), PayrollPeriodStatus.CLOSED),
    "reopen": (frozenset({PayrollPeriodStatus.CLOSED}), PayrollPeriodStatus.OPEN),
}


class PeriodPreview(BaseModel):
    """``generate`` 的**预演**结果（不落库，05 §6.1.1 + BR-PR-18）。"""

    period_code: str
    start_date: date
    end_date: date
    source: str
    unsettled_log_count: int
    unsettled_amount: Decimal


class PeriodCreated(BaseModel):
    """``create`` 的结果：落库后的周期 + 核对数（BR-PR-18，供 UI 核对）。"""

    id: UUID
    period_code: str
    start_date: date
    end_date: date
    status: PayrollPeriodStatus
    source: str
    unsettled_log_count: int
    unsettled_amount: Decimal


def month_bounds(year: int, month: int) -> tuple[date, date]:
    """某自然月的 ``(1 日, 实际月末)``。大小月 / 闰年以 ``calendar`` 为准。"""
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def parse_anchor_month(anchor_month: str | None) -> tuple[int, int]:
    """解析 ``YYYY-MM`` → ``(year, month)``；缺失 / 格式错 → ``10001``。"""
    if not anchor_month or not anchor_month.strip():
        raise BusinessError(ErrorCode.PARAM_INVALID, "该模式必须提供 anchor_month（格式 YYYY-MM）")
    raw = anchor_month.strip()
    year_str, sep, month_str = raw.partition("-")
    if sep != "-" or len(year_str) != 4 or not (year_str.isdigit() and month_str.isdigit()):
        raise BusinessError(
            ErrorCode.PARAM_INVALID,
            f"anchor_month 格式应为 YYYY-MM，收到 {anchor_month!r}",
            details={"anchor_month": anchor_month},
        )
    year, month = int(year_str), int(month_str)
    if not 1 <= month <= 12:
        raise BusinessError(
            ErrorCode.PARAM_INVALID,
            f"anchor_month 的月份必须在 01~12，收到 {anchor_month!r}",
            details={"anchor_month": anchor_month},
        )
    return year, month


def require_reason(reason: str | None, *, action: str) -> str:
    """close / reopen 必填原因（05 §4.3，缺 → ``10006``）。"""
    text = (reason or "").strip()
    if not text:
        raise BusinessError(ErrorCode.REASON_REQUIRED, f"{action}必须填写原因")
    return text


__all__ = [
    "ACTION_CLOSE",
    "ACTION_REOPEN",
    "DOC_TYPE_PAYROLL_PERIOD",
    "PERIOD_CODE_MAX_LENGTH",
    "PERIOD_MANAGE_PERMISSION",
    "SOURCES",
    "SOURCE_CUTTING_BOUNDARY",
    "SOURCE_MANUAL",
    "TRANSITION_BY_ACTION",
    "PeriodCreated",
    "PeriodGenerateMode",
    "PeriodPreview",
    "month_bounds",
    "parse_anchor_month",
    "require_reason",
]
