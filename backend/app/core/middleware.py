"""request_id 中间件与全局异常处理（docs/05-接口设计规范.md §3、§4）。

``request_id`` 用 ULID（26 位 Crockford Base32，字典序即时间序），
不引入额外依赖：本项目只需要「可追踪 + 可排序 + 短」，UUID4 够长且不可排序。

约定：
    - 每个响应都带 ``X-Request-ID`` 头，且与响应体 ``request_id`` 一致
    - 只对 ``application/json`` 且 body 是 dict 且含 ``code`` 键的响应注入 body 字段，
      SSE（``text/event-stream``）与静态资源一律不碰
    - 日志里 ``extra={"request_id": ...}``，便于排障时按 ID 串起全链路
"""

import json
import logging
import secrets
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import HTTP_STATUS_BY_CODE, BusinessError, ErrorCode

logger = logging.getLogger("app.request")

REQUEST_ID_HEADER = "X-Request-ID"
_ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford Base32（去掉 I L O U）

# 同毫秒内自增用的进程内状态（用于保证 ULID 单调）
_last_timestamp: int = -1
_last_random: int = 0


def new_request_id() -> str:
    """生成 26 位 ULID：10 位毫秒时间戳 + 16 位随机。

    同一毫秒内多次调用时，随机部分**自增**以保证进程内单调，
    这样同一进程产生的 ID 字典序与生成顺序一致（便于按 ID 排序定位）。
    """
    timestamp = int(datetime.now(tz=UTC).timestamp() * 1000)
    random_part = secrets.randbits(80)

    global _last_timestamp, _last_random
    if timestamp == _last_timestamp:
        # 同一毫秒：随机部分 +1，避免同毫秒内碰撞
        random_part = (_last_random + 1) & ((1 << 80) - 1)
    _last_timestamp = timestamp
    _last_random = random_part

    value = (timestamp << 80) | random_part
    chars: list[str] = []
    for _ in range(26):
        chars.append(_ULID_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def get_request_id(request: Request) -> str:
    """从 scope 取 request_id；缺失时现场生成一个。"""
    state: dict[str, Any] = request.scope.get("state") or {}
    return str(state.get("request_id") or new_request_id())


class RequestIdMiddleware:
    """纯 ASGI 中间件：注入 request_id 并回写响应头 / 响应体。

    刻意不用 ``BaseHTTPMiddleware``：它会缓冲响应体并破坏 SSE 与后台任务。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = new_request_id()
        scope.setdefault("state", {})["request_id"] = request_id
        logger.info(
            "request_start",
            extra={
                "request_id": request_id,
                "method": scope.get("method"),
                "path": scope.get("path"),
            },
        )

        send_with_id = _bind_request_id(send, request_id)
        await self.app(scope, receive, send_with_id)


def _bind_request_id(send: Send, request_id: str) -> Send:
    """包装 send：加响应头 + 给 JSON body 注入 request_id 字段。"""

    async def send_wrapper(message: Message) -> None:
        if message["type"] == "http.response.start":
            headers = message.setdefault("headers", [])
            key = REQUEST_ID_HEADER.lower().encode()
            # 兜底 500 由 Starlette 最外层的 ServerErrorMiddleware 生成，会绕过本中间件；
            # 而业务/校验异常处理器已显式带头。因此这里只在"尚未设置"时追加，避免重复头。
            if not any(existing_key.lower() == key for existing_key, _ in headers):
                headers.append((key, request_id.encode()))
        elif message["type"] == "http.response.body":
            message = _inject_into_json_body(message, request_id)
        await send(message)

    return send_wrapper


def _inject_into_json_body(message: Message, request_id: str) -> Message:
    """把 request_id 写进 JSON 响应体；非 JSON 或无 ``code`` 键则原样返回。"""
    headers: list[tuple[bytes, bytes]] = message.get("headers") or []
    content_type = ""
    for key, value in headers:
        if key == b"content-type":
            content_type = value.decode("latin-1")
            break
    if "application/json" not in content_type:
        return message

    body = message.get("body") or b""
    if not body:
        return message

    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return message
    if not isinstance(payload, dict) or "code" not in payload:
        return message

    payload["request_id"] = request_id
    new_body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    return {
        "type": "http.response.body",
        "body": new_body,
        "more_body": message.get("more_body", False),
    }


# ---------------------------------------------------------------------------
# 全局异常处理（docs/05 §4：任何失败都必须变成统一响应体）
# ---------------------------------------------------------------------------


def register_exception_handlers(app: FastAPI) -> None:
    """注册三个异常处理器：业务异常 / 请求校验失败 / 兜底。"""

    @app.exception_handler(BusinessError)
    async def handle_business_error(request: Request, exc: BusinessError) -> JSONResponse:
        request_id = get_request_id(request)
        logger.info(
            "business_error",
            extra={
                "request_id": request_id,
                "code": int(exc.code),
                "path": request.url.path,
            },
        )
        return JSONResponse(
            status_code=exc.http_status,
            content={
                "code": int(exc.code),
                "message": exc.message,
                "data": None,
                "details": exc.details or None,
                "request_id": request_id,
            },
            headers={REQUEST_ID_HEADER: request_id},
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Pydantic 校验失败 → ``10001``，并附字段级错误明细。

        字段级明细是前端行内红字的数据来源（docs/06 §5）。
        """
        request_id = get_request_id(request)
        fields: list[dict[str, object]] = []
        for error in exc.errors():
            location = [str(part) for part in error.get("loc", ()) if part != "body"]
            fields.append(
                {
                    "field": ".".join(location) or "(root)",
                    "message": str(error.get("msg", "参数不合法")),
                    "type": str(error.get("type", "")),
                }
            )
        return JSONResponse(
            status_code=HTTP_STATUS_BY_CODE[ErrorCode.PARAM_INVALID],
            content={
                "code": int(ErrorCode.PARAM_INVALID),
                "message": "参数校验失败，请检查标红字段",
                "data": None,
                "details": {"fields": fields},
                "request_id": request_id,
            },
            headers={REQUEST_ID_HEADER: request_id},
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        """把路由层的 404 / 405 等也收进统一响应体（docs/05 §3：所有响应同一形状）。

        ⚠️ 错误码口径说明：docs/05 §4 **没有为"路由不存在 / 方法不允许"登记专用码**。
        这里复用最接近的已登记码 ``10008 非法操作``，并**保持 HTTP 404/405 原状**
        （让网关、监控与浏览器行为不变），因此这是「code 与 HTTP 映射表」的唯一例外。
        已登记为 docs/12 §5 待规范补登专用码（如 ``10010 接口不存在``）。
        """
        request_id = get_request_id(request)
        logger.info(
            "http_exception",
            extra={
                "request_id": request_id,
                "status": exc.status_code,
                "path": request.url.path,
                "method": request.method,
            },
        )
        message = (
            f"接口不存在：{request.method} {request.url.path}"
            if exc.status_code == HTTPStatus.NOT_FOUND
            else f"请求方法不允许：{request.method} {request.url.path}"
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": int(ErrorCode.ILLEGAL_OPERATION),
                "message": message,
                "data": None,
                "details": {"path": request.url.path, "method": request.method},
                "request_id": request_id,
            },
            headers={REQUEST_ID_HEADER: request_id},
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        """兜底：返回 ``99999``，**绝不把异常细节暴露给客户端**（docs/05 §4）。

        完整栈只进服务端日志；客户端拿 ``request_id`` 去查日志
        （docs/06 §5：禁止把 "500 Internal Server Error" 直接抛给用户）。
        """
        request_id = get_request_id(request)
        logger.exception(
            "unhandled_exception",
            extra={
                "request_id": request_id,
                "path": request.url.path,
                "method": request.method,
            },
        )
        return JSONResponse(
            status_code=HTTP_STATUS_BY_CODE[ErrorCode.INTERNAL],
            content={
                "code": int(ErrorCode.INTERNAL),
                "message": "服务器内部错误，请把请求编号提供给技术支持",
                "data": None,
                "details": None,
                "request_id": request_id,
            },
            headers={REQUEST_ID_HEADER: request_id},
        )
