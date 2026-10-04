"""基础资料接口的请求 / 响应模型（docs/05 §2、§3）。

⚠️ 五条约定：
    1. **请求模型不接受 ``version`` 以外的并发字段，也不接受 ``id``** —— 路径里
       已经有业务编码，再让请求体传 id 就是给了客户端两个互相矛盾的身份来源
    2. PATCH **必传 ``version``**（§4.4）→ 缺失报 ``10001``，不匹配报 ``10003``
    3. **响应里不出现 ``deleted_at`` / ``created_by`` / ``updated_by``**（docs/05 §3），
       ``remark`` 保留
    4. **金额 / 单价 / 手数在响应里一律 ``str``**（docs/05 §3 铁律）——
       JS 的 ``number`` 表示不了 ``0.378000``
    5. 组 D（款号 / 比例 / 款号工序 / 单价）的**聚合版本**取 ``styles.version``：
       全量替换会把子表旧行删光，子表自己的 ``version`` 每次从 1 重来，
       拿它当乐观锁等于没有锁（详见 :class:`RatioReplaceIn`）
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.common.enums import ConflictPolicy, RateSource, SizeClass, TemplateCopyMode

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


class DocumentLogOut(BaseModel):
    """一条审计日志（docs/04 §7.9；详情页「变更历史」抽屉的每一行）。

    ⚠️ **刻意不含 `operator_id`**：界面显示的是冗余的 `operator_name`（用户改名后日志
    仍可读，这是那张列存在的理由），而 UUID 对用户没有意义 —— 把它暴露出去只会诱使
    前端拿它去拼"用户详情"的跳转，而那条路径不存在。
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    doc_type: str = Field(description="单据 / 主数据类型，如 Style / OperationRate")
    doc_no: str = Field(description="单据号 / 业务编码；单价是 `{款号或ALL}/{工序号}`")
    action: str = Field(description="CREATE/UPDATE/SUBMIT/DELETE/RESTORE/...")
    from_status: str | None = Field(default=None, description="变更前状态")
    to_status: str | None = Field(default=None, description="变更后状态")
    operator_name: str = Field(description="操作人姓名（冗余，不随改名变）")
    reason: str | None = Field(default=None, description="变更原因")
    changed_fields: dict[str, Any] | None = Field(
        default=None, description="字段级 diff（各单据自定义）"
    )
    created_at: datetime


class SuggestedStyleNoOut(BaseModel):
    """建议款号（Q-P0-04：**只作参考，用户输入一律优先**）。

    ⚠️ 调这个接口会**消耗一个序号**（`style_no_sequences.next_no + 1`），所以它是
    「帮我看看下一个号是多少」，不是「预览」。用户拿了这个号又改掉，序号就空了一格
    —— 空一号不影响唯一性（唯一索引在 `styles` 上），只影响"建议号跳号"。
    """

    style_no: str = Field(description="建议款号，如 HB-2026-0001；**不保证最终被采用**")
    customer_id: UUID | None = Field(
        default=None, description="归属客户；null = 走全厂序列（前缀 ST）"
    )


# ==================================================================
# 组 D：款号 / 色码尺码 / 比例 / 款号工序 / 工序单价（T-BASE-002）
# ==================================================================


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


class StyleCreate(BaseModel):
    """新建款号。

    ⚠️ **``style_no`` 必填且由用户自定义**（业务方 2026-10-03 决策 Q-P0-04），
    ``suggest_style_no=true`` 只是让服务端**额外**回一个建议号供参考，
    不替代用户输入 —— 这样"实际用了哪个号"永远与用户所见一致。

    ⚠️ **不做格式正则校验**：R1 写的是 ``{客户前缀}-{年份}-{4 位}``，但同一份
    文档的 Q-P0-04 决策改成"货号自定义"，两者冲突。在规范给出新口径前，
    只做"去空白 + 转大写 + 长度上限"，**不发明正则**（AGENTS.md §2.3）。
    已登记为需修订项（modules/01 §2 R1 与 §4.5 的"格式校验"）。
    """

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "style_no": "HB-2026-0001",
                "suggest_style_no": True,
                "customer_id": None,
                "name": "全棉圆领 T 恤",
                "bulk_qty": 3000,
                "category_id": "0f0d5a5c-1c1a-4a0e-9d1e-000000000001",
            }
        },
    )

    style_no: Annotated[
        str, Field(min_length=1, max_length=32, description="款号（必填，用户自定义）")
    ]
    name: Annotated[str, Field(min_length=1, max_length=128, description="款名")]
    category_id: UUID = Field(description="商品分类（04 §7.11，必填；缺 → 10001）")
    suggest_style_no: bool = Field(
        default=False, description="额外返回建议号（客户前缀+年份+客户内序号）供参考"
    )
    customer_id: UUID | None = Field(
        default=None, description="归属客户；可空（Q-P0-10：客户只用于建议号分组与筛选）"
    )
    customer_style_no: str | None = Field(default=None, max_length=64, description="客户货号备注")
    bulk_qty: int | None = Field(default=None, ge=0, description="大货数量")
    merchandiser_id: UUID | None = Field(default=None, description="跟单员；数据范围按此隔离")
    remark: str | None = Field(default=None, max_length=500)


class StylePatch(PatchIn):
    model_config = ConfigDict(extra="forbid")

    # ⚠️ 没有 style_no：款号是业务键且历史单据按字符串引用它，改号即改历史指向
    name: str | None = Field(default=None, min_length=1, max_length=128)
    category_id: UUID | None = Field(
        default=None, description="分类可改；影响该款全部未来单据（ADR-0020）"
    )
    customer_id: UUID | None = None
    customer_style_no: str | None = Field(default=None, max_length=64)
    bulk_qty: int | None = Field(default=None, ge=0)
    merchandiser_id: UUID | None = None
    is_active: bool | None = Field(default=None, description="停用即 R2：不允许新建裁剪/打菲单")
    remark: str | None = Field(default=None, max_length=500)


class StyleDisableIn(DisableIn):
    """停用款号的请求体 = ``reason`` + ``version``。

    ⚠️ ``version`` 必传：款号是**共享档案**，停用之前必须确认手上这份还是最新的
    （否则会把别人刚改完的款号停掉，而界面还在显示旧内容）。字典的
    ``POST /{key}/{code}/disables`` 不需要 version —— 字典行没有子表、没人会在
    另一个界面上同时改它；款号有工序/单价/比例四张子表，必须乐观锁。
    """

    version: Annotated[int, Field(ge=1, description="乐观锁版本号（必传，不匹配报 10003）")]


class StyleOut(Versioned):
    """款号详情。"""

    style_no: str = Field(description="款号，存库统一大写")
    name: str
    category_id: UUID
    customer_id: UUID | None = None
    customer_style_no: str | None = None
    bulk_qty: int | None = None
    merchandiser_id: UUID | None = None
    last_used_at: datetime | None = Field(default=None, description="最近使用；候选默认按此排序")
    is_active: bool = True
    suggested_style_no: str | None = Field(
        default=None, description="建议号；仅在请求 suggest_style_no=true 时返回"
    )


class StyleListOut(Versioned):
    """款号列表行。多带 ``customer_name`` / ``category_name`` —— 前端列表要直接显示，
    为此让前端再查两次字典是不可接受的往返。"""

    style_no: str
    name: str
    category_id: UUID
    category_name: str | None = None
    customer_id: UUID | None = None
    customer_name: str | None = None
    # ⚠️ 这两个字段不是「列表页装饰」：`customer_style_no` 是印在唛头上的客户货号，
    #    `bulk_qty` 是大货数量（09 §1.1）。列表页要能排序/筛选、详情页要能编辑回显，
    #    缺了它们页面只能显示「接口未返回」—— 那是把数据缺失误报成功能缺失。
    customer_style_no: str | None = Field(default=None, description="客户款号，印在唛头上")
    bulk_qty: int | None = Field(default=None, description="大货数量")
    merchandiser_id: UUID | None = None
    merchandiser_name: str | None = Field(
        default=None, description="跟单员姓名（列表/详情都要直接显示 UUID 之外的东西）"
    )
    is_active: bool = True
    last_used_at: datetime | None = None


class StyleColorCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    color_group: Annotated[str, Field(min_length=1, max_length=32, description="色组")]
    color_code: Annotated[str, Field(min_length=1, max_length=32, description="色码 BLK / WHT")]
    color_name: Annotated[str, Field(min_length=1, max_length=64, description="中文色名")]
    material_color_code: str | None = Field(
        default=None, max_length=32, description="面料对应色/缸别"
    )


class StyleColorOut(Versioned):
    style_no: str
    color_group: str
    color_code: str
    color_name: str
    material_color_code: str | None = None


class StyleSizeCreate(BaseModel):
    """新增款号尺码：两种互斥模式（modules/01 §5.3）。

    - **单码**：``size_code`` + ``size_name``（+ 可选 ``sort_no``）
    - **整套带出**：``size_group_name``（码表名）→ 按 ``sort_order`` 批量建

    两种都传或都不传都是请求写错了，在 Schema 层就拒（``10001``），
    放到 service 里判会浪费一次往返。
    """

    model_config = ConfigDict(extra="forbid")

    size_group_name: str | None = Field(
        default=None, max_length=64, description="码表名；给了就一键带出整套尺码"
    )
    size_code: str | None = Field(default=None, max_length=32, description="尺码码 S / M / L")
    size_name: str | None = Field(default=None, max_length=64, description="实际尺码 S(155/80A)")
    sort_no: int | None = Field(default=None, ge=0, description="排序；不给则追加到末尾")

    @model_validator(mode="after")
    def _exactly_one_mode(self) -> "StyleSizeCreate":
        if self.size_group_name is not None and self.size_code is not None:
            raise ValueError("size_group_name 与 size_code 只能传一个（二选一）")
        if self.size_group_name is None and not (self.size_code and self.size_name):
            raise ValueError("请传 size_code + size_name，或传 size_group_name 一键带出整套")
        return self


class StyleSizeOut(Versioned):
    style_no: str
    size_code: str
    size_name: str
    sort_no: int


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
    "customers": (CustomerCreate, CustomerPatch),
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
