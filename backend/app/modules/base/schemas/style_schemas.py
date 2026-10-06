"""款号主表、色码与尺码的请求 / 响应模型（T-BASE-002）。"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .common_schemas import DisableIn, PatchIn, Versioned


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
