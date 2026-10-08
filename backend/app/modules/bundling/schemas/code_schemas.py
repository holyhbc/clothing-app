"""打菲**码**侧的入参/出参模型：拆分预演、标签两接口、码列表/详情、单码作废。

（T-BUND-007b；docs/05 §2、§3 + modules/03 §5.5、§5.4、§6）

⚠️ **这里装的是「一手 = 一个码」视角的全部契约**（ADR-0016）：预演说「会生成哪些码」、
标签按手印、码查询按手列、作废按手撤。它们同属一个口径，分开放会让「共 M 手」在
四个文件里各算一遍，而那正是标签上印错手数的来源。

⚠️ 依赖方向单向：本模块 ``import .order_schemas`` 只为 ``MAX_REASON``（作废单码与
驳回同属「必须填原因」那一族），**不**被 ``order_schemas`` 反向引用。
"""

from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.common.enums import LabelExportFormat
from app.core.pydantic_types import Str

from .order_schemas import MAX_REASON

# ------------------------------------------------------------------ 拆分预演（T-BUND-004 接线）


class SplitLineIn(BaseModel):
    """预演的一行（modules/03 §5.5 的 ``{size_code, hands, cutting_size_line_id}``）。

    ⚠️ ``cutting_size_line_id`` 必带：每手件数取**那条裁剪尺码明细**的 ``qty_per_hand``
    （Q-B15），不传就等于让服务端猜「这一手几件」。
    """

    model_config = ConfigDict(extra="forbid")

    size_code: Annotated[str, Field(max_length=16, description="尺码码")]
    hands: Annotated[int, Field(ge=1, description="★ 本行手数（= 码数），必须 > 0")]
    cutting_size_line_id: Annotated[
        UUID, Field(description="★ 引用裁剪尺码明细行（每手件数的权威来源）")
    ]


class SplitPreviewIn(BaseModel):
    """预演入参（``POST /bundling-orders/{id}/split``，**整个 body 可省**）。

    ⚠️ **不传 body = 用当前单据的行**（modules/03 §5.5）：主管想核对的是「这张单会打成
    什么样」，而绝大多数时候这个问题的答案就在单据本身。传 ``lines`` 才是「我改了手数，
    先看看会变成什么样」的试算。
    ⚠️ ``color_code`` **不在入参里**：色码一律取本单（ADR-0016 一码一色），让前端指定
    色码等于让它挑一个服务端不认的口径。
    """

    model_config = ConfigDict(extra="forbid")

    lines: Annotated[
        list[SplitLineIn] | None,
        Field(default=None, min_length=1, max_length=500, description="不传 = 用本单当前明细"),
    ] = None


class SplitBundleOut(BaseModel):
    """预演出的**一手**（modules/03 §5.5 的 ``bundles[]``）。"""

    model_config = ConfigDict(from_attributes=True)

    bundle_no: str
    hands: Annotated[int, Field(description="手序号（第 N 手）")]
    bundle_qty: Str = Field(description="该手件数（= 裁剪尺码明细 qty_per_hand）")


class SplitPreviewLineOut(BaseModel):
    """一个尺码的预演结果（modules/03 §5.5 的 ``previews[]``）。"""

    model_config = ConfigDict(from_attributes=True)

    color_code: str
    size_code: str
    cutting_size_line_id: UUID
    hands: Annotated[int, Field(description="本行手数 = 码数")]
    bundle_qty: Str = Field(description="该手件数")
    remainder_qty: Str = Field(description="余数（整件口径下恒为 0，尾数不出码）")
    start_hand_seq: Annotated[int, Field(description="本行第一手的序号（Q-B13 跨行接着编）")]
    bundles: list[SplitBundleOut]


class HandConflictOut(BaseModel):
    """一个手序号冲突（提交 / 审核时会变成 ``31005``，预演**展示而不报错**）。"""

    model_config = ConfigDict(from_attributes=True)

    code: Annotated[str, Field(description="冲突错误码（恒为 31005）")]
    color_code: str
    size_code: str
    hands: int
    bundle_no: str = Field(description="预演将生成的码")
    existing_bundle_no: str = Field(description="库里已存在的 ACTIVE 码")
    message: str


class SplitPreviewOut(BaseModel):
    """预演响应（modules/03 §5.5；**只读不落库**，不预占、不写日志）。"""

    model_config = ConfigDict(from_attributes=True)

    previews: list[SplitPreviewLineOut] = Field(description="逐尺码的拆分预览")
    hands_total: Annotated[int, Field(description="合计手数（= 将生成的码数）")]
    planned_qty: Str = Field(description="合计件数")
    balance_qty: Str = Field(description="合计余数（整件口径恒为 0）")
    conflicts: list[HandConflictOut] = Field(
        description="手序号与库内已有 ACTIVE 码冲突的清单（提交后会报 31005）"
    )


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


# ------------------------------------------------------------------ 码查询 / 单码作废（T-BUND-007b）


class BundleListOut(BaseModel):
    """码列表行（``GET /bundles``，modules/03 §6）。

    ⚠️ **带 ``hands_total_of_size``（共 M 手）**：列表页要能直接显示「第 N 手 / 共 M 手」
    （03 §11.5.2 的单内「按手列表」），而前端做这个拼接需要每一行的分母。
    ⚠️ 数量（``bundle_qty`` / ``counted_qty``）是 ``Str``，序号与计数（``hands`` /
    ``hands_total_of_size``）是 ``int`` —— 与既有出参同口径（05 §3）。
    """

    model_config = ConfigDict(from_attributes=True)

    bundle_no: str
    doc_id: UUID
    line_id: UUID
    hands: Annotated[int, Field(description="★ 手序号（第 N 手）")]
    hands_total_of_size: Annotated[int, Field(description="★ 共 M 手（该尺码总手数）")]
    style_no: str
    color_code: str
    size_code: str
    operation_no: str
    bundle_qty: Str = Field(description="该手件数")
    counted_qty: Str = Field(description="已计件数（部分生产时小于 bundle_qty）")
    counted_by_name: Annotated[str | None, Field(description="计件人姓名（未计件为空）")]
    counted_at: Annotated[
        Any | None, Field(default=None, description="计件时间（判「未计件」看它是否为空）")
    ]
    status: Annotated[str, Field(description="ACTIVE / VOIDED")]
    qr_content: str


class LabelPrintRecordOut(BaseModel):
    """一行打印留痕（码详情里的「打印记录列表」，append-only，B15）。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    hands_seq: Annotated[int, Field(description="打印时记下的手序号快照")]
    hands_total_of_size: int | None = None
    printed_qty: Annotated[int, Field(description="本次打印张数")]
    is_reprint: bool
    print_seq: int | None = None
    printed_by: UUID
    printed_at: Any


class BundleDetailOut(BundleListOut):
    """单码详情（``GET /bundles/{bundle_no}``，modules/03 §6 + ADR-0016 §6）。

    ⚠️ 比列表多三组字段，且**都是现场真会问的**：
        ① ``cutting_size_line_id`` + ``dye_lot_no`` / ``bolt_no``（**回查裁剪来源**：
        「这手是从哪缸哪匹布裁出来的」是车间与跟单最高频的追溯问句）
        ② ``voided_at`` / ``void_reason``（作废是终态，扫码枪要能显示「为什么作废」）
        ③ ``print_records``（B15：重打要能逐次追溯，「印过几次」靠它）
    """

    cutting_size_line_id: UUID = Field(description="★ 引用裁剪尺码明细行（件数权威来源）")
    dye_lot_no: Annotated[
        str | None, Field(default=None, description="★ 来源缸号（回查 cutting_order_lines）")
    ]
    bolt_no: Annotated[
        str | None, Field(default=None, description="★ 来源匹号（09 §1.2，与缸号共同构成批次身份）")
    ]
    voided_at: Annotated[Any | None, Field(default=None, description="作废时间（未作废为空）")]
    void_reason: str | None = None
    print_records: list[LabelPrintRecordOut] = Field(
        default_factory=list, description="打印留痕（按打印时间倒序，append-only）"
    )


class VoidCodeIn(BaseModel):
    """作废单码入参（``POST /bundles/{bundle_no}/voids``，modules/03 §6 + 08 §2.2）。

    ⚠️ ``void_reason`` **必填**（08 §2.2「作废码必填原因」）：``min_length=1`` 挡得住
    「没传」，挡不住全空白（``"   "``），所以 service 里还有一次 ``require_reason`` 报
    ``10002`` —— 两层不是重复，一个管「有没有」，一个管「有没有认真填」。
    """

    model_config = ConfigDict(extra="forbid")

    void_reason: Annotated[
        str,
        Field(min_length=1, max_length=MAX_REASON, description="作废原因（必填，≤500 字）"),
    ]


class VoidCodeOut(BaseModel):
    """作废单码响应。

    ⚠️ **不含**结转数字：单码作废**不动** ``cutting_outputs``（08 §2.2「作废码写
    ``voided_at`` + ``void_reason``」），所以响应里给出任何结转相关的数都会让前端
    以为结转变了 —— 而那正是「结转与实际码数长期不一致」的起点。
    """

    model_config = ConfigDict(from_attributes=True)

    bundle_no: str
    doc_no: str
    status: Annotated[str, Field(description="恒为 VOIDED（B12 不可恢复，行保留）")]
    voided_at: Any
    void_reason: str


__all__ = [
    "BundleDetailOut",
    "BundleListOut",
    "HandConflictOut",
    "LabelExportOut",
    "LabelExportQuery",
    "LabelItemOut",
    "LabelPrintIn",
    "LabelPrintOut",
    "LabelPrintRecordOut",
    "SplitBundleOut",
    "SplitLineIn",
    "SplitPreviewIn",
    "SplitPreviewLineOut",
    "SplitPreviewOut",
    "VoidCodeIn",
    "VoidCodeOut",
]
