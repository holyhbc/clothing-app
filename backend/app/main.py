"""应用装配（docs/01-技术选型与架构.md §4 分层）。

``create_app()`` 是**工厂函数**而不是模块级单例：
测试需要反复创建应用并注入依赖覆盖（``app.dependency_overrides``），
模块级单例会导致状态跨用例污染（AGENTS.md 强制隔离，docs/10 §2.2）。

本文件只装配：中间件、异常处理器、健康检查、生命周期。
**不放任何业务路由**（业务路由由各模块的 router.py 挂载，T-BASE/T-AUTH 起）。
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI, status
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import get_settings
from app.core.db import dispose_engine
from app.core.errors import BusinessError
from app.core.middleware import RequestIdMiddleware, register_exception_handlers
from app.core.responses import ApiResponse

API_PREFIX = "/api/v1"


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用。"""

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        """生命周期：启动时准备、关闭时释放连接池。"""
        del application
        yield
        await dispose_engine()

    app = FastAPI(
        title="服装厂 ERP API",
        version=get_settings().app_version,
        description=(
            "服装厂 ERP 后端。统一响应 `{code, message, data, request_id}`，"
            "错误码见 docs/05 §4；金额与数量在响应里一律为字符串。"
        ),
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # request_id 必须最外层，才能覆盖异常处理器产生的响应
    app.add_middleware(RequestIdMiddleware)
    register_exception_handlers(app)

    _register_health_endpoints(app)
    _register_placeholders(app)
    _register_module_routers(app)
    return app


def _register_module_routers(app: FastAPI) -> None:
    """挂载业务模块路由。**只有这里**能把 router 挂到应用上。"""
    from app.modules.auth.router import router as auth_router
    from app.modules.base.router import router as base_router
    from app.modules.system.router import router as system_router

    app.include_router(auth_router, prefix=API_PREFIX)
    app.include_router(base_router, prefix=API_PREFIX)
    app.include_router(system_router, prefix=API_PREFIX)


def _register_health_endpoints(app: FastAPI) -> None:
    """存活与就绪探针（docs/05 §1）。

    ``/healthz`` 只表示进程活着；``/readyz`` 检查依赖（数据库、Redis）是否可达。
    两者都注册根路径与 ``/api/v1`` 前缀两套：规范（docs/05 §1）要求不带前缀，
    而部署文档（docs/11 §6）的冒烟脚本用的是带前缀版本，两边都兼容避免歧义。
    """

    @app.get("/healthz", tags=["系统"], summary="存活探针")
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "alive"})

    @app.get("/readyz", tags=["系统"], summary="就绪探针（检查数据库与 Redis）")
    async def readyz() -> JSONResponse:
        """依赖全部可用才 200。

        语义口径：**"未配置" 与 "连不上" 同样算不可用**（都返回 503）。
        理由：进程连不上数据库时任何业务接口都无法服务，此时报 ready 会让
        编排器继续往里打流量，是撒谎。``checks`` 里保留具体原因供人排查。

        生产环境不会出现 not_configured —— ``Settings`` 在 app_env=production 时
        已强制要求 DATABASE_URL 与 JWT_SECRET（见 app/core/config.py）。
        """
        checks = {
            "database": await _check_database(),
            "redis": await _check_redis(),
        }
        healthy = all(status_ == "up" for status_ in checks.values())
        return JSONResponse(
            status_code=status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "ready" if healthy else "not_ready", "checks": checks},
        )

    # 带前缀的别名（兼容 docs/11 §6 冒烟脚本）
    for path, endpoint, summary in (
        (f"{API_PREFIX}/healthz", healthz, "存活探针（带前缀别名）"),
        (f"{API_PREFIX}/readyz", readyz, "就绪探针（带前缀别名）"),
    ):
        app.add_api_route(
            path,
            endpoint,
            methods=["GET"],
            tags=["系统"],
            summary=summary,
            include_in_schema=False,
        )


async def _check_database() -> str:
    """探测数据库连通性。未配置连接串返回 ``not_configured``。"""
    from app.core.config import require_database_url

    try:
        url = require_database_url()
    except BusinessError:
        return "not_configured"

    try:
        conn = await asyncio.wait_for(
            asyncpg.connect(url.replace("postgresql+asyncpg://", "postgresql://"), timeout=2),
            timeout=3,
        )
    except (OSError, asyncpg.PostgresError, TimeoutError):
        return "down"
    await conn.close()
    return "up"


async def _check_redis() -> str:
    """探测 Redis 连通性。未配置返回 ``not_configured``。"""
    settings = get_settings()
    url = settings.redis_url.get_secret_value()
    if not url:
        return "not_configured"

    client = Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
    try:
        await asyncio.wait_for(client.ping(), timeout=3)
    except (RedisError, OSError, TimeoutError):
        return "down"
    finally:
        await client.aclose()
    return "up"


def _register_placeholders(app: FastAPI) -> None:
    """占位端点：明确告知"未实现"而不是静默 404（docs/05 §1）。"""

    @app.get(
        f"{API_PREFIX}/events/stream",
        tags=["系统"],
        summary="SSE 事件流（P2 实现）",
        response_model=ApiResponse[dict[str, str]],
    )
    async def events_stream_placeholder() -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            content={
                "code": 10008,
                "message": "SSE 事件流尚未实现，计划在 P2（扫码计件）阶段交付",
                "data": None,
                "details": {"planned_milestone": "P2", "design": "docs/05-接口设计规范.md §8"},
            },
        )


app = create_app()
