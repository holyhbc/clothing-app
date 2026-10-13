"""计件接口的请求 / 响应模型（``docs/modules/04-计件.md`` §6.1、§6.2）。

## 为什么响应里的数量与金额是**字符串**

``docs/05 §3``：金额与数量在 JSON 里一律是字符串。``qty`` 是 ``numeric(14,3)``，
``amount`` 是 ``numeric(18,4)``，走 Python ``float`` 会丢精度 —— 60.0 件 ×
0.4199 元/件 这种数在二进制浮点下必然出现 ``25.193999999999996``。

## ``employee_id`` 为什么在管理端必传、在员工端必须不传

管理端是**工位机代员工计件**：组长在工位机上选定操作员，所以请求体里必须有
``employee_id``（``04-计件 §6.1``）。员工端则相反 —— 它的值一律从 token 取，
请求体出现 ``employee_id`` 即 422（``§6.2`` 的 ``extra="forbid"`` + 无该字段）。
两个端点共用同一个 service 方法，**入参形态也不同**：
:func:`CountPieceworkIn.employee_id` 必填，:func:`SelfCountPieceworkIn` 不含它。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.core.pydantic_types import Str

#: ``qty`` 的下界用 ``gt=0``：``<= 0`` 由 schema 直接挡掉并回 422（``10001``），
#: service 里**不再重复判** —— 两处判就会漂移（改了 schema 忘了 service 是常见坑）。
PositiveQty = Annotated[Decimal, Field(gt=0, description="本次实际完成的件数（整件）")]


class CountPieceworkIn(BaseModel):
    """管理端扫码计件请求（``POST /piecework/count``，工位机代员工计件）。

    ⚠️ ``qty`` **可选**：不传 = 这一手全件（``bundles.bundle_qty``，BR-PW-26），
    传了是**部分生产**（BR-PW-27，≤ ``bundle_qty``）。
    ⚠️ **没有默认值 1**：09 §4.2 与 ADR-0016 都否掉了「扫码 +1 件」的旧口径，
    留一个 ``= 1`` 会让漏传的请求静默按 1 件计，事后无从发现。
    """

    model_config = ConfigDict(extra="forbid")

    bundle_no: str = Field(min_length=8, max_length=64, description="打菲号（扫出来的）")
    employee_id: UUID = Field(description="操作员（工位机代员工计件，故必传）")
    scanned_at: datetime = Field(description="扫码时刻；work_date 由它按 Asia/Shanghai 推导")
    station_id: str | None = Field(default=None, max_length=32, description="工位机编号")
    device_id: str | None = Field(default=None, max_length=64, description="扫码枪设备号")
    qty: PositiveQty | None = Field(
        default=None,
        description="★ 可选。不传 = 该手全件（bundles.bundle_qty）；传了必须 0 < qty ≤ bundle_qty",
    )


class SelfCountPieceworkIn(BaseModel):
    """员工端扫码计件请求（``POST /self/piecework/count``，T-PIECE-004）。

    ⚠️ **刻意不含 ``employee_id``**：一律从 token 取，``extra="forbid"`` 保证
    前端传了就是 422（``04-计件 §6.2``）。
    """

    model_config = ConfigDict(extra="forbid")

    bundle_no: str = Field(min_length=8, max_length=64)
    scanned_at: datetime
    device_id: str | None = Field(default=None, max_length=64)
    qty: PositiveQty | None = None


class CountPieceworkOut(BaseModel):
    """扫码计件响应（``04-计件 §6.1``「响应要点」列逐项）。

    ## 手数与件数**并列**，不是二选一

    ADR-0016 §6：车间的真实产出单位是「手」。60 件的手做完了 → 主计数 **+1 手**、
    副计数 +60 件；只做完 28 件 → 主计数**仍 +1 手**、副计数 +28。
    所以响应里 ``hands`` 与 ``qty`` 都给，``hands`` 才是主计数。
    """

    log_id: UUID
    bundle_no: str
    style_no: str
    operation_no: str
    color_name: str
    size_name: str
    hands: int = Field(description="第 N 手")
    hands_total_of_size: int = Field(description="该尺码共 M 手")
    qty: Str = Field(description="本次实际件数（字符串，05 §3）")
    bundle_qty: Str = Field(description="这一手共几件（权威列 bundles.bundle_qty）")
    unit_price: Str
    amount: Str
    work_date: date
    idempotent: bool = Field(default=False, description="True = 幂等命中，未新增流水")
    today_hands: int = Field(description="★ 主计数：该员工今日手数")
    today_qty: Str
    today_amount: Str


class PieceworkErrorDetail(BaseModel):
    """``32001`` 的 ``details`` 结构（04-计件 §9「客户端处理」列）。

    ⚠️ **工位机不弹窗打断**（06 §2.5 第 3 条），所以这几个字段是给界面直接
    渲染成一行黄色提示的：「XL 第 2 手，陈美玲 09:44 已计 60 件」。
    """

    counted_by_name: str
    counted_at: datetime
    counted_qty: Str
    bundle_qty: Str
    hands: int


class SelfTodayOut(BaseModel):
    """员工端首页大字卡（``GET /self/piecework/today``，T-PIECE-004）。"""

    work_date: date
    today_hands: int
    today_qty: Str
    today_amount: Str
    month_hands: int
    month_qty: str
    month_amount: str
