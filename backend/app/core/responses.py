"""统一响应包装（docs/05-接口设计规范.md §3）。

响应结构固定::

    { "code": 0, "message": "ok", "data": {...}, "request_id": "01JB..." }

列表统一::

    { "code": 0, "data": { "items": [], "total": 0, "page": 1, "page_size": 20 } }

约定：
    - 金额 / 数量 / 单价在响应里**一律字符串**，防止 JS 浮点丢精度（docs/05 §3）
    - 成功 ``code`` 恒为 0；非 0 为业务错误码
    - 不返回 ``deleted_at`` / ``created_by`` 等内部字段，``remark`` 保留
    - ``request_id`` 由中间件注入，不要求每个 router 手工填写
"""

from pydantic import BaseModel, ConfigDict, Field


class PageData[T](BaseModel):
    """分页结构。``page`` 从 1 起；``page_size`` 上限 200（docs/05 §2）。"""

    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "example": {"items": [], "total": 0, "page": 1, "page_size": 20},
        },
    )

    items: list[T] = Field(description="当前页数据")
    total: int = Field(ge=0, description="总条数（用于分页器）")
    page: int = Field(ge=1, description="当前页码，从 1 起")
    page_size: int = Field(ge=1, le=200, description="每页条数，上限 200")


class ErrorData(BaseModel):
    """错误响应里的 ``details`` 结构（docs/05 §4）。

    只约定「字段级错误明细」与「冲突对象」两类常见形态，具体字段由各模块定义，
    因此这里用 ``dict[str, Any]`` 承载而不是固定字段。
    """

    model_config = ConfigDict(extra="allow")


class ApiResponse[T](BaseModel):
    """统一响应包装。"""

    model_config = ConfigDict(populate_by_name=True)

    code: int = Field(default=0, description="0 表示成功；非 0 为业务错误码")
    message: str = Field(default="ok", description="面向用户的文案")
    data: T | None = Field(default=None, description="业务数据；失败时为 null")
    details: dict[str, object] | None = Field(
        default=None, description="失败时的结构化补充信息（字段级错误、冲突区间等）"
    )
    request_id: str | None = Field(default=None, description="请求追踪 ID，与响应头一致")


def ok(data: object | None = None) -> dict[str, object]:
    """成功响应（同步场景，如中间件或 CLI）。"""
    return {"code": 0, "message": "ok", "data": data}


def page_ok(items: list[object], total: int, page: int, page_size: int) -> dict[str, object]:
    """分页成功响应。"""
    return ok(
        {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    )
