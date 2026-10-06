"""基础资料接口（设计稿 §4.4）。

九个资源 × 7 个动作，全部**由 :mod:`resources` 注册表生成**。手写九份 handler
意味着「停用必填 reason」这种规则会有九份拷贝，漏一份就是可以随便停用主数据的
漏洞 —— 所以这里一个函数注册多个路径。

⚠️ 权限声明用**运行时按路径参数**（:func:`_permission`）而不是装饰器上的常量，
因为每个端点的权限点取决于命中的资源（``base:create`` vs ``base:operation:manage``）。
OpenAPI 里用 ``x-permission`` 标注九个资源的实际权限点。

---- 拆分说明（T-BASE-010d）----
原 ``router.py``（1362 行）拆为 7 个内容文件 + 本 ``__init__``：``deps``（共享
依赖 / 权限助手 / 导出常量）、``dict_handlers``（字典 handler）、``dict_factory``
（路径注册机制）、``style_router``、``style_child_router``、``material_router``、
``rate_router``。纯代码搬运，路径 / 权限点 / 响应结构不变。

⚠️ ``app/main.py`` 依赖本包导出**名为 ``router`` 的单个 APIRouter**（不是列表）。
⚠️ ``register_resource_routes(router)`` 必须在所有手写端点之后调用：它注册 9 资源的
   ``/{key}/{code}`` 形态，早于手写端点会遮蔽。
"""

from fastapi import APIRouter

from app.modules.base.resources import RESOURCES, DictResource

from .deps import EXPORT_GLOBAL_PERMISSION
from .dict_factory import register_resource_routes
from .dict_handlers import _EXPORT_COLUMN_MAP
from .material_router import router as _material_router
from .rate_router import router as _rate_router
from .style_child_router import router as _style_child_router
from .style_router import router as _style_router

# ⚠️ **不加 /base 前缀**：设计稿 §4.4 与 docs/05 §9.5.2 规定的路径是
# ``/api/v1/colors``、``/api/v1/workshops``，顶层直挂。挂到 ``/api/v1/base/*``
# 会让前端与 docs/05 §9 的示例对不上。
router = APIRouter(tags=["基础资料"])

# 按源码顺序 include 手写端点；相对顺序不影响匹配（无前缀冲突），
# 保持源码顺序便于与拆分前逐段对照。
router.include_router(_material_router)
router.include_router(_style_router)
router.include_router(_style_child_router)
router.include_router(_rate_router)

# 导入即注册：资源路由在模块加载时挂上具体路径，且必须**最后**注册。
register_resource_routes(router)

__all__ = [
    "EXPORT_GLOBAL_PERMISSION",
    "RESOURCES",
    "_EXPORT_COLUMN_MAP",
    "DictResource",
    "register_resource_routes",
    "router",
]
