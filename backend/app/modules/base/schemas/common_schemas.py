"""公共片段与通用响应模型（docs/05 §2、§3）。"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.common.enums import SizeClass
from app.core.pydantic_types import Str

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


class StockBatchOptionOut(OptionOut):
    """布批（缸号 / 匹号）候选（``docs/05 §9.5.2`` 末行，T-BASE-007）。

    ⚠️ **继承 :class:`OptionOut`** 而不是另立一个形状：前端 ``<Combo>`` 的
    ``fetchOptions`` 约定返回 ``OptionOut``（``{value,label,sub?,disabled?}``），
    另立形状就要在前端开第二个分支去适配同一个组件 —— 而 Combo 是**通用组件**，
    为一个业务改它的契约就是让所有使用者都得跟着看两处。

    :param value: **批次 UUID**（不是编码）。⚠️ 与九个基础资料的候选相反 ——
        布批没有业务编码可提交，唯一键是 ``(warehouse_id, material_id,
        dye_lot_no, bolt_no)``（``04 §7.9``），而裁剪行要的就是
        ``material_stocks.id``（ADR-0022「不允许自由输入缸号」）。
    :param dye_lot_no: 缸号
    :param bolt_no: 匹号（同缸多匹 → 多行）
    :param width_cm: 门幅（**该批实测值**；C24 门幅校验的输入，不能取档案层）
    :param available_qty: 可用量 = ``stock_qty - locked_qty``（C16）。
        ⚠️ **不是列**，是运行时计算值（``material_stocks`` 上没有这一列）。
    """

    dye_lot_no: str
    bolt_no: str
    width_cm: Str
    available_qty: Str
    material_id: UUID
    supplier_id: UUID | None = None
    #: 采购用途（``NORMAL`` / ``REWORK_RECEIPT`` / ``RETURN`` / ``SAMPLE``，ADR-0012）。
    #: ⚠️ **不作为过滤条件**：BR-ST-25 明确返修布（``REWORK_RECEIPT``）可被裁剪单
    #: 正常选批领用，所以这里「不过滤、只展示」—— 过滤会与 BR-ST-25 冲突且不报错。
    purpose: str


class DisableIn(BaseModel):
    """停用请求体。``reason`` **必填**（§4.4 / docs/06 §5）。"""

    model_config = ConfigDict(json_schema_extra={"example": {"reason": "该色已停用"}})

    reason: Annotated[str, Field(min_length=1, max_length=200, description="停用原因（必填）")]
    remark: str | None = Field(default=None, max_length=500, description="备注")


class PatchIn(BaseModel):
    """通用 PATCH 体基类：``version`` 必传。"""

    version: Annotated[int, Field(ge=1, description="乐观锁版本号（必传，不匹配报 10003）")]


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


class DisableOut(BaseModel):
    """停用结果。"""

    code: str = Field(description="业务编码")
    is_active: bool = False
    reason: str = Field(description="停用原因，回显以便前端确认")


def payload_dict(payload: BaseModel) -> dict[str, Any]:
    """去掉值为 ``None`` 的键。

    PATCH 是部分更新语义：``{"name": null}`` 应该表示"不改名字"，而不是"把名字
    清成 NULL"。所以必须过滤掉 None，否则前端只想改备注却把名称清空了。
    """
    return {key: value for key, value in payload.model_dump().items() if value is not None}
