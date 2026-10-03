"""基线数据 CLI 测试（docs/07 §6、docs/11 §9 步骤 5）。

覆盖：
    - ``seed_baseline`` 幂等（连跑 3 次行数与内容零变化，docs/04 §7.4 TC-33 同款要求）
    - ``--check`` 只校验不写入，且能发现漂移
    - 初始超管：无口令变量时**不创建**，不强塞弱口令
    - ``restore_builtin`` 只列出缺失项；恢复时补齐并写 RESTORE 留痕
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.cli import restore_builtin, seed_baseline
from app.common.permissions_registry import role_codes
from app.core.errors import BusinessError

pytestmark = pytest.mark.usefixtures("_schema")


async def _purge_permission(session: AsyncSession, code: str) -> None:
    """删除一个权限点及其全部角色绑定（模拟"用户删掉了这个权限点"）。"""
    await session.execute(
        text(
            "DELETE FROM role_permissions WHERE permission_id IN "
            "(SELECT id FROM permissions WHERE code = :code)"
        ),
        {"code": code},
    )
    await session.execute(text("DELETE FROM permissions WHERE code = :code"), {"code": code})


async def _purge_role(session: AsyncSession, code: str) -> None:
    """删除一个角色及其全部绑定。"""
    await session.execute(
        text(
            "DELETE FROM role_permissions WHERE role_id IN "
            "(SELECT id FROM roles WHERE code = :code)"
        ),
        {"code": code},
    )
    await session.execute(text("DELETE FROM roles WHERE code = :code"), {"code": code})


async def _counts(session: AsyncSession) -> dict[str, int]:
    return {
        "permissions": await session.scalar(text("SELECT count(*) FROM permissions")),
        "roles": await session.scalar(text("SELECT count(*) FROM roles")),
        "role_permissions": await session.scalar(text("SELECT count(*) FROM role_permissions")),
        "users": await session.scalar(text("SELECT count(*) FROM users")),
    }


# ---------------------------------------------------------------------------
# seed_baseline
# ---------------------------------------------------------------------------


async def test_seed_is_idempotent_over_three_runs(ddl_session: AsyncSession) -> None:
    """TC-A01：连跑 3 次，行数与内容完全一致。"""
    await seed_baseline.seed_permissions(ddl_session)
    await seed_baseline.seed_roles(ddl_session)
    await seed_baseline.seed_role_permissions(ddl_session)
    first = await _counts(ddl_session)
    content_first = await ddl_session.execute(
        text("SELECT code, name, module FROM permissions ORDER BY code")
    )
    snapshot = content_first.all()

    for _ in range(2):
        await seed_baseline.seed_permissions(ddl_session)
        await seed_baseline.seed_roles(ddl_session)
        await seed_baseline.seed_role_permissions(ddl_session)

    assert await _counts(ddl_session) == first
    again = await ddl_session.execute(
        text("SELECT code, name, module FROM permissions ORDER BY code")
    )
    assert again.all() == snapshot, "seed 覆盖了已有行的内容"


async def test_seed_does_not_overwrite_renamed_permission(ddl_session: AsyncSession) -> None:
    """用户改过权限点名称后，重跑 seed **不得**改回去（docs/04 §7.4 幂等规则第 3 条）。"""
    await ddl_session.execute(
        text("UPDATE permissions SET name = '我改过的名字' WHERE code = 'base:read'")
    )
    await seed_baseline.seed_permissions(ddl_session)
    name = await ddl_session.scalar(text("SELECT name FROM permissions WHERE code = 'base:read'"))
    assert name == "我改过的名字"


async def test_check_passes_on_consistent_data(ddl_session: AsyncSession) -> None:
    """--check 在数据一致时不报问题。

    ⚠️ 必须先跑一遍 ``seed_dict_library``：权限点与角色由**迁移 0002** 自带，
    而字典内置库（16 色 / 8 尺码 / 2 码表 / 6 分类）来自 **seed 命令**、不在迁移里。
    部署顺序是「迁移 → seed」，CI 只跑迁移，所以字典数据得由用例自己准备 ——
    这正是本用例要断言的对象。踩过：容器里跑 CI 报"内置 colors 数量 0 != 16"，
    看起来像 seed 坏了，其实是数据压根没被 seed 过。
    """
    from app.cli.seed_dicts import seed_dict_library

    await seed_dict_library(ddl_session)
    assert await seed_baseline.check_baseline(ddl_session) == []


async def test_check_detects_missing_permission(ddl_session: AsyncSession) -> None:
    """--check 能发现"代码有、库里没有"的权限点。"""
    # 先删绑定再删权限点，避免外键约束
    await ddl_session.execute(
        text(
            "DELETE FROM role_permissions WHERE permission_id IN "
            "(SELECT id FROM permissions WHERE code = 'base:read')"
        )
    )
    await ddl_session.execute(text("DELETE FROM permissions WHERE code = 'base:read'"))
    problems = await seed_baseline.check_baseline(ddl_session)
    assert any("缺少权限点" in problem for problem in problems)


async def test_check_detects_unregistered_permission(ddl_session: AsyncSession) -> None:
    """--check 能发现"库里有、代码没登记"的权限点（防手工 INSERT 绕过规范）。"""
    await ddl_session.execute(
        text(
            "INSERT INTO permissions (code, name, module, action, sort_order, "
            "created_by, updated_by, version) "
            "VALUES ('ghost:read', '幽灵权限', 'ghost', 'read', 999, "
            "gen_random_uuid(), gen_random_uuid(), 1)"
        )
    )
    problems = await seed_baseline.check_baseline(ddl_session)
    assert any("未登记的权限点" in problem for problem in problems)


async def test_check_detects_incomplete_super_admin(ddl_session: AsyncSession) -> None:
    """--check 能发现超管权限被裁剪。"""
    await ddl_session.execute(
        text(
            "DELETE FROM role_permissions WHERE permission_id IN "
            "(SELECT id FROM permissions WHERE code = 'payroll:pay')"
        )
    )
    problems = await seed_baseline.check_baseline(ddl_session)
    assert any("super_admin" in problem for problem in problems)


async def test_check_detects_missing_role(ddl_session: AsyncSession) -> None:
    await _purge_role(ddl_session, "accountant")
    problems = await seed_baseline.check_baseline(ddl_session)
    assert any("缺少内置角色" in problem for problem in problems)


# ---------------------------------------------------------------------------
# 初始超管
# ---------------------------------------------------------------------------


async def test_initial_admin_skipped_without_password_env(
    ddl_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未设置初始口令变量时**不创建**账号，并明确提示（不生成弱口令）。"""
    monkeypatch.delenv(seed_baseline.ENV_INITIAL_PASSWORD, raising=False)
    note = await seed_baseline.ensure_initial_admin(ddl_session)
    assert "跳过" in note
    assert seed_baseline.ENV_INITIAL_PASSWORD in note
    assert await ddl_session.scalar(text("SELECT count(*) FROM users")) == 0


async def test_initial_admin_created_and_bound_to_super_admin(
    ddl_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """给了口令则创建 ADMIN 并绑定 super_admin，且首次登录强制改密。"""
    monkeypatch.setenv(seed_baseline.ENV_INITIAL_PASSWORD, "InitPassw0rd")
    note = await seed_baseline.ensure_initial_admin(ddl_session)

    assert "已创建" in note
    row = (
        await ddl_session.execute(
            text("SELECT must_change_password, is_active FROM users WHERE employee_no = 'ADMIN'")
        )
    ).one()
    assert row[0] is True, "初始账号必须强制改密"
    assert row[1] is True

    bound = await ddl_session.scalar(
        text(
            "SELECT count(*) FROM user_roles ur "
            "JOIN users u ON u.id = ur.user_id "
            "JOIN roles r ON r.id = ur.role_id "
            "WHERE u.employee_no = 'ADMIN' AND r.code = 'super_admin'"
        )
    )
    assert bound == 1

    # 已有账号时不再重复创建
    again = await seed_baseline.ensure_initial_admin(ddl_session)
    assert "跳过初始超管创建" in again
    assert await ddl_session.scalar(text("SELECT count(*) FROM users")) == 1


async def test_initial_admin_rejects_weak_password(
    ddl_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """弱口令直接被策略拦下（docs/07 §1.1）。"""
    monkeypatch.setenv(seed_baseline.ENV_INITIAL_PASSWORD, "short")
    with pytest.raises(BusinessError, match="8"):
        await seed_baseline.ensure_initial_admin(ddl_session)


# ---------------------------------------------------------------------------
# restore_builtin
# ---------------------------------------------------------------------------


async def test_restore_lists_missing_items(ddl_session: AsyncSession) -> None:
    """只列缺失项时**不写入**任何数据。"""
    await _purge_permission(ddl_session, "base:read")
    await _purge_role(ddl_session, "warehouse_keeper")
    before = await ddl_session.scalar(text("SELECT count(*) FROM permissions"))

    output = await restore_builtin.restore(ddl_session)

    assert "base:read" in output
    assert "warehouse_keeper" in output
    assert await ddl_session.scalar(text("SELECT count(*) FROM permissions")) == before, (
        "只列模式不得写库"
    )


async def test_restore_builtin_restores_and_logs_restore_action(ddl_session: AsyncSession) -> None:
    """恢复后补齐数据，并写 RESTORE 留痕（ADR-0025 §决策 3 的解除方式）。"""
    await ddl_session.execute(
        text(
            "DELETE FROM role_permissions WHERE permission_id IN "
            "(SELECT id FROM permissions WHERE code = 'base:read')"
        )
    )
    await ddl_session.execute(text("DELETE FROM permissions WHERE code = 'base:read'"))

    output = await restore_builtin.restore(ddl_session, do_permissions=True, do_roles=True)

    assert "已恢复" in output
    restored = await ddl_session.scalar(
        text("SELECT count(*) FROM permissions WHERE code = 'base:read'")
    )
    assert restored == 1
    assert set(role_codes()).issubset(
        set((await ddl_session.execute(text("SELECT code FROM roles"))).scalars().all())
    )

    logged = await ddl_session.scalar(
        text("SELECT count(*) FROM document_logs WHERE action = 'RESTORE' AND doc_no = 'base:read'")
    )
    assert logged == 1, "恢复必须留痕，否则 seed_baseline 会再次复活该行"


async def test_restore_builtin_is_noop_when_nothing_missing(ddl_session: AsyncSession) -> None:
    """数据齐全时恢复是空操作，不产生多余留痕。"""
    await restore_builtin.restore(ddl_session, do_permissions=True, do_roles=True)

    logged = await ddl_session.scalar(
        text("SELECT count(*) FROM document_logs WHERE action = 'RESTORE'")
    )
    assert logged == 0
