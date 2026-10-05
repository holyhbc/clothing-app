"""裁剪单三层明细的**增量维护**（T-CUT-001b-2）。

============================  ===============================================
TC-C01b2-01                   ``patch`` 版本不符 → ``10003``
TC-C01b2-02                   ``patch`` 只改表头，**不动**三层明细
TC-C01b2-03                   非 ``DRAFT`` 状态改 → ``30001``
TC-C01b2-04                   ``delete`` 三层**全部**软删，无物理删
TC-C01b2-05                   删单后 ``get`` → 不存在（INV-7）
TC-C01b2-06                   ``PUT /lines`` 替换后**全单**汇总重算
TC-C01b2-07                   ``PUT /size-lines`` 后颜色/行/头三级都对
TC-C01b2-08                   ``size_line_no`` 省略时由服务端分配
TC-C01b2-09                   行号分配用 **max+1 而非 count+1**（软删过的行不撞号）
TC-C01b2-10                   比例完全未配 → ``20006``
TC-C01b2-11                   比例**部分**缺配 → 不拦，只提示
TC-C01b2-12                   比例含款号没有的尺码 → ``20007``
TC-C01b2-13                   ``suggest-lines`` 写快照，**不动**比例主数据
TC-C01b2-14                   切模式未确认 → 拒；已确认 → 清明细并留痕
TC-C01b2-15                   ``fabric_qty > available_qty`` → ``40006``
TC-C01b2-16                   **并发**改同一单 → 只有一个成功，另一个 ``10003``
============================  ===============================================

⚠️ **TC-C01b2-06 守的是这一卡最容易犯的错**：只重算「本次替换的那几行」。
单头是**全表的和**，所以漏掉任何一行，单头就与逐行相加对不上 —— 而这种错在界面上
**完全看不出来**（表头和列表都是同一份错数据）。

⚠️ **TC-C01b2-09 守的是一个真的会撞号的实现**：行号若按 ``count + 1`` 分配，
一张「原本 3 行、软删 2 行、现存 1 行」的明细表，下一个号会被发成 2，
与软删行撞上 ``uq_cutting_size_lines`` —— 而报错完全看不出根因是「分配算法不对」。

⚠️ **TC-C01b2-16 必须用独立引擎真提交**（``docs/10 §5.4``「共享一个 session 测不出来」）：
同一个连接上的语句会被 PostgreSQL 串行化，测出来的是「顺序执行」。
"""

import asyncio
import os
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.common.enums import DataScope
from app.core.errors import BusinessError, ErrorCode
from app.core.permissions import AuthContext
from app.modules.cutting.models import (
    CuttingOrder,
    CuttingOrderLine,
    CuttingOrderLineColor,
    CuttingOrderSizeLine,
)
from app.modules.cutting.schemas import (
    CuttingOrderPatchIn,
    EntryModeSwitchIn,
    LineColorIn,
    PutColorsIn,
    PutLinesIn,
    PutSizeLinesIn,
    SizeLineIn,
)
from app.modules.cutting.service import CuttingOrderService
from tests.factories.cutting import (
    add_ratio,
    add_style_sizes,
    ctx,
    line_in,
    payload,
    size_line,
)
from tests.factories.user import OPERATOR_ID

_CTX = ctx()


async def _create(db_session, cutting_world, **overrides) -> CuttingOrder:
    """建一张草稿单（测试里的固定操作人）。"""
    return await CuttingOrderService(db_session).create(
        payload(cutting_world, **overrides), OPERATOR_ID
    )


async def _color_id(service: CuttingOrderService, order: CuttingOrder) -> object:
    """取该单第一个行内颜色的 id（三个接口都要它）。"""
    return (await service.get(order.id, _CTX)).lines[0].colors[0].id


async def _live_counts(db_session, order_id) -> dict[str, int]:
    """三层的**存活**行数（软删的必须排除，否则测不出级联）。"""
    out: dict[str, int] = {}
    out["lines"] = (
        await db_session.execute(
            select(func.count())
            .select_from(CuttingOrderLine)
            .where(CuttingOrderLine.doc_id == order_id, CuttingOrderLine.deleted_at.is_(None))
        )
    ).scalar_one()
    line_ids = list(
        (
            await db_session.execute(
                select(CuttingOrderLine.id).where(
                    CuttingOrderLine.doc_id == order_id, CuttingOrderLine.deleted_at.is_(None)
                )
            )
        ).scalars()
    )
    out["colors"] = 0
    out["size_lines"] = 0
    if line_ids:
        color_ids = list(
            (
                await db_session.execute(
                    select(CuttingOrderLineColor.id).where(
                        CuttingOrderLineColor.line_id.in_(line_ids),
                        CuttingOrderLineColor.deleted_at.is_(None),
                    )
                )
            ).scalars()
        )
        out["colors"] = len(color_ids)
        if color_ids:
            out["size_lines"] = (
                await db_session.execute(
                    select(func.count())
                    .select_from(CuttingOrderSizeLine)
                    .where(
                        CuttingOrderSizeLine.line_color_id.in_(color_ids),
                        CuttingOrderSizeLine.deleted_at.is_(None),
                    )
                )
            ).scalar_one()
    return out


# ------------------------------------------------------------------ TC-C01b2-01 / 02 / 03


async def test_patch_version_conflict(db_session, cutting_world):
    """TC-C01b2-01：版本不符 → ``10003``，且**动手之前**就拒。"""
    order = await _create(db_session, cutting_world)
    with pytest.raises(BusinessError) as caught:
        await CuttingOrderService(db_session).patch(
            order.id,
            CuttingOrderPatchIn(version=order.version + 5, remark="改备注"),
            OPERATOR_ID,
            _CTX,
        )
    assert caught.value.code is ErrorCode.OPTIMISTIC_LOCK_CONFLICT
    assert caught.value.details is not None
    assert caught.value.details["current"] == order.version


async def test_patch_only_touches_header(db_session, cutting_world):
    """TC-C01b2-02：``patch`` **不动**三层明细。"""
    order = await _create(db_session, cutting_world)
    before = await _live_counts(db_session, order.id)
    # ⚠️ **先取出版本号**：``order`` 与返回值是**同一个 ORM 对象**（identity map），
    #    patch 之后 ``order.version`` 已经是新值，再拿它 +1 比就是个自欺欺人的断言
    expected_version = order.version
    updated = await CuttingOrderService(db_session).patch(
        order.id,
        CuttingOrderPatchIn(version=order.version, remark="改个备注", ply_count=2),
        OPERATOR_ID,
        _CTX,
    )
    await db_session.flush()
    assert updated.remark == "改个备注"
    assert updated.ply_count == 2
    assert updated.version == expected_version + 1
    assert await _live_counts(db_session, order.id) == before


async def test_patch_rejects_non_draft(db_session, cutting_world):
    """TC-C01b2-03：``SUBMITTED`` 及以后**只读**（C14 / 08 §1.1）。

    ⚠️ 直接改库设状态，而不是调 ``submit`` —— 状态机在 b-3，而这里要测的是
    「状态不允许时 service 会不会拒」这条守卫本身。
    """
    order = await _create(db_session, cutting_world)
    await db_session.execute(
        text("UPDATE cutting_orders SET status = 'SUBMITTED' WHERE id = :i"), {"i": order.id}
    )
    await db_session.refresh(order)
    with pytest.raises(BusinessError) as caught:
        await CuttingOrderService(db_session).patch(
            order.id,
            CuttingOrderPatchIn(version=order.version, remark="改"),
            OPERATOR_ID,
            _CTX,
        )
    assert caught.value.code is ErrorCode.CUTTING_STATUS_NOT_ALLOWED


# ------------------------------------------------------------------ TC-C01b2-04 / 05


async def test_delete_soft_deletes_all_three_levels(db_session, cutting_world):
    """TC-C01b2-04：三层**全部**软删，且应用账号**无 DELETE 权限**可用。

    ⚠️ 这就是为什么必须软删：``erp_app`` 对四张裁剪表没有 DELETE 权限
    （``04 §6.2.1``），真删会撞 ``permission denied``。而只软删表头是不够的 ——
    子表若不软删，详情页仍会显示已删单据的尺码明细。
    """
    order = await _create(db_session, cutting_world)
    assert await _live_counts(db_session, order.id) == {
        "lines": 1,
        "colors": 1,
        "size_lines": 1,
    }
    await CuttingOrderService(db_session).delete(order.id, order.version, OPERATOR_ID, _CTX)
    await db_session.flush()

    assert await _live_counts(db_session, order.id) == {
        "lines": 0,
        "colors": 0,
        "size_lines": 0,
    }
    # ⚠️ 行确实还在库里，只是软删了 —— 证明是软删不是物理删
    total = (
        await db_session.execute(
            select(func.count())
            .select_from(CuttingOrderLine)
            .where(CuttingOrderLine.doc_id == order.id)
        )
    ).scalar_one()
    assert total == 1, "行被物理删了 —— AGENTS §2.1 禁止，04 §6.2.1 也无 DELETE 权限"


async def test_deleted_order_is_not_readable(db_session, cutting_world):
    """TC-C01b2-05：软删后 ``get`` → 不存在（INV-7）。"""
    order = await _create(db_session, cutting_world)
    service = CuttingOrderService(db_session)
    await service.delete(order.id, order.version, OPERATOR_ID, _CTX)
    with pytest.raises(BusinessError) as caught:
        await service.get(order.id, _CTX)
    assert caught.value.code is ErrorCode.CUTTING_STATUS_NOT_ALLOWED


# ------------------------------------------------------------------ TC-C01b2-06 / 07


async def test_put_lines_recalcs_whole_order(db_session, cutting_world):
    """TC-C01b2-06：替换布批行后，**全单**汇总重算（含没被碰的行）。

    ⚠️ 本例特意放**两行**，替换时两行都原样带回 —— 若只重算「本次碰过的行」，
    单头就与逐行相加对不上。这是这一卡最容易犯的错。
    """
    order = await CuttingOrderService(db_session).create(
        payload(
            cutting_world,
            lines=[
                line_in(cutting_world, line_no=1, fabric_qty="96.000", output_qty="180.000"),
                line_in(
                    cutting_world,
                    line_no=2,
                    fabric_qty="31.500",
                    output_qty="60.000",
                    colors=[LineColorIn(color_code="BLK", size_lines=[size_line(1, "L", 1, 50)])],
                ),
            ],
        ),
        OPERATOR_ID,
    )
    assert order.fabric_qty == Decimal("127.500")

    updated = await CuttingOrderService(db_session).put_lines(
        order.id,
        PutLinesIn(
            version=order.version,
            items=[
                line_in(
                    cutting_world,
                    line_no=1,
                    fabric_qty="100.000",
                    output_qty="180.000",
                    colors=[
                        LineColorIn(
                            color_code="WHT",
                            size_lines=[
                                size_line(1, "L", 2, 60),
                                size_line(2, "XL", 1, 60),
                            ],
                        )
                    ],
                ),
                line_in(
                    cutting_world,
                    line_no=2,
                    fabric_qty="31.500",
                    output_qty="60.000",
                    colors=[LineColorIn(color_code="BLK", size_lines=[size_line(1, "L", 1, 50)])],
                ),
            ],
        ),
        OPERATOR_ID,
        _CTX,
    )
    await db_session.flush()

    # 耗料 100 + 31.5 = 131.5（**两行都参与**）
    assert updated.fabric_qty == Decimal("131.500")
    # 行可出件数 180 + 60 = 240
    assert updated.output_qty == Decimal("240.000")
    # 余量 = (180-180) + (60-50) = 10，并入裁损（waste_qty 全 0）
    assert updated.balance_qty == Decimal("10.000")
    assert await _live_counts(db_session, order.id) == {"lines": 2, "colors": 2, "size_lines": 3}


async def test_put_lines_rejects_negative_balance(db_session, cutting_world):
    """TC-C01b2-06（负数侧）：行可出件数 < Σ明细 → ``30002``。"""
    order = await _create(db_session, cutting_world)
    with pytest.raises(BusinessError) as caught:
        await CuttingOrderService(db_session).put_lines(
            order.id,
            PutLinesIn(
                version=order.version,
                items=[line_in(cutting_world, output_qty="30.000")],
            ),
            OPERATOR_ID,
            _CTX,
        )
    assert caught.value.code is ErrorCode.CUTTING_QTY_CONFLICT


async def test_put_size_lines_recalcs_three_levels(db_session, cutting_world):
    """TC-C01b2-07：替换尺码明细后，颜色 / 行 / 头**三级**汇总都对。

    ⚠️ 行可出件数必须**够装**：行 120、明细 2×60 + 1×60 = 180 → 负余量，
    所以这里先建一张行可出件数足够的单子（220），再换明细。
    """
    order = await CuttingOrderService(db_session).create(
        payload(
            cutting_world,
            lines=[
                line_in(
                    cutting_world,
                    fabric_qty="96.000",
                    output_qty="220.000",
                    colors=[LineColorIn(color_code="WHT", size_lines=[size_line(1, "L", 2, 60)])],
                )
            ],
        ),
        OPERATOR_ID,
    )
    service = CuttingOrderService(db_session)
    color_id = await _color_id(service, order)

    updated = await service.put_size_lines(
        order.id,
        PutSizeLinesIn(
            version=order.version,
            line_color_id=color_id,
            items=[
                SizeLineIn(size_code="L", hands=2, qty_per_hand=60),
                SizeLineIn(size_code="XL", hands=1, qty_per_hand=60),
            ],
        ),
        OPERATOR_ID,
        _CTX,
    )
    await db_session.flush()

    color = updated.lines[0].colors[0]
    assert color.output_qty_total == Decimal("180.000"), "颜色层 = 120 + 60"
    assert color.hands_total == Decimal("3")
    assert color.balance_qty_total == Decimal("0")
    assert updated.lines[0].balance_qty == Decimal("40.000"), "行余量 = 220 - 180"
    assert updated.fabric_qty == Decimal("96.000")
    assert updated.hands_total == 3, "表头手数也要跟着重算"


async def test_put_size_lines_rejects_overflow(db_session, cutting_world):
    """TC-C01b2-07（负数侧）：尺码明细合计超出行可出件数 → ``30002``。"""
    order = await _create(db_session, cutting_world)
    service = CuttingOrderService(db_session)
    color_id = await _color_id(service, order)
    with pytest.raises(BusinessError) as caught:
        await service.put_size_lines(
            order.id,
            PutSizeLinesIn(
                version=order.version,
                line_color_id=color_id,
                items=[SizeLineIn(size_code="L", hands=5, qty_per_hand=60)],
            ),
            OPERATOR_ID,
            _CTX,
        )
    assert caught.value.code is ErrorCode.CUTTING_QTY_CONFLICT


# ------------------------------------------------------------------ TC-C01b2-08 / 09


async def test_size_line_no_is_assigned_by_server(db_session, cutting_world):
    """TC-C01b2-08：``size_line_no`` **省略**时由服务端分配。"""
    order = await _create(db_session, cutting_world)
    service = CuttingOrderService(db_session)
    color_id = await _color_id(service, order)

    await service.put_size_lines(
        order.id,
        PutSizeLinesIn(
            version=order.version,
            line_color_id=color_id,
            items=[
                SizeLineIn(size_code="L", hands=1, qty_per_hand=10),
                SizeLineIn(size_code="XL", hands=1, qty_per_hand=10),
                SizeLineIn(size_code="XXL", hands=1, qty_per_hand=10),
            ],
        ),
        OPERATOR_ID,
        _CTX,
    )
    await db_session.flush()
    rows = list(
        (
            await db_session.execute(
                select(CuttingOrderSizeLine.size_line_no)
                .where(
                    CuttingOrderSizeLine.line_color_id == color_id,
                    # ⚠️ 必须滤掉软删：被替换掉的旧行 line_color_id 相同，不滤会数出 4 行
                    CuttingOrderSizeLine.deleted_at.is_(None),
                )
                .order_by(CuttingOrderSizeLine.size_line_no)
            )
        ).scalars()
    )
    assert rows == [1, 2, 3], f"行号应由服务端顺序分配，实际 {rows}"


async def test_size_line_no_uses_max_not_count(db_session, cutting_world):
    """TC-C01b2-09：行号分配用 **max + 1**，不是 ``count + 1``。

    ⚠️ **场景要挑对**，否则这条断言证明不了任何事。因为迁移 0011 把唯一键改成了
    部分索引（``WHERE deleted_at IS NULL``），**软删的行不再占号**，所以
    「删掉中间几行」那种场景下 ``count + 1`` 恰好也是对的。

    真正会撞号的是**存活行中间有洞**：存活 ``{1, 3}``（2 被删）时
    ``count = 2`` → 下一个发 ``3``，**与存活的 3 撞**；
    ``max + 1 = 4`` 才对。

    ⚠️ 撞了之后的报错是 ``duplicate key value violates unique constraint
    "uq_cutting_size_lines"`` —— 完全看不出根因是「行号分配算法不对」。
    """
    order = await CuttingOrderService(db_session).create(
        payload(
            cutting_world,
            lines=[
                line_in(
                    cutting_world,
                    output_qty="220.000",
                    colors=[
                        LineColorIn(
                            color_code="WHT",
                            size_lines=[size_line(1, "L", 1, 10)],
                        )
                    ],
                )
            ],
        ),
        OPERATOR_ID,
    )
    service = CuttingOrderService(db_session)
    color_id = await _color_id(service, order)

    async def replace(items):
        fresh = await service.get(order.id, _CTX)
        await service.put_size_lines(
            order.id,
            PutSizeLinesIn(version=fresh.version, line_color_id=color_id, items=items),
            OPERATOR_ID,
            _CTX,
        )
        await db_session.flush()

    def row(no: int | None, code: str) -> SizeLineIn:
        return SizeLineIn(size_line_no=no, size_code=code, hands=1, qty_per_hand=10)

    # 建 3 行（行号 1/2/3）
    await replace([row(1, "L"), row(2, "XL"), row(3, "XXL")])
    # 全量替换成 1/3 两行 —— **2 被软删**，存活 {1, 3}（count=2, max=3）
    await replace([row(1, "L"), row(3, "XXL")])

    live = sorted(
        (
            await db_session.execute(
                select(CuttingOrderSizeLine.size_line_no).where(
                    CuttingOrderSizeLine.line_color_id == color_id,
                    CuttingOrderSizeLine.deleted_at.is_(None),
                )
            )
        ).scalars()
    )
    assert live == [1, 3], f"前置条件：存活 {{1, 3}}（2 已软删），实际 {live}"

    # 再加一行（行号省略 → 服务端分配）：必须是 4 = max + 1，而不是 3（撞存活行）
    await replace([row(1, "L"), row(3, "XXL"), row(None, "XXXL")])
    live = sorted(
        (
            await db_session.execute(
                select(CuttingOrderSizeLine.size_line_no).where(
                    CuttingOrderSizeLine.line_color_id == color_id,
                    CuttingOrderSizeLine.deleted_at.is_(None),
                )
            )
        ).scalars()
    )
    assert live == [1, 3, 4], f"新行应拿到 4（max+1），实际 {live}"


# ------------------------------------------------------------------ TC-C01b2-10 / 11 / 12


async def test_suggest_raises_when_no_ratio(db_session, cutting_world):
    """TC-C01b2-10：该 ``(款号, 颜色)`` **完全没有**比例 → ``20006``（C19①）。"""
    order = await _create(db_session, cutting_world)
    with pytest.raises(BusinessError) as caught:
        await CuttingOrderService(db_session).suggest_lines(
            order.id, cutting_world["style"].style_no, "WHT", OPERATOR_ID, _CTX
        )
    assert caught.value.code is ErrorCode.SIZE_RATIO_INCOMPLETE


async def test_suggest_tolerates_partial_ratio(db_session, cutting_world):
    """TC-C01b2-11：**部分**缺配 → **不拦**，只返回 ``missing_size_codes``（C19②）。

    ⚠️ 这是「假失败比没守卫更坏」的正例：比例缺一个是**主数据维护问题**，
    拦住它会让裁剪员没法裁这一床布，而带出来的建议仍然可用。
    """
    style_no = cutting_world["style"].style_no
    await add_style_sizes(db_session, cutting_world["style"], ["L", "XL", "XXL"])
    await add_ratio(db_session, cutting_world["style"], "WHT", {"L": "1", "XL": "2"})
    order = await _create(db_session, cutting_world)

    out = await CuttingOrderService(db_session).suggest_lines(
        order.id, style_no, "WHT", OPERATOR_ID, _CTX
    )
    assert [item.size_code for item in out.items] == ["L", "XL"]
    assert out.missing_size_codes == ["XXL"], "缺配的尺码要提示，但**不拦**"
    assert out.hands_total == "3"


async def test_suggest_rejects_unknown_size_in_ratio(db_session, cutting_world):
    """TC-C01b2-12：比例里有款号**未定义**的尺码 → ``20007``（C19③，主数据脏）。"""
    style_no = cutting_world["style"].style_no
    await add_style_sizes(db_session, cutting_world["style"], ["L"])
    await add_ratio(db_session, cutting_world["style"], "WHT", {"L": "1", "3XL": "1"})
    order = await _create(db_session, cutting_world)

    with pytest.raises(BusinessError) as caught:
        await CuttingOrderService(db_session).suggest_lines(
            order.id, style_no, "WHT", OPERATOR_ID, _CTX
        )
    assert caught.value.code is ErrorCode.SIZE_RATIO_SIZE_MISMATCH
    assert caught.value.details is not None
    assert caught.value.details["unknown_size_codes"] == ["3XL"]


# ------------------------------------------------------------------ TC-C01b2-13


async def test_suggest_writes_snapshot_and_never_touches_master(db_session, cutting_world):
    """TC-C01b2-13：带出时写 ``ratio_snapshot``，且**比例主数据零变更**（C29）。"""
    from app.modules.base.models import StyleColorSizeRatio

    style_no = cutting_world["style"].style_no
    await add_style_sizes(db_session, cutting_world["style"], ["L", "XL"])
    await add_ratio(db_session, cutting_world["style"], "WHT", {"L": "1", "XL": "2"})
    order = await _create(db_session, cutting_world)

    before = (
        await db_session.execute(select(func.count()).select_from(StyleColorSizeRatio))
    ).scalar_one()
    out = await CuttingOrderService(db_session).suggest_lines(
        order.id, style_no, "WHT", OPERATOR_ID, _CTX
    )
    await db_session.flush()
    after = (
        await db_session.execute(select(func.count()).select_from(StyleColorSizeRatio))
    ).scalar_one()
    assert after == before, "带出建议碰了 style_color_size_ratios —— C29 铁律"

    assert out.ratio_snapshot == {"L": "1", "XL": "2"}
    loaded = await CuttingOrderService(db_session).get(order.id, _CTX)
    assert loaded.lines[0].colors[0].ratio_snapshot == {"L": "1", "XL": "2"}, "快照要落库"


# ------------------------------------------------------------------ TC-C01b2-14


async def test_switch_mode_requires_confirm(db_session, cutting_world):
    """TC-C01b2-14（未确认侧）：从 ``MASTER`` 切走**必须** ``confirm=true``（C27）。"""
    order = await _create(db_session, cutting_world)
    service = CuttingOrderService(db_session)
    color_id = await _color_id(service, order)
    with pytest.raises(BusinessError) as caught:
        await service.switch_entry_mode(
            order.id,
            EntryModeSwitchIn(
                version=order.version,
                line_color_id=color_id,
                mode="MANUAL",
                confirm=False,
            ),
            OPERATOR_ID,
            _CTX,
        )
    assert caught.value.code is ErrorCode.PARAM_INVALID
    assert "二次确认" in caught.value.message


async def test_switch_mode_clears_size_lines_and_leaves_trace(db_session, cutting_world):
    """TC-C01b2-14（已确认侧）：切走清该颜色的尺码明细 + 写 ``entry_mode_changed_*``。"""
    from datetime import datetime as _dt

    from app.modules.cutting.models import CuttingEntryMode

    order = await _create(db_session, cutting_world)
    service = CuttingOrderService(db_session)
    color_id = await _color_id(service, order)

    color = await service.switch_entry_mode(
        order.id,
        EntryModeSwitchIn(
            version=order.version,
            line_color_id=color_id,
            mode=CuttingEntryMode.MANUAL,
            confirm=True,
        ),
        OPERATOR_ID,
        _CTX,
    )
    await db_session.flush()

    assert color.entry_mode is CuttingEntryMode.MANUAL
    assert isinstance(color.entry_mode_changed_at, _dt)
    assert color.entry_mode_changed_by == OPERATOR_ID
    # ⚠️ hands 是 CHECK > 0 的 NOT NULL，所以「清 hands」只能靠**删行**实现
    live = (
        await db_session.execute(
            select(func.count())
            .select_from(CuttingOrderSizeLine)
            .where(
                CuttingOrderSizeLine.line_color_id == color_id,
                CuttingOrderSizeLine.deleted_at.is_(None),
            )
        )
    ).scalar_one()
    assert live == 0, "切走 MASTER 必须清掉该颜色的尺码明细（前端已二次确认）"
    assert color.ratio_snapshot is None, "切走时比例快照失效 —— 新模式下不再按比例带出"


async def test_switch_mode_rejects_same_mode(db_session, cutting_world):
    """切到**当前已是**的模式 → 拒（否则会白清一遍手数）。"""
    order = await _create(db_session, cutting_world)
    service = CuttingOrderService(db_session)
    color_id = await _color_id(service, order)
    with pytest.raises(BusinessError) as caught:
        await service.switch_entry_mode(
            order.id,
            EntryModeSwitchIn(
                version=order.version,
                line_color_id=color_id,
                mode="MASTER",
                confirm=True,
            ),
            OPERATOR_ID,
            _CTX,
        )
    assert caught.value.code is ErrorCode.PARAM_INVALID


# ------------------------------------------------------------------ TC-C01b2-15


async def test_fabric_qty_exceeding_available_is_rejected(db_session, cutting_world):
    """TC-C01b2-15：``fabric_qty > available_qty`` → ``40006``（C38 / ADR-0022）。

    ⚠️ **草稿态就拦**，不留到审核：让用户填完一整张单才发现布不够，
    是最难解释的一种失败。而审核时要拦的是「累计占用」（C36），那是 b-3 的活。
    """
    order = await _create(db_session, cutting_world)
    with pytest.raises(BusinessError) as caught:
        await CuttingOrderService(db_session).put_lines(
            order.id,
            PutLinesIn(
                version=order.version,
                items=[line_in(cutting_world, fabric_qty="9999.000")],
            ),
            OPERATOR_ID,
            _CTX,
        )
    assert caught.value.code is ErrorCode.BATCH_STOCK_INSUFFICIENT
    assert caught.value.details is not None


async def test_fabric_qty_at_available_is_allowed(db_session, cutting_world):
    """**刚好等于**可用量要放行 —— 边界是 ``>`` 不是 ``>=``。"""
    order = await _create(db_session, cutting_world)
    cutting_world["stock"].stock_qty = Decimal("100.000")
    await db_session.flush()
    updated = await CuttingOrderService(db_session).put_lines(
        order.id,
        PutLinesIn(
            version=order.version,
            items=[line_in(cutting_world, fabric_qty="100.000")],
        ),
        OPERATOR_ID,
        _CTX,
    )
    assert updated.fabric_qty == Decimal("100.000")


# ------------------------------------------------------------------ TC-C01b2-16


async def test_concurrent_patch_only_one_wins(cutting_world_persisted):
    """TC-C01b2-16：**并发**改同一单 → 只有一个成功，另一个 ``10003``。

    ⚠️ ``docs/10 §5.4``：必须 ``asyncio.gather`` + **独立 session**，
    共享一个 session 测不出来（同一连接上的语句会被 PG 串行化）。
    ⚠️ 兜底是「乐观锁 + 业务层冲突处理」，断言里同时查了 ``version`` 只 +1。
    ⚠️ 单据用 ``cutting_world_persisted``（**真提交**版）而不是 ``cutting_world``：
    ``db_session`` 的外层事务没提交之前，并发任务用自己的连接**看不见那张单**，
    症状是 5 个任务全部报「裁剪单不存在」—— 完全看不出根因是夹具的事务隔离。
    """
    world = cutting_world_persisted
    engine = create_async_engine(_db_url(), pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        created = await CuttingOrderService(session).create(payload(world), OPERATOR_ID)
        await session.commit()
        order_id, version = created.id, created.version

    async def patch_once(index: int) -> str:
        async with factory() as session:
            try:
                await CuttingOrderService(session).patch(
                    order_id,
                    CuttingOrderPatchIn(version=version, remark=f"并发 {index}"),
                    OPERATOR_ID,
                    _CTX,
                )
                return "ok"
            except BusinessError as exc:
                return exc.code.name

    try:
        results = await asyncio.gather(*(patch_once(i) for i in range(5)))
        # ⚠️ **先断言再清理**：清理是真删，所以断言必须在它之前读完数据。
        #    反过来的话 `session.get` 返回 None，而报错是 `assert None is not None`
        #    —— 完全看不出根因是「清理跑早了」
        async with factory() as session:
            fresh = await session.get(CuttingOrder, order_id)
            assert fresh is not None, "并发 patch 之后单据应该还在"
            final_version, remark = fresh.version, str(fresh.remark)
    finally:
        await _purge_world(world, order_id)
        await engine.dispose()

    assert results.count("ok") == 1, f"只应有一个成功，实际 {results}"
    assert results.count("OPTIMISTIC_LOCK_CONFLICT") == 4, f"其余应报 10003，实际 {results}"
    assert final_version == version + 1, "version 只应 +1 一次"
    assert remark.startswith("并发")


def _db_url() -> str:
    """应用账号连接串（并发任务用它，验的是**应用侧**的乐观锁）。"""
    import os

    return os.environ["DATABASE_URL"]


async def _purge_world(world, order_id) -> None:
    """**物理删除**真提交的世界（单据 + 款号 + 物料 + 布批 + 车间）。

    ⚠️ **为什么必须物理删，而不是软删**（踩过一次）：软删的行**仍然存在**，
    而 ``test_document_logs.py::test_suggested_no_returns_a_number_without_creating_a_style``
    断言的是 ``select(Style) == []`` —— 它**不过滤软删**。所以软删清理之后，
    那个用例报 ``assert 11 == []``，而报错完全看不出「有另一个用例留下了行」。

    ⚠️ **为什么能用物理删**（这是本仓的既定做法，见 ``conftest.ddl_session`` 的说明）：
    清理走**迁移账号**（``erp_ddl``）。``erp_app`` 对这些表**没有 DELETE 权限**
    （``04 §6.2.1``）—— 而这正是本项目要守的边界，用迁移账号清理**不破坏**它：
    被测的是「应用账号不能硬删」，不是「这张表永远不能被删」。
    真正验那条边界的用例另有（``test_migrations.py`` / ``test_cutting_tables.py``）。

    ⚠️ 顺序：**先子后父**（同 ``04 §7`` 的锁序）。
    """
    engine = create_async_engine(os.environ["DATABASE_URL_MIGRATION"], pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            for statement in (
                # ⚠️ 三条裁剪表的删除**按 stock_id 也带一遍**，不只是本单：
                #    上一轮跑崩时可能留下了「引用同一个布批的孤儿行」，
                #    只按 doc_id 删会撞 `fk_cutting_order_lines_stock`
                "DELETE FROM cutting_order_size_lines WHERE line_id IN "
                "(SELECT id FROM cutting_order_lines "
                " WHERE doc_id = :order OR stock_id = :stock)",
                "DELETE FROM cutting_order_line_colors WHERE line_id IN "
                "(SELECT id FROM cutting_order_lines "
                " WHERE doc_id = :order OR stock_id = :stock)",
                "DELETE FROM cutting_order_lines WHERE doc_id = :order OR stock_id = :stock",
                "DELETE FROM cutting_orders WHERE id = :order",
                "DELETE FROM material_stocks WHERE material_id = :material",
                "DELETE FROM materials WHERE id = :material",
                "DELETE FROM style_sizes WHERE style_id = :style",
                "DELETE FROM styles WHERE id = :style",
                "DELETE FROM workshops WHERE id = :workshop",
            ):
                await session.execute(
                    text(statement),
                    {
                        "order": order_id,
                        "stock": world["stock"],
                        "style": world["style"],
                        "material": world["material"],
                        "workshop": world["workshop"],
                    },
                )
            await session.commit()
    finally:
        await engine.dispose()


# ------------------------------------------------------------------ 数据范围


async def test_write_apis_reject_out_of_scope(db_session, cutting_world):
    """三个写接口都要过数据范围 —— 漏一个就是越权写。"""
    order = await _create(db_session, cutting_world)
    outsider = AuthContext(
        user_id=OPERATOR_ID,
        name="别的车间",
        employee_no="A002",
        workshop_id=None,
        group_no=None,
        permissions=frozenset(),
        data_scope=DataScope.WORKSHOP,
        allowed_workshop_ids=frozenset(),
    )
    service = CuttingOrderService(db_session)
    calls = (
        lambda: service.patch(
            order.id, CuttingOrderPatchIn(version=order.version, remark="x"), OPERATOR_ID, outsider
        ),
        lambda: service.put_lines(
            order.id, PutLinesIn(version=order.version, items=[]), OPERATOR_ID, outsider
        ),
        lambda: service.delete(order.id, order.version, OPERATOR_ID, outsider),
        lambda: service.suggest_lines(
            order.id, cutting_world["style"].style_no, "WHT", OPERATOR_ID, outsider
        ),
    )
    for call in calls:
        with pytest.raises(BusinessError) as caught:
            await call()
        assert caught.value.code is ErrorCode.DATA_SCOPE_DENIED


# ------------------------------------------------------------------ 补：put_colors


async def test_put_colors_replaces_only_that_line(db_session, cutting_world):
    """``PUT /lines/{line_id}/colors`` 只动**那一行**的颜色。"""
    order = await CuttingOrderService(db_session).create(
        payload(
            cutting_world,
            lines=[
                line_in(
                    cutting_world,
                    line_no=1,
                    output_qty="220.000",
                    colors=[LineColorIn(color_code="WHT", size_lines=[size_line(1, "L", 2, 60)])],
                ),
                line_in(
                    cutting_world,
                    line_no=2,
                    output_qty="100.000",
                    colors=[LineColorIn(color_code="BLK", size_lines=[size_line(1, "L", 1, 50)])],
                ),
            ],
        ),
        OPERATOR_ID,
    )
    service = CuttingOrderService(db_session)
    loaded = await service.get(order.id, _CTX)
    line1 = loaded.lines[0]

    updated = await service.put_colors(
        order.id,
        line1.id,
        PutColorsIn(
            version=order.version,
            items=[
                LineColorIn(color_code="WHT", size_lines=[size_line(1, "L", 3, 60)]),
                LineColorIn(color_code="BLK", size_lines=[size_line(1, "L", 1, 40)]),
            ],
        ),
        OPERATOR_ID,
        _CTX,
    )
    await db_session.flush()
    assert {c.color_code for c in updated.lines[0].colors} == {"WHT", "BLK"}
    assert updated.lines[0].colors[0].output_qty_total + updated.lines[0].colors[
        1
    ].output_qty_total == Decimal("220.000"), "180 + 40 = 220"
    assert updated.lines[0].balance_qty == Decimal("0.000")
    # 第 2 行不受影响
    assert updated.lines[1].output_qty == Decimal("100.000")
