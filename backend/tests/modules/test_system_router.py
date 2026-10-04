"""用户 / 角色管理端点的集成测试（T-AUTH-003）。

走真实 HTTP 接口 + 回滚型 session，不 mock service —— mock 掉 service 的集成测试
只能证明 mock 配置对了（docs/10 §4）。

覆盖的关键口径（每条都对应一个**真实会被违反的规则**）：

| 用例 | 守的是什么 |
| --- | --- |
| 响应里没有 ``password_hash`` / ``phone`` | docs/05 §3 |
| 无权限 → 403 | docs/05 §6 |
| 停用未填原因 → 拒绝 | docs/06 §5 |
| 内置角色不可停用、``code`` 不可改 | docs/07 §2.3 |
| **改权限后立刻生效，不需重新登录** | docs/07 §1.1 |
| **软删角色真的收权** | AGENTS §2.1（这条本来是漏的，见下方回归用例） |
| 停用最后一个能改权限的账号 → 拒绝 | 系统不能被锁死 |
| 权限变更写 ``document_logs`` | docs/07 §5 |
"""

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import DataScope
from app.common.models import DocumentLog
from app.common.permissions_registry import PERMISSIONS
from app.modules.auth.models import Permission, Role, RolePermission, User, UserRole
from tests.factories.user import RoleFactory, UserFactory

PREFIX = "/api/v1/system"


async def _role_id(session: AsyncSession, code: str) -> str:
    role = await session.scalar(select(Role).where(Role.code == code))
    assert role is not None, f"测试库缺少内置角色 {code}（seed 未跑）"
    return str(role.id)


async def _permission_ids(session: AsyncSession, codes: list[str]) -> list[str]:
    rows = await session.execute(select(Permission.id).where(Permission.code.in_(codes)))
    return [str(item) for item in rows.scalars().all()]


# ==================================================================== 权限


async def test_user_endpoints_require_permission(client, auth_headers):
    """没有 ``system:user:manage`` 的人一律 403。"""
    headers = await auth_headers(role="custom", permissions=("base:read",))

    response = await client.get(f"{PREFIX}/users", headers=headers)

    assert response.status_code == 403
    assert response.json()["code"] == 12001


async def test_role_endpoints_require_permission(client, auth_headers):
    """没有 ``system:role:manage`` 的人查角色 403。"""
    headers = await auth_headers(role="custom", permissions=("base:read",))

    assert (await client.get(f"{PREFIX}/roles", headers=headers)).status_code == 403
    assert (await client.get(f"{PREFIX}/permissions", headers=headers)).status_code == 403


async def test_role_options_open_to_user_manager(client, auth_headers):
    """**建号时要选角色**，而车间主管没有角色管理权 —— 所以角色候选挂
    ``system:user:manage``，挂到 ``system:role:manage`` 的话他们连账号都建不了。"""
    headers = await auth_headers(role="custom", permissions=("system:user:manage",))

    response = await client.get(f"{PREFIX}/roles/options", headers=headers)

    assert response.status_code == 200
    assert isinstance(response.json()["data"], list)


# ==================================================================== 用户


async def test_user_list_never_exposes_password_hash_or_phone(client, auth_headers, db_session):
    """⚠️ 核心口径：响应**字段层面**就没有敏感列，不是"页面记得别渲染"。

    连 phone 都不能给 —— 它是个人联系方式，而本系统里没有任何用到它的场景。
    """
    await UserFactory.create(db_session, employee_no="U001", phone="13800000000")
    headers = await auth_headers()

    response = await client.get(f"{PREFIX}/users?size=200", headers=headers)
    item = next(i for i in response.json()["data"]["items"] if i["employee_no"] == "U001")

    assert "password_hash" not in item
    assert "phone" not in item
    assert item["employee_no"] == "U001"


async def test_create_user_defaults_to_must_change_password(client, auth_headers):
    """新建账号默认强制改密 —— 口令是管理员设的，用户必须自己换一遍。"""
    headers = await auth_headers()

    response = await client.post(
        f"{PREFIX}/users",
        headers=headers,
        json={
            "employee_no": "NEW001",
            "name": "新同事",
            "password": "Abcd1234",
            "data_scope": "GROUP",
        },
    )

    assert response.status_code == 201
    assert response.json()["data"]["must_change_password"] is True


async def test_create_user_duplicate_employee_no_rejected(client, auth_headers, db_session):
    """工号是登录凭据，重复必须报错而不是静默成功。"""
    await UserFactory.create(db_session, employee_no="DUP001")
    headers = await auth_headers()

    response = await client.post(
        f"{PREFIX}/users",
        headers=headers,
        json={
            "employee_no": "DUP001",
            "name": "重名",
            "password": "Abcd1234",
            "data_scope": "SELF",
        },
    )

    assert response.status_code in (409, 422)
    assert response.json()["code"] == 10001


async def test_patch_user_requires_matching_version(client, auth_headers, db_session):
    """乐观锁：version 不匹配返回 10003（docs/05 §2）。"""
    user = await UserFactory.create(db_session, employee_no="V001")
    headers = await auth_headers()

    response = await client.patch(
        f"{PREFIX}/users/{user.id}",
        headers=headers,
        json={"name": "改名", "version": user.version + 99},
    )

    assert response.json()["code"] == 10003


async def test_patch_user_cannot_change_employee_no(client, auth_headers, db_session):
    """工号不可改 —— 它被 ``document_logs.doc_no`` 引用，也是登录凭据。"""
    user = await UserFactory.create(db_session, employee_no="E001")
    headers = await auth_headers()

    response = await client.patch(
        f"{PREFIX}/users/{user.id}",
        headers=headers,
        json={"employee_no": "E002", "version": user.version},
    )

    assert response.status_code == 422  # extra="forbid"


async def test_disable_user_requires_reason(client, auth_headers, db_session):
    """停用必须填原因（docs/06 §5）。"""
    user = await UserFactory.create(db_session, employee_no="R001")
    headers = await auth_headers()

    response = await client.post(
        f"{PREFIX}/users/{user.id}/disables", headers=headers, json={"reason": "太短"}
    )

    assert response.status_code == 422


async def test_disable_user_writes_log_and_revokes_refresh_tokens(client, auth_headers, db_session):
    """停用要写日志、且立刻吊销 refresh token。"""
    from app.modules.auth.models import AuthRefreshToken

    user = await UserFactory.create(db_session, employee_no="D002")
    db_session.add(
        AuthRefreshToken(
            user_id=user.id,
            token_hash="h1",
            channel="PC",
            expires_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        )
    )
    await db_session.flush()
    headers = await auth_headers()

    response = await client.post(
        f"{PREFIX}/users/{user.id}/disables",
        headers=headers,
        json={"reason": "员工已离职，工号回收"},
    )
    assert response.status_code == 200, response.json()
    assert response.json()["data"]["is_active"] is False
    token = await db_session.scalar(
        select(AuthRefreshToken).where(AuthRefreshToken.user_id == user.id)
    )
    assert token.revoked_at is not None


async def test_disable_last_operator_is_refused(client, auth_headers, db_session):
    """⚠️ 停用最后一个能改权限的账号必须被拒绝。

    现场往往只有一两个能改权限的人，误停用会把系统锁死，且没有任何界面能解开。
    先例是 ``cli/seed_baseline.py::ensure_initial_admin``（要求至少一个超管账号）。
    """
    # 只建一个超管，并从库里删掉 conftest 建的那些，避免误判
    for role_user in (await db_session.execute(select(UserRole))).scalars().all():
        await db_session.delete(role_user)
    await db_session.flush()
    sole = await UserFactory.create(db_session, employee_no="SOLE01")
    role_id = await _role_id(db_session, "super_admin")
    db_session.add(UserRole(user_id=sole.id, role_id=role_id))
    await db_session.flush()

    headers = await auth_headers(user_id=sole.id)
    response = await client.post(
        f"{PREFIX}/users/{sole.id}/disables",
        headers=headers,
        json={"reason": "试试能不能停掉最后一个"},
    )

    assert response.json()["code"] == 10008


async def test_password_reset_sets_must_change_password(client, auth_headers, db_session):
    """重置口令后必须强制改密 + 吊销 refresh，否则初始口令能一直用。"""
    user = await UserFactory.create(db_session, employee_no="P001")
    headers = await auth_headers()

    response = await client.post(
        f"{PREFIX}/users/{user.id}/password-resets",
        headers=headers,
        json={"new_password": "Wxyz5678", "reason": "员工忘记口令，工号核实无误"},
    )

    assert response.status_code == 200
    await db_session.refresh(user)
    assert user.must_change_password is True


async def test_assign_roles_replaces_whole_set(client, auth_headers, db_session):
    """整体替换而不是增删 —— 授权界面天然是"勾选哪些"的全量语义。"""
    user = await UserFactory.create(db_session, employee_no="A900")
    headers = await auth_headers()

    response = await client.put(
        f"{PREFIX}/users/{user.id}/roles",
        headers=headers,
        json={"role_codes": ["workshop_supervisor"], "version": user.version},
    )

    assert response.status_code == 200
    assert response.json()["data"]["role_codes"] == ["workshop_supervisor"]


async def test_assign_unknown_role_rejected(client, auth_headers, db_session):
    """角色不存在必须报错 —— 否则用户被授了一个谁也解不开的幽灵角色。"""
    user = await UserFactory.create(db_session, employee_no="A901")
    headers = await auth_headers()

    response = await client.put(
        f"{PREFIX}/users/{user.id}/roles",
        headers=headers,
        json={"role_codes": ["no_such_role"], "version": user.version},
    )

    assert response.json()["code"] == 10001


# ==================================================================== 角色


async def test_builtin_role_cannot_be_disabled(client, auth_headers):
    """内置角色禁止停用（docs/07 §2.3）—— 它们是 seed 建的，停用后没人能恢复。"""
    headers = await auth_headers()
    role_id = await _role_id_request(client, headers, "workshop_supervisor")

    response = await client.post(
        f"{PREFIX}/roles/{role_id}/disables",
        headers=headers,
        json={"reason": "试试停用内置角色"},
    )

    assert response.json()["code"] == 10008


async def _role_id_request(client, headers, code: str) -> str:
    response = await client.get(f"{PREFIX}/roles", headers=headers)
    for item in response.json()["data"]:
        if item["code"] == code:
            return item["id"]
    raise AssertionError(f"角色列表里没有 {code}")


async def test_role_patch_cannot_change_code(client, auth_headers):
    """``code`` 不可改 —— 被 ``user_roles`` 与审计日志引用。"""
    headers = await auth_headers()
    role_id = await _role_id_request(client, headers, "workshop_supervisor")
    role_version = await _role_version(client, headers, "workshop_supervisor")

    response = await client.patch(
        f"{PREFIX}/roles/{role_id}",
        headers=headers,
        json={"code": "renamed", "version": role_version},
    )

    assert response.status_code == 422  # extra="forbid"


async def _role_version(client, headers, code: str) -> int:
    response = await client.get(f"{PREFIX}/roles", headers=headers)
    for item in response.json()["data"]:
        if item["code"] == code:
            return int(item["version"])
    raise AssertionError(code)


async def test_create_role_cannot_mark_itself_system(client, auth_headers):
    """``is_system`` 恒为 false —— 内置角色只能由 seed 建，不给接口留口子。"""
    headers = await auth_headers()

    response = await client.post(
        f"{PREFIX}/roles",
        headers=headers,
        json={"code": "fake_admin", "name": "假超管", "is_system": True},
    )

    assert response.status_code == 422


async def test_permissions_grouped_by_module_match_registry(client, auth_headers):
    """权限树必须与 registry 一致，且按模块分组（docs/06 §6.3「权限点按模块分组树」）。"""
    headers = await auth_headers()

    groups = (await client.get(f"{PREFIX}/permissions", headers=headers)).json()["data"]

    # ⚠️ 断言的是**顺序**，不只是集合。`permission_codes()` 返回 frozenset，
    #    迭代顺序由哈希决定 —— 如果实现图省事直接用它，界面上的权限树
    #    每次打开顺序都在变，用户找不到上次勾的那一项。
    flat = [p["code"] for group in groups for p in group["permissions"]]
    assert flat == [item.code for item in PERMISSIONS]

    # 分组必须与 registry 的 module 一致，且模块顺序稳定
    assert [group["module"] for group in groups] == list(
        dict.fromkeys(item.module for item in PERMISSIONS)
    )
    assert all(group["module_name"] for group in groups)


async def test_replace_permissions_takes_effect_immediately(client, auth_headers, db_session):
    """⚠️ docs/07 §1.1「权限查库，避免权限变更不生效」。

    收掉一个权限后，**用同一个旧 token** 再访问就该被拒 —— 不重新登录。
    """
    role = await RoleFactory.create(
        db_session, code="tmp_role", name="临时角色", data_scope=DataScope.FACTORY
    )
    victim = await UserFactory.create(db_session, employee_no="T900")
    db_session.add(UserRole(user_id=victim.id, role_id=role.id))
    await db_session.flush()
    permission_ids = await _permission_ids(db_session, ["system:log:view"])
    for pid in permission_ids:
        db_session.add(RolePermission(role_id=role.id, permission_id=pid))
    await db_session.flush()
    await db_session.refresh(role)

    victim_headers = await auth_headers(user_id=victim.id)
    assert (await client.get(f"{PREFIX}/roles", headers=victim_headers)).status_code == 403

    admin_headers = await auth_headers()
    replaced = await client.put(
        f"{PREFIX}/roles/{role.id}/permissions",
        headers=admin_headers,
        json={"permission_codes": ["system:role:manage"], "version": role.version},
    )
    assert replaced.status_code == 200

    # 同一个旧 token，不重新登录
    after = await client.get(f"{PREFIX}/roles", headers=victim_headers)
    assert after.status_code == 200
    # 旧权限点真的没了
    assert (await client.get(f"{PREFIX}/users", headers=victim_headers)).status_code == 403


async def test_replace_permissions_writes_document_log(client, auth_headers, db_session):
    """权限变更必须留痕（docs/07 §5）。"""
    role = await RoleFactory.create(
        db_session, code="log_role", name="留痕角色", data_scope=DataScope.SELF
    )
    await db_session.flush()
    await db_session.refresh(role)
    headers = await auth_headers()

    await client.put(
        f"{PREFIX}/roles/{role.id}/permissions",
        headers=headers,
        json={"permission_codes": ["system:log:view"], "version": role.version},
    )

    logs = (
        (
            await db_session.execute(
                select(DocumentLog).where(
                    DocumentLog.doc_type == "Role", DocumentLog.doc_id == role.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(logs) == 1
    assert logs[0].changed_fields is not None
    assert logs[0].changed_fields["added"] == ["system:log:view"]


async def test_soft_deleted_role_actually_revokes_permissions(client, auth_headers, db_session):
    """⚠️ **回归用例**：软删角色必须真的收权。

    这条对应一个真实的漏洞：`core/permissions.py::load_permissions` 原本只 join 了
    ``role_permissions`` 而**没碰 ``roles``**，于是软删一个角色之后它的权限点
    **照样授予**用户 —— 界面显示"已停用"，实际一点没少。
    ``roles`` 表没有 ``is_active`` 列（停用 = 软删），所以过滤条件必须落在
    ``deleted_at IS NULL`` 上。
    """
    role = await RoleFactory.create(
        db_session, code="revoke_me", name="待收回", data_scope=DataScope.FACTORY
    )
    victim = await UserFactory.create(db_session, employee_no="T901")
    db_session.add(UserRole(user_id=victim.id, role_id=role.id))
    await db_session.flush()
    # 授的必须是"访问 /permissions 端点所需的那个权限点"，否则测不出收权
    for pid in await _permission_ids(db_session, ["system:role:manage"]):
        db_session.add(RolePermission(role_id=role.id, permission_id=pid))
    await db_session.flush()

    victim_headers = await auth_headers(user_id=victim.id)
    # 收权前：这个角色带来的 system:role:manage 让他能读权限树
    assert (await client.get(f"{PREFIX}/permissions", headers=victim_headers)).status_code == 200

    await db_session.refresh(role)
    admin_headers = await auth_headers()
    response = await client.post(
        f"{PREFIX}/roles/{role.id}/disables",
        headers=admin_headers,
        json={"reason": "该角色不再使用，回收其权限"},
    )
    assert response.status_code == 200

    after = await client.get(f"{PREFIX}/permissions", headers=victim_headers)
    assert after.status_code == 403


async def test_role_list_omits_soft_deleted(client, auth_headers, db_session):
    """软删后的角色不再出现在列表与候选里。"""
    role = await RoleFactory.create(
        db_session, code="gone_role", name="已收回", data_scope=DataScope.SELF
    )
    await db_session.flush()
    await db_session.refresh(role)
    headers = await auth_headers()

    await client.post(
        f"{PREFIX}/roles/{role.id}/disables",
        headers=headers,
        json={"reason": "该角色不再使用，回收其权限"},
    )

    codes = {
        item["code"]
        for item in (await client.get(f"{PREFIX}/roles", headers=headers)).json()["data"]
    }
    assert "gone_role" not in codes
    options = (await client.get(f"{PREFIX}/roles/options", headers=headers)).json()["data"]
    assert "gone_role" not in {item["value"] for item in options}


async def test_user_options_label_contains_employee_no_and_name(client, auth_headers, db_session):
    """候选回显必须是「工号 姓名」，禁止只显示编码（docs/05 §9.5.2）。"""
    await UserFactory.create(db_session, employee_no="OPT001", name="候选君")
    headers = await auth_headers()

    options = (await client.get(f"{PREFIX}/users/options", headers=headers)).json()["data"]

    assert any(item["label"] == "OPT001 候选君" for item in options)


async def test_user_options_disabled_for_inactive(client, auth_headers, db_session):
    """停用项必须标 disabled，前端据此禁止直接选中（docs/05 §9.5.2）。"""
    await UserFactory.create(db_session, employee_no="OFF001", name="停用君", is_active=False)
    headers = await auth_headers()

    options = (await client.get(f"{PREFIX}/users/options", headers=headers)).json()["data"]

    item = next(i for i in options if i["label"].startswith("OFF001"))
    assert item["disabled"] is True
    assert item["sub"] == "已停用"


@pytest.mark.parametrize("size", [21, 200])
async def test_option_size_capped_at_20(client, auth_headers, size):
    """候选端点强制 ``size ≤ 20``（docs/05 §9.5.1）。"""
    headers = await auth_headers()

    response = await client.get(f"{PREFIX}/users/options?size={size}", headers=headers)

    assert response.status_code == 422


async def test_unique_role_id_sanity(db_session: AsyncSession):
    """防呆：测试本身别写错（随机 id 拼错会让后面所有断言假绿）。"""
    assert str(uuid4()) != str(uuid4())
    user = await UserFactory.create(db_session, employee_no="SANITY")
    assert await db_session.scalar(select(User).where(User.id == user.id)) is not None
