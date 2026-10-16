"""``GET /piecework/operators`` + ``piecework:count`` 角色授权（T-PIECE-006）。

======================================  ==========================================
TC-PW-OP01                            无 ``piecework:read`` → ``12001``
TC-PW-OP02                            候选行 shape：``value`` 是 UUID、``label`` =「工号 姓名」
TC-PW-OP03                            ★ WORKSHOP 范围：只见本车间
TC-PW-OP04                            ★ GROUP 范围：只见本组
TC-PW-OP05                            关键字命中工号**或**姓名
TC-PW-OP06                            停用员工保留但 ``disabled`` + ``sub=已停用``
TC-PW-OP07                            ★ 传别的 ``workshop_id`` **不放大**范围
TC-PW-OP08                            ★ 角色 seed：四个工位机角色有 ``piecework:count``
TC-PW-OP09                            ★ 组长真能计件（seed 授权端到端）
======================================  ==========================================

## 为什么单开一个文件

这个端点解决的是「工位机选不出操作员」—— 唯一的候选来源 ``/system/users/options``
要 ``system:user:manage``，组长不该有它。所以本文件的**重点不是**返回格式，而是
「换个 token 真的少了几个人」：数据范围写没写、写错了从不报错，只表现为**多列了
别人的工人**。而 TC-PW-OP08/09 与之相对 —— 只加数据范围却忘了给角色授权，
组长点进页面看到的是**空下拉**，那也是一种「静默失败」。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.common.enums import DataScope
from tests.factories.user import UserFactory
from tests.modules.pw_query_helpers import READ_PERMS, hdr

OPERATORS = "/api/v1/piecework/operators"
COUNT = "/api/v1/piecework/count"


async def _names(client: Any, headers: dict[str, str], q: str) -> list[dict[str, Any]]:
    response = await client.get(OPERATORS, params={"q": q}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def test_tc_pw_op01_requires_read(client, db_session, auth_headers, piecework_world) -> None:
    headers = await hdr(db_session, auth_headers, piecework_world, ())
    response = await client.get(OPERATORS, headers=headers)
    assert response.status_code == 403
    assert response.json()["code"] == 12001


async def test_tc_pw_op02_row_shape(client, db_session, auth_headers, piecework_world) -> None:
    user = await UserFactory.create(
        db_session,
        employee_no="OPA001",
        name="王小明",
        workshop_id=piecework_world["workshop_id"],
    )
    headers = await hdr(db_session, auth_headers, piecework_world, READ_PERMS)
    rows = await _names(client, headers, "OPA001")
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["value"] == str(user.id)
    assert row["label"] == "OPA001 王小明"
    assert row["disabled"] is False
    assert row["sub"] is None


async def test_tc_pw_op03_workshop_scope(
    client, db_session, auth_headers, piecework_world, other_piecework_world
) -> None:
    mine = await UserFactory.create(
        db_session,
        employee_no="OPM001",
        name="本车间工人",
        workshop_id=piecework_world["workshop_id"],
    )
    theirs = await UserFactory.create(
        db_session,
        employee_no="OPX001",
        name="外车间工人",
        workshop_id=other_piecework_world["workshop_id"],
    )
    headers = await hdr(
        db_session,
        auth_headers,
        piecework_world,
        READ_PERMS,
        data_scope=DataScope.WORKSHOP,
        workshop_id=piecework_world["workshop_id"],
    )
    assert [r["value"] for r in await _names(client, headers, "OPM001")] == [str(mine.id)]
    # ⚠️ 关键断言：本车间的令牌**搜不到**外车间的人（搜到了就是越权可见）
    assert await _names(client, headers, "OPX001") == []
    assert theirs.id != mine.id


async def test_tc_pw_op04_group_scope(client, db_session, auth_headers, piecework_world) -> None:
    group_a = await UserFactory.create(
        db_session,
        employee_no="OPGA01",
        name="A 组工人",
        workshop_id=piecework_world["workshop_id"],
        group_no="A",
    )
    group_b = await UserFactory.create(
        db_session,
        employee_no="OPGB01",
        name="B 组工人",
        workshop_id=piecework_world["workshop_id"],
        group_no="B",
    )
    headers = await hdr(
        db_session,
        auth_headers,
        piecework_world,
        READ_PERMS,
        data_scope=DataScope.GROUP,
        workshop_id=piecework_world["workshop_id"],
        group_no="A",
    )
    assert [r["value"] for r in await _names(client, headers, "OPGA01")] == [str(group_a.id)]
    assert await _names(client, headers, "OPGB01") == []
    assert group_b.id != group_a.id


async def test_tc_pw_op05_keyword_matches_no_or_name(
    client, db_session, auth_headers, piecework_world
) -> None:
    user = await UserFactory.create(
        db_session,
        employee_no="KWD12345",
        name="关键字甲",
        workshop_id=piecework_world["workshop_id"],
    )
    headers = await hdr(db_session, auth_headers, piecework_world, READ_PERMS)
    # 工号片段命中
    assert str(user.id) in {r["value"] for r in await _names(client, headers, "KWD12")}
    # 姓名命中
    assert str(user.id) in {r["value"] for r in await _names(client, headers, "关键字甲")}


async def test_tc_pw_op06_disabled_marked(client, db_session, auth_headers, piecework_world) -> None:
    user = await UserFactory.create(
        db_session,
        employee_no="OPD001",
        name="已离职",
        workshop_id=piecework_world["workshop_id"],
        is_active=False,
    )
    headers = await hdr(db_session, auth_headers, piecework_world, READ_PERMS)
    rows = await _names(client, headers, "OPD001")
    assert len(rows) == 1, rows
    assert rows[0]["value"] == str(user.id)
    assert rows[0]["disabled"] is True
    assert rows[0]["sub"] == "已停用"


async def test_tc_pw_op07_workshop_param_does_not_widen(
    client, db_session, auth_headers, piecework_world, other_piecework_world
) -> None:
    await UserFactory.create(
        db_session,
        employee_no="OPW001",
        name="本车间工人",
        workshop_id=piecework_world["workshop_id"],
    )
    headers = await hdr(
        db_session,
        auth_headers,
        piecework_world,
        READ_PERMS,
        data_scope=DataScope.WORKSHOP,
        workshop_id=piecework_world["workshop_id"],
    )
    response = await client.get(
        OPERATORS,
        params={"q": "OPW001", "workshop_id": str(other_piecework_world["workshop_id"])},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"] == []


async def test_tc_pw_op08_role_seed_grants_count() -> None:
    """按 ``04-计件 §10`` 矩阵：这四个角色要能工位机计件（L-024 的推进）。

    ⚠️ 反向断言同样要紧：``piecework:count`` 是**写**权限，误加到 ``employee``
    （SELF 范围）就等于在阶段二 Q-PW09 定稿前先把员工端写入口开了。"""
    from app.common.permissions_registry import ROLES, resolve_role_permissions

    by_code = {role.code: set(resolve_role_permissions(role)) for role in ROLES}
    for code in ("factory_manager", "workshop_supervisor", "line_leader", "piecework_settler"):
        assert "piecework:count" in by_code[code], f"{code} 缺 piecework:count"
    for code in ("employee", "merchandiser", "warehouse_keeper"):
        assert "piecework:count" not in by_code[code], f"{code} 不该有 piecework:count"


async def test_tc_pw_op09_line_leader_can_count(client, auth_headers, piecework_world) -> None:
    """内建 ``line_leader``（组长）真的能扫码计件 —— 证明 seed 授权端到端可用。

    ⚠️ 用**内建角色**而不是 ``custom``：TC-PW-R01 已经验过「没有权限点就 12001」，
    这里要验的恰恰相反 —— 真实组长账号（走 registry 的权限集）**不会**被 12001 挡下。"""
    headers = await auth_headers(
        role="line_leader",
        data_scope=DataScope.WORKSHOP,
        workshop_id=piecework_world["workshop_id"],
        group_no="A",
    )
    body = {
        "bundle_no": piecework_world["bundle_no"],
        "employee_id": str(piecework_world["employee"].id),
        "scanned_at": datetime.now(tz=UTC).isoformat(),
    }
    response = await client.post(COUNT, json=body, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["code"] == 0
