"""打菲 **码侧**的规则缺口：手号占用、改手数冻结、`/split` 权限、部分生产可见
（T-BUND-008）。

============================  ==============================================
TC-25                        反审核后手号**仍占号**（同单再审核 → ``31005``）；
                             **新单**因 ``doc_no`` 不同而手号可从 ``XL01`` 重编
TC-28（③）                   已计件码的 ``bundle_qty`` 在**每个可达写入口**下都不变
TC-31（权限两条）             ``line_leader`` 调 ``/split`` 成功；``employee`` → ``12001``
TC-33                        部分生产（28/60）在**单码详情**里看得见剩余与计件人
============================  ==============================================

⚠️ **TC-28 的 ①② 与卡面不符，是实现缺口不是用例缺口**：``31006`` 在 ``ErrorCode`` 里
已登记（T-BUND-007b）却**全仓无抛出点**；且 ``patch`` 只改表头、``put_lines`` 只允许
``DRAFT``/``REJECTED`` —— 已审核的单根本改不了手数，「改手数同步未计件码的
``bundle_qty``」这条路径当前**不可达**。本文件因此只断 **③**（那条不变量在当前实现下
可达且恒真），①② 登记为缺陷，见任务卡的「发现的实现缺陷清单」。

⚠️ **TC-25 的关键证据是「报错来自库层唯一约束」**：反审核后码全 ``VOIDED``，而 ④ 预检
只看 ``ACTIVE`` 码 —— 预检压根没参与。所以断的是 ``details`` 里带 ``constraint``；
预检报出的 ``31005`` 带的是 ``hands``、压根没有这个键。

⚠️ **TC-33 必须用真用户当 ``counted_by``**：详情里的 ``counted_by_name`` 是
``LEFT JOIN users`` 取的（ADR-0016 §6「提示 XL 第 2 手，陈美玲 09:44 已计」），
填一个不存在的 uuid 会让那条断言恒为 ``None``，于是「计件人姓名」这条字段**永远没被测过**。
"""

from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.common.enums import DataScope
from app.core.errors import BusinessError, ErrorCode
from app.modules.auth.models import User
from app.modules.bundling.models import Bundle
from app.modules.bundling.schemas import BundlingOrderPatchIn, LineIn, PutLinesIn
from app.modules.bundling.service import BundlingOrderService
from tests.factories.bundling import ctx, payload, read_reserved
from tests.factories.bundling_approve import (
    REVIEWER_ID,
    approve_order,
    mark_code_counted,
    order_codes,
    prepare_order,
    reviewer_ctx,
)
from tests.factories.bundling_http import WRITER_PERMISSIONS, approved_order, submitted_order
from tests.factories.user import OPERATOR_ID

BUNDLES = "/api/v1/bundles"
SPLIT = "/api/v1/bundling-orders/{order_id}/split"
ZERO = Decimal("0")


# ------------------------------------------------------------------ TC-25


async def test_reversed_order_keeps_hands_occupied_but_new_order_reuses_numbering(
    db_session, bundling_world
) -> None:
    """TC-25：反审核后 ``XL02`` **仍占号**（同单再审核 → ``31005``）；**新单**从 ``XL01`` 重编。

    ⚠️ 反审核 → 再审核这条走的**不是** ④ 预检而是**库层唯一约束**：预检只看
    ``status='ACTIVE'`` 的码（``VOIDED`` 已退出业务），而此刻全是 ``VOIDED``。
    所以这里断言的是「``details`` 带 ``constraint``」——预检报出的 ``31005`` 带的是
    ``hands``、压根没有这个键（见 ``test_duplicated_hand_is_rejected``）。
    """
    world = bundling_world
    service = BundlingOrderService(db_session)
    order = await prepare_order(db_session, world, plan=(("XL", 2),))
    await approve_order(db_session, order)
    await service.reverse(order.id, "打错了，重新打", REVIEWER_ID, reviewer_ctx())

    with pytest.raises(BusinessError) as caught:
        await approve_order(db_session, order)
    assert caught.value.code is ErrorCode.BUNDLE_HAND_DUPLICATED
    assert caught.value.details is not None
    # ⚠️ 同单再审核时 ``bundle_no`` 本身也一模一样（``doc_no`` 前缀相同），于是
    #    **两个**唯一约束都会撞，而 PG 先报哪一个不保证（实测是 ``uq_bundles_no``）。
    #    两条都在 ``approve_mixin.HAND_CONFLICT_CONSTRAINTS`` 里、都归 ``31005`` ——
    #    关键是它来自**库层唯一约束**（``details`` 带 ``constraint``），而预检报出的
    #    ``31005`` 带的是 ``hands``、压根没有这个键（见 ``test_duplicated_hand_is_rejected``）。
    assert caught.value.details["constraint"] in {"uq_bundles_no", "uq_bundles_hand"}, (
        f"必须是库层唯一约束兜的：{caught.value.details}"
    )

    old = await order_codes(db_session, order.id)
    assert [row.status for row in old] == ["VOIDED", "VOIDED"], "旧码保持作废、行仍在（B12）"

    # ⚠️ 先撤回释放预占，再开新单：反审核后 ``reserved_qty`` 还占着 120，
    #    不释放的话新单提交会撞 ``30002``，而报错指向余量、与本条要验的无关。
    await service.withdraw(order.id, OPERATOR_ID, ctx())
    # ⚠️ **这里必须重读一次结转行**（``read_reserved`` 自带 ``populate_existing``）：
    #    ``lock_cutting_outputs`` 的 ``SELECT ... FOR UPDATE`` **没带**
    #    ``populate_existing``，同一个 session 里它会把上面几次 Core UPDATE 之前的旧值
    #    塞回 identity map，于是第二次 ``submit`` 的「先查可用量」那一层按旧值判不足、
    #    报出与真实余量不符的 ``30002``（生产每个请求一个 session，碰不到；测试一个
    #    session 跑完整条链路就会踩到）。已登记为缺陷，见任务卡「发现的实现缺陷清单」。
    assert await read_reserved(db_session, world["style"].style_no, size_code="XL") == ZERO
    size_line = next(row for row in world["cutting_size_lines"] if row.size_code == "XL")
    fresh = await service.create(
        payload(
            world,
            lines=[{"cutting_size_line_id": size_line.id, "size_code": "XL", "hands": 2}],
        ),
        OPERATOR_ID,
    )
    await service.submit(fresh.id, OPERATOR_ID, ctx())
    await approve_order(db_session, fresh)

    new_codes = await order_codes(db_session, fresh.id)
    assert [row.bundle_no.rsplit("-", 2)[-2] for row in new_codes] == ["XL01", "XL02"], (
        "新单因 doc_no 不同而手号可从 XL01 重编"
    )
    assert new_codes[0].bundle_no != old[0].bundle_no
    assert [row.status for row in await order_codes(db_session, order.id)] == [
        "VOIDED",
        "VOIDED",
    ], "旧码不因新单而复活"


# ------------------------------------------------------------------ TC-28（③）


async def test_counted_code_bundle_qty_never_moves(db_session, bundling_world) -> None:
    """TC-28（③）：已计件码的 ``bundle_qty`` 在**每个可达写入口**下都不变。

    ⚠️ 逐个入口点名（而不是只试一个）：``patch``（改表头）/ ``put_lines``（改手数）
    / ``reverse``（整单反审核）/ ``void_code``（单码作废）。已审核单能碰到的写入口就这四个，
    全被拒或全不碰 ``bundle_qty``，不变量才成立 —— 只试一个入口时，别人加一条新入口
    不会有任何用例报警。
    ⚠️ ``ck_bundles_cnt(counted_qty <= bundle_qty)`` 是最后一道 DB 兜底；业务侧一旦
    真的去改已计件码的件数，插入/更新会直接被 CHECK 拒 —— 而那报错对用户毫无意义。
    """
    world = bundling_world
    service = BundlingOrderService(db_session)
    order = await prepare_order(db_session, world, plan=(("XL", 2),))
    await approve_order(db_session, order)
    await mark_code_counted(db_session, order.id, hands=2)
    size_line = next(row for row in world["cutting_size_lines"] if row.size_code == "XL")
    version = order.version

    with pytest.raises(BusinessError) as header:
        await service.patch(
            order.id, BundlingOrderPatchIn(version=version, remark="改备注"), OPERATOR_ID, ctx()
        )
    assert header.value.code is ErrorCode.CUTTING_STATUS_NOT_ALLOWED

    with pytest.raises(BusinessError) as lines:
        await service.put_lines(
            order.id,
            PutLinesIn(
                version=version,
                items=[
                    LineIn(
                        line_no=1,
                        color_code="WHT",
                        size_code="XL",
                        operation_no="OP01",
                        cutting_size_line_id=size_line.id,
                        hands=3,
                    )
                ],
            ),
            OPERATOR_ID,
            ctx(),
        )
    assert lines.value.code is ErrorCode.CUTTING_STATUS_NOT_ALLOWED

    with pytest.raises(BusinessError) as reverse:
        await service.reverse(order.id, "试试", REVIEWER_ID, reviewer_ctx())
    assert reverse.value.code is ErrorCode.PIECEWORK_SETTLED

    codes = {row.bundle_no: row for row in await order_codes(db_session, order.id)}
    counted_no = next(row.bundle_no for row in codes.values() if row.hands == 2)
    with pytest.raises(BusinessError) as void:
        await service.void_code(counted_no, "试试", OPERATOR_ID, ctx())
    assert void.value.code is ErrorCode.PIECEWORK_SETTLED

    rows = (
        await db_session.execute(
            select(Bundle.bundle_qty, Bundle.counted_qty, Bundle.status)
            .where(Bundle.doc_id == order.id)
            .order_by(Bundle.hands)
            .execution_options(populate_existing=True)
        )
    ).all()
    assert [(Decimal(str(r[0])), Decimal(str(r[1])), str(r[2])) for r in rows] == [
        (Decimal("60"), Decimal("0"), "ACTIVE"),
        (Decimal("60"), Decimal("1"), "ACTIVE"),
    ], "已计件那手的件数与计数都没动，且两手都还是 ACTIVE（没被半路作废）"


# ------------------------------------------------------------------ TC-31（权限）


async def test_split_is_open_to_line_leader_and_closed_to_employee(
    client: AsyncClient, db_session: Any, auth_headers: Any, bundling_world
) -> None:
    """TC-31 后半：``/split`` 只读不落库 → ``line_leader``（有 ``bundling:read``）**能用**，
    而 ``employee``（无任何打菲权限）→ ``12001``。

    ⚠️ 「能用」这条是 TC-31 的**正面**断言，光断 ``employee`` 被拒会让「预演因为没人能用
    而没人调过」也照样绿 —— 而 ADR-0016 的验证方式之一就是主管在提交前先预演。
    ⚠️ 刻意换成 ``bundling:read`` 就够的 ``line_leader``：``03 §4`` 给 ``/split`` 登记的
    权限点是 ``bundling:read``（只读预演不写库），**不是** ``bundling:approve``。
    换成有审批权的角色，这条就变成在测「超管当然能用」了。
    """
    order = await submitted_order(
        client,
        db_session,
        await auth_headers(role="custom", permissions=WRITER_PERMISSIONS),
        bundling_world,
    )
    # ⚠️ ``line_leader`` 内置范围是 **GROUP** 而 ``bundling_orders`` 上没有组别列，
    #    不给可见范围的话它连「看得到这张单」都做不到，用例就变成在测 ``12002``
    #    而不是测 ``/split`` 的权限点（那正是 TC-31 要断的东西）。
    leader = await auth_headers(
        role="line_leader",
        data_scope=DataScope.WORKSHOP,
        workshop_id=bundling_world["workshop"].id,
    )
    employee = await auth_headers(role="employee", data_scope=DataScope.SELF)

    allowed = await client.post(SPLIT.format(order_id=order["id"]), headers=leader)
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["data"]["hands_total"] == 1

    denied = await client.post(SPLIT.format(order_id=order["id"]), headers=employee)
    assert denied.status_code == 403, denied.text
    assert denied.json()["code"] == ErrorCode.PERMISSION_DENIED
    assert len(await order_codes(db_session, order["id"])) == 0, "预演与拒绝都不生成码"


# ------------------------------------------------------------------ TC-33


async def test_partially_counted_hand_detail_shows_remaining_qty(
    client: AsyncClient, db_session: Any, auth_headers: Any, bundling_world
) -> None:
    """TC-33：部分生产（``counted_qty=28`` / ``bundle_qty=60``）在单码详情里**看得见剩余**。

    ⚠️ 用**真用户**当 ``counted_by``：详情里的 ``counted_by_name`` 是
    ``LEFT JOIN users`` 取的（ADR-0016 §6「提示 XL 第 2 手，陈美玲 09:44 已计」），
    填一个不存在的 uuid 会让那条断言恒为 ``None``，于是「计件人姓名」这条字段**永远没被测过**。
    ⚠️ 同时断「剩余 32 件不改码」：``bundle_no`` 与 ``bundle_qty`` 都不得变。
    """
    order = await approved_order(client, db_session, auth_headers, bundling_world, hands=2)
    # ⚠️ 两个身份都必须给 **FACTORY** 范围：这单是 ``approved_order`` 用另一个 token
    #    建的，而 ``custom`` 角色默认 SELF（只看得到自己建的），不给范围就撞 ``12002`` ——
    #    报错指向数据范围，与这条要验的「部分生产看得见」毫无关系。
    employee = await auth_headers(
        role="custom", permissions=("bundling:read",), data_scope=DataScope.FACTORY
    )
    worker = await auth_headers(
        role="custom",
        permissions=("bundling:read",),
        data_scope=DataScope.FACTORY,
        employee_no="W007",
    )
    user_id = worker["X-Test-User-Id"]
    worker_name = await db_session.scalar(select(User.name).where(User.id == UUID(user_id)))
    target = next(row for row in await order_codes(db_session, order["id"]) if row.hands == 2)
    await _write_partial(db_session, order["id"], hands=2, counted_qty=28, counted_by=user_id)

    detail = await client.get(f"{BUNDLES}/{target.bundle_no}", headers=employee)
    assert detail.status_code == 200, detail.text
    data = detail.json()["data"]
    assert data["bundle_qty"] == "60.000"
    assert data["counted_qty"] == "28.000"
    assert data["counted_at"] is not None, "已计件·部分也是**已计件**"
    assert data["counted_by_name"] == worker_name, (
        f"计件人姓名要能从码上直接显示（ADR-0016 §6）：{data['counted_by_name']} != {worker_name}"
    )
    assert data["status"] == "ACTIVE"
    assert data["bundle_no"] == target.bundle_no, "剩余 32 件不改码"

    pending = await client.get(
        BUNDLES, params={"doc_id": order["id"], "counted": "false"}, headers=employee
    )
    assert pending.status_code == 200
    assert [row["hands"] for row in pending.json()["data"]["items"]] == [1], (
        "部分生产的手**已计件**，不该出现在未计件清单里"
    )


async def _write_partial(
    db_session: Any, doc_id: str, *, hands: int, counted_qty: int, counted_by: str
) -> None:
    """把某一手写成「已计件·部分」（模拟计件模块回写；``piecework_logs`` 属 P2 / L-096）。

    ⚠️ **只 UPDATE 不插行**：``bundles`` 已由审核生成，插一行同 ``bundle_no`` 的会撞
    ``uq_bundles_no``，而报错完全看不出根因是「测试重复造了一条码」。
    ⚠️ ``synchronize_session="fetch"`` 不能省（同 ``mark_code_counted``）：Core UPDATE
    默认不刷新 identity map，同一 session 里 service 后面那次 ``FOR UPDATE`` 会拿到
    「没计件」的旧值，``32003`` 就不触发。
    """
    from datetime import UTC, datetime

    await db_session.execute(
        update(Bundle)
        .where(Bundle.doc_id == doc_id, Bundle.hands == hands)
        .values(counted_qty=counted_qty, counted_by=counted_by, counted_at=datetime.now(tz=UTC))
        .execution_options(synchronize_session="fetch")
    )
    await db_session.flush()
