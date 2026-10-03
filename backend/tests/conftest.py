"""全局测试夹具（docs/10-测试规范.md §2.1 / §2.2）。

三层结构：
    1. **session 级**：等库就绪 → 跑一次 ``alembic upgrade head`` 建立 schema
    2. **function 级** ``db_session``：独立连接 + 嵌套事务 + savepoint，结束回滚
    3. **function 级** ``client``：ASGI 内存客户端，``get_db`` 覆盖为 ``db_session``

铁律：
    - 每个用例独立事务、结束回滚 → **用例零依赖，顺序随机仍通过**
    - 禁止连生产库：用 ``garment_erp_test``，库名不匹配直接拒绝启动
    - 覆盖完必须 ``clear()``，否则依赖覆盖会漏到下一个用例
"""

import os
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# 必须在导入 app.* 之前设置：Settings 在导入期就会读环境变量
os.environ.setdefault("APP_ENV", "test")
# HS256 要求密钥 ≥32 字节，否则 pyjwt 发 InsecureKeyLengthWarning；
# 本项目把警告当错误，所以这里必须给够长度（生产用 openssl rand -hex 32）
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production-0123456789abcdef")
os.environ.setdefault("JWT_EXPIRE_MINUTES", "15")
os.environ.setdefault("DATABASE_URL", "")
os.environ.setdefault("REDIS_URL", "")

from app.core.config import get_settings
from app.core.db import get_db
from app.main import create_app

#: 允许跑测试的库名白名单：防止误连生产库（docs/10 §2.2）
ALLOWED_TEST_DATABASES = frozenset({"garment_erp_test"})


def _require_test_database() -> str:
    """取测试库连接串，并**强制校验库名**在白名单内。"""
    url = os.environ.get("DATABASE_URL_MIGRATION") or os.environ.get("DATABASE_URL", "")
    if not url:
        pytest.skip("未配置测试库连接串（DATABASE_URL / DATABASE_URL_MIGRATION），跳过数据库用例")
    db_name = url.rsplit("/", 1)[-1].split("?", 1)[0]
    if db_name not in ALLOWED_TEST_DATABASES:
        pytest.fail(f"拒绝在非测试库上跑测试：{db_name}（白名单 {sorted(ALLOWED_TEST_DATABASES)}）")
    return url


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    """每个用例前后清掉 Settings 单例缓存，避免用例间污染。"""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def migration_url() -> str:
    """迁移连接串（迁移账号 erp_ddl）。"""
    return _require_test_database()


@pytest.fixture(scope="session")
def app_database_url(migration_url: str) -> str:
    """应用连接串（运行账号 erp_app）。

    优先取 ``DATABASE_URL``；若只配了迁移串，则把用户名替换成 ``erp_app``
    ——因为权限测试必须以**受限账号**身份验证（docs/04 §6.2.1）。
    """
    app_url = os.environ.get("DATABASE_URL", "")
    if app_url:
        return app_url
    prefix, _, _rest = migration_url.rpartition("://")
    _, _, host_part = migration_url.rpartition("@")
    credentials, _, _ = host_part.partition(":")
    password = credentials.split(":", 1)[1] if ":" in credentials else ""
    return f"{prefix}//erp_app:{password}@{host_part}"


@pytest.fixture(scope="session")
def _schema(migration_url: str) -> Iterator[None]:
    """整个测试会话只建一次 schema（跑真实迁移，顺带验证闸门 4 的前半段）。"""
    from alembic.config import Config

    from alembic import command

    config = Config("alembic.ini")
    os.environ["DATABASE_URL_MIGRATION"] = migration_url
    command.upgrade(config, "head")
    yield
    # 不做 downgrade：让数据库留在 head 状态，方便本地反复跑；
    # 迁移可回滚性由闸门 4（docs/02 §3）在专门命令里验证。


@pytest_asyncio.fixture
async def db_session(_schema: None, app_database_url: str) -> AsyncIterator[AsyncSession]:
    """每个用例一个独立事务，结束回滚（docs/10 §2.2）。"""
    engine = create_async_engine(app_database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    connection: AsyncConnection = await engine.connect()
    transaction = await connection.begin()
    try:
        yield session_factory(bind=connection, join_transaction_mode="create_savepoint")
    finally:
        await transaction.rollback()
        await connection.close()
        await engine.dispose()


@pytest_asyncio.fixture
async def ddl_session(_schema: None, migration_url: str) -> AsyncIterator[AsyncSession]:
    """以**迁移账号**（erp_ddl）连接的 session。

    为什么需要它：``seed_baseline`` / ``restore_builtin`` 是**运维操作**，生产上用
    ``DATABASE_URL_MIGRATION`` 执行。而 ``erp_app`` 被 init 脚本 REVOKE 掉了全部
    DELETE 权限（docs/04 §6.2.1「生产库不给应用账号任何硬删能力」），因此
    「删除一行权限点以模拟被删」这类用例**只能**用迁移账号。

    事务回滚语义与 :func:`db_session` 相同，测试仍零污染。
    """
    engine = create_async_engine(migration_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    connection: AsyncConnection = await engine.connect()
    transaction = await connection.begin()
    try:
        yield session_factory(bind=connection, join_transaction_mode="create_savepoint")
    finally:
        await transaction.rollback()
        await connection.close()
        await engine.dispose()


@pytest.fixture
def app() -> FastAPI:
    """每个用例一个全新的应用实例（顺序随机仍通过）。"""
    return create_app()


@pytest_asyncio.fixture
async def client(app: FastAPI, db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """ASGI 内存客户端；``get_db`` 覆盖为回滚型 session。"""
    app.dependency_overrides[get_db] = lambda: db_session
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        # 必须清理，否则依赖覆盖会漏到后续用例
        app.dependency_overrides.clear()


@pytest.fixture
def auth_headers() -> object:
    """按角色生成带 token 的请求头。

    ⚠️ T-AUTH-002 之前返回占位实现（还没有签发 token 的能力）；
    真实实现见 T-AUTH-002，届时按角色参数化（docs/10 §2.2）。
    """

    def _make(*, role: str = "super_admin", user_id: str | None = None) -> dict[str, str]:
        del role, user_id
        raise NotImplementedError("认证头工厂待 T-AUTH-002 实现（当前无签发 token 的能力）")

    return _make
