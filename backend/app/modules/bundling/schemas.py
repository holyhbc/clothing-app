"""打菲单草稿态的入参/出参模型（docs/05 §2、§3 + modules/03 §4/§7）。

⚠️ **表头汇总列不入参**（`hands_total` / `output_qty` / `balance_qty` / `planned_qty`）：
service 重算并覆盖，不信任前端。所以这几列连字段都没有 —— 传了会被
``extra="forbid"`` 挡下，报 ``10001`` 而不是「悄悄被忽略」。
**能被忽略的入参是最坏的一种**：前端以为自己设的值生效了。

⚠️ 响应数量一律 ``str``（docs/05 §3）：pydantic v2 的 ``str`` 不接受 ``Decimal``，
直接从 ORM 构造会报 ``Input should be a valid string [input_value=Decimal('96.000')]``，
完全看不出根因是「出参类型不能直接从 ORM 构造」。用 :mod:`app.core.pydantic_types.Str`
统一承接。
"""

from datetime import date
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.common.enums import LabelExportFormat
from app.core.pydantic_types import Str

#: 单页最大行数。打菲单明细通常 ≤ 20 行（色码 × 尺码），上限留 500 防恶意提交。
MAX_LINES = 500

#: 状态动作的**原因**最大长度（驳回 / 作废）。与 ``remark`` 同一口径 —— DB 列是
#: ``text`` 放得下，这里限的是「让人能在列表页一眼看完」的长度，不是存储上限。
MAX_REASON = 500


class LineIn(BaseModel):
    """打菲明细入参（按尺码，每行必带 `cutting_size_line_id`）。

    ⚠️ ``size_code`` 允许同尺码多行（同尺码可来自裁剪的多个布批 / 多条尺码明细行，
    ADR-0017 §4）；唯一键是 ``(doc_id, line_no)``。

    ⚠️ ``cutting_size_line_id`` 是每行**必填**（B26）—— 它是件数与手数的权威来源，
    `size_code` / `color_code` 为冗余快照。
    """

    model_config = ConfigDict(extra="forbid")

    line_no: Annotated[int, Field(ge=1, description="1 起；UNIQUE (doc_id, line_no)")]
    color_code: Annotated[str, Field(max_length=16, description="色码（与表头一致）")]
    size_code: Annotated[str, Field(max_length=16, description="尺码码")]
    operation_no: Annotated[str, Field(max_length=16, description="工序号（行级冗余，与表头一致）")]
    cutting_size_line_id: Annotated[
        UUID, Field(description="★ 引用裁剪的尺码明细行（件数与手数权威来源）")
    ]
    hands: Annotated[int, Field(ge=1, description="★ 本行手数（整数，ADR-0020）")]
    #: 计划件数。**不入参**（由 service 重算 = hands × qty_per_hand）。
    #: 前端可传用于预览，但服务端会用裁剪明细的 qty_per_hand 重算。
    planned_qty: Annotated[
        int | None, Field(default=None, ge=0, description="预览用，服务端重算")
    ] = None
    group_no: Annotated[
        str | None, Field(default=None, max_length=32, description="派给的车间组别")
    ] = None
    workstation_no: Annotated[
        str | None, Field(default=None, max_length=32, description="工位号")
    ] = None
    remark: Annotated[str | None, Field(default=None, max_length=500)] = None


class BundlingOrderCreateIn(BaseModel):
    """建打菲单入参（表头 + 明细一次提交，modules/03 §4 `create`）。

    ⚠️ **表头汇总不接受传入**（`hands_total` / `output_qty` / `balance_qty` / `planned_qty`）：
    modules/03 §4 明确「差异由 service 重算并覆盖入参，不信任前端」。
    """

    model_config = ConfigDict(extra="forbid")

    workshop_id: Annotated[UUID, Field(description="车间（数据范围过滤依据 INV-8）")]
    style_no: Annotated[str, Field(max_length=32, description="款号")]
    operation_no: Annotated[
        str, Field(max_length=16, description="工序号（B9：不同工序必开不同单）")
    ]
    color_group: Annotated[str, Field(max_length=32, description="色组")]
    color_code: Annotated[str, Field(max_length=16, description="色码（B2/B3：一码一色）")]
    bundle_qty: Annotated[
        int, Field(ge=1, description="一扎几件（录入参考值，审核后不作权威，Q-B11）")
    ]
    doc_date: Annotated[date, Field(description="单据日期，决定所属期间")]
    source_cutting_order_id: Annotated[UUID, Field(description="来源裁剪单，必须 APPROVED（B8）")]
    lines: Annotated[
        list[LineIn],
        Field(
            min_length=1,
            max_length=MAX_LINES,
            description="明细行（每行必带 cutting_size_line_id）",
        ),
    ]
    remark: Annotated[str | None, Field(default=None, max_length=500)] = None


class BundlingOrderPatchIn(BaseModel):
    """改表头（``PATCH /bundling-orders/{id}``，modules/03 §4 `update`）。

    ⚠️ **明细不在这里**：改明细必须走 ``PUT /lines``（全量替换，各自带锁 + 各自重算）。
    混进 PATCH 会让「改个备注」也要锁住整张单的明细。
    """

    model_config = ConfigDict(extra="forbid")

    version: Annotated[int, Field(ge=1, description="乐观锁版本号")]
    doc_date: Annotated[date | None, Field(default=None, description="单据日期")] = None
    bundle_qty: Annotated[
        int | None, Field(default=None, ge=1, description="一扎几件（参考值）")
    ] = None
    remark: Annotated[str | None, Field(default=None, max_length=500)] = None


class PutLinesIn(BaseModel):
    """明细**全量替换**（``PUT /bundling-orders/{id}/lines``，modules/03 §4 `put_lines`）。

    ⚠️ **全量替换语义**：不在 ``items`` 里的旧行被**软删**。这是刻意的 ——
    「按比例带出」需要能整组替换掉，而增删改混合的语义每次都要重新推导
    「哪些是新增、哪些是删除」，出错时静默留下一半旧数据。
    """

    model_config = ConfigDict(extra="forbid")

    version: Annotated[int, Field(ge=1, description="乐观锁版本号")]
    items: Annotated[list[LineIn], Field(max_length=MAX_LINES, description="明细行（全量）")]


# ------------------------------------------------------------------ 状态动作入参（T-BUND-005a）


class RejectIn(BaseModel):
    """驳回入参（``POST .../rejections``；08 §1.1：**必填原因**）。

    ⚠️ ``reason`` 落**两处**：``bundling_orders.rejected_reason`` 与
    ``document_logs.reason`` —— 只落后者的话，单据上看不到「为什么被驳回」。
    ⚠️ ``min_length=1`` 挡不住全空白（``"   "``），所以 service 里还有一次
    空白校验并报 ``10002``（03 §9：缺原因 → ``10002`` 弹原因输入框）。
    """

    model_config = ConfigDict(extra="forbid")

    reason: Annotated[
        str,
        Field(min_length=1, max_length=MAX_REASON, description="驳回原因（必填，≤500 字）"),
    ]


class CancelIn(BaseModel):
    """作废入参（``POST .../cancellations``；08 §1.1：必填原因，终态）。

    ⚠️ 字段名是 ``cancelled_reason`` 而不是 ``reason``：表列同名，而
    ``reason`` 会被误当成通用原因字段，将来反审核（``reverse``，也要必填原因）
    接手时就出现「两个字段都是原因」的歧义。
    """

    model_config = ConfigDict(extra="forbid")

    cancelled_reason: Annotated[
        str, Field(min_length=1, max_length=MAX_REASON, description="作废原因（必填，≤500 字）")
    ]


# ------------------------------------------------------------------ 出参片段


class LineOut(BaseModel):
    """打菲明细出参。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    line_no: int
    color_code: str
    size_code: str
    operation_no: str
    cutting_size_line_id: UUID
    hands: int
    planned_qty: Str = Field(description="= hands × qty_per_hand（精确整数，service 重算）")
    available_qty_before: Str = Field(description="提交时从 cutting_outputs 读到的可用量快照")
    group_no: str | None = None
    workstation_no: str | None = None
    remark: str | None = None
    version: int
    created_at: Any
    updated_at: Any


class BundlingOrderOut(BaseModel):
    """打菲单出参（详情 = 表头 + 明细；列表 = 明细为空）。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    doc_no: str = Field(description="BD-YYYYMMDD-6 位（B1）")
    workshop_id: UUID
    style_no: str
    operation_no: str
    color_group: str
    color_code: str
    bundle_qty: int = Field(description="一扎几件（录入参考值，审核后不作权威）")
    hands_total: int = Field(description="= Σ明细 hands = 生成码数（B21）")
    output_qty: Str = Field(description="= Σ bundles.bundle_qty（手数 × 每手件数）")
    balance_qty: Str = Field(description="本单余数（仅裁剪侧人工指定出数时产生）")
    source_cutting_order_id: UUID
    label_print_qty: int
    status: str = Field(description="DRAFT / SUBMITTED / APPROVED / REJECTED / CANCELLED")
    version: int
    approved_by: UUID | None = None
    approved_at: Any | None = None
    rejected_reason: str | None = None
    cancelled_reason: str | None = None
    doc_date: date
    remark: str | None = None
    created_at: Any
    updated_at: Any
    lines: list[LineOut] = Field(default_factory=list)


class BundlingOrderListOut(BaseModel):
    """列表行（省掉明细 —— 列表页不需要它们）。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    doc_no: str
    workshop_id: UUID
    style_no: str
    operation_no: str
    color_code: str
    doc_date: date
    hands_total: int
    output_qty: Str
    balance_qty: Str
    status: str
    version: int
    created_at: Any


# ------------------------------------------------------------------ 标签（T-BUND-006）


class LabelExportQuery(BaseModel):
    """标签导出查询条件（``GET /bundling-orders/{id}/labels``，modules/03 §6）。

    ⚠️ **手号区间在每个尺码内各自成立**（Q-B13：同尺码手号连续编到 N），所以
    ``from_hands=1&to_hands=2`` 命中的是每个尺码的 1、2 手，不是全单的前两手。
    """

    model_config = ConfigDict(extra="forbid")

    from_hands: Annotated[
        int | None, Field(default=None, ge=1, description="起始手号（含，每尺码各自）")
    ] = None
    to_hands: Annotated[
        int | None, Field(default=None, ge=1, description="结束手号（含，每尺码各自）")
    ] = None
    size_code: Annotated[
        str | None, Field(default=None, max_length=16, description="只导某个尺码；不传=全部")
    ] = None
    export_format: Annotated[
        LabelExportFormat, Field(default=LabelExportFormat.DATA, description="data / csv")
    ] = LabelExportFormat.DATA


class LabelItemOut(BaseModel):
    """**一手**的标签数据（03 §5.4 标签内容清单）。

    ⚠️ ``bundle_qty`` 是 ``Str``（05 §3「数量一律字符串」）；而 ``hands_seq`` /
    ``hands_total_of_size`` 是**序号与计数**，与既有出参（``hands_total`` /
    ``label_print_qty``）同口径用 ``int`` —— 标成字符串会让前端算「共 M 手」时做字符串拼接。
    """

    model_config = ConfigDict(from_attributes=True)

    bundle_no: str
    style_no: str
    color_code: str
    size_code: str
    operation_no: str
    hands_seq: Annotated[int, Field(description="★ 第 N 手")]
    hands_total_of_size: Annotated[int, Field(description="★ 共 M 手（该尺码总手数）")]
    bundle_qty: Str = Field(description="★ 该手件数（= bundles.bundle_qty）")
    hands_text: str = Field(description="★ 「第 N 手 / 共 M 手」成品文案（B25 必印）")
    qr_content: str = Field(description="二维码内容（恒等于 bundle_no，ADR-0004）")
    barcode_content: str = Field(description="条码内容（Code128，同 qr_content，B6）")


class LabelExportOut(BaseModel):
    """标签导出响应（03 §6）。"""

    model_config = ConfigDict(from_attributes=True)

    doc_no: str
    size_code: str | None = None
    hands_count: Annotated[int, Field(description="本次导出的手数 = items 行数")]
    total_qty: Str = Field(description="本次导出的件数合计")
    items: list[LabelItemOut]
    csv_text: Annotated[
        str | None, Field(default=None, description="仅 format=csv 时有值（表头 1 行 + 每手 1 行）")
    ] = None


class LabelPrintIn(BaseModel):
    """打印登记入参（``POST /bundling-orders/{id}/label-prints``，modules/03 §6 / B15）。

    ⚠️ ``hands_seq`` **必传但允许为缺省**：缺省由 service 报 ``10002``（03 §6 明列的码），
    而不是让 pydantic 先报 ``10001`` —— 少一个必填业务参数的码与少一个非法参数是不同的提示。
    ⚠️ ``size_code`` 是本卡补的：手号是**每个尺码各自**编号的，不带尺码时「第 3 手」
    根本指不到具体哪一手（同单多尺码时）。默认 ``None`` = 本单全部尺码。
    """

    model_config = ConfigDict(extra="forbid")

    hands_seq: Annotated[
        int | None, Field(default=None, ge=1, description="★ 本次打印第几手（必传，缺失 10002）")
    ] = None
    size_code: Annotated[
        str | None, Field(default=None, max_length=16, description="只登记某个尺码；不传=全部")
    ] = None
    hands_total_of_size: Annotated[
        int | None, Field(default=None, ge=1, description="共 M 手快照（与服务端权威值核对）")
    ] = None
    from_hands: Annotated[
        int | None, Field(default=None, ge=1, description="区间起点；不传=取 hands_seq")
    ] = None
    to_hands: Annotated[
        int | None, Field(default=None, ge=1, description="区间终点；不传=单手")
    ] = None
    printed_qty: Annotated[
        int, Field(default=1, ge=1, description="每手本次打印张数（多打备用时 >1）")
    ] = 1
    is_reprint: Annotated[bool, Field(default=False, description="是否重打（B15）")] = False
    print_seq: Annotated[
        int | None, Field(default=None, ge=1, description="重打批次序号（重打必填）")
    ] = None


class LabelPrintOut(BaseModel):
    """打印登记响应（03 §6 的 ``print_id`` / ``printed_count``）。

    ⚠️ ``print_id`` 改为 ``print_ids`` **列表**：一次登记按手**逐行**留痕
    （哪一手印过要能逐行追溯），多手时不存在单一的 id。
    """

    model_config = ConfigDict(from_attributes=True)

    doc_no: str
    print_ids: list[UUID] = Field(description="本次追加的留痕行 id（每手一行）")
    printed_count: Annotated[int, Field(description="本次打印张数 = 每手张数 × 手数")]
    hands_total: Annotated[int, Field(description="本次登记的手数")]
    label_print_qty: Annotated[int, Field(description="本单累计打印张数（重打也累加）")]
    is_reprint: bool
    print_seq: int | None = None
