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

本文件由 5 个子模块聚合重导出，**对外导入面保持不变**：现有代码继续使用
``from app.modules.base.schemas import X``，不要改深路径（设计稿 §2.6）。
"""

from pydantic import BaseModel

from .common_schemas import (
    DeleteOut,
    DictOut,
    DictRow,
    DisableIn,
    DisableOut,
    DocumentLogOut,
    OptionOut,
    PatchIn,
    StockBatchOptionOut,
    SuggestedStyleNoOut,
    Versioned,
    payload_dict,
)
from .dict_schemas import (
    ColorCreate,
    ColorPatch,
    CustomerCreate,
    CustomerPatch,
    OperationCreate,
    OperationPatch,
    ProductCategoryCreate,
    ProductCategoryPatch,
    SizeCreate,
    SizeGroupCreate,
    SizeGroupItemIn,
    SizeGroupPatch,
    SizePatch,
    UomUnitCreate,
    UomUnitPatch,
    WarehouseCreate,
    WarehousePatch,
    WorkshopCreate,
    WorkshopGroupCreate,
    WorkshopGroupPatch,
    WorkshopPatch,
)
from .rate_schemas import (
    OperationRateCreate,
    OperationRateListOut,
    OperationRateOut,
    OperationRateSetOut,
    RateResolveOut,
)
from .style_child_schemas import (
    CopiedPriceOut,
    RatioItemIn,
    RatioListOut,
    RatioOut,
    RatioReplaceIn,
    StyleDetailOut,
    StyleOperationItemIn,
    StyleOperationOut,
    StyleOperationsListOut,
    StyleOperationsReplaceIn,
    TemplateCopyIn,
    TemplateCopyOut,
)
from .style_schemas import (
    StyleColorCreate,
    StyleColorOut,
    StyleCreate,
    StyleDisableIn,
    StyleListOut,
    StyleOut,
    StylePatch,
    StyleSizeCreate,
    StyleSizeOut,
)

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

__all__ = [
    "ACTION_BY_OPERATION",
    "WRITE_MODELS",
    "ColorCreate",
    "ColorPatch",
    "CopiedPriceOut",
    "CustomerCreate",
    "CustomerPatch",
    "DeleteOut",
    "DictOut",
    "DictRow",
    "DisableIn",
    "DisableOut",
    "DocumentLogOut",
    "OperationCreate",
    "OperationPatch",
    "OperationRateCreate",
    "OperationRateListOut",
    "OperationRateOut",
    "OperationRateSetOut",
    "OptionOut",
    "PatchIn",
    "ProductCategoryCreate",
    "ProductCategoryPatch",
    "RateResolveOut",
    "RatioItemIn",
    "RatioListOut",
    "RatioOut",
    "RatioReplaceIn",
    "SizeCreate",
    "SizeGroupCreate",
    "SizeGroupItemIn",
    "SizeGroupPatch",
    "SizePatch",
    "StockBatchOptionOut",
    "StyleColorCreate",
    "StyleColorOut",
    "StyleCreate",
    "StyleDetailOut",
    "StyleDisableIn",
    "StyleListOut",
    "StyleOperationItemIn",
    "StyleOperationOut",
    "StyleOperationsListOut",
    "StyleOperationsReplaceIn",
    "StyleOut",
    "StylePatch",
    "StyleSizeCreate",
    "StyleSizeOut",
    "SuggestedStyleNoOut",
    "TemplateCopyIn",
    "TemplateCopyOut",
    "UomUnitCreate",
    "UomUnitPatch",
    "Versioned",
    "WarehouseCreate",
    "WarehousePatch",
    "WorkshopCreate",
    "WorkshopGroupCreate",
    "WorkshopGroupPatch",
    "WorkshopPatch",
    "payload_dict",
]
