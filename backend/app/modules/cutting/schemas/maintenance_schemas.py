"""裁剪单的增量维护入参与比例建议出参（docs/05 §2、§3）。

PATCH 表头与三条 PUT（全量替换颜色 / 尺码明细 / 布批行）共用
:data:`common_schemas.Version` 作乐观锁；比例建议的 ``Suggest*Out`` 也在这里。
依赖方向只允许指向 :mod:`common_schemas` 与 :mod:`order_schemas`。
"""

from datetime import date
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.core.pydantic_types import Str
from app.modules.cutting.models import CuttingEntryMode

from .common_schemas import (
    MAX_COLORS_PER_LINE,
    MAX_LINES,
    MAX_SIZE_LINES_PER_COLOR,
    Version,
)
from .order_schemas import LineColorIn, OrderLineIn, SizeLineIn


class CuttingOrderPatchIn(BaseModel):
    """改表头（``PATCH /cutting-orders/{id}``，modules/02 §6）。

    ⚠️ **三层明细不在这里**：改明细必须走三条 PUT（各自带锁 + 各自重算），
    混进 PATCH 会让「改表头」这个轻操作去锁住整张单的行。
    """

    model_config = ConfigDict(extra="forbid")

    version: Version
    doc_date: date | None = None
    delivery_date: date | None = None
    ply_count: Annotated[int | None, Field(default=None, ge=1)]
    remark_source: Annotated[str | None, Field(default=None, max_length=500)] = None
    remark: Annotated[str | None, Field(default=None, max_length=500)] = None


class PutColorsIn(BaseModel):
    """行内颜色全量替换（``PUT /lines/{line_id}/colors``）。

    ⚠️ **全量替换语义**：不在 ``items`` 里的旧颜色被**软删**。这是刻意的 ——
    「按比例带出」需要能整组替换掉，而增删改混合的语义每次都要重新推导
    「哪些是新增、哪些是删除」，出错时静默留下一半旧数据。
    """

    model_config = ConfigDict(extra="forbid")

    version: Version
    items: Annotated[
        list[LineColorIn], Field(max_length=MAX_COLORS_PER_LINE, description="行内颜色（全量）")
    ]


class PutSizeLinesIn(BaseModel):
    """尺码明细全量替换（``PUT /size-lines``）。

    ⚠️ 定位靠 ``line_color_id``（不是 ``line_id + color_code``）：颜色是
    **全量替换**的，本次请求里的颜色行是**新建**的，用 ``color_code`` 定位
    会指向刚被软删的旧行。
    """

    model_config = ConfigDict(extra="forbid")

    version: Version
    line_color_id: UUID = Field(description="所属行内颜色")
    items: Annotated[
        list[SizeLineIn],
        Field(
            max_length=MAX_SIZE_LINES_PER_COLOR,
            description="尺码明细（全量；size_line_no 由服务端分配，前端传了会被拒）",
        ),
    ]


class EntryModeSwitchIn(BaseModel):
    """切换颜色级录入模式（``POST /entry-mode``，C26/C27）。

    ⚠️ ``confirm`` **必传且必须为 ``True``**：从 ``MASTER`` 切走会清掉该颜色
    现有的尺码明细 ``hands``，前端必须弹二次确认并明示「已有 N 行手数将被清空」。
    服务端不接受 ``False`` —— 那个「用户可能没看见弹窗」的场景代价太大。
    """

    model_config = ConfigDict(extra="forbid")

    version: Version
    line_color_id: UUID
    mode: CuttingEntryMode
    confirm: bool = Field(description="二次确认；切走 MASTER 时必须为 true")


class SuggestSizeLineOut(BaseModel):
    """比例建议的一行（``{size_code: ratio}`` 的一条）。"""

    model_config = ConfigDict(from_attributes=True)

    size_code: str
    ratio: Str = Field(description="建议手数（可小数，如 1.5 手）")


class SuggestLinesOut(BaseModel):
    """按比例带出的建议（``GET /suggest-lines``，C18/C19）。

    :param hands_total: ``Σratio`` = 建议手数合计，**不要求整数**（比例可小数，ADR-0013）
    :param missing_size_codes: 该款尺码集合里**还没配比例**的尺码。
        ⚠️ **部分缺配不拦**（C19②），这是「提示用户去补」的数据来源，
        而拦住它是 `20006` 的**另一种**触发条件（完全无配）
    """

    model_config = ConfigDict(from_attributes=True)

    items: list[SuggestSizeLineOut]
    hands_total: Str = Field(description="Σratio 建议手数合计；字符串（docs/05 §3）")
    missing_size_codes: list[str] = Field(default_factory=list)
    #: ⚠️ `ratio_snapshot` 已在带出时**写入** ``cutting_order_line_colors``
    #: （同一事务内，§7「带出建议时读到的比例在同一事务内写入」）。
    #: 之所以不留「等保存时再写」的口子：主数据随时可能被改，
    #: 隔一个请求再快照，拍下来的就可能是**另一份**比例。
    ratio_snapshot: dict[str, Any] = Field(description="{size_code: ratio} 快照，已落库")


class RatioWarningOut(BaseModel):
    """C20 的「黄色提示」：``Σhands`` 与 ``Σratio`` 偏离超阈值，**不拦**。

    ⚠️ 刻意**不是**错误码：modules/02 C20 写明「偏离 > 20% 给黄色提示**不拦截**，
    裁剪明细可以任意偏离比例而**不需要任何理由**」（C18）。
    """

    actual_hands: str = Field(description="本单该颜色实际 Σhands")
    suggested_hands: str = Field(default="0", description="Σratio；无比例时为 0")
    deviation_pct: str = Field(default="0", description="偏离百分比，正数偏高、负数偏低")


class PutLinesIn(BaseModel):
    """布批行**全量替换**（``PUT /cutting-orders/{id}/lines``）。

    ⚠️ 全量替换意味着不在 ``items`` 里的旧行被**软删**（连同其颜色与尺码明细）。
    「删掉某一行」与「改某一行」因此走同一个接口 —— 前端只需把页面上现有的行
    原样带上再改要改的那几个。
    """

    model_config = ConfigDict(extra="forbid")

    version: Version
    items: Annotated[list[OrderLineIn], Field(max_length=MAX_LINES, description="布批行（全量）")]
