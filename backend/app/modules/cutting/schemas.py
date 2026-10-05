"""裁剪单的请求 / 响应模型（docs/05 §2、§3 + [modules/02-裁剪](../docs/modules/02-裁剪.md) §6）。

## 三层嵌套的结构（ADR-0017）

``POST /cutting-orders`` 一次性收表头 + 布批行 + 行内颜色 + 尺码明细：

```
CuttingOrderCreateIn
└── lines[]                      布批行（≤200）—— ★ 耗料记在行
    └── colors[]                 行内颜色（≤10）—— 一床可多色
        └── size_lines[]         尺码明细（≤50）—— ★ 出数权威来源
```

## 五条约定（与 ``app/modules/base/schemas.py`` 同款）

1. **请求模型一律 ``extra="forbid"``**（docs/05 §2）—— 少一个字段是 typo，
   多一个字段是「以为服务端会用而其实没用」，后者更贵
2. 响应里**不出现** ``deleted_at`` / ``created_by`` / ``updated_by``
3. **数量与金额在响应里一律 ``str``**（docs/05 §3 铁律）—— JS 的 ``number``
   表示不了 ``0.378000``，而本模块的耗料是 ``numeric(14,3)``
4. 请求侧数量用 ``Decimal``（docs/03 §1.1 第 8 条：**禁止 float**）
5. ``version`` 用**表头聚合行**的版本，不用子表的 —— 全量替换子表会让子表的
   ``version`` 每次从 1 重来，拿它当乐观锁等于没有锁（同 ``RatioReplaceIn``）

## ⚠️ ``hands`` / ``qty_per_hand`` 是 ``int`` 而不是 ``Decimal``（ADR-0020）

``modules/02 §3.4`` 的字段表还写着 ``numeric(14,4)`` +「可小数 1.5 手」，
那是 **ADR-0020 之前**的残留，已被 ADR-0020 取代（「手数是权威输入、件数 = 乘法
**精确值**，不再 floor、不允许 1.5 手」），与同文档 C13/C20/C21、
``04 §7.7.2`` 及**已建的 ``integer`` 列**全部冲突 —— 以 ``04 §7.7.2`` 为准。

用 ``int`` 而不是 ``Decimal`` 的收益是**类型层面就挡住** ``1.5 手``：
写 ``hands: 1.5`` 会在 Pydantic 校验阶段报 ``10001``，而如果声明成
``Decimal`` 再在 service 里判，那条校验很容易被后来的人漏掉。
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.cutting.models import CuttingEntryMode

# ------------------------------------------------------------------ 入参片段

#: 款号（09 §1.1）。``max_length=32`` 与 ``styles.style_no`` 的列宽一致。
StyleNo = Annotated[str, Field(min_length=1, max_length=32, examples=["HB-2026-0018"])]
#: 色码（09 §1.2），与 ``colors.color_code`` 列宽一致。
ColorCode = Annotated[str, Field(min_length=1, max_length=32, examples=["WHT"])]
#: 尺码码（09 §1.3），与 ``sizes.size_code`` 列宽一致。
SizeCode = Annotated[str, Field(min_length=1, max_length=32, examples=["XL"])]

# ⚠️ **三层嵌套的规模上限**（modules/02 §6）。写在这里而不是散在各处：
# 上限是**契约**的一部分，前端据此分页/虚拟滚动，后端据此拒绝超大请求。
MAX_LINES = 200
MAX_COLORS_PER_LINE = 10
MAX_SIZE_LINES_PER_COLOR = 50


class SizeLineIn(BaseModel):
    """尺码明细入参（第 3 层，**出数的权威来源**）。

    ⚠️ ``size_line_no`` 在**同颜色内**唯一即可，**允许** ``(line_color_id, size_code)``
    重复 —— 同尺码多行、各行手数不同是 ADR-0014 模式 C 的正常形态（C30）。
    """

    model_config = ConfigDict(extra="forbid")

    size_line_no: Annotated[int, Field(ge=1, description="明细行号；同颜色内唯一")]
    size_code: SizeCode
    hands: Annotated[int, Field(ge=1, description="★ 本行裁几手（整数，用户直接输入；ADR-0020）")]
    qty_per_hand: Annotated[
        int, Field(ge=1, description="★ 本行每手几件（整数）；同尺码多行可不同")
    ]
    #: 人工指定的件数。``None`` = 由服务端算 ``hands × qty_per_hand``（精确整数）。
    #: 传了且与服务端算的不同 → 该颜色转 ``MANUAL`` 且差额进 ``balance_qty``（C13/C28）。
    output_qty: Annotated[
        int | None,
        Field(default=None, ge=0, description="人工指定件数；不传 = 服务端算 hands × qty_per_hand"),
    ]
    hands_seq: Annotated[
        int | None, Field(default=None, ge=1, description="手序号（打菲按手拆分用，ADR-0016）")
    ]
    remark: Annotated[str | None, Field(default=None, max_length=500)] = None


class LineColorIn(BaseModel):
    """行内颜色入参（第 2 层，ADR-0017）。

    ⚠️ ``UNIQUE (line_id, color_code)`` —— 同一匹布上同一颜色只能一行，
    但**一行可以 N 个颜色**（C32）。
    """

    model_config = ConfigDict(extra="forbid")

    color_code: ColorCode
    entry_mode: CuttingEntryMode = Field(
        default=CuttingEntryMode.MASTER, description="录入模式（颜色级，C26）"
    )
    qty_per_hand: Annotated[
        Decimal | None,
        Field(default=None, ge=0, description="模式 A/B 的默认每手件数（尺码明细行的才是权威值）"),
    ]
    uniform_qty: Annotated[
        Decimal | None, Field(default=None, ge=0, description="模式 B 的统一件数")
    ]
    size_lines: Annotated[
        list[SizeLineIn],
        Field(
            default_factory=list,
            max_length=MAX_SIZE_LINES_PER_COLOR,
            description=f"尺码明细，最多 {MAX_SIZE_LINES_PER_COLOR} 行",
        ),
    ]


class OrderLineIn(BaseModel):
    """布批行入参（第 1 层，**★ 耗料记在行**）。

    ⚠️ ``stock_id`` **必填**（ADR-0022 / C38）：不允许自由输入缸号，必须从
    ``material_stocks`` 里选一个已存在的布批行。而 ``dye_lot_no`` / ``bolt_no`` /
    ``material_id`` / ``supplier_id`` 是**服务端从 stock_id 反查**的快照 ——
    请求里**不接受**（传了就是给客户端两个可能互相矛盾的身份来源，
    同 ``base/schemas.py`` 的约定 1）。
    """

    model_config = ConfigDict(extra="forbid")

    line_no: Annotated[int, Field(ge=1, description="1 起；UNIQUE (doc_id, line_no)")]
    stock_id: UUID
    width_cm: Annotated[
        Decimal | None,
        Field(default=None, gt=0, description="铺布用门幅；不传取批次实测值（04 §7.7.2）"),
    ]
    fabric_qty: Annotated[
        Decimal, Field(gt=0, description="★ 行耗料（米），由铺布实耗正向录入，不由出数反推（C35）")
    ]
    waste_qty: Annotated[
        Decimal, Field(default=Decimal("0"), ge=0, description="布头（可再裁，入库）+ 布损（C11）")
    ]
    #: ★ **正向录入**的估算值（业务确认 2026-10-04 口径 A，C34）：
    #: 服务端只校验它 ``>= Σ(颜色 Σ尺码 output_qty)``，**不会**用它覆盖。
    #: 差值就是行余量 ``balance_qty``。
    output_qty: Annotated[
        Decimal, Field(default=Decimal("0"), ge=0, description="行可出件数估算（正向录入）")
    ]
    color_plan: Annotated[str | None, Field(default=None, max_length=64)] = None
    remark: Annotated[str | None, Field(default=None, max_length=500)] = None
    colors: Annotated[
        list[LineColorIn],
        Field(default_factory=list, max_length=MAX_COLORS_PER_LINE, description="行内颜色"),
    ]


class CuttingOrderCreateIn(BaseModel):
    """建裁剪单入参（表头 + 三层明细一次提交）。

    ⚠️ **表头汇总不接受传入**（``fabric_qty`` / ``output_qty`` / ``cut_waste_qty`` /
    ``balance_qty`` / ``hands_total``）：C6 明确「差异由 service 重算并覆盖入参，
    不信任前端」。所以这五列连字段都没有 —— 传了会被 ``extra="forbid"`` 挡下，
    报 ``10001`` 而不是「悄悄被忽略」。**能被忽略的入参是最坏的一种**：
    前端以为自己设的值生效了。
    """

    model_config = ConfigDict(extra="forbid")

    workshop_id: UUID = Field(description="车间（C2 必填；数据范围过滤依据 INV-8）")
    style_id: UUID
    doc_date: date = Field(description="单据日期，决定所属期间")
    delivery_date: date | None = None
    ply_count: Annotated[int, Field(default=1, ge=1, description="铺布层数（C4）")]
    entry_mode_default: CuttingEntryMode = Field(default=CuttingEntryMode.MASTER)
    remark_source: Annotated[str | None, Field(default=None, max_length=500)] = None
    remark: Annotated[str | None, Field(default=None, max_length=500)] = None
    lines: Annotated[
        list[OrderLineIn], Field(default_factory=list, max_length=MAX_LINES, description="布批行")
    ]


# ------------------------------------------------------------------ 出参片段


class SizeLineOut(BaseModel):
    """尺码明细出参。``output_qty`` / ``balance_qty`` 是 ``str``（docs/05 §3）。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    size_line_no: int
    size_code: str
    hands: int
    qty_per_hand: int
    output_qty: str = Field(description="= hands × qty_per_hand（精确整数，不取整）")
    output_qty_manual: bool = Field(description="是否人工指定过件数（C28）")
    balance_qty: str = Field(description="仅人工指定时 > 0 的差额")
    hands_seq: int | None = None
    remark: str | None = None


class LineColorOut(BaseModel):
    """行内颜色出参。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    line_id: UUID
    color_code: str
    entry_mode: CuttingEntryMode
    qty_per_hand: str | None = None
    uniform_qty: str | None = None
    ratio_snapshot: dict[str, Any] | None = Field(
        default=None, description="下单时的比例快照；**不接受前端传入**（C29 零污染）"
    )
    hands_total: str = Field(description="Σ(尺码 hands)，service 重算")
    output_qty_total: str = Field(description="Σ(尺码 output_qty)，service 重算")
    balance_qty_total: str = Field(description="Σ(尺码 balance_qty)；仅人工指定出数时 > 0")
    entry_mode_changed_at: datetime | None = None
    entry_mode_changed_by: UUID | None = None
    size_lines: list[SizeLineOut] = Field(default_factory=list)


class OrderLineOut(BaseModel):
    """布批行出参。

    ⚠️ ``output_qty`` 是**正向录入的估算值**，而 ``size_line_sum_qty`` 是
    **Σ(颜色 Σ尺码 output_qty)** —— 两者是**不同的数**，前者 ``>=`` 后者，
    差值就是 ``balance_qty``（C34 口径 A）。把两个数合成一个字段正是
    「行余量恒为 0」那个缺陷的来源，所以这里**两个都给**。
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    line_no: int
    stock_id: UUID
    supplier_id: UUID | None = None
    material_id: UUID
    style_no: str
    dye_lot_no: str
    bolt_no: str
    color_plan: str | None = None
    width_cm: str | None = None
    fabric_qty: str
    fabric_weight_kg: str | None = None
    waste_qty: str
    output_qty: str = Field(description="★ 正向录入的可出件数估算")
    size_line_sum_qty: str = Field(default="0", description="Σ(颜色 Σ尺码 output_qty)")
    balance_qty: str = Field(description="= output_qty - size_line_sum_qty（行余量，C34）")
    colors: list[LineColorOut] = Field(default_factory=list)


class CuttingOrderOut(BaseModel):
    """裁剪单出参（详情 = 三层结构；列表 = 三层为空）。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    doc_no: str = Field(description="CT-YYYYMMDD-6 位（C1）")
    workshop_id: UUID
    style_id: UUID
    style_no: str
    color_codes: str = Field(description="逗号分隔的色码汇总，仅列表/筛选用")
    doc_date: date
    delivery_date: date | None = None
    ply_count: int
    entry_mode_default: CuttingEntryMode
    fabric_qty: str = Field(description="= Σ行 fabric_qty（C6）")
    output_qty: str = Field(description="= Σ行 output_qty（C6）")
    cut_waste_qty: str = Field(description="裁损合计，**含 balance_qty**（C13）")
    balance_qty: str = Field(description="尾数（不足件，不入库 / 不出码 / 不计件）")
    hands_total: int = Field(description="= Σ尺码 hands")
    status: str = Field(description="DRAFT / SUBMITTED / APPROVED / REJECTED / CANCELLED")
    remark_source: str | None = None
    approved_by: UUID | None = None
    approved_at: datetime | None = None
    rejected_reason: str | None = None
    cancelled_reason: str | None = None
    version: int
    remark: str | None = None
    created_at: datetime
    updated_at: datetime
    lines: list[OrderLineOut] = Field(default_factory=list)


class CuttingOrderListOut(BaseModel):
    """列表行（省掉三层明细 —— 列表页不需要它们）。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    doc_no: str
    workshop_id: UUID
    style_no: str
    doc_date: date
    ply_count: int
    fabric_qty: str
    output_qty: str
    balance_qty: str
    hands_total: int
    status: str
    version: int
    created_at: datetime
