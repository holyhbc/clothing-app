"""打菲单接口装配（``docs/05`` + ``modules/03-打菲.md`` §6）。

## 拆分说明（T-BUND-007a 建包，T-BUND-007b 续）

21 个端点（查询 + 草稿写 + 六个状态动作 + 八个辅助能力）放进一个 ``router.py`` 会超
400 行（ADR-0030），按 ADR-0031 改包：``deps``（共享依赖 / 权限助手 / 响应包装）、
``order_router``（读与草稿写）、``action_router``（六个状态动作）、
``aux_router``（预演 / 标签 / 码 / 统计 / 导出）。纯代码拆分，路径 / 权限点 /
响应结构不变。

⚠️ ``app/main.py`` 依赖本包导出**名为 ``router`` 的单个 APIRouter**（与 ``base`` 同约定）。

⚠️⚠️ **include 顺序即路由匹配顺序**（T-CUT-001c-1 的教训）：**顶层静态段必须排在
``/{order_id}`` 之前** —— 所以 ``aux_router``（含 ``/bundling-orders/statistics`` 与
``/bundling-orders/exports``）**必须排第一**。否则它们会被
``/bundling-orders/{order_id}`` 抢先匹配成 UUID 解析失败（422）：永远命中不了，
而 OpenAPI 里**照样列着它们** —— 契约说有、实际调不到，是最难查的一类缺陷。
``test_bundling_router2.py`` 的 TC-BX-08 把这条立成守卫。
"""

from fastapi import APIRouter

from .action_router import router as _action_router
from .aux_router import router as _aux_router
from .deps import BUNDLING_TAGS
from .order_router import router as _order_router

# ⚠️ **不加资源前缀**：各内容 router 写的是完整路径（与 ``base/router`` 同款），
# 挂成 ``/bundling/*`` 会让前端与 docs/05 §9 的示例对不上。
router = APIRouter(tags=BUNDLING_TAGS)

# ⚠️ 顺序不可换：见本文件 docstring 的「include 顺序即路由匹配顺序」。
#    ① aux_router：**带顶层静态段**（``/statistics`` ``/exports``），必须先于 ``/{order_id}``
router.include_router(_aux_router)
router.include_router(_order_router)
router.include_router(_action_router)

__all__ = ["BUNDLING_TAGS", "router"]
