"""计件只读查询用例的**共享助手**（``04-计件 §6.1``）。

⚠️ 单独一个模块（而不是塞进两个测试文件里各写一遍）：两个测试文件都需要
「灌 N 条流水」「造一个权限恰好等于 X 的用户」这两件事，各写一份的话
改一处漏一处 —— 而权限用例恰恰是「看起来在验、其实没验」的高发区。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID, uuid4

from app.common.enums import DataScope

LOGS = "/api/v1/piecework-logs"
SUMMARY = "/api/v1/piecework/summary"
STATION = "/api/v1/piecework/stations"

READ_PERMS = ("piecework:read",)
EXPORT_PERMS = ("piecework:read", "piecework:export", "system:export:manage")


async def seed(
    db_session: Any, world: Mapping[str, Any], count: int = 3, station: str | None = None
) -> list[UUID]:
    """灌 N 条流水（走 service，保证口径与真实写入完全一致）。**按手序号顺序**灌，
    所以最后灌的那一手就是「本工位最近一次扫码」（``last_bundle_no`` 断言靠它）。"""
    from app.modules.piecework.service import PieceworkService
    from tests.factories.piecework_world import ctx_for, scanned_at

    ids: list[UUID] = []
    for no in await bundle_nos(db_session, world, count):
        out = await PieceworkService(db_session).count_piecework(
            bundle_no=no,
            employee_id=world["employee"].id,
            scanned_at=scanned_at(),
            qty=None,
            operator_id=ctx_for().user_id,
            ctx=ctx_for(),
            station_id=station,
        )
        ids.append(out.log_id)
    return ids


async def bundle_nos(db_session: Any, world: Mapping[str, Any], count: int) -> list[str]:
    """按手序号取前 ``count`` 个码。"""
    from sqlalchemy import select

    from app.modules.bundling.models import Bundle

    rows = (
        (
            await db_session.execute(
                select(Bundle.bundle_no)
                .where(Bundle.doc_id == world["order"]["id"], Bundle.deleted_at.is_(None))
                .order_by(Bundle.hands)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) >= count, f"测试世界只铺了 {len(rows)} 个码，{count} 条流水铺不下"
    return list(rows[:count])


async def hdr(
    db_session: Any,
    auth_headers: Any,
    world: Mapping[str, Any],
    perms: tuple[str, ...],
    *,
    data_scope: DataScope = DataScope.FACTORY,
    workshop_id: Any = None,
    group_no: str | None = None,
    user_id: Any = None,
) -> dict[str, str]:
    """造一个**权限点恰好等于 ``perms``** 的用户的请求头。

    ⚠️ **不能直接用 ``auth_headers(role="custom", ...)``**：那个 ``custom`` 角色在
    一个 ``db_session`` 里是**复用**的，而 ``grant_permissions`` 只加不减 ——
    于是「先建一个只有 ``piecework:read`` 的用户、断言导出 403」这条用例，会因为
    前面某条用例已经给 ``custom`` 加了 ``piecework:export``，而**测出 200**。
    那种绿是假的：它验的是「角色上碰巧有这些权限」，不是「这次请求的权限判定对不对」。

    所以这里**每个请求自建一个唯一 code 的角色**，权限集就是 ``perms`` 本身。

    ⚠️ ``user_id`` 存在时**用那个已有用户**（自审 / 归属校验要测「操作者是谁」），
    但必须**先给他挂上 ``perms`` 角色** —— 不然他是裸的、没有任何权限点。
    """
    from tests.factories.user import RoleFactory, UserFactory, UserRoleFactory, grant_permissions

    role = await RoleFactory.create(
        db_session,
        code=f"pwq_{uuid4().hex[:10]}",
        name="计件查询用例角色",
        data_scope=data_scope,
    )
    if perms:
        await grant_permissions(db_session, role=role, codes=perms)
    if user_id is not None:
        await UserRoleFactory.create(db_session, user_id=user_id, role_id=role.id)
        return await auth_headers(user_id=user_id)
    user = await UserFactory.create(
        db_session,
        employee_no=f"Q{uuid4().hex[:7].upper()}",
        data_scope=data_scope,
        workshop_id=workshop_id if workshop_id is not None else world["workshop_id"],
        group_no=group_no,
    )
    await UserRoleFactory.create(db_session, user_id=user.id, role_id=role.id)
    return await auth_headers(user_id=user.id)


async def self_hdr(
    db_session: Any, auth_headers: Any, employee: Any, perms: tuple[str, ...] = READ_PERMS
) -> dict[str, str]:
    """**员工本人**的令牌（``user_id`` == 流水的 ``employee_id``）。

    ⚠️ 造不出来就验不了 SELF：用 ``auth_headers`` 默认新建的那个用户，uuid 与流水上的
    ``employee_id`` 对不上，SELF 过滤后永远是 0 行 —— 而 0 与「过滤对了」长得一模一样。
    所以必须**就着已有的那个员工**发令牌，再给他挂一个角色授权限。
    """
    from tests.factories.user import RoleFactory, UserRoleFactory, grant_permissions

    role = await RoleFactory.create(
        db_session,
        code=f"pwqself_{uuid4().hex[:8]}",
        name="员工本人角色",
        data_scope=DataScope.SELF,
    )
    await grant_permissions(db_session, role=role, codes=perms)
    await UserRoleFactory.create(db_session, user_id=employee.id, role_id=role.id)
    return await auth_headers(user_id=employee.id)


__all__ = [
    "EXPORT_PERMS",
    "LOGS",
    "READ_PERMS",
    "STATION",
    "SUMMARY",
    "bundle_nos",
    "hdr",
    "seed",
    "self_hdr",
]
