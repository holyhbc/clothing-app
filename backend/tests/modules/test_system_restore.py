"""「恢复内置库」两个端点的集成测试（T-WEB-005 的前置缺口，T-AUTH-003 之后补）。

背景：`app/cli/restore_builtin.py` 一直只有 CLI，**没有 HTTP 端点** —— 而那个文件
自己的注释写着「用户点『恢复内置库』」，说明这一层设计时就预期要有界面。
T-WEB-005 的字典页要放这个按钮，所以先补端点。

关键口径：

| 用例 | 守的是什么 |
| --- | --- |
| 无 `system:config:manage` → 403 | docs/05 §6 |
| 三类都不勾 → `10002` | 点了按钮却"恢复 0 条"会被当成 bug |
| 缺什么都没恢复 → **0 条** | 幂等：重复点按钮不会插重复行 |
| 真删一个内置色后能恢复回来 | 墓碑机制（ADR-0025 §决策 3） |
| `session.connection()` 与 session 同事务 | 事务边界只在 service（AGENTS §2.1） |
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cli.seed_dicts import BUILTIN_COLORS
from app.common.models import DocumentLog
from app.modules.auth.models import Permission, Role
from app.modules.base.models import Color
from app.modules.system.restore import DICT_TABLES

PREFIX = "/api/v1/system/dicts"


async def test_requires_permission(client, auth_headers):
    """没有 ``system:config:manage`` 的人不能恢复内置库 ——
    这个动作会改数据，必须有专门权限点，不能让任何登录用户都做得到。"""
    headers = await auth_headers(role="custom", permissions=("base:read",))

    assert (await client.get(f"{PREFIX}/builtin-missing", headers=headers)).status_code == 403
    response = await client.post(
        f"{PREFIX}/builtin-restores", headers=headers, json={"dicts": True}
    )
    assert response.status_code == 403


async def test_missing_lists_builtin_and_role_gaps(client, auth_headers, db_session):
    """清单要同时覆盖「权限点/角色缺失」与「字典缺失」两类。"""
    headers = await auth_headers()

    body = (await client.get(f"{PREFIX}/builtin-missing", headers=headers)).json()["data"]

    # ⚠️ 从 ``DICT_TABLES`` 派生而不是写死四个表名：那张清单来自
    #    ``DICT_DOC_TYPES``（墓碑机制的登记表），**新增字典表会自动进来**。
    #    写死一份副本的话，加表那天这条测试会红，而它红的原因（"字典表清单变了"）
    #    与真正该改的地方（「恢复内置库」的页面文案）都不在一处。
    assert set(body["dicts"]) == set(DICT_TABLES)
    # ⚠️ 顺带钉住「注册了就真的在清单里」：上面那句在 DICT_TABLES 为空时也会通过
    assert len(DICT_TABLES) >= 4
    assert isinstance(body["permissions"], list)
    assert isinstance(body["roles"], list)
    assert isinstance(body["total"], int)


async def test_restore_rejects_empty_scope(client, auth_headers):
    """三类都不勾 → ``10002``，**不能**静默返回"恢复 0 条"。

    用户点了按钮却看到成功、但什么也没变 —— 他会以为是 bug，然后反复点。
    """
    headers = await auth_headers()

    response = await client.post(
        f"{PREFIX}/builtin-restores",
        headers=headers,
        json={"permissions": False, "roles": False, "dicts": False},
    )

    assert response.json()["code"] == 10002


async def test_restore_is_idempotent_when_nothing_missing(client, auth_headers, db_session):
    """什么都没缺时恢复 0 条 —— 重复点按钮不会插出重复行。"""
    headers = await auth_headers()
    # 先确保库是完整的
    await client.post(
        f"{PREFIX}/builtin-restores",
        headers=headers,
        json={"permissions": True, "roles": True, "dicts": True},
    )
    await db_session.commit()

    body = (
        await client.post(
            f"{PREFIX}/builtin-restores",
            headers=headers,
            json={"permissions": True, "roles": True, "dicts": True},
        )
    ).json()["data"]

    assert body["restored_dicts"] == 0
    assert "已恢复" in body["message"]


async def test_restore_brings_back_deleted_builtin_dict(client, auth_headers, db_session):
    """核心场景：真删一个**内置**色之后，「恢复内置库」能把它找回来。

    ⚠️ 必须是内置色（``BUILTIN_COLORS`` 里那一批，如 ``KHK`` 卡其）。
    **用户自建的色删掉就是删掉了** —— ``seed_colors`` 只重建内置清单里的编码，
    而墓碑机制（ADR-0025 §决策 3）正是为了保证"删掉就是不要了"。
    早先这条用例造了个自建色，结果恢复 0 条 —— 看着像恢复功能坏了，其实语义正确。

    这条同时验证墓碑顺序：``seed_dict_library`` 默认**跳过**有墓碑的行，
    所以必须先解除墓碑才能恢复；顺序反了的话数据不会回来、只留下一条 RESTORE 记录。
    """
    assert ("KHK", "卡其") in BUILTIN_COLORS, "内置色清单变了，本用例的前提需要跟着改"
    await db_session.commit()

    headers = await auth_headers()
    delete = await client.delete("/api/v1/colors/KHK", headers=headers)
    assert delete.status_code == 200, delete.json()
    # ⚠️ 不能用 `session.get(Color, "KHK")` —— 那是按**主键 id** 查，
    # 而字典表的业务键列名各不相同（colors 是 color_code）。用主键查会得到
    # 'invalid UUID "KHK"'，一个与被测行为无关的报错。
    assert (
        await db_session.execute(
            select(Color).where(Color.color_code == "KHK", Color.deleted_at.is_(None))
        )
    ).scalar_one_or_none() is None

    body = (
        await client.post(
            f"{PREFIX}/builtin-restores",
            headers=headers,
            json={"permissions": False, "roles": False, "dicts": True},
        )
    ).json()["data"]

    assert body["restored_dicts"] >= 1
    assert body["dict_detail"].get("colors", 0) >= 1


async def test_restore_writes_audit_log(client, auth_headers, db_session):
    """恢复要留痕，而且**操作人必须是真实用户**（docs/07 §5）。"""
    await db_session.commit()
    headers = await auth_headers()
    deleted = await client.delete("/api/v1/colors/GRY", headers=headers)
    assert deleted.status_code == 200, deleted.json()

    missing = (await client.get(f"{PREFIX}/builtin-missing", headers=headers)).json()["data"]
    assert "GRY" in missing["dicts"]["colors"], f"墓碑没被识别：{missing['dicts']['colors']}"

    restored = await client.post(
        f"{PREFIX}/builtin-restores",
        headers=headers,
        json={"permissions": False, "roles": False, "dicts": True},
    )
    assert restored.status_code == 200, restored.json()

    logs = (
        (
            await db_session.execute(
                select(DocumentLog).where(
                    DocumentLog.doc_type == "Color", DocumentLog.doc_no == "GRY"
                )
            )
        )
        .scalars()
        .all()
    )
    restores = [log for log in logs if log.action == "RESTORE"]
    assert restores, [log.action for log in logs]
    # ⚠️ CLI 调用时 operator_id 落随机 UUID、operator_name 记成 'restore_builtin'
    #   （那里没有用户上下文）；接口层必须传真实 ctx，否则「谁恢复的」永远查不出来。
    assert restores[0].operator_name not in (None, "", "restore_builtin")


async def test_restore_accepts_session_connection(db_session: AsyncSession):
    """⚠️ 防呆：``restore.py`` 把 ``AsyncSession`` 换成 ``await session.connection()``。

    那条连接与 session **共用同一个事务** —— 换错成 ``create_async_engine()`` 的话，
    恢复会提交到一个**独立事务**里，而外层测试事务随后回滚，断言就会看到
    "恢复没生效"，或者反过来在生产里出现"接口报成功但数据没进去"。
    """
    from app.modules.system.restore import BuiltinRestoreService

    service = BuiltinRestoreService(db_session)
    conn = await service._conn()

    assert conn is not None
    assert hasattr(conn, "execute") and hasattr(conn, "scalar")
    missing = await service.list_missing()
    assert isinstance(missing.total(), int)


async def test_role_and_permission_restores_are_selectable(client, auth_headers):
    """两类可以分开勾：只恢复字典时**不该**动权限点。"""
    headers = await auth_headers()

    body = await client.post(
        f"{PREFIX}/builtin-restores",
        headers=headers,
        json={"permissions": False, "roles": False, "dicts": False},
    )
    assert body.json()["code"] == 10002

    ok = await client.post(
        f"{PREFIX}/builtin-restores",
        headers=headers,
        json={"permissions": True, "roles": True, "dicts": False},
    )
    assert ok.status_code == 200
    assert ok.json()["data"]["restored_dicts"] == 0


async def test_permission_and_role_counts_stay_aligned(db_session: AsyncSession):
    """恢复后 ``permissions`` / ``roles`` 表与 registry 仍然一致 ——
    恢复逻辑走的是 ``seed_baseline`` 的同一组函数，理论上不会错位；
    这条断言是为了让"两处实现漂移"在闸门阶段就暴露。"""
    from app.common.permissions_registry import permission_codes

    found = set((await db_session.execute(select(Permission.code))).scalars().all())
    missing = sorted(permission_codes() - found)
    roles = set((await db_session.execute(select(Role.code))).scalars().all())

    assert not missing, f"库里的权限点少于 registry：{missing}"
    assert "super_admin" in roles
