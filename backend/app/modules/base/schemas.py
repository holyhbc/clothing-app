"""基础资料接口的请求 / 响应模型（docs/05 §2、§3）。

⚠️ 三条约定：
    1. **请求模型不接受 ``version`` 以外的并发字段，也不接受 ``id``** —— 路径里
       已经有业务编码，再让请求体传 id 就是给了客户端两个互相矛盾的身份来源
    2. PATCH **必传 ``version``**（§4.4）→ 缺失报 ``10001``，不匹配报 ``10003``
    3. **响应里不出现 ``deleted_at`` / ``created_by`` / ``updated_by``**（docs/05 §3），
       ``remark`` 保留
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.common.enums import SizeClass

# ------------------------------------------------------------------ 公共片段


class Versioned(BaseModel):
    """所有资源的公共响应字段。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(description="主键")
    version: int = Field(ge=1, description="乐观锁版本号")
    remark: str | None = Field(default=None, description="备注")
    created_at: datetime = Field(description="创建时间")
    updated_at: datetime = Field(description="更新时间")


class DictOut(Versioned):
    """基础资料通用响应体。

    ⚠️ **资源特有字段一律可空**：九个资源的字段集不同，用一个大模型换来的是
    "每个响应都多出七八个 null 字段"。代价是 OpenAPI 不够精确；
    换来的是新增资源不用改这个类、也不会漏掉某个字段的转换。
    真正需要精确契约的写入侧（Create / Update）仍然逐资源显式声明。
    """

    is_active: bool = Field(default=True, description="是否启用；停用后不参与新建单据")
    is_builtin: bool | None = Field(default=None, description="是否内置（仅字典表有）")
    # 车间 / 组别 / 仓库 / 单位
    code: str | None = Field(default=None, description="编码")
    workshop_id: UUID | None = Field(default=None, description="所属车间")
    group_no: str | None = Field(default=None, description="组别号")
    warehouse_type: str | None = Field(default=None, description="仓库类型")
    decimal_places: int | None = Field(default=None, description="单位小数位")
    # 颜色
    color_code: str | None = Field(default=None, description="色码")
    color_family: str | None = Field(default=None, description="色卡族")
    pantone_code: str | None = Field(default=None, description="潘通号")
    # 尺码 / 码表
    size_code: str | None = Field(default=None, description="尺码码")
    size_class: SizeClass | None = Field(default=None, description="尺码类")
    sort_order: int | None = Field(default=None, description="排序")
    name: str | None = Field(default=None, description="名称")
    # 工序
    operation_no: str | None = Field(default=None, description="工序号")
    is_piecework: bool | None = Field(default=None, description="是否计件工序")
    default_bundle_qty: Decimal | None = Field(default=None, description="默认一扎件数")
    # 商品分类
    sort: int | None = Field(default=None, description="界面排序")


class DictRow(DictOut):
    """列表行：额外带引用计数（§4.4「分页 + 每行 ref_count」）。

    ``ref_count`` 让界面能**提前**把"删不掉"的行标灰，而不是让用户点了删除才
    收到 20003。``None`` 表示该资源不参与引用检查（纯软删表）。
    """

    ref_count: int | None = Field(default=None, description="被引用次数；不可删的资源为 null")


class OptionOut(BaseModel):
    """候选下拉项（docs/05 §9.5.2）。

    :param value: 提交给后端的值（业务编码，不是 UUID）
    :param label: 显示文本
    :param sub: 副文本（分类、围度等辅助信息）
    :param disabled: 是否已停用 —— 前端据此显示"已停用"样式且**禁止直接选中**
    """

    value: str
    label: str
    sub: str | None = None
    disabled: bool = False


class DisableIn(BaseModel):
    """停用请求体。``reason`` **必填**（§4.4 / docs/06 §5）。"""

    model_config = ConfigDict(json_schema_extra={"example": {"reason": "该色已停用"}})

    reason: Annotated[str, Field(min_length=1, max_length=200, description="停用原因（必填）")]
    remark: str | None = Field(default=None, max_length=500, description="备注")


class PatchIn(BaseModel):
    """通用 PATCH 体基类：``version`` 必传。"""

    version: Annotated[int, Field(ge=1, description="乐观锁版本号（必传，不匹配报 10003）")]


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


class DeleteOut(BaseModel):
    """删除结果。真删时回传被级联删除的行数。"""

    deleted: bool = True
    cascaded: int = Field(default=0, ge=0, description="级联删除的明细行数（如码表成员）")


class DisableOut(BaseModel):
    """停用结果。"""

    code: str = Field(description="业务编码")
    is_active: bool = False
    reason: str = Field(description="停用原因，回显以便前端确认")


#: 资源 key → (Create 模型, Patch 模型)。**只用于 OpenAPI 与入参校验**，
#: 落库字段由 service 按注册表白名单取，避免"模型加了字段就自动写库"。
WRITE_MODELS: dict[str, tuple[type[BaseModel], type[BaseModel]]] = {
    "workshops": (WorkshopCreate, WorkshopPatch),
    "workshop-groups": (WorkshopGroupCreate, WorkshopGroupPatch),
    "warehouses": (WarehouseCreate, WarehousePatch),
    "uom-units": (UomUnitCreate, UomUnitPatch),
    "product-categories": (ProductCategoryCreate, ProductCategoryPatch),
    "colors": (ColorCreate, ColorPatch),
    "sizes": (SizeCreate, SizePatch),
    "size-groups": (SizeGroupCreate, SizeGroupPatch),
    "operations": (OperationCreate, OperationPatch),
}

#: 操作日志用的动作名映射（docs/08 §1.1 的 DocumentAction）
ACTION_BY_OPERATION: dict[str, str] = {
    "create": "CREATE",
    "patch": "UPDATE",
    "disable": "UPDATE",
    "delete": "DELETE",
}


def payload_dict(payload: BaseModel) -> dict[str, Any]:
    """去掉值为 ``None`` 的键。

    PATCH 是部分更新语义：``{"name": null}`` 应该表示"不改名字"，而不是"把名字
    清成 NULL"。所以必须过滤掉 None，否则前端只想改备注却把名称清空了。
    """
    return {key: value for key, value in payload.model_dump().items() if value is not None}
