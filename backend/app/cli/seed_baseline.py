"""基线数据初始化（docs/11 §9 部署步骤 5；docs/modules/01 §5）。

写入内容（docs/07 §2.2 / §2.3）：
    1. ``permissions`` —— 122 个权限点
    2. ``roles``        —— 10 个内置角色
    3. ``role_permissions`` —— 角色权限绑定
    4. ``users``        —— 首个超管账号（仅当一个都没有时创建）

⚠️ 幂等是硬要求（docs/04 §7.4、docs/07 §6）：
    - 全部 ``ON CONFLICT DO NOTHING``
    - **不覆盖**用户改过的 ``is_active`` 与名称
    - 可重复执行，连跑 3 次结果完全一致

⚠️ 为什么迁移 0002 已经 seed 过一次，这里还要再 seed：
    迁移负责"建表时就有权限点"（保证任何环境升级后权限齐全），CLI 负责
    "新增权限点版本后补齐"（权限点会随业务增长，迁移不能为每个新点发一个版本）。
    两者都幂等，共存无害。

用法::

    python -m app.cli.seed_baseline                # 幂等补齐
    python -m app.cli.seed_baseline --check        # 只校验不写入，CI 可用
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from app.cli.seed_dicts import (
    BUILTIN_COLORS,
    BUILTIN_PRODUCT_CATEGORIES,
    BUILTIN_SIZE_GROUPS,
    BUILTIN_SIZES,
    check_dict_library,
    seed_dict_library,
)
from app.common.permissions_registry import (
    PERMISSIONS,
    ROLES,
    permission_codes,
    resolve_role_permissions,
    role_codes,
)
from app.core.config import get_settings
from app.core.errors import BusinessError

#: 首个超管的初始口令。**必须**由环境变量提供，绝不写死在代码里（docs/11 §2）。
ENV_INITIAL_PASSWORD = "ERP_INITIAL_ADMIN_PASSWORD"  # noqa: S105 —— 这是环境变量名，不是口令


async def seed_permissions(conn: AsyncConnection) -> tuple[int, int]:
    """写入权限点，返回 (尝试数, 实际新增数)。"""
    attempted = 0
    inserted = 0
    for item in PERMISSIONS:
        attempted += 1
        result = await conn.execute(
            sa.text(
                "INSERT INTO permissions (code, name, module, action, sort_order, "
                "created_by, updated_by, version) "
                "VALUES (:code, :name, :module, :action, :sort_order, "
                "gen_random_uuid(), gen_random_uuid(), 1) "
                "ON CONFLICT (code) DO NOTHING"
            ),
            {
                "code": item.code,
                "name": item.name,
                "module": item.module,
                "action": item.action,
                "sort_order": item.sort_order,
            },
        )
        inserted += result.rowcount or 0
    return attempted, inserted


async def seed_roles(conn: AsyncConnection) -> tuple[int, int]:
    """写入内置角色，返回 (尝试数, 实际新增数)。"""
    attempted = 0
    inserted = 0
    for role in ROLES:
        attempted += 1
        result = await conn.execute(
            sa.text(
                "INSERT INTO roles (code, name, data_scope, is_system, description, "
                "created_by, updated_by, version) "
                "VALUES (:code, :name, :data_scope, true, :description, "
                "gen_random_uuid(), gen_random_uuid(), 1) "
                "ON CONFLICT (code) DO NOTHING"
            ),
            {
                "code": role.code,
                "name": role.name,
                "data_scope": role.data_scope.value,
                "description": role.description,
            },
        )
        inserted += result.rowcount or 0
    return attempted, inserted


async def seed_role_permissions(conn: AsyncConnection) -> int:
    """绑定角色→权限，返回新增条数。"""
    total = 0
    for role in ROLES:
        codes = list(resolve_role_permissions(role))
        if not codes:
            continue
        result = await conn.execute(
            sa.text(
                "INSERT INTO role_permissions (role_id, permission_id, created_at) "
                "SELECT r.id, p.id, now() FROM roles r, permissions p "
                "WHERE r.code = :role_code AND p.code = ANY(CAST(:codes AS text[])) "
                "ON CONFLICT DO NOTHING"
            ),
            {"role_code": role.code, "codes": codes},
        )
        total += result.rowcount or 0
    return total


async def ensure_initial_admin(conn: AsyncConnection) -> str:
    """确保存在至少一个超管账号，返回提示文案。

    初始口令只从环境变量取；未提供则**不创建**账号并提示，而不是生成一个弱口令。
    """
    from app.core.security import hash_password, validate_password_strength

    existing = await conn.scalar(sa.text("SELECT count(*) FROM users"))
    if existing:
        return f"已存在 {existing} 个账号，跳过初始超管创建"

    raw_password = os.environ.get(ENV_INITIAL_PASSWORD, "")
    if not raw_password:
        return (
            f"未设置 {ENV_INITIAL_PASSWORD}，跳过初始超管创建。"
            "首次部署请先设置该变量（至少 8 位含字母数字）再重跑本命令"
        )
    validate_password_strength(raw_password)

    await conn.execute(
        sa.text(
            "INSERT INTO users (employee_no, name, password_hash, data_scope, "
            "is_active, must_change_password, created_by, updated_by, version) "
            "VALUES ('ADMIN', '系统管理员', :password_hash, 'FACTORY', true, true, "
            "gen_random_uuid(), gen_random_uuid(), 1)"
        ),
        {"password_hash": hash_password(raw_password)},
    )
    role_id = await conn.scalar(sa.text("SELECT id FROM roles WHERE code = 'super_admin'"))
    await conn.execute(
        sa.text(
            "INSERT INTO user_roles (user_id, role_id) VALUES (:user_id, :role_id) "
            "ON CONFLICT DO NOTHING"
        ),
        {
            "user_id": await conn.scalar(
                sa.text("SELECT id FROM users WHERE employee_no = 'ADMIN'")
            ),
            "role_id": role_id,
        },
    )
    return "已创建初始超管账号 ADMIN（首次登录强制改密）"


async def check_baseline(conn: AsyncConnection) -> list[str]:
    """只校验不写入，返回问题清单（空表示通过）。"""
    problems: list[str] = []
    db_codes = set((await conn.execute(sa.text("SELECT code FROM permissions"))).scalars().all())
    missing = permission_codes() - db_codes
    extra = db_codes - permission_codes()
    if missing:
        problems.append(f"数据库缺少权限点 {len(missing)} 个：{sorted(missing)[:5]}")
    if extra:
        problems.append(f"数据库存在未登记的权限点 {len(extra)} 个：{sorted(extra)[:5]}")

    db_roles = set((await conn.execute(sa.text("SELECT code FROM roles"))).scalars().all())
    missing_roles = role_codes() - db_roles
    if missing_roles:
        problems.append(f"数据库缺少内置角色：{sorted(missing_roles)}")

    admin_perms = await conn.scalar(
        sa.text(
            "SELECT count(*) FROM role_permissions rp "
            "JOIN roles r ON r.id = rp.role_id WHERE r.code = 'super_admin'"
        )
    )
    if admin_perms != len(permission_codes()):
        problems.append(f"super_admin 权限数 {admin_perms} != 全部权限数 {len(permission_codes())}")
    problems.extend(await check_dict_library(conn))
    return problems


async def run(check_only: bool = False) -> int:
    """执行基线数据初始化。返回进程退出码。"""
    url = get_settings().database_url_migration.get_secret_value() or os.environ.get(
        "DATABASE_URL_MIGRATION", ""
    )
    if not url:
        print("FATAL: 未配置迁移连接串，请设置 DATABASE_URL_MIGRATION", file=sys.stderr)
        return 1

    engine = create_async_engine(url, pool_pre_ping=True)
    try:
        async with engine.begin() as conn:
            if check_only:
                problems = await check_baseline(conn)
                if problems:
                    for problem in problems:
                        print(f"FAIL: {problem}", file=sys.stderr)
                    return 1
                print(
                    f"OK: 基线数据一致（权限点 {len(PERMISSIONS)} 个 / 角色 {len(ROLES)} 个 / "
                    f"内置色 {len(BUILTIN_COLORS)} / 内置尺码 {len(BUILTIN_SIZES)} / "
                    f"内置码表 {len(BUILTIN_SIZE_GROUPS)} / 内置分类 {len(BUILTIN_PRODUCT_CATEGORIES)}）"
                )
                return 0

            perm_result = await seed_permissions(conn)
            role_result = await seed_roles(conn)
            binding_count = await seed_role_permissions(conn)
            admin_note = await ensure_initial_admin(conn)
            # 字典内置库放在权限/角色之后：码表成员要引用 sizes.id
            dict_result = await seed_dict_library(conn)

        print(
            f"权限点：尝试 {perm_result[0]} 新增 {perm_result[1]}；"
            f"角色：尝试 {role_result[0]} 新增 {role_result[1]}；"
            f"权限绑定新增 {binding_count}"
        )
        print(
            "字典内置库新增："
            + "；".join(f"{table} {count}" for table, count in dict_result.items())
        )
        print(admin_note)
        print("OK: 基线数据已同步（幂等，可重复执行）")
        return 0
    except BusinessError as exc:
        print(f"BUSINESS ERROR: {exc.message}", file=sys.stderr)
        return 1
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="初始化基线数据（权限点 / 内置角色 / 初始超管）")
    parser.add_argument(
        "--check",
        action="store_true",
        help="只校验数据库与代码是否一致，不写入任何数据（CI 可用）",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(check_only=args.check)))


if __name__ == "__main__":
    main()
