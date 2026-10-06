"""工序单价的请求 / 响应模型（ADR-0026，T-BASE-002）。"""

from datetime import date
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.common.enums import RateSource

from .common_schemas import Versioned


class OperationRateCreate(BaseModel):
    """设价 / 调价（ADR-0026 §4、R11/R18/R19/R20）。

    ⚠️ ``style_no`` 与 ``product_category_id`` 的三种合法组合（**不能同时给**）：

    ====================================  ==========================  ============
    ``style_no``  ``product_category_id`` 档位                       ``rate_source``
    ====================================  ==========================  ============
    给             不给                     1 款号专用价              ``STYLE``
    不给           给                       2 分类通用价              ``CATEGORY``
    不给           不给                     3 全厂同工序统一价        ``OPERATION``
    ====================================  ==========================  ============

    ⚠️ 任务卡 TC-B29 写的是「同时为空 → 10001」，与 ADR-0026 §2 的档位 3 直接
    冲突（业务方 2026-10-03 明确确认**存在**「全厂同工序统一价」这一档）。
    这里按 ADR-0026 §1 已修正的 CHECK（"不能同时有值"）实现，
    并把「同时有值」拦成 ``10001``。已登记为需修订项。
    """

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "operation_no": "01",
                "style_no": "HB-2026-0001",
                "unit_price": "0.350000",
                "effective_from": "2026-08-16",
                "reason": "2026 年 8 月工价上调",
            }
        },
    )

    operation_no: Annotated[str, Field(min_length=1, max_length=16)]
    unit_price: Annotated[
        Decimal, Field(ge=0, max_digits=12, decimal_places=6, description="工价 元/件")
    ]
    effective_from: date = Field(description="生效日（含）；允许未来日期（R19）")
    style_no: str | None = Field(
        default=None, max_length=32, description="档位1 必填；给分类价时留空"
    )
    product_category_id: UUID | None = Field(default=None, description="档位2 必填")
    effective_to: date | None = Field(
        default=None, description="失效日（不含）；不给 = 长期有效（当前档）"
    )
    reason: str | None = Field(default=None, max_length=500, description="调价必填（R20 → 10006）")

    @model_validator(mode="after")
    def _not_both(self) -> "OperationRateCreate":
        if self.style_no is not None and self.product_category_id is not None:
            raise ValueError(
                "style_no 与 product_category_id 不能同时给：一个价要么限款号、要么限分类"
            )
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("effective_to 必须晚于 effective_from（区间不能为空）")
        return self


class OperationRateOut(Versioned):
    """单价区间行。

    ``is_current`` 是**派生**字段（``effective_to IS NULL``），只出现在响应里，
    不入库 —— 04 §7.8.3 明确"不设 is_current 布尔列"。
    """

    operation_no: str
    style_no: str | None = Field(default=None, description="空 = 不限款号（档位2/3）")
    product_category_id: UUID | None = Field(default=None, description="空 = 不限分类")
    effective_from: date
    effective_to: date | None = None
    unit_price: str = Field(description="工价；字符串（docs/05 §3）")
    reason: str | None = None
    is_current: bool = Field(default=False, description="effective_to IS NULL 时为 true（派生）")
    rate_source: RateSource | None = Field(
        default=None, description="该行所属档位；列表接口按行的匹配形态派生"
    )


class OperationRateSetOut(BaseModel):
    """设价 / 调价结果：新档 + 被关闭的旧档。"""

    rate: OperationRateOut
    closed_rates: list[OperationRateOut] = Field(
        default_factory=list, description="本次被关闭的旧区间（旧行只改 effective_to）"
    )
    document_log_id: UUID | None = None


class OperationRateListOut(BaseModel):
    items: list[OperationRateOut]
    total: int = Field(ge=0)


class RateResolveOut(BaseModel):
    """取价预演结果（ADR-0026 §2）。真正的取价只发生在计件流水落库那一刻，
    本接口**不写库**（modules/01 §6 末条）。"""

    style_no: str
    operation_no: str
    work_date: date
    unit_price: str = Field(description="命中的单价；字符串（docs/05 §3）")
    effective_from: date
    effective_to: date | None = None
    rate_source: RateSource = Field(description="命中哪一档价（STYLE/CATEGORY/OPERATION）")
    product_category_id: UUID | None = Field(
        default=None, description="命中行的分类维度（档位2 才有）"
    )
