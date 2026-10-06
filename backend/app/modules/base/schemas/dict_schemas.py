"""九个字典资源与客户的请求模型（docs/05 §2、§4.4）。"""

from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.common.enums import SizeClass

from .common_schemas import PatchIn

# ------------------------------------------------------- 逐资源写入体


class WorkshopCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Annotated[str, Field(min_length=1, max_length=32, description="车间编码，全厂唯一")]
    name: Annotated[str, Field(min_length=1, max_length=64)]
    remark: str | None = Field(default=None, max_length=500)


class WorkshopPatch(PatchIn):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=64, description="编码不可改")
    remark: str | None = Field(default=None, max_length=500)


class WorkshopGroupCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workshop_id: UUID
    group_no: Annotated[str, Field(min_length=1, max_length=32, description="车间内唯一")]
    name: Annotated[str, Field(min_length=1, max_length=64)]
    remark: str | None = Field(default=None, max_length=500)


class WorkshopGroupPatch(PatchIn):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=64)
    remark: str | None = Field(default=None, max_length=500)


class WarehouseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Annotated[str, Field(min_length=1, max_length=32)]
    name: Annotated[str, Field(min_length=1, max_length=64)]
    warehouse_type: Annotated[
        str, Field(description="FABRIC / TRIMMING / FINISHED_GOOD，与库存双栈对应")
    ]
    remark: str | None = Field(default=None, max_length=500)


class WarehousePatch(PatchIn):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=64)
    warehouse_type: str | None = None
    remark: str | None = Field(default=None, max_length=500)


class UomUnitCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Annotated[str, Field(min_length=1, max_length=16, description="M / YD / PCS / KG")]
    name: Annotated[str, Field(min_length=1, max_length=32)]
    decimal_places: Annotated[
        int, Field(ge=0, le=6, description="数量小数位，参与 numeric 精度对齐")
    ]
    remark: str | None = Field(default=None, max_length=500)


class UomUnitPatch(PatchIn):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=32)
    decimal_places: int | None = Field(default=None, ge=0, le=6)
    remark: str | None = Field(default=None, max_length=500)


class ProductCategoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Annotated[str, Field(min_length=1, max_length=32)]
    name: Annotated[str, Field(min_length=1, max_length=32)]
    sort: int = Field(default=0, description="界面排序")
    remark: str | None = Field(default=None, max_length=500)


class ProductCategoryPatch(PatchIn):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=32)
    sort: int | None = None
    remark: str | None = Field(default=None, max_length=500)


class ColorCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"color_code": "NVY", "name": "藏青"}},
    )

    color_code: Annotated[str, Field(min_length=1, max_length=32, description="色码，全局唯一")]
    name: Annotated[str, Field(min_length=1, max_length=64)]
    color_family: str | None = Field(default=None, max_length=32)
    pantone_code: str | None = Field(default=None, max_length=32)
    remark: str | None = Field(default=None, max_length=500)


class ColorPatch(PatchIn):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=64)
    color_family: str | None = Field(default=None, max_length=32)
    pantone_code: str | None = Field(default=None, max_length=32)
    remark: str | None = Field(default=None, max_length=500)


class SizeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    size_code: Annotated[str, Field(min_length=1, max_length=32)]
    name: Annotated[str, Field(min_length=1, max_length=64, description="如 XL(170/92A)")]
    size_class: SizeClass
    sort_order: Annotated[int, Field(ge=0, default=0)]
    remark: str | None = Field(default=None, max_length=500)


class SizePatch(PatchIn):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=64)
    size_class: SizeClass | None = None
    sort_order: int | None = Field(default=None, ge=0)
    remark: str | None = Field(default=None, max_length=500)


class SizeGroupItemIn(BaseModel):
    """码表成员。``items`` 是**全量替换**语义（§4.4）。"""

    model_config = ConfigDict(extra="forbid")

    size_id: UUID
    sort_order: Annotated[int, Field(ge=0)]


class SizeGroupCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=64, description="码表名，全局唯一")]
    size_class: SizeClass
    items: Annotated[
        list[SizeGroupItemIn], Field(min_length=1, description="有序尺码成员，至少 1 个")
    ]
    remark: str | None = Field(default=None, max_length=500)


class SizeGroupPatch(PatchIn):
    model_config = ConfigDict(extra="forbid")

    size_class: SizeClass | None = None
    items: list[SizeGroupItemIn] | None = Field(default=None, description="全量替换成员")
    remark: str | None = Field(default=None, max_length=500)


class OperationCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"operation_no": "01", "name": "裁"}},
    )

    operation_no: Annotated[str, Field(min_length=1, max_length=16, description="工序号，全厂唯一")]
    name: Annotated[str, Field(min_length=1, max_length=64)]
    workshop_id: UUID | None = Field(default=None, description="空 = 通用工序")
    is_piecework: bool = Field(default=True, description="是否计件工序")
    default_bundle_qty: Annotated[
        Decimal,
        Field(
            default=Decimal("1"), gt=0, max_digits=14, decimal_places=3, description="默认一扎件数"
        ),
    ]
    sort_order: Annotated[int, Field(ge=0, default=0)]
    remark: str | None = Field(default=None, max_length=500)


class OperationPatch(PatchIn):
    model_config = ConfigDict(extra="forbid")

    # ⚠️ 没有 operation_no：§4.4「引用方是字符串冗余，改号会让历史单据指向不存在的工序」
    name: str | None = Field(default=None, min_length=1, max_length=64)
    workshop_id: UUID | None = None
    is_piecework: bool | None = None
    default_bundle_qty: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=3)
    sort_order: int | None = Field(default=None, ge=0)
    remark: str | None = Field(default=None, max_length=500)


class CustomerCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"code": "HB", "name": "海博制衣有限公司"}},
    )

    code: Annotated[str, Field(min_length=1, max_length=32, description="客户编码，全厂唯一")]
    name: Annotated[str, Field(min_length=1, max_length=128, description="客户全称")]
    short_name: str | None = Field(default=None, max_length=64)
    contact: str | None = Field(default=None, max_length=64)
    phone: str | None = Field(default=None, max_length=32)
    address: str | None = Field(default=None, max_length=255)
    tax_no: str | None = Field(default=None, max_length=32, description="税号")
    settlement_period_days: Annotated[
        int, Field(default=0, ge=0, description="账期天数，应收核销用")
    ]
    remark: str | None = Field(default=None, max_length=500)


class CustomerPatch(PatchIn):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=128, description="编码不可改")
    short_name: str | None = Field(default=None, max_length=64)
    contact: str | None = Field(default=None, max_length=64)
    phone: str | None = Field(default=None, max_length=32)
    address: str | None = Field(default=None, max_length=255)
    tax_no: str | None = Field(default=None, max_length=32)
    settlement_period_days: int | None = Field(default=None, ge=0)
    remark: str | None = Field(default=None, max_length=500)
