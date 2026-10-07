"""打菲单**审核六步**的测试（T-BUND-005b）。

TC-AP-01 ``1:2:2:1`` → 恰好 6 个码、手号 ``L01/XL01/XL02/XXL01/XXL02/3XL01``；
TC-AP-02 每手件数取裁剪明细的 ``qty_per_hand``（不 floor）、余数归 ``balance_qty``；
TC-AP-03 超打 → ``31004`` 且零副作用；TC-AP-04 手序号重复 → ``31005``；TC-AP-05 制单人
自审 → ``10005``；TC-AP-06 **2000 手**语句数 ≈ 明细行数；TC-AP-08 **20 并发** approve 一成一败。

⚠️ **TC-AP-06 与 TC-AP-08 是这一卡最要紧的两条**：前者守「禁止逐条 INSERT」（改成 ORM 循环
后 2000 手会变 2000 次往返，2C VPS 上审核要秒级卡住所有人），后者守「状态字段只由条件 UPDATE
写」—— 少了 ``WHERE ... AND status='SUBMITTED'``，20 个并发请求会全部成功、把同一批手号
生成两次，症状是车间拿到 2 倍的码而没人知道为什么。
⚠️ 并发用例必须**独立引擎真提交**（docs/10 §5.4），否则并发任务看不见那张单。
⚠️ 反审核用例在同目录的 ``test_bundling_reverse.py``（反审核是**独立入口**，
顺带钉住「不传前端 hands」与审核的关系）。
"""

from decimal import Decimal

import pytest
from sqlalchemy import event

from app.common.enums import DocumentStatus
from app.core.errors import BusinessError, ErrorCode
from app.modules.bundling.models import Bundle
from app.modules.bundling.service import BundlingOrderService
from tests.factories.bundling import attach_output, ctx, payload
from tests.factories.bundling_approve import (
    COLOR,
    approve_order,
    ensure_sizes,
    order_codes,
    order_logs,
    order_status,
    prepare_order,
    read_bundled,
    set_size_hands,
)
from tests.factories.user import OPERATOR_ID

ZERO = Decimal("0")

# ------------------------------------------------------------------ TC-AP-01


async def test_approve_generates_one_code_per_hand(db_session, bundling_world) -> None:
    """TC-AP-01：``1:2:2:1`` → 恰好 6 个码，手号连续且每手件数 = 裁剪明细的 ``qty_per_hand``。"""
    order = await prepare_order(db_session, bundling_world)
    fresh = await approve_order(db_session, order)

    assert fresh.status is DocumentStatus.APPROVED
    assert fresh.hands_total == 6
    codes = await order_codes(db_session, order.id)
    # ⚠️ ``bundle_no`` 是 **5 段**（``BD`` / 日期 / 单号 / 尺码+手号 / 件序号），
    # 所以尺码+手号是倒数第 **2** 段 —— 用 ``rsplit("-", 1)`` 取（件序号在最后一段）。
    # 打菲号第 4 段 = 尺码码 + 手序号（Q-B13：同尺码跨行连编，从 1 起）
    assert [c.bundle_no.rsplit("-", 2)[-2] for c in codes] == [
        "3XL01",
        "L01",
        "XL01",
        "XL02",
        "XXL01",
        "XXL02",
    ], f"手号必须逐手连编（Q-B13），实际 {[c.bundle_no for c in codes]}"
    assert all(c.bundle_qty == Decimal("60") for c in codes), "每手件数直接取 qty_per_hand"
    assert all(c.status == "ACTIVE" for c in codes)
    # ③ 每个尺码 1..N 无缺号
    per_size: dict[str, list[int]] = {}
    for code in codes:
        per_size.setdefault(code.size_code, []).append(code.hands)
    assert per_size == {"L": [1], "XL": [1, 2], "XXL": [1, 2], "3XL": [1]}


async def test_approve_does_not_floor_qty_per_hand(db_session, bundling_world) -> None:
    """TC-AP-02：**不 floor**：``hands=3, qty_per_hand=33`` → 3 码 × 33 = 99，余数恒 0。

    ⚠️ 断言「余数归 ``balance_qty``」在 Q-B15 之后的具体含义是**本单 ``balance_qty`` 恒为
    0** —— 差额只在裁剪侧人工指定出数时产生，且已在裁剪侧记入 ``balance_qty``（09 §4.2），
    打菲不承接。这里同时验「不 floor」与「余数不出码」，否则将来有人把 ``floor()`` 加回来
    不会有任何用例报警（99 → 3×33 恰好整除，正是最容易悄悄改动的地方）。
    """
    await set_size_hands(db_session, bundling_world, "XL", hands=3, qty_per_hand=33)
    await attach_output(
        db_session,
        bundling_world["style"].id,
        bundling_world["style"].style_no,
        bundling_world["workshop"].id,
        size_code="XL",
        output_qty=Decimal("99"),
    )
    order = await BundlingOrderService(db_session).create(
        payload(
            bundling_world,
            lines=[
                {
                    "cutting_size_line_id": next(
                        row.id
                        for row in bundling_world["cutting_size_lines"]
                        if row.size_code == "XL"
                    ),
                    "size_code": "XL",
                    "hands": 3,
                }
            ],
        ),
        OPERATOR_ID,
    )
    await BundlingOrderService(db_session).submit(order.id, OPERATOR_ID, ctx())
    fresh = await approve_order(db_session, order)

    codes = await order_codes(db_session, order.id)
    assert [c.hands for c in codes] == [1, 2, 3]
    assert all(c.bundle_qty == Decimal("33") for c in codes), "每手件数不取整"
    assert fresh.output_qty == Decimal("99.000")
    assert fresh.balance_qty == ZERO, "本单不出余数码，余数归裁剪侧（09 §4.2）"


# ------------------------------------------------------------------ TC-AP-03 / 04 / 05


async def test_over_production_is_rejected_without_side_effects(db_session, bundling_world) -> None:
    """TC-AP-03：**审核时**超打 → ``31004`` 且**零副作用**（码数仍为 0、状态仍 ``SUBMITTED``）。

    ⚠️ **不能在提交时就造超打**：``submit`` 自己也跑 ③ 防超打，那张单根本提交不上去，
    于是「审核还拦不拦超打」这条**永远不会被验到** —— 而审核恰恰是必须重算一遍的地方
    （裁剪侧在提交与审核之间改过手数，单据里存的是提交那一刻的快照）。

    所以这里走「先合法提交、**再把裁剪侧改小**」这条路：它同时是 ③ 在审核里**重算**
    而非复用提交快照的证明。
    """
    world = bundling_world
    style_no = world["style"].style_no
    await ensure_sizes(db_session, world, (("XL", 2),))
    await attach_output(
        db_session,
        world["style"].id,
        style_no,
        world["workshop"].id,
        size_code="XL",
        output_qty=Decimal("120"),
    )
    order = await BundlingOrderService(db_session).create(
        payload(
            world,
            lines=[
                {
                    "cutting_size_line_id": next(
                        row.id for row in world["cutting_size_lines"] if row.size_code == "XL"
                    ),
                    "size_code": "XL",
                    "hands": 2,
                }
            ],
        ),
        OPERATOR_ID,
    )
    service = BundlingOrderService(db_session)
    await service.submit(order.id, OPERATOR_ID, ctx())

    # 裁剪侧改小（改单是 02 模块的能力；这里直接改行，模拟它已发生）
    await set_size_hands(db_session, world, "XL", hands=1)
    await db_session.flush()

    with pytest.raises(BusinessError) as caught:
        await approve_order(db_session, order)
    assert caught.value.code is ErrorCode.BUNDLE_HANDS_MISMATCH
    assert (caught.value.details["planned_hands"], caught.value.details["cutting_hands"]) == (2, 1)

    assert await order_codes(db_session, order.id) == [], "超打不许留下半个码"
    assert await order_status(db_session, order.id) is DocumentStatus.SUBMITTED
    assert await read_bundled(db_session, style_no, size_code="XL") == ZERO, "结转不许被动过"
    assert [row for row in await order_logs(db_session, order.id) if row.action == "APPROVE"] == []


async def test_duplicated_hand_is_rejected(db_session, bundling_world) -> None:
    """TC-AP-04：手序号重复 → ``31005``，且**零副作用**。

    ⚠️ 走的���**唯一索引兜底**（⑤）而不是 ④ 预检：预检只看 ``status='ACTIVE'`` 的码
    （``VOIDED`` 已退出业务，拿它当冲突会让主管看到一堆消不掉的「冲突」），所以要触发它得先
    造一个「ACTIVE 但已存在」的情形 —— 这里直接塞一行 ACTIVE 码，再让审核撞上去。

    ⚠️ 也顺带钉住 B12：**``VOIDED`` 的码继续占着手号**，所以反审核后重开单据不会被手号
    悄悄复用。
    """
    order = await prepare_order(db_session, bundling_world, plan=(("XL", 2),))
    size_line = next(row for row in bundling_world["cutting_size_lines"] if row.size_code == "XL")
    db_session.add(
        Bundle(
            doc_id=order.id,
            line_id=order.lines[0].id,
            bundle_no=f"{order.doc_no}-XL01-0001",
            hands=1,
            style_no=order.style_no,
            color_code=COLOR,
            size_code="XL",
            operation_no="OP01",
            cutting_size_line_id=size_line.id,
            bundle_qty=Decimal("60"),
            qr_content=f"{order.doc_no}-XL01-0001",
            created_by=OPERATOR_ID,
            updated_by=OPERATOR_ID,
        )
    )
    await db_session.flush()

    with pytest.raises(BusinessError) as caught:
        await approve_order(db_session, order)
    assert caught.value.code is ErrorCode.BUNDLE_HAND_DUPLICATED
    assert caught.value.details["hands"] == 1
    assert await order_status(db_session, order.id) is DocumentStatus.SUBMITTED
    assert len(await order_codes(db_session, order.id)) == 1, "拦下后不许补插第 2 手"


async def test_creator_cannot_approve_own_order(db_session, bundling_world) -> None:
    """TC-AP-05：制单人自审 → ``10005``（08 §4）。

    ⚠️ 判的是 ``ctx.user_id``（认证身份）而不是 ``operator_id``：两者不一致说明调用方自己
    传错了身份，用 ``operator_id`` 判会把「传错身份」变成静默放过。
    """
    order = await prepare_order(db_session, bundling_world, plan=(("XL", 1),))
    with pytest.raises(BusinessError) as caught:
        await BundlingOrderService(db_session).approve(order.id, OPERATOR_ID, ctx())
    assert caught.value.code is ErrorCode.SELF_APPROVAL_FORBIDDEN
    assert await order_status(db_session, order.id) is DocumentStatus.SUBMITTED


# ------------------------------------------------------------------ TC-AP-06


async def test_bulk_insert_statement_count_is_line_bounded(db_session, bundling_world) -> None:
    """TC-AP-06：**2000 手**的 INSERT 语句数 = 明细行数，与手数无关（03 §5.3）。

    ⚠️ 用 ``hands=2000`` 而不是「2000 个码」：单尺码手号只有 2 位（``MAX_HAND_SEQ`` = 99），
    2000 手必须摊到 **21 个尺码**上。这个约束本身就是要点 —— 它意味着「大手数排版」在库里
    表现为「多尺码多明细行」，批量展开的语句数因此天然是行数量级。

    断言同时钉住两件事：语句数 ≤ 明细行数（没有逐条 INSERT），以及**恰好 2000 个码**
    （语句数少了但码数对不上，说明有尺码被静默漏掉）。
    """
    world = bundling_world
    plan = (*((f"Z{i:02d}", 99) for i in range(1, 21)), ("ZZ0", 20))
    assert sum(hands for _, hands in plan) == 2000

    # 裁剪侧要有对应尺码（3 位码 Z01..Z20 / ZZ0），hands 与打菲行一致以过 ③ 防超打
    await ensure_sizes(db_session, world, plan)

    order = await BundlingOrderService(db_session).create(
        payload(
            world,
            lines=[
                {
                    "cutting_size_line_id": next(
                        row.id for row in world["cutting_size_lines"] if row.size_code == size_code
                    ),
                    "size_code": size_code,
                    "hands": hands,
                }
                for size_code, hands in plan
            ],
        ),
        OPERATOR_ID,
    )
    await BundlingOrderService(db_session).submit(order.id, OPERATOR_ID, ctx())

    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", _record)
    try:
        await approve_order(db_session, order)
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    inserts = [s for s in statements if "INSERT INTO bundles" in s]
    assert len(inserts) == len(plan), (
        f"批量生成必须是「每条明细一条 INSERT」（{len(plan)} 条），实际 {len(inserts)} 条"
    )
    assert all("generate_series" in s for s in inserts), "必须走 generate_series 批量展开"
    codes = await order_codes(db_session, order.id)
    assert len(codes) == 2000, f"应生成 2000 个码，实际 {len(codes)}"
    # ③ 手号 1..N 无缺（按尺码分组验一遍：语句数对了但手号漏了，同样是废品）
    by_size: dict[str, list[int]] = {}
    for code in codes:
        by_size.setdefault(code.size_code, []).append(code.hands)
    assert len(by_size) == len(plan)
    for size_code, hands in plan:
        assert by_size[size_code] == list(range(1, hands + 1)), f"{size_code} 手号不连续"
