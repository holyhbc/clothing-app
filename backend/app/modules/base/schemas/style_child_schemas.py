"""款号子表：比例、款号工序、模板复制的请求 / 响应模型（T-BASE-002）。"""

from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.common.enums import ConflictPolicy, RateSource, TemplateCopyMode

from .common_schemas import Versioned
from .rate_schemas import OperationRateOut
from .style_schemas import StyleColorOut, StyleListOut, StyleSizeOut


class RatioItemIn(BaseModel):
    """比例行。``ratio`` 是**手数**（可小数，如 1.5 手）。"""

    model_config = ConfigDict(extra="forbid")

    size_code: Annotated[str, Field(min_length=1, max_length=32)]
    ratio: Annotated[
        Decimal,
        Field(gt=0, max_digits=14, decimal_places=4, description="手数；必须 > 0（DB CHECK）"),
    ]


class RatioReplaceIn(BaseModel):
    """按 ``(style_no, color_code)`` **全量替换**比例（modules/01 §6）。

    ⚠️ ``version`` 取的是**款号聚合行** ``styles.version``，不是比例行的版本：
    全量替换会把旧行全删再全插，比例行自己的 ``version`` 每次都从 1 重来，
    用它当乐观锁等于没有锁。设计稿 §5 允许"MAX(version) 或建聚合行"，
    这里选聚合行（``styles`` 天然就是这一族子表的聚合根）。
    """

    model_config = ConfigDict(extra="forbid")

    style_no: Annotated[str, Field(min_length=1, max_length=32)]
    color_code: Annotated[str, Field(min_length=1, max_length=32)]
    version: Annotated[int, Field(ge=1, description="款号 version（聚合行乐观锁，不匹配 → 10003）")]
    items: Annotated[
        list[RatioItemIn],
        Field(max_length=100, description="比例行，最多 100 行（全量替换语义）"),
    ]


class RatioOut(Versioned):
    style_no: str
    color_code: str
    size_code: str
    ratio: str = Field(description="手数；响应一律字符串（docs/05 §3）")


class RatioListOut(BaseModel):
    """比例查询结果。

    :param hands_total: ``Σratio``（手数合计，**不要求整数**）
    :param missing_size_codes: 该款尺码集合里**还没配比例**的尺码，供前端提示去补
        （R24：部分缺配只提示不拦）
    """

    items: list[RatioOut]
    hands_total: str = Field(description="手数合计 Σratio；字符串（docs/05 §3）")
    missing_size_codes: list[str] = Field(
        default_factory=list, description="该款已定义但该色未配比例的尺码（仅提示，不拦）"
    )
    document_log_id: UUID | None = Field(default=None, description="写入时的操作日志 id")


class StyleOperationItemIn(BaseModel):
    """款号工序行。

    ⚠️ ``operation_no`` 必须在 ``operations`` 里**存在且启用**（modules/01 §6），
    这条校验在 service 里做（要查库）；``sequence > 0`` / ``bundle_qty > 0``
    这里就拦掉（DB 也有 CHECK，双保险）。
    """

    model_config = ConfigDict(extra="forbid")

    operation_no: Annotated[str, Field(min_length=1, max_length=16)]
    sequence: Annotated[int, Field(ge=1, description="工序顺序 1,2,3…")]
    bundle_qty: Annotated[
        Decimal,
        Field(gt=0, max_digits=14, decimal_places=3, description="该款该工序一扎几件（R9）"),
    ]
    is_piecework: bool = Field(default=True, description="该款该工序是否计件")
    is_final_operation: bool = Field(
        default=False, description="是否最后一道工序（整烫）；一款至多一道"
    )
    remark: str | None = Field(default=None, max_length=500)


class StyleOperationsReplaceIn(BaseModel):
    """按款号**全量替换**款号工序配置（modules/01 §6）。"""

    model_config = ConfigDict(extra="forbid")

    version: Annotated[int, Field(ge=1, description="款号 version（聚合行乐观锁）")]
    items: Annotated[
        list[StyleOperationItemIn],
        Field(max_length=500, description="款号工序行，最多 500 行（全量替换语义）"),
    ]


class StyleOperationOut(Versioned):
    style_no: str
    operation_no: str
    operation_name: str | None = Field(default=None, description="工序名（列表直接显示用）")
    sequence: int
    bundle_qty: str = Field(description="一扎件数；字符串（docs/05 §3）")
    is_piecework: bool = True
    is_final_operation: bool = False
    remark: str | None = None


class StyleOperationsListOut(BaseModel):
    items: list[StyleOperationOut]
    document_log_id: UUID | None = None


class StyleDetailOut(BaseModel):
    """款号详情 = 款号 + 色组 + 尺码 + 款号工序 + 现行价（设计稿 §4.5）。"""

    style: StyleListOut
    colors: list[StyleColorOut]
    sizes: list[StyleSizeOut]
    operations: list[StyleOperationOut]
    current_rates: list["OperationRateOut"] = Field(
        default_factory=list, description="各工序的当前有效价（effective_to IS NULL）"
    )


class TemplateCopyIn(BaseModel):
    """工序与单价模板复制（modules/01 §5.1 / ADR-0009 §3）。

    ``copy_mode`` 与 ``conflict_policy`` **都必填、不设系统默认** —— 默默覆盖别人
    配好的工价比报错危险得多（ADR-0026 风险 ③ 同类问题）。
    """

    model_config = ConfigDict(extra="forbid")

    copy_mode: TemplateCopyMode = Field(description="①沿用源价 ②加比例 ③只复制工序结构")
    conflict_policy: ConflictPolicy = Field(description="目标款号已有同工序时怎么办")
    price_ratio: Decimal | None = Field(
        default=None,
        gt=0,
        max_digits=6,
        decimal_places=4,
        description="模式②必填：1.0800 = 上浮 8%",
    )

    @model_validator(mode="after")
    def _ratio_required_for_mode_2(self) -> "TemplateCopyIn":
        if self.copy_mode == TemplateCopyMode.COPY_PRICE_WITH_RATIO and self.price_ratio is None:
            raise ValueError("copy_mode=COPY_PRICE_WITH_RATIO 时 price_ratio 必填且 > 0")
        return self


class CopiedPriceOut(BaseModel):
    """逐项新价回显（ADR-0029 验证方式：复制错误率必须为 0，逐项核对）。"""

    operation_no: str
    source_unit_price: str
    target_unit_price: str = Field(description="四舍五入到 6 位后的新价")
    rate_source: RateSource = Field(description="源价命中的档位；档位2（分类价）不复制")


class TemplateCopyOut(BaseModel):
    """复制结果。

    ⚠️ ``messages`` 里**必须**带「尺码比例未复制」提示（modules/01 §5.1）：
    不提示的话用户以为已经全配好了，问题会推迟到出布头对不上账时才爆。
    """

    target_style_no: str
    source_style_no: str
    copy_mode: TemplateCopyMode
    conflict_policy: ConflictPolicy
    structure_written: int = Field(ge=0, description="写入的工序结构行数")
    structure_overwritten: int = Field(default=0, ge=0, description="覆盖的工序结构行数")
    price_written: int = Field(default=0, ge=0, description="写入的单价行数")
    price_overwritten: int = Field(default=0, ge=0, description="关闭旧区间后重写的单价行数")
    skipped: list[dict[str, str]] = Field(
        default_factory=list,
        description="跳过明细：{target: operation_no, reason: ...}",
    )
    prices: list[CopiedPriceOut] = Field(default_factory=list, description="逐项新价，供人工核对")
    messages: list[str] = Field(
        default_factory=list, description="给用户的提示（含尺码比例未复制）"
    )
    document_log_id: UUID | None = None
    operations: list[StyleOperationOut] = Field(
        default_factory=list, description="复制后的工序快照"
    )
    rates: list["OperationRateOut"] = Field(default_factory=list, description="复制后的单价快照")
