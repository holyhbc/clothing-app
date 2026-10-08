"""打菲 router 的**共享依赖与助手**（ADR-0031：13 个端点超出单文件 400 行 → 改包）。

分层：``deps`` ← :mod:`.order_router` / :mod:`.action_router` ← :mod:`__init__`。
本模块**不含任何端点**，只有被两个子 router 共用的东西 —— 与 ``base/router/deps.py``
同一个理由：子 router 之间互相 import 会成环。

## 三条硬约束（每个端点都必须遵守）

1. **Router 里不许查库、不许过滤数据范围**（``docs/03 §1.1`` 第 6 条 / ``07 §3.2``）：
   列表也必须经 service，否则数据范围过滤能被绕过。
2. **响应一律走** :func:`app.core.responses.ok`（``05 §3``）：统一 ``{code, message, data}``，
   数量与金额在 ``data`` 里是**字符串**。
3. **每个端点都带 ``x-permission`` 且函数体里显式 ``_require``**（``03 §2.1`` 第 8 条
   「前端隐藏不是安全」）。``openapi_extra`` 只是文档。
"""

from datetime import datetime
from enum import Enum
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Path, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.errors import BusinessError, ErrorCode
from app.core.numbering import BUSINESS_TZ
from app.core.permissions import AuthContext, get_auth_context
from app.core.responses import ok
from app.modules.bundling.models import BundlingOrder
from app.modules.bundling.schemas import BundlingOrderOut
from app.modules.bundling.service import BundlingOrderService

SessionDep = Annotated[AsyncSession, Depends(get_db)]
ContextDep = Annotated[AuthContext, Depends(get_auth_context)]

#: OpenAPI 标签（``docs/05 §9.5.2`` 要求 ``tags="打菲"`` + 每个端点 ``summary``）。
#: 标成 ``list[str | Enum]`` 是为了与 base / cutting 的 router 同一类型 —— 标 ``list[str]``
#: 会因 list 的不变性让 ``tags=`` 报 arg-type。
BUNDLING_TAGS: list[str | Enum] = ["打菲"]

#: 打菲单 id 路径参数。``UUID`` 类型本身就让 FastAPI 返回 422 —— 不必自己解析，
#: 而自己解析的话错误信息会变成 500（``docs/05 §2`` 要求格式错误是 422）。
OrderId = Annotated[UUID, Path(description="打菲单 id")]


def _require(ctx: AuthContext, permission: str, action: str) -> None:
    """显式权限校验（``docs/05 §6``）。

    不用 ``Depends(require_permission(...))``：那个依赖只体现为声明，错误文案是通用的
    「无操作权限」。这里能给出**具体动作**，用户报错时知道该找谁。
    """
    if not ctx.has(permission):
        raise BusinessError(
            ErrorCode.PERMISSION_DENIED,
            f"你没有{action}的权限，请联系管理员开通",
            details={"required_permission": permission},
        )


def _service(session: AsyncSession) -> BundlingOrderService:
    return BundlingOrderService(session)


def _order_payload(order: BundlingOrder) -> dict[str, object]:
    """单据 → 统一响应包装（**每次写完都要带新的 ``version``**，见 05 §4 乐观锁）。

    ⚠️ 必须走这里而不是 ``ok(order)``：出参要从 ORM ``model_validate``，而 ``hands_total``
    这类列的暴露口径（数量转字符串）由 :class:`BundlingOrderOut` 定死。
    """
    return ok(BundlingOrderOut.model_validate(order).model_dump(mode="json"))


def _csv_response(text: str, resource: str) -> Response:
    """CSV 下载（``text/csv; charset=utf-8-sig``）。

    ⚠️ **BOM 必须真写进字节里**：Excel 不看 ``charset`` 参数去猜编码，没 BOM 就按 GBK
    打开 → 中文全是乱码，而「生成命令成功、下载下来打不开」排查起来极贵。
    ⚠️ 文件名带时间戳（05 §9.1 ``{资源}_{筛选摘要}_{YYYYMMDD_HHmm}``）：同一份 CSV
    下载两次，浏览器会命中缓存，于是「重导了一次」看到的还是旧文件。
    """
    stamp = datetime.now(tz=BUSINESS_TZ).strftime("%Y%m%d-%H%M")
    return Response(
        content=text.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8-sig",
        headers={
            "Content-Disposition": f'attachment; filename="{resource}-{stamp}.csv"',
            "X-Row-Count": str(text.count("\n") - 1),
        },
    )
