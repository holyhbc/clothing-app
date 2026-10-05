"""Alembic 迁移环境（异步，docs/04-数据库规范.md §6.2）。

三条硬规则：
    1. 连接串**只从环境变量取**（优先 ``DATABASE_URL_MIGRATION`` 迁移账号），
       绝不写进 alembic.ini 或代码（docs/11 §2）
    2. ``target_metadata`` 必须能反映模型 —— 靠 import ``app.common.models``
       触发所有模型注册，否则 autogenerate 会漏表
    3. 迁移文件里**不写业务逻辑**，只写 DDL

⚠️ 已合并到 main 的迁移文件禁止修改，修正只能写新迁移（docs/04 §6.2）。
"""

import asyncio
from logging.config import fileConfig

from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.common.models import Base, register_all_models

config = context.config

# ⚠️ 注册入口是**共享的**（app/common/models.py::register_all_models），
#    守卫 tests/modules/test_docs_ddl_sync.py 用的是同一个 —— 两边各写一份
#    的后果是 T-CUT-001b-1 真的踩到了：「env.py 按 pkgutil 遍历注册全了，
#    而测试靠 import app.main 碰运气」，于是裁剪模块在还没有 router 时
#    注册不上，守卫报「表没建」，症状却依赖用例执行顺序。
register_all_models()

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _get_url() -> str:
    """取迁移连接串：优先迁移账号，其次应用账号，最后报错。"""
    from app.core.config import get_settings

    settings = get_settings()
    url = (
        settings.database_url_migration.get_secret_value()
        or settings.database_url.get_secret_value()
    )
    if not url:
        raise RuntimeError(
            "迁移需要 DATABASE_URL_MIGRATION（迁移账号 erp_ddl）"
            "或至少 DATABASE_URL；请检查 .env（docs/04 §6.2.1）"
        )
    return url


def run_migrations_offline() -> None:
    """离线模式：只输出 SQL，不连库（用于生成评审脚本）。"""
    context.configure(
        url=_get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """同步上下文里执行迁移。"""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def include_object(object_, name, type_, reflected, compare_to) -> bool:
    """告诉 autogenerate 哪些对象不参与比对。

    目前只跳过一类：**表达式 GIN 索引**（``*_trgm``）。

    原因是它们无法被可靠复现：PostgreSQL 会把表达式规范化（补 ``::text``
    转换、调整括号），autogenerate 拿模型里的 ``sa.text("(a || ' ' || b)")`` 与
    数据库里读回来的规范化文本逐字比，必然判成"删掉再建一个"，于是 ``alembic
    check`` 永远不为空、每次 autogenerate 都产出一堆无意义的 drop/create。

    这些索引的正确性由**另一条守卫**保证：``tests/modules/test_base_dict_service.py``
    断言 ``idx_*_trgm`` 存在，且在 ``SET enable_seqscan = off`` 下
    ``EXPLAIN`` 能命中 ``Bitmap Index Scan``（docs/04 §5.1）—— 那条才是
    "索引表达式与查询谓词一致"的真正判据。

    ⚠️ 如果新增别的表达式索引，也要在这里加一条，并配套写 EXPLAIN 断言。
    """
    return not (type_ == "index" and name is not None and name.endswith("_trgm"))


async def run_async_migrations() -> None:
    """用 asyncpg 异步连接跑迁移。

    用 async_engine_from_config 而非 create_db_engine()：迁移连接池参数与
    应用不同（迁移是短连接、串行，不需要常驻池）。
    """
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _get_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """在线模式入口。"""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
