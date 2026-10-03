"""恢复内置数据（docs/04-数据库规范.md §7.4 规则第 4 条）。

⚠️ 为什么需要独立命令：``seed_baseline`` 是**一次性初始化**，不是每次启动都跑。
用户真删了内置行之后（业务方确认"删掉就是不要了"），``seed_baseline`` 绝不能
把它们悄悄插回来；但用户点「恢复内置库」时又应该能找回。

两条命令的职责**不重叠**：

===================  ==========================================  ==========================
命令                  行为                                        何时用
===================  ==========================================  ==========================
seed_baseline        只补"键不存在"的行；被真删的（有墓碑）不补    部署 / 新增权限点版本
restore_builtin      显式恢复被真删的内置行，并清除墓碑            用户点「恢复内置库」
===================  ==========================================  ==========================

⚠️ 本卡范围：``permissions`` / ``roles`` / ``role_permissions``（静态配置，
没有"用户删除"的概念）。字典项（``colors`` / ``sizes`` / ``size_groups``）的
墓碑机制在 T-BASE-001 落地（ADR-0025 §决策 3），本命令届时扩展。

用法::

    python -m app.cli.restore_builtin --list          # 看看有哪些被删了
    python -m app.cli.restore_builtin --permissions    # 恢复权限点与角色
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from app.cli.seed_baseline import seed_permissions, seed_role_permissions, seed_roles
from app.common.permissions_registry import PERMISSIONS, ROLES
from app.core.config import get_settings

#: 墓碑用的 doc_type。权限点与角色不是业务单据，但删除留痕同样写 document_logs，
#: 保证"谁删的、什么时候删的"可审计（docs/07 §5）。
TOMBSTONE_DOC_TYPE = "PermissionRegistry"


def _migration_url() -> str:
    """取迁移连接串。缺失时返回空串，由调用方决定退出码。"""
    return get_settings().database_url_migration.get_secret_value() or os.environ.get(
        "DATABASE_URL_MIGRATION", ""
    )


async def list_missing(conn: AsyncConnection) -> tuple[list[str], list[str]]:
    """列出数据库里缺失的权限点与角色（= 被真删的）。"""
    db_permissions = set(
        (await conn.execute(sa.text("SELECT code FROM permissions"))).scalars().all()
    )
    db_roles = set((await conn.execute(sa.text("SELECT code FROM roles"))).scalars().all())
    missing_permissions = sorted({item.code for item in PERMISSIONS} - db_permissions)
    missing_roles = sorted({role.code for role in ROLES} - db_roles)
    return missing_permissions, missing_roles


async def clear_tombstones(conn: AsyncConnection, codes: list[str], doc_type: str) -> int:
    """清除墓碑：写入 ``RESTORE`` 记录（ADR-0025 §决策 3 的解除方式）。

    判定口径：存在 ``DELETE`` 且不存在更新的 ``RESTORE`` 才算墓碑。
    """
    if not codes:
        return 0
    operator_id = await conn.scalar(sa.text("SELECT gen_random_uuid()"))
    await conn.execute(
        sa.text(
            "INSERT INTO document_logs (doc_type, doc_id, doc_no, action, "
            "operator_id, operator_name, reason) "
            "SELECT :doc_type, gen_random_uuid(), code, 'RESTORE', :operator_id, "
            "'restore_builtin', '用户显式恢复内置数据' "
            "FROM unnest(CAST(:codes AS text[])) AS code"
        ),
        {"doc_type": doc_type, "operator_id": operator_id, "codes": codes},
    )
    return len(codes)


async def restore(
    conn: AsyncConnection,
    *,
    do_list: bool = False,
    do_permissions: bool = False,
    do_roles: bool = False,
) -> str:
    """恢复逻辑（接已有连接），返回给用户看的汇总文案。

    与 :func:`run` 分开是为了可测：测试用回滚型 session 直接调它，不必起引擎。
    """
    missing_permissions, missing_roles = await list_missing(conn)
    lines: list[str] = []

    if do_list or not (do_permissions or do_roles):
        lines.append(f"缺失权限点 {len(missing_permissions)} 个：{missing_permissions or '无'}")
        lines.append(f"缺失内置角色 {len(missing_roles)} 个：{missing_roles or '无'}")
        if not (do_permissions or do_roles):
            return "\n".join(lines)

    if do_permissions and missing_permissions:
        await clear_tombstones(conn, missing_permissions, "Permission")
    if do_roles and missing_roles:
        await clear_tombstones(conn, missing_roles, "Role")

    perm_result = await seed_permissions(conn)
    role_result = await seed_roles(conn)
    binding_count = await seed_role_permissions(conn)
    lines.append(
        f"已恢复：权限点新增 {perm_result[1]} 个，角色新增 {role_result[1]} 个，"
        f"权限绑定新增 {binding_count} 条（墓碑已解除）"
    )
    lines.append("OK: 恢复完成")
    return "\n".join(lines)


async def run(
    *,
    do_list: bool = False,
    do_permissions: bool = False,
    do_roles: bool = False,
) -> int:
    """CLI 入口：建引擎 → 调 :func:`restore` → 打印。返回进程退出码。"""
    url = _migration_url()
    if not url:
        print("FATAL: 未配置迁移连接串，请设置 DATABASE_URL_MIGRATION", file=sys.stderr)
        return 1

    engine = create_async_engine(url, pool_pre_ping=True)
    try:
        async with engine.begin() as conn:
            output = await restore(
                conn, do_list=do_list, do_permissions=do_permissions, do_roles=do_roles
            )
    finally:
        await engine.dispose()
    print(output)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="显式恢复被删除的内置数据（seed_baseline 不会自动复活它们）"
    )
    parser.add_argument("--list", action="store_true", help="只列出缺失项，不写入")
    parser.add_argument("--permissions", action="store_true", help="恢复权限点")
    parser.add_argument("--roles", action="store_true", help="恢复内置角色")
    args = parser.parse_args()
    raise SystemExit(
        asyncio.run(run(do_list=args.list, do_permissions=args.permissions, do_roles=args.roles))
    )


if __name__ == "__main__":
    main()
