"""INV-P0-4：权限点三处一致性守卫。

docs/07 §2.2 明确「三处不一致视为**闸门 1 失败**」：

    1. ``docs/07 §2.2`` 表格          —— 规范（契约）
    2. ``app/common/permissions_registry.py`` —— 后端代码（单一来源）
    3. ``permissions`` 表 seed        —— 数据库（由 1 派生）
    4. ``packages/shared/enums/permissions.ts`` —— 前端常量（T-WEB-001 落地）

本文件逐对双向断言（差集必须为空）。任何一处漂移都会让闸门 1 失败。

末尾另有「一致性 5：状态枚举映射」—— `packages/shared/src/enums/status.ts` 的键
集合必须与后端 `DocumentStatus` 完全相等（docs/06 §1「枚举值来自后端，前端只做
中文映射」）。放在本文件而不是新建一个，是因为这里已经是"跨语言契约漂移"的
收口处，两处漂移的修法与排查手段相同。

⚠️ 解析规范表格的三个坑（都踩过）：
    1. 权限点 code 里**含下划线**（``base:rate_template:manage``）——
       用 ``[a-z]+`` 会漏掉它
    2. 表格里有**两个已作废的码**（``self:piecework:count``、``bundling:void_code``），
       它们只出现在"作废说明"文字里，**不是权限点**，必须显式排除
    3. 「领域专用动作」那一行里的 ``rate:manage`` / ``code:void`` 等是**动作词**，
       不是权限点（它们只出现在带反引号的动作说明中，没有模块前缀）
"""

import re
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import DataScope
from app.common.permissions_registry import (
    PERMISSIONS,
    ROLES,
    SELF_PERMISSION_CODES,
    permission_codes,
    resolve_role_permissions,
    role_codes,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DOC_07 = REPO_ROOT / "docs" / "07-认证与权限规范.md"

#: docs/07 §2.2 明确"已作废 / 不存在"的码，**不得**出现在权限点表里
DEPRECATED_CODES: frozenset[str] = frozenset(
    {
        "self:piecework:count",  # REV-2026-10：员工端不开放计件写入
        "bundling:void_code",  # 领域动作必须写成两级，现行 bundling:code:void
    }
)

#: docs/07 §2.2 里的动作词（不是权限点，无模块前缀）
ACTION_WORDS: frozenset[str] = frozenset({"rate:manage", "order:create", "cost:view", "code:void"})

#: 表格首列 → 权限点模块前缀
_MODULE_BY_LABEL: dict[str, str] = {
    "基础资料": "base",
    "裁剪": "cutting",
    "打菲": "bundling",
    "计件": "piecework",
    "工资": "payroll",
    "采购": "purchase",
    "库存": "stock",
    "销售": "sales",
    "往来": "finance",
    "凭证": "finance",
    "系统": "system",
}

#: 权限点 code 里**可能含下划线**（base:rate_template:manage），正则必须允许
_CODE_PATTERN = re.compile(r"`([a-z_]+(?::[a-z_]+)+)`")


def _parse_doc_permission_codes() -> set[str]:
    """从 docs/07 §2.2 表格抽出全部权限点。"""
    text_ = DOC_07.read_text(encoding="utf-8")
    section = text_.split("### 2.2 权限点总表")[1].split("**权限点增补流程**")[0]

    codes: set[str] = set()
    for line in section.split("\n"):
        if not line.strip().startswith("|"):
            continue
        first_cell = line.strip().strip("|").split("|")[0].strip()
        module = _MODULE_BY_LABEL.get(first_cell)
        if module is None:
            continue
        codes.update(code for code in _CODE_PATTERN.findall(line) if code.startswith(f"{module}:"))

    # 员工自助段（在 REV 作废说明**之前**，否则会误收 self:piecework:count）
    self_part = section.split("**员工自助**")[1].split("> **REV-2026-10**")[0]
    codes.update(re.findall(r"`(self:[a-z_]+(?::[a-z_]+)*)`", self_part))
    return codes


def _parse_doc_role_codes() -> set[str]:
    """从 docs/07 §2.3 抽出内置角色 code。"""
    section = DOC_07.read_text(encoding="utf-8").split("### 2.3 内置角色")[1]
    return set(re.findall(r"^\| `([a-z_]+)` \|", section, re.MULTILINE))


DOC_PERMISSION_CODES = _parse_doc_permission_codes()
DOC_ROLE_CODES = _parse_doc_role_codes()


# ---------------------------------------------------------------------------
# 解析器自身的行为（防止解析器坏了导致后面的断言空转）
# ---------------------------------------------------------------------------


def test_doc_parser_finds_a_plausible_number_of_codes() -> None:
    """守卫解析器：数量明显偏少说明表格结构变了，解析器需要跟着改。"""
    assert len(DOC_PERMISSION_CODES) > 100, (
        f"只解析到 {len(DOC_PERMISSION_CODES)} 个权限点，解析器可能失效"
    )
    assert len(DOC_ROLE_CODES) == 10, f"内置角色应为 10 个，解析到 {sorted(DOC_ROLE_CODES)}"


def test_doc_parser_excludes_deprecated_codes() -> None:
    """两个已作废的码不得被当成权限点收进来。"""
    assert not (DOC_PERMISSION_CODES & DEPRECATED_CODES)
    assert not (permission_codes() & DEPRECATED_CODES)


def test_doc_parser_excludes_action_words() -> None:
    """「领域专用动作」行的动作词不是权限点。"""
    assert not (DOC_PERMISSION_CODES & ACTION_WORDS)


# ---------------------------------------------------------------------------
# 一致性 1：registry == docs/07 §2.2
# ---------------------------------------------------------------------------


def test_registry_matches_doc_exactly() -> None:
    """registry 的权限点集合必须与 docs/07 §2.2 **完全相等**（双向差集为空）。"""
    registry = permission_codes()
    missing_in_registry = DOC_PERMISSION_CODES - registry
    extra_in_registry = registry - DOC_PERMISSION_CODES
    assert not missing_in_registry, f"docs/07 §2.2 有但 registry 缺：{sorted(missing_in_registry)}"
    assert not extra_in_registry, f"registry 有但 docs/07 §2.2 未登记：{sorted(extra_in_registry)}"


def test_registry_matches_doc_roles_exactly() -> None:
    """内置角色集合必须与 docs/07 §2.3 完全相等。"""
    assert role_codes() == DOC_ROLE_CODES


def test_underscore_codes_are_present() -> None:
    """回归守卫：含下划线的码曾因正则 `[a-z]+` 被漏掉。

    ``base:rate_template:manage`` 被 modules/01 §6 引用并声称"07 §2.2 已登记"，
    但用错误的正则解析时会整条消失。
    """
    assert "base:rate_template:manage" in permission_codes()


def test_permission_names_and_sort_are_populated() -> None:
    """每个权限点都要有中文名与排序，否则界面会出现空白行。"""
    for item in PERMISSIONS:
        assert item.name.strip(), f"{item.code} 缺中文名"
        assert item.module.strip(), f"{item.code} 缺模块"
        assert item.action.strip(), f"{item.code} 缺动作词"
        assert item.sort_order > 0, f"{item.code} 排序值非法"
    orders = [item.sort_order for item in PERMISSIONS]
    assert len(set(orders)) == len(orders), "sort_order 有重复"


def test_self_permissions_are_the_documented_four() -> None:
    """员工自助固定 4 个（docs/07 §2.2）。"""
    assert {
        "self:profile:read",
        "self:piecework:read",
        "self:piecework:bind",
        "self:payroll:read",
    } == SELF_PERMISSION_CODES


# ---------------------------------------------------------------------------
# 一致性 2：角色引用的权限点都存在；数据范围与 docs/07 §2.3 一致
# ---------------------------------------------------------------------------


def test_every_role_permission_exists() -> None:
    """角色不能引用未登记的权限点（否则运行时永远匹配不上）。"""
    known = permission_codes()
    unknown = {
        (role.code, code)
        for role in ROLES
        for code in resolve_role_permissions(role)
        if code not in known
    }
    assert not unknown, f"角色引用了不存在的权限点：{sorted(unknown)}"


def test_super_admin_has_all_permissions() -> None:
    """超管必须拿到全部权限点（docs/07 §2.3「所有权限」）。"""
    super_admin = next(role for role in ROLES if role.code == "super_admin")
    assert set(resolve_role_permissions(super_admin)) == permission_codes()


def test_warehouse_keeper_has_no_cost_permission() -> None:
    """仓管**不可见成本**（docs/07 §2.3 + §5「成本可见性」）。"""
    keeper = next(role for role in ROLES if role.code == "warehouse_keeper")
    assert "stock:cost:view" not in resolve_role_permissions(keeper)


def test_employee_role_has_no_admin_permissions() -> None:
    """员工角色只走 ``/api/v1/self/**``，不持有任何管理权限。"""
    employee = next(role for role in ROLES if role.code == "employee")
    assert resolve_role_permissions(employee) == ()


@pytest.mark.parametrize(
    ("role_code", "expected_scope"),
    [
        ("super_admin", DataScope.FACTORY),
        ("factory_manager", DataScope.FACTORY),
        ("workshop_supervisor", DataScope.WORKSHOP),
        ("line_leader", DataScope.GROUP),
        ("warehouse_keeper", DataScope.FACTORY),
        ("accountant", DataScope.FACTORY),
        ("purchaser", DataScope.FACTORY),
        ("merchandiser", DataScope.SELF),
        ("piecework_settler", DataScope.FACTORY),
        ("employee", DataScope.SELF),
    ],
)
def test_role_data_scopes_match_doc(role_code: str, expected_scope: DataScope) -> None:
    """各角色数据范围必须与 docs/07 §2.3 表格一致。"""
    role = next(item for item in ROLES if item.code == role_code)
    assert role.data_scope == expected_scope


# ---------------------------------------------------------------------------
# 一致性 3：数据库 seed == registry
# ---------------------------------------------------------------------------


async def test_db_permissions_match_registry(db_session: AsyncSession) -> None:
    """``permissions`` 表内容必须与 registry 完全一致（不多不少不少）。"""
    db_codes = set((await db_session.execute(text("SELECT code FROM permissions"))).scalars().all())
    assert db_codes == permission_codes(), (
        f"数据库与 registry 不一致：缺 {sorted(permission_codes() - db_codes)[:5]}，"
        f"多 {sorted(db_codes - permission_codes())[:5]}"
    )


async def test_db_roles_match_registry(db_session: AsyncSession) -> None:
    """``roles`` 表必须是 10 个内置角色，且 ``is_system=true``。"""
    rows = (await db_session.execute(text("SELECT code, data_scope, is_system FROM roles"))).all()
    assert {row[0] for row in rows} == role_codes()
    assert all(row[2] is True for row in rows), "内置角色必须标记 is_system=true"


async def test_db_role_permission_bindings_match_registry(db_session: AsyncSession) -> None:
    """角色→权限绑定必须与 registry 的解析结果逐角色一致。"""
    rows = (
        await db_session.execute(
            text(
                "SELECT r.code, p.code FROM role_permissions rp "
                "JOIN roles r ON r.id = rp.role_id "
                "JOIN permissions p ON p.id = rp.permission_id"
            )
        )
    ).all()
    actual: dict[str, set[str]] = {}
    for role_code, perm_code in rows:
        actual.setdefault(role_code, set()).add(perm_code)

    for role in ROLES:
        expected = set(resolve_role_permissions(role))
        assert actual.get(role.code, set()) == expected, (
            f"角色 {role.code} 的绑定与 registry 不一致："
            f"缺 {sorted(expected - actual.get(role.code, set()))[:5]}，"
            f"多 {sorted(actual.get(role.code, set()) - expected)[:5]}"
        )


async def test_seed_is_idempotent(db_session: AsyncSession) -> None:
    """TC-A01：重复执行 seed 后行数与内容**零变化**（docs/07 §6 幂等要求）。"""
    from app.cli.seed_baseline import seed_permissions, seed_role_permissions, seed_roles

    before = {
        "permissions": await db_session.scalar(text("SELECT count(*) FROM permissions")),
        "roles": await db_session.scalar(text("SELECT count(*) FROM roles")),
        "role_permissions": await db_session.scalar(text("SELECT count(*) FROM role_permissions")),
    }
    for _ in range(2):
        await seed_permissions(db_session)
        await seed_roles(db_session)
        await seed_role_permissions(db_session)

    after = {
        "permissions": await db_session.scalar(text("SELECT count(*) FROM permissions")),
        "roles": await db_session.scalar(text("SELECT count(*) FROM roles")),
        "role_permissions": await db_session.scalar(text("SELECT count(*) FROM role_permissions")),
    }
    assert before == after, f"seed 不幂等：{before} → {after}"


# ---------------------------------------------------------------------------
# 一致性 4：前端常量（T-WEB-001 落地后才存在）
# ---------------------------------------------------------------------------

FRONTEND_PERMISSIONS = (
    REPO_ROOT / "frontend" / "packages" / "shared" / "src" / "enums" / "permissions.ts"
)


@pytest.mark.skipif(
    not FRONTEND_PERMISSIONS.exists(),
    reason="前端权限点常量待 T-WEB-001 落地（packages/shared/enums/permissions.ts）",
)
def test_frontend_constants_match_registry() -> None:
    """前端权限点常量必须与 registry 一致（docs/07 §2.2 的第三处）。"""
    content = FRONTEND_PERMISSIONS.read_text(encoding="utf-8")
    frontend_codes = set(re.findall(r"['\"]([a-z_]+:[a-z_:]+)['\"]", content))
    assert frontend_codes == permission_codes(), (
        f"前端常量与 registry 不一致：缺 {sorted(permission_codes() - frontend_codes)[:5]}，"
        f"多 {sorted(frontend_codes - permission_codes())[:5]}"
    )


# ---------------------------------------------------------------------------
# 一致性 5：状态枚举映射（docs/06 §1，2026-10-03 / T-WEB-001 落地后生效）
# ---------------------------------------------------------------------------

FRONTEND_STATUS = REPO_ROOT / "frontend" / "packages" / "shared" / "src" / "enums" / "status.ts"


@pytest.mark.skipif(
    not FRONTEND_STATUS.exists(),
    reason="前端状态映射待 T-WEB-001 落地（packages/shared/src/enums/status.ts）",
)
def test_frontend_status_keys_match_backend_enum() -> None:
    """前端状态映射的键集合必须与后端 ``DocumentStatus`` 完全相等。

    ⚠️ 后端加一档而前端没跟上时，界面会显示**空白标签**（而不是报错）——
    用户以为系统坏了，而后端返回的数据完全正确。所以必须双向断言。
    """
    from app.common.enums import DocumentStatus

    content = FRONTEND_STATUS.read_text(encoding="utf-8")
    keys = set(re.findall(r"^\s{2}([A-Z_]+):\s*\{", content, re.M))
    backend = {member.value for member in DocumentStatus}
    assert keys == backend, (
        f"状态映射与后端枚举不一致：缺 {sorted(backend - keys)}，多 {sorted(keys - backend)}"
    )
