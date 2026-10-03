"""数据库 engine / session factory / FastAPI 依赖（docs/01-技术选型与架构.md §5.3）。

铁律：
    - 只用 asyncpg + SQLAlchemy async
    - ``session.begin()`` **只允许出现在 service 层方法第一层**；
      本模块的 ``get_db`` 只负责"关掉自动提交"，不做业务事务
    - 连接池上限按 docs/01 §3 的 2C/12G 资源预算
"""

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings, get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def create_db_engine(settings: Settings | None = None) -> AsyncEngine:
    """按配置创建 engine（进程内单例）。

    ``pool_pre_ping=True`` 是必须的：PostgreSQL 会因空闲超时或重启断开连接，
    不做探活就会出现偶发 ``ConnectionDoesNotExist``。
    """
    resolved = settings or get_settings()
    url = resolved.database_url.get_secret_value()
    if not url:
        raise RuntimeError(
            "DATABASE_URL 未配置；请在 .env 设置应用连接串（账号 erp_app，docs/04 §6.2.1）"
        )
    options: dict[str, Any] = {
        "echo": resolved.db_echo,
        "pool_pre_ping": True,
        "pool_size": resolved.db_pool_size,
        "max_overflow": resolved.db_max_overflow,
    }
    # 测试库用独立 database，用 sqlite 之类不需要 pool 参数的场景尚未出现，
    # 但真要接入时这里需要分支；先保持显式，避免隐式默认值。
    return create_async_engine(url, **options)


def get_engine() -> AsyncEngine:
    """取进程级单例 engine。"""
    global _engine
    if _engine is None:
        _engine = create_db_engine()
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """取进程级单例 session 工厂。"""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：每请求一个 session，请求结束必关。

    ⚠️ 这里**不开事务**。事务边界唯一入口是 service 层
    （docs/03-代码规范.md §1.1 第 5 条），Router 与 Repository 都不得自行 commit。
    """
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def dispose_engine() -> None:
    """关闭 engine（应用关闭时调用，docs/03 §1.1：生命周期由 main 统一管理）。"""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
