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
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
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

from app.common.enums import DataScope
from app.common.permissions_registry import ROLES, resolve_role_permissions
from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import create_access_token
from app.main import create_app
from app.modules.auth.models import Permission, Role, RolePermission
from app.modules.auth.service import AuthService
from tests.factories.user import (
    RoleFactory,
    UserFactory,
    UserRoleFactory,
    grant_permissions,
)

#: 角色 code → registry 定义。测试里写角色名而不是手写权限点字面量
ROLE_BY_CODE: dict[str, Any] = {seed.code: seed for seed in ROLES}


async def _ensure_builtin_role(session: AsyncSession, code: str) -> Role:
    """取内置角色；迁移没 seed 时就地补一个（保证测试不依赖 seed 时机）。"""
    found = await session.execute(select(Role).where(Role.code == code))
    role = found.scalar_one_or_none()
    if role is not None:
        return role

    seed = ROLE_BY_CODE[code]
    role = await RoleFactory.create(
        session, code=seed.code, name=seed.name, data_scope=seed.data_scope, is_system=True
    )
    codes = resolve_role_permissions(seed)
    if codes:
        rows = await session.execute(select(Permission).where(Permission.code.in_(list(codes))))
        # 批量插入：super_admin 有 122 个权限点，逐条插会让每个用例多 122 次往返
        await session.execute(
            pg_insert(RolePermission.__table__),
            [{"role_id": role.id, "permission_id": item.id} for item in rows.scalars().all()],
        )
        await session.flush()
    return role


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
async def _clean_redis_namespace() -> AsyncIterator[None]:
    """清掉 ``auth:login_fail:*`` 与 ``idem:*``。

    ⚠️ 这是一个真实的测试隔离陷阱：Redis 里的登录失败计数**跨用例存活**，
    而数据库每个用例都回滚。两者语义不一致的后果是 —— 某个用例把 A001 锁了，
    后面所有用 A001 的用例都会拿到 10004，而且失败原因看起来毫不相干
    （"账号已停用"的用例报 429）。这类 bug 极难定位，所以在夹具层根治。
    """
    from app.core.cache import close_redis, get_redis

    client = get_redis()
    if client is None:
        yield  # 未配置 Redis 时幂等与锁定走进程内/DB 降级路径，无需清理
        return
    for pattern in ("auth:login_fail:*", "idem:*"):
        keys = [key async for key in client.scan_iter(match=pattern)]
        if keys:
            await client.delete(*keys)
    yield
    # 必须关闭连接：``get_redis`` 是模块级单例，而 pytest-asyncio 每个用例开一个
    # 新 event loop。单例跨 loop 复用会在下一个用例里抛
    # "Future attached to a different loop"，且报错点离真正的原因很远。
    await close_redis()


@pytest_asyncio.fixture
async def db_session(
    _schema: None, app_database_url: str, _clean_redis_namespace: None
) -> AsyncIterator[AsyncSession]:
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


@pytest_asyncio.fixture
async def auth_headers(db_session: AsyncSession) -> AsyncIterator[Any]:
    """按角色生成带真实 access token 的请求头（docs/10 §2.2）。

    直接用内置角色表建用户并按 registry 授权，这样测试里的权限集合同生产一致
    —— 手写 ``{"base:update"}`` 这类字面量会在权限点改名后静默失效。

    用法::

        async def test_x(client, auth_headers):
            headers = await auth_headers(role="workshop_supervisor")

    :param role: 内置角色 code；``custom`` 表示自建角色（配合 ``permissions``）
    :param data_scope: 覆盖用户数据范围（测数据范围用）
    :param permissions: 自定义权限点 code 列表（``role="custom"`` 时用）
    :param user_id: 直接给已有用户发令牌，不新建用户
    """

    async def _make(
        *,
        role: str = "super_admin",
        data_scope: DataScope | None = None,
        permissions: tuple[str, ...] | None = None,
        user_id: UUID | None = None,
        employee_no: str | None = None,
        workshop_id: UUID | None = None,
        group_no: str | None = None,
    ) -> dict[str, str]:
        service = AuthService(db_session)

        if user_id is not None:
            user = await service.get_active_user(user_id)
        else:
            if role != "custom":
                # 未知角色名要立刻报错，而不是建出一个没权限的账号
                if role not in ROLE_BY_CODE:
                    raise AssertionError(f"未知的内置角色：{role}")
                role_obj = await _ensure_builtin_role(db_session, role)
            else:
                # 复用已存在的 custom 角色：一个用例里可能要建多个用户，
                # 每次新建会撞 uq_roles_code
                found = await db_session.execute(select(Role).where(Role.code == "custom"))
                role_obj = found.scalar_one_or_none()
                if role_obj is None:
                    role_obj = await RoleFactory.create(
                        db_session,
                        code="custom",
                        name="自定义角色",
                        data_scope=data_scope or DataScope.SELF,
                    )
            user = await UserFactory.create(
                db_session,
                employee_no=employee_no or f"T{uuid4().hex[:7].upper()}",
                data_scope=data_scope or role_obj.data_scope,
                workshop_id=workshop_id,
                group_no=group_no,
            )
            await UserRoleFactory.create(db_session, user_id=user.id, role_id=role_obj.id)
            if permissions is not None:
                await grant_permissions(db_session, role=role_obj, codes=permissions)

        role_ids = await service.get_role_ids(user.id)
        token, _expires_in = create_access_token(
            user_id=str(user.id),
            role_ids=role_ids,
            data_scope=DataScope(user.data_scope).value,
        )
        # ⚠️ 把 ``user_id`` 一并返回：有些用例要拿它当外键值用
        # （如 ``styles.merchandiser_id`` 是真外键 → ``users.id``，传随机 UUID 会撞
        # FK 违例，而报错出现在 seed 里、与真正的原因隔了三层）。
        return {"Authorization": f"Bearer {token}", "X-Test-User-Id": str(user.id)}

    yield _make
