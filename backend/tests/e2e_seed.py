"""E2E 数据准备（docs/10 §4「数据通过 API seed 准备，不通过 UI 造数据」）。

## 为什么要有这个脚本

E2E-00 的被测行为是「**页面上看到的数据 = 后端存的数据**」。如果用 UI 点出款号、
工序和单价，那么一旦失败，可能是业务逻辑错，也可能是上一条用例没点完 ——
定位成本很高。把「建数据」与「验数据」分开之后：

- 建数据走 **service 层**（有权限校验、有状态机、有并发保护），跑得通说明数据合法；
- 验数据走 **浏览器**（路由、渲染、交互），跑得通说明界面读的是真数据。

## 与单测种子脚本的分工

| 脚本 | 库 | 用途 |
| --- | --- | --- |
| `tests/conftest.py` 的 fixture | `garment_erp_test` | 单测，每个用例独立事务 |
| 本脚本 | `garment_erp_e2e`（`E2E_DATABASE_URL`） | E2E，**每轮全清重建** |

⚠️ **绝不能用 ``TRUNCATE``**：AGENTS §2.1 禁止对业务数据做硬删除，
而且 E2E 库也可能被人当测试库连着调试。这里一律走 ``DELETE`` + 软删字段
（``deleted_at``），并**先删子表后删父表**，与 04 §7 的外键顺序一致。

## 用法::

    E2E_DATABASE_URL=... E2E_ADMIN_PASSWORD=... python -m tests.e2e_seed

⚠️ 幂等：重复执行只会把 E2E 的那批数据复位，不会重复插入。
"""

import asyncio
import os
import sys
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.cli.seed_baseline import run as seed_baseline
from app.common.enums import DataScope
from app.common.permissions_registry import permission_codes
from app.core.errors import BusinessError
from app.core.permissions import AuthContext
from app.core.security import hash_password
from app.modules.base.models import (
    Operation,
    OperationRate,
    ProductCategory,
    Style,
    StyleOperation,
)
from app.modules.base.schemas import (
    OperationRateCreate,
    StyleOperationItemIn,
    StyleOperationsReplaceIn,
)
from app.modules.base.service import RateService, StyleService

#: E2E 专用款号前缀。⚠️ 用固定前缀而不是随机数：失败后能一眼看出库里残留了什么，
#: 而随机款号在 trace 与截图里是一串没法检索的十六进制。
STYLE_NO = "E2E-2026-0001"
#: E2E 专用账号。⚠️ **不复用 ADMIN**：`ADMIN` 的 ``must_change_password=true``，
#: 登录后会被守卫重定向到改密页 —— E2E 里那不是被测行为，只会卡住流程。
E2E_EMPLOYEE_NO = "E2EADMIN"
E2E_PRIOR_DATES = (date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1))
E2E_OPERATIONS = ("01", "02", "90")
#: 内置分类 code（`seed_dicts.py::BUILTIN_PRODUCT_CATEGORIES`，不是自定义码）
E2E_CATEGORY_CODE = "SET"


async def _e2e_admin_id(conn: AsyncConnection) -> UUID:
    user_id = (
        await conn.execute(
            text("SELECT id FROM users WHERE employee_no = :no"), {"no": E2E_EMPLOYEE_NO}
        )
    ).scalar_one()
    return UUID(str(user_id))


async def _ensure_admin(conn: AsyncConnection) -> AuthContext | None:
    """建一个**不用改密**的 E2E 管理员，返回它的 `AuthContext`。

    ⚠️ 走 `users` 表的 INSERT 而不是调 ``POST /users``：那要 token，
      而 token 又要先有账号 —— 鸡生蛋。E2E 种子是「数据库的准备」，
      这一步必须能独立完成（docs/10 §4 的精神：造数据不经过业务界面）。
    """
    existing = (
        await conn.execute(
            text("SELECT id FROM users WHERE employee_no = :no"), {"no": E2E_EMPLOYEE_NO}
        )
    ).scalar_one_or_none()
    if existing is None:
        password = os.environ.get("E2E_ADMIN_PASSWORD", "")
        if not password:
            print("FATAL: 未设置 E2E_ADMIN_PASSWORD", file=sys.stderr)
            return None
        await conn.execute(
            text(
                "INSERT INTO users (employee_no, name, password_hash, data_scope, "
                "is_active, must_change_password, created_by, updated_by, version) "
                # ⚠️ `created_by` 是 **UUID** 不是工号字符串 —— 传工号会得到
                #   `invalid input for query argument: invalid UUID`，而报错里完全
                #   看不出是这个字段（与 `seed_baseline` 同款写法）
                "VALUES (:no, 'E2E 管理员', :password_hash, 'FACTORY', true, false, "
                "gen_random_uuid(), gen_random_uuid(), 1) RETURNING id"
            ),
            {"no": E2E_EMPLOYEE_NO, "password_hash": hash_password(password)},
        )
        role_id = (
            await conn.execute(text("SELECT id FROM roles WHERE code = 'super_admin'"))
        ).scalar_one()
        await conn.execute(
            # ⚠️ 表名是 `user_roles` 不是 `role_assignments`（与 `seed_baseline` 一致）：
            #   写错时 SQLAlchemy 抛 `relation does not exist`，而报错完全看不出
            #   是种子脚本自己写错了表名
            text(
                "INSERT INTO user_roles (user_id, role_id) VALUES (:user_id, :role_id) "
                "ON CONFLICT DO NOTHING"
            ),
            {"user_id": await _e2e_admin_id(conn), "role_id": role_id},
        )
    return AuthContext(
        user_id=await _e2e_admin_id(conn),
        name="E2E 管理员",
        employee_no=E2E_EMPLOYEE_NO,
        workshop_id=None,
        group_no=None,
        permissions=frozenset(permission_codes()),
        data_scope=DataScope.FACTORY,
    )


async def _clean(conn: AsyncConnection) -> None:
    """清掉上一轮 E2E 数据。

    ⚠️ **必须用迁移账号**（``erp_ddl``）跑，**不能**用应用账号：
       ``operation_rates`` 按 ADR-0029 **没给应用账号 DELETE 权限** ——
       试过用 ``erp_app`` 清，拿到的是
       ``ProgrammingError: permission denied for table operation_rates``，
       而报错完全看不出「是你的账号权限不够，不是 SQL 写错了」。
       顺带说：这个报错本身就是 ADR-0029 生效的证据（应用账号真的删不掉单价历史）。
    """
    for statement in (
        delete(OperationRate).where(OperationRate.style_no == STYLE_NO),
        delete(StyleOperation).where(StyleOperation.style_no == STYLE_NO),
        delete(Style).where(Style.style_no == STYLE_NO),
    ):
        await conn.execute(statement)


async def run() -> int:
    url = os.environ.get("E2E_DATABASE_URL") or os.environ.get("DATABASE_URL_MIGRATION", "")
    if not url:
        print("FATAL: 未设置 E2E_DATABASE_URL", file=sys.stderr)
        return 1
    # 清理用迁移账号，业务写入用应用账号 —— 见 _clean 的注释
    admin_url = os.environ.get("DATABASE_URL_MIGRATION") or url

    # 基线（权限点 / 角色 / 内置字典）先跑一次：幂等，缺了就补
    if await seed_baseline() != 0:
        print("FATAL: 基线数据初始化失败", file=sys.stderr)
        return 1

    engine = create_async_engine(url, pool_pre_ping=True)
    admin_engine = create_async_engine(admin_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with admin_engine.begin() as conn:
            await _clean(conn)
            ctx = await _ensure_admin(conn)
        if ctx is None:
            return 1
        async with factory() as session:
            try:
                await _seed_business(session, ctx)
                # ⚠️ 必须**显式 commit**：`async with factory()` 退出时会把未提交的
                #   事务回滚，于是脚本打印「OK：数据已就绪」而库里一行都没有 ——
                #   症状是 E2E 首条用例就在登录后看到空列表，却看不出是种子没写进去。
                #   （SQLAlchemy 2.0 的 autobegin 让这里极易漏）
                await session.commit()
            except BusinessError as exc:
                # ⚠️ 业务错误要**原样报出来**：种子脚本里的 10008（工序必须启用）之类
                #   直接指到某条业务规则，比「seed 失败了」有用一百倍
                print(f"BUSINESS ERROR: {exc.message}", file=sys.stderr)
                return 1
    finally:
        await engine.dispose()
        await admin_engine.dispose()

    print(f"OK: E2E 数据已就绪（款号 {STYLE_NO} / 3 道工序 / 3 段单价区间）")
    return 0


async def _run_checked() -> int:
    """跑 `run` 并把**任何**异常原样带出来。

    ⚠️ 不加这一层的话，`_ensure_admin` 里的 ``relation does not exist`` 这类
    脚本自身错误会在 ``finally`` 之后照常抛出，但 stdout 已经打完了 ——
    看到的只是「基线 OK」而看不到「种子没写进去」，极难定位。
    """
    try:
        return await run()
    except Exception:  # noqa: BLE001 —— 入口要兜住一切并原样打印
        import traceback

        traceback.print_exc()
        return 1


async def _seed_business(session: AsyncSession, ctx: AuthContext) -> None:
    """款号 / 工序 / 单价区间。⚠️ 全部走 service：校验与状态机在这一层。"""
    from app.modules.base.schemas import StyleCreate

    category = (
        await session.execute(
            select(ProductCategory).where(
                # ⚠️ 内置分类的真实 code 是 `SET`/`DRESS`/… （`BUILTIN_PRODUCT_CATEGORIES`），
                #   凭直觉写 `TSHIRT` 会拿到 `NoResultFound` —— 报错只说「没找到一行」，
                #   不会说「你要找的分类不存在」
                ProductCategory.code == E2E_CATEGORY_CODE,
                ProductCategory.deleted_at.is_(None),
            )
        )
    ).scalar_one()
    for operation_no in E2E_OPERATIONS:
        exists = (
            await session.execute(
                select(Operation).where(
                    Operation.operation_no == operation_no, Operation.deleted_at.is_(None)
                )
            )
        ).scalar_one_or_none()
        if exists is None:
            session.add(
                Operation(
                    operation_no=operation_no,
                    name={"01": "裁", "02": "车", "90": "整烫"}[operation_no],
                    created_by=ctx.user_id,
                    updated_by=ctx.user_id,
                )
            )
    await session.flush()

    await StyleService(session, ctx).create(
        StyleCreate(
            style_no=STYLE_NO,
            name="E2E 全棉圆领 T 恤",
            category_id=category.id,
            bulk_qty=1200,
        )
    )
    # ⚠️ `version` 是**款号当前版本**：全量替换是写操作，必须乐观锁（10003）——
    #   拿刚 create 出来的 version 硬填不如显式取一次，免得 create 的返回值变了就静默错
    detail = await StyleService(session, ctx).get_required(STYLE_NO)
    await StyleService(session, ctx).replace_style_operations(
        STYLE_NO,
        StyleOperationsReplaceIn(
            version=detail.version,
            items=[
                StyleOperationItemIn(
                    operation_no="01", sequence=1, bundle_qty=1, is_piecework=True
                ),
                StyleOperationItemIn(
                    operation_no="02", sequence=2, bundle_qty=1, is_piecework=False
                ),
                StyleOperationItemIn(
                    operation_no="90",
                    sequence=3,
                    bundle_qty=1,
                    is_piecework=False,
                    is_final_operation=True,
                ),
            ],
        ),
    )
    rates = RateService(session, ctx)
    for index, price in enumerate(("0.350000", "0.400000", "0.450000")):
        await rates.set_rate(
            OperationRateCreate(
                operation_no="01",
                style_no=STYLE_NO,
                unit_price=Decimal(price),
                effective_from=E2E_PRIOR_DATES[index],
                reason="E2E 种子：历史区间",
            )
        )


def main() -> None:
    raise SystemExit(asyncio.run(_run_checked()))


if __name__ == "__main__":
    main()
