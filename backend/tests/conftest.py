"""全局测试夹具（docs/10-测试规范.md §2.2）。

T-INFRA-003 阶段只提供环境变量与 app 工厂；数据库相关夹具（``db_session`` /
``client`` 的事务回滚）在 T-INFRA-004 补齐。
"""

import os
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

# 必须在导入 app.* 之前设置：Settings 在导入期就会读环境变量
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production")
os.environ.setdefault("JWT_EXPIRE_MINUTES", "15")
# 故意留空：本卡不连库，/readyz 应报 not_configured
os.environ.setdefault("DATABASE_URL", "")

from app.core.config import get_settings
from app.main import create_app


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    """每个用例前后清掉 Settings 单例缓存，避免用例间污染。"""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def app() -> FastAPI:
    """每个用例一个全新的应用实例（tests 零依赖、顺序随机仍通过）。"""
    return create_app()


@pytest.fixture
async def client(app: FastAPI) -> Iterator[AsyncClient]:
    """ASGI 内存客户端，不占端口、不起真实服务。"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
