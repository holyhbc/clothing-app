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

from app.common.enums import RateSource
from app.core.pydantic_types import Str
from app.modules.piecework.models import PieceworkLogType

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


# ====================================================================== 只读查询（T-PIECE-003）


class LogQuery(BaseModel):
    """计件流水筛选条件（``GET /piecework-logs`` / ``export`` / ``summary`` **共用**）。

    ⚠️ **列表、导出、汇总共用同一个对象**（07 §3.2 铁律 3）：三处各写一套筛选，
    「列表看到的」与「导出的」就会在对账时不一致 —— 而对账恰恰是导出存在的理由。

    ⚠️ ``is_settled`` 是**算出来的**（判据 ``payroll_id IS NULL``，04 §7.1），
    不是某个布尔列：两个来源必然漂移，而结算回写是 service 行为、不允许手工填。
    """

    model_config = ConfigDict(extra="forbid")

    work_date_from: date | None = None
    work_date_to: date | None = None
    style_no: str | None = Field(default=None, max_length=32)
    operation_no: str | None = Field(default=None, max_length=16)
    workshop_id: UUID | None = None
    group_no: str | None = Field(default=None, max_length=32)
    employee_id: UUID | None = None
    log_type: PieceworkLogType | None = None
    bundle_no: str | None = Field(default=None, max_length=64)
    is_settled: bool | None = Field(default=None, description="True=已结算（payroll_id 非空）")
    sort: str = Field(default="-work_date", max_length=32, description="白名单字段，可加 - 前缀")
    page: int = Field(default=1, ge=1)
    size: int = Field(default=20, ge=1, le=200, description="上限 200（05 §2）")


class LogRowOut(BaseModel):
    """列表 / 导出的一行（``04-计件 §6.1``「响应要点」逐项）。

    ⚠️ **手数与件数都在**：``hands`` 是第几手、``hands_total_of_size`` 是该尺码共几手，
    而 ``qty`` 是这次实际做的件数（可能 < ``bundle_qty``，部分生产）。
    三者混用会让主管看到「第 1 手做了 60 件」而其实是「第 2 手做了 28 件」。
    """

    log_id: UUID
    bundle_no: str = Field(description="完整不截断（BR-PW-30）：前端要拿它扫回那一张单")
    style_no: str
    operation_no: str
    operation_name: str | None = None
    color_name: str | None = None
    size_code: str | None = None
    size_name: str | None = None
    employee_id: UUID
    employee_no: str | None = None
    employee_name: str | None = None
    group_no: str | None = None
    workshop_name: str | None = None
    work_date: date
    hands: int | None = None
    hands_total_of_size: int | None = None
    bundle_qty: Str = "0.000"
    qty: Str = "0.000"
    unit_price: Str = "0.000000"
    amount: Str = "0.0000"
    log_type: PieceworkLogType
    payroll_id: UUID | None = None
    is_settled: bool = False


class LogDetailOut(BaseModel):
    """流水详情（§6.1）：本体 + ``bundle`` 子对象 + 关联流水。"""

    model_config = ConfigDict(from_attributes=True)

    log_id: UUID
    bundle_no: str
    style_no: str
    operation_no: str
    employee_id: UUID
    employee_name: str | None = None
    group_no: str | None = None
    work_date: date
    qty: Str = "0.000"
    unit_price: Str = "0.000000"
    amount: Str = "0.0000"
    log_type: PieceworkLogType
    payroll_id: UUID | None = None
    reversal_of_id: UUID | None = None
    remark: str | None = None
    scanned_at: datetime
    created_at: datetime
    bundle: BundleInfo | None = None
    reversal_of: RelatedLog | None = None
    reversed_by: list[RelatedLog] = Field(default_factory=list)


class BundleInfo(BaseModel):
    """详情里的 ``bundle``（§6.1「响应要点」第二行）。"""

    hands: int
    bundle_qty: Str = "0.000"
    counted_qty: Str = "0.000"
    counted_by_name: str | None = None
    cutting_size_line_id: UUID | None = None


class RelatedLog(BaseModel):
    """关联流水（红冲 ↔ 原单）。"""

    log_id: UUID
    log_type: PieceworkLogType
    qty: Str = "0.000"
    amount: Str = "0.0000"
    work_date: date


class SummaryRowOut(BaseModel):
    """汇总行（``GET /piecework/summary``）。

    ⚠️ **手数与件数并列**（BR-PW-31、ADR-0016 §6）：车间的主计数是「手」，
    只给 ``total_qty`` 的话主管看到的是「做完了 3600 件」而不是「做了 60 手」。
    """

    employee_id: UUID | None = None
    employee_no: str | None = None
    employee_name: str | None = None
    style_no: str | None = None
    color_name: str | None = None
    operation_no: str | None = None
    group_no: str | None = None
    total_hands: int = Field(description="★ 手数（COUNT(*)）")
    total_qty: Str = "0.000"
    total_amount: Str = "0.0000"


class StationTodayOut(BaseModel):
    """工位机大字卡数据（``GET /piecework/stations/{station_no}/today``）。"""

    station_no: str
    work_date: date
    today_hands: int = Field(description="★ 主计数：今日做了多少**手**")
    today_qty: Str = "0.000"
    today_amount: Str = "0.0000"
    last_bundle_no: str | None = None
    last_hands: int | None = None
    last_scanned_at: datetime | None = None


class OperatorOptionOut(BaseModel):
    """工位机 / 补录页的「操作员」候选（``GET /piecework/operators``，T-PIECE-006）。

    ⚠️ ``value`` 是**用户 UUID**（不是工号）：计件请求体 ``employee_id`` 要的就是它。
    ``label`` 必须是「工号 姓名」（05 §9.5.2 禁止只显示编码）—— 只给 UUID 的话
    下拉里是一串人不可读的标识，组长根本挑不出人。

    ⚠️ **与 base 的 ``OptionOut`` 不同**（那个 ``value`` 是业务编码字符串）：员工
    天然没有「业务编码主键」，工号是登录凭据可改、UUID 才稳定，故这里 ``value`` 是 UUID。
    """

    value: UUID
    label: str
    sub: str | None = None
    disabled: bool = False


class PieceworkRateOut(BaseModel):
    """计件单价查询的一行（``GET /piecework/rates``，T-PIECE-009）。

    ⚠️ **不复用 base 的 ``OperationRateOut``**：那个的 ``is_current`` 口径是
    ``effective_to IS NULL``（「长期有效」），而本端点按 ``work_date`` 生效 ——
    一条**未来生效**的价 `effective_to IS NULL`，在 ``work_date`` 当天并不生效，
    两个口径会打架。故单列一个响应，``is_current`` 一律按 ``work_date`` 判定。

    ⚠️ 行已被「当天生效」过滤，故 ``is_current`` 恒为 ``True``；保留它是**显式语义**
    —— 防止调用方拿它当 base 那个 ``effective_to IS NULL`` 来理解。
    """

    operation_no: str
    style_no: str | None = Field(default=None, description="空 = 不限款号（档位2/3）")
    product_category_id: UUID | None = Field(default=None, description="空 = 不限分类")
    unit_price: Str = Field(description="工价；字符串（docs/05 §3）")
    effective_from: date
    effective_to: date | None = None
    rate_source: RateSource = Field(description="该行所属档位（STYLE/CATEGORY/OPERATION）")
    is_current: bool = Field(
        default=True, description="该 work_date 当天是否生效（本端点恒为 true，见类注）"
    )


# ====================================================================== 红冲 / 补录（T-PIECE-004）


class PieceworkReverseIn(BaseModel):
    """红冲请求（``POST /piecework-logs/{log_id}/reverse``，§5.3 步骤 3）。

    ⚠️ **类名带 `Piecework` 前缀**：`bundling` 模块已有同名 `ReverseIn`
    （打菲反审核），两个同名类会让 FastAPI 把**两份**组件都改名成带模块路径的
    `app__modules__...__ReverseIn` —— 那会把已发布的 `bundling.ReverseIn`
    一起改名，是破坏性的契约变更。所以新加的这份显式取唯一名。

    ⚠️ **只有 ``reason`` 一个字段**（结构上只容得下这一个动作）：
    不接受 ``force`` / 自定义数量 —— 红冲是「把这手撤销回未计件」，
    数量必须**从原流水抄**（``qty=-L1.qty``），不能由前端传一个来改。
    ``super_admin`` 的自审例外沿用打菲的做法（不做 ``force``，见 08 §4 注释）。
    """

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=500, description="为什么红冲（必填，10002 兜底）")


class ReverseOut(BaseModel):
    """红冲响应。⚠️ ``bundle_counted_reset`` 回答「这手是不是回到未计件了」。"""

    reversal_log_id: UUID
    bundle_no: str
    style_no: str
    operation_no: str
    reversed_qty: Str = "0.000"
    reversed_amount: Str = "0.0000"
    bundle_counted_reset: bool = Field(
        description="bundles.counted_* 是否已清空（true = 该手未计件）"
    )


class PieceworkLogIn(BaseModel):
    """手工补录 / 补录请求（``POST /piecework-logs``，§5.3 步骤 4 + §5.4）。

    ⚠️ ``log_type`` 决定两个分支：
    - ``MANUAL``（漏扫码）：``reversal_of_id`` **不许传**，``remark`` 必填
    - ``SUPPLEMENT``（返工补录）：``reversal_of_id`` **必填**，``remark`` 必填

    ⚠️ **故意不接受 ``employee_id``**：工位机是组长代员工补录，操作员走 token；
    员工本人是谁，从被补录的原流水 / 绑定的业务语义来。这在类型上就表达不出来 ——
    前端想「补录时随手写个员工」根本传不进来。
    """

    model_config = ConfigDict(extra="forbid")

    log_type: PieceworkLogType = Field(description="MANUAL 或 SUPPLEMENT")
    bundle_no: str = Field(min_length=8, max_length=64)
    bundle_id: UUID | None = Field(default=None, description="可选，服务端可反查")
    reversal_of_id: UUID | None = Field(default=None, description="SUPPLEMENT 必填")
    remark: str = Field(default="", max_length=500, description="补录原因；空串由 service 回 10002")
    qty: PositiveQty | None = Field(
        default=None, description="默认 = bundle_qty；≤ bundle_qty（32006）"
    )


class PieceworkLogWriteOut(BaseModel):
    """手工补录 / 补录响应（§6.1「同上 + document_log_id」）。"""

    log_id: UUID
    document_log_id: UUID
    bundle_no: str
    style_no: str
    operation_no: str
    qty: Str = "0.000"
    bundle_qty: Str = "0.000"
    unit_price: Str = "0.000000"
    amount: Str = "0.0000"
    log_type: PieceworkLogType
    work_date: date
