"""打菲标签**数据导出**与**打印留痕**的测试（T-BUND-006）。

TC-LB-01 导出含「第 N 手 / 共 M 手」+ 该手件数；TC-LB-02 落库 ``hands_seq``；
TC-LB-03 缺 ``hands_seq`` → ``10002``；TC-LB-04 重打新增行 + 写日志；
TC-LB-05 字段完整性（款号 / 色 / 码 / 工序 / 码内容 = 码本身）；TC-LB-06 数据范围。

⚠️ 「共 M 手」钉的是**同尺码**总手数（Q-B13）：拿全单总手数会让「XL 第 1 手」印成
「第 1 手 / 共 6 手」，而那 6 手里有 5 手属于别的尺码 —— 员工按标签认领时会数错，
所以那条断言必须**按尺码分组**（``test_export_labels_total_is_per_size``）。
⚠️ ``print_seq`` 的两处（重打必带、不得复用）最容易悄悄退化：``04 §7.16`` **没有**唯一
索引兜底，重入幂等靠 ``Idempotency-Key``（T-BUND-007 接线），service 侧靠锁表头 + 查后插。
"""

import csv
from decimal import Decimal
from io import StringIO
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy import select

from app.common.enums import DataScope, DocumentAction, LabelExportFormat
from app.core.errors import BusinessError, ErrorCode
from app.modules.bundling.label_repository import existing_print_seq_hands
from app.modules.bundling.models import Bundle, BundleLabelPrint, BundleStatus, BundlingOrder
from app.modules.bundling.schemas import LabelPrintIn
from app.modules.bundling.service import BundlingOrderService
from app.modules.bundling.service.label_guard import (
    MAX_LABEL_HANDS,
    assert_hand_range,
    assert_within_max_hands,
)
from tests.factories.bundling import ctx
from tests.factories.bundling_approve import approve_order, order_logs, prepare_order
from tests.factories.user import OPERATOR_ID, WorkshopFactory

#: 单尺码 3 手 —— 打印用例要「第 3 手」，默认的 1:2:2:1 计划里没有哪一尺码有 3 手。
XL_PLAN: tuple[tuple[str, int], ...] = (("XL", 3),)


# ------------------------------------------------------------------ 助手


async def approved(db_session, world, plan: tuple[tuple[str, int], ...] = XL_PLAN):
    """建一张**已审核**（已出码）的打菲单。"""
    order = await prepare_order(db_session, world, plan)
    return await approve_order(db_session, order)


async def print_rows(db_session, order_id) -> list[BundleLabelPrint]:
    """读本单全部打印留痕（按手号 + 打印时间排序）。"""
    stmt = (
        select(BundleLabelPrint)
        .where(BundleLabelPrint.doc_id == order_id)
        .order_by(BundleLabelPrint.hands_seq, BundleLabelPrint.printed_at)
        .execution_options(populate_existing=True)
    )
    return list((await db_session.scalars(stmt)).all())


async def label_print_qty(db_session, order_id) -> int:
    """重读表头累计打印张数（走 Core UPDATE，必须 ``populate_existing``）。"""
    return int(
        await db_session.scalar(
            select(BundlingOrder.label_print_qty)
            .where(BundlingOrder.id == order_id)
            .execution_options(populate_existing=True)
        )
    )


async def void_hand(db_session, doc_id, hands_seq: int, size_code: str = "XL") -> str:
    """把一手**单码作废**（T-BUND-007 的 ``void_code`` 未上线，这里直接走 ORM 造前置）。

    ⚠️ 用 UPDATE 而不是删行（B10：作废是业务终态不是软删），而 ``bundles`` 是软删表、
    应用账号有 UPDATE 权限，所以这条路在生产真实存在 —— 不是测试专用后门。
    """
    bundle_no = await db_session.scalar(
        select(Bundle.bundle_no).where(
            Bundle.doc_id == doc_id, Bundle.hands == hands_seq, Bundle.size_code == size_code
        )
    )
    await db_session.execute(
        sa.update(Bundle)
        .where(Bundle.doc_id == doc_id, Bundle.hands == hands_seq, Bundle.size_code == size_code)
        .values(status=BundleStatus.VOIDED, void_reason="测试造作废")
    )
    return str(bundle_no)


def register(session, order_id, **kwargs):
    """登记打印（默认：单手第 3 手、印 1 张、首次打印）。"""
    return BundlingOrderService(session).register_print(
        order_id, OPERATOR_ID, ctx(), **{"hands_seq": 3, **kwargs}
    )


# ------------------------------------------------------------------ 导出（只读）
async def test_export_labels_carries_hand_seq_total_and_qty(db_session, bundling_world):
    """TC-LB-01/05：导出含款号 / 色 / 尺码 / 码 / 工序 / 第 N 手 / 共 M 手 / 该手件数。"""
    order = await approved(db_session, bundling_world)
    result = await BundlingOrderService(db_session).export_labels(order.id, ctx())

    assert [item.hands_seq for item in result.items] == [1, 2, 3]
    assert all(item.bundle_qty == Decimal("60") for item in result.items), "该手件数取 bundle_qty"
    assert result.items[0].hands_text == "第 1 手 / 共 3 手"
    first = result.items[0]
    assert (first.style_no, first.color_code, first.size_code) == ("BD-TEST-1", "WHT", "XL")
    assert first.operation_no == "OP01"
    # ADR-0004 + B6：二维码内容 == 条码内容 == bundle_no（纯文本，码里不带数量）
    assert first.qr_content == first.barcode_content == first.bundle_no
    assert result.hands_count == 3
    assert result.total_qty == Decimal("180.000")


async def test_export_labels_total_is_per_size(db_session, bundling_world):
    """TC-LB-01（续）：「共 M 手」= **同尺码**总手数（Q-B13），不是全单总手数。"""
    order = await approved(db_session, bundling_world, (("L", 1), ("XL", 3)))
    result = await BundlingOrderService(db_session).export_labels(order.id, ctx())

    totals = {(item.size_code, item.hands_seq): item.hands_total_of_size for item in result.items}
    assert totals == {("L", 1): 1, ("XL", 1): 3, ("XL", 2): 3, ("XL", 3): 3}
    assert result.hands_count == 4


async def test_export_labels_csv_rows_match_hands(db_session, bundling_world):
    """TC-LB-01（续）：``format=csv`` 的**数据行数 = 手数**（外加 1 行中文表头）。"""
    order = await approved(db_session, bundling_world)
    service = BundlingOrderService(db_session)
    result = await service.export_labels(order.id, ctx(), export_format=LabelExportFormat.CSV)

    assert result.csv_text is not None
    rows = list(csv.reader(StringIO(result.csv_text)))
    assert len(rows) == 4, f"表头 1 行 + 数据 3 行，实际 {len(rows)} 行"
    assert rows[0][0] == "款号" and "第N手" in rows[0]
    assert [row[3] for row in rows[1:]] == ["1", "2", "3"], "第 N 手逐手递增"
    assert rows[1][5] == "60.000", "该手件数按原精度出参（05 §3）"

    # 不传 format 时不产出 CSV（大单白渲染一遍是白干活）
    assert (await service.export_labels(order.id, ctx())).csv_text is None


async def test_export_labels_filters_by_size_and_range(db_session, bundling_world):
    """TC-LB-01（续）：按尺码 + 手号区间过滤；手号区间在**每个尺码各自**编号。"""
    order = await approved(db_session, bundling_world, (("L", 1), ("XL", 3)))
    service = BundlingOrderService(db_session)

    only_l = await service.export_labels(order.id, ctx(), size_code="L")
    assert [(i.size_code, i.hands_seq, i.hands_total_of_size) for i in only_l.items] == [
        ("L", 1, 1)
    ]

    hand2 = await service.export_labels(order.id, ctx(), from_hands=2, to_hands=2)
    assert [(i.size_code, i.hands_seq) for i in hand2.items] == [("XL", 2)], (
        "L 只有 1 手，第 2 手区间在 L 上无码；不能把别的尺码的手凑进来"
    )

    tail = await service.export_labels(order.id, ctx(), size_code="XL", from_hands=2, to_hands=3)
    assert [i.hands_seq for i in tail.items] == [2, 3]


async def test_export_labels_rejects_inverted_range(db_session, bundling_world):
    """TC-LB-01（续）：``from_hands > to_hands`` → ``10001``（而不是静默返回空）。"""
    order = await approved(db_session, bundling_world)
    with pytest.raises(BusinessError) as caught:
        await BundlingOrderService(db_session).export_labels(
            order.id, ctx(), from_hands=3, to_hands=1
        )
    assert caught.value.code is ErrorCode.PARAM_INVALID


async def test_export_labels_requires_approved_order(db_session, bundling_world):
    """草稿 / 反审核后的单**不能**出标签：码还不存在或已全 ``VOIDED``，印了就是废标签。"""
    order = await prepare_order(db_session, bundling_world, XL_PLAN)
    with pytest.raises(BusinessError) as caught:
        await BundlingOrderService(db_session).export_labels(order.id, ctx())
    assert caught.value.code is ErrorCode.CUTTING_STATUS_NOT_ALLOWED


async def test_export_labels_skips_voided_hands(db_session, bundling_world):
    """单码作废后该手**不再**出现在导出里（作废是业务终态，标签作废同步）。"""
    order = await approved(db_session, bundling_world)
    await void_hand(db_session, order.id, hands_seq=2)
    result = await BundlingOrderService(db_session).export_labels(order.id, ctx())
    assert [item.hands_seq for item in result.items] == [1, 3]
    assert result.hands_count == 2


async def test_export_labels_enforces_data_scope(db_session, bundling_world):
    """TC-LB-06：他车间主管导出 → ``12002``；不存在的单 → ``30001``（不同的码）。"""
    order = await approved(db_session, bundling_world)
    service = BundlingOrderService(db_session)
    other = await WorkshopFactory.create(db_session)

    with pytest.raises(BusinessError) as denied:
        await service.export_labels(order.id, ctx(other.id, DataScope.WORKSHOP))
    assert denied.value.code is ErrorCode.DATA_SCOPE_DENIED

    with pytest.raises(BusinessError) as missing:
        await service.export_labels(uuid4(), ctx())
    assert missing.value.code is ErrorCode.CUTTING_STATUS_NOT_ALLOWED


# ------------------------------------------------------------------ 打印留痕（只追加）


async def test_register_print_appends_row_and_bumps_qty(db_session, bundling_world):
    """TC-LB-02：落库 ``hands_seq=3`` + 「共 3 手」快照，表头累计 +1。"""
    order = await approved(db_session, bundling_world)
    result = await register(db_session, order.id, hands_total_of_size=3)

    rows = await print_rows(db_session, order.id)
    assert len(rows) == 1
    row = rows[0]
    assert (row.hands_seq, row.hands_total_of_size) == (3, 3)
    assert (row.printed_qty, row.is_reprint, row.print_seq) == (1, False, None)
    assert row.printed_by == OPERATOR_ID and row.printed_at is not None
    assert list(result.print_ids) == [row.id] and result.printed_count == 1
    assert await label_print_qty(db_session, order.id) == 1


async def test_register_print_requires_hands_seq(db_session, bundling_world):
    """TC-LB-03：缺 ``hands_seq`` → ``10002``（03 §6 明列，不是 10001）。"""
    order = await approved(db_session, bundling_world)
    with pytest.raises(BusinessError) as caught:
        await BundlingOrderService(db_session).register_print(
            order.id, OPERATOR_ID, ctx(), from_hands=1, to_hands=1
        )
    assert caught.value.code is ErrorCode.MISSING_BUSINESS_PARAM
    assert await print_rows(db_session, order.id) == []


async def test_register_print_requires_print_seq_when_reprinting(db_session, bundling_world):
    """重打必须带 ``print_seq``：没有它，重复请求无法与首次打印区分、去重无从下手。"""
    order = await approved(db_session, bundling_world)
    with pytest.raises(BusinessError) as caught:
        await register(db_session, order.id, is_reprint=True)
    assert caught.value.code is ErrorCode.MISSING_BUSINESS_PARAM


async def test_register_print_rejects_conflicting_hands_seq(db_session, bundling_world):
    """``hands_seq``（本次第 N 手）与 ``from_hands``（区间起点）矛盾 → ``10001``。"""
    order = await approved(db_session, bundling_world)
    with pytest.raises(BusinessError) as caught:
        await register(db_session, order.id, hands_seq=3, from_hands=1, to_hands=2)
    assert caught.value.code is ErrorCode.PARAM_INVALID


async def test_register_print_rejects_stale_hands_total(db_session, bundling_world):
    """「共 M 手」以库内权威值为准：前端报了一个对不上的数 → ``10001`` 而不是照抄。"""
    order = await approved(db_session, bundling_world)
    with pytest.raises(BusinessError) as caught:
        await register(db_session, order.id, hands_total_of_size=99)
    assert caught.value.code is ErrorCode.PARAM_INVALID


async def test_register_print_rejects_unknown_hand(db_session, bundling_world):
    """区间内没有码 → ``31001``（码不存在），而**有码但已作废** → ``31002``。"""
    order = await approved(db_session, bundling_world)
    with pytest.raises(BusinessError) as caught:
        await register(db_session, order.id, hands_seq=9)
    assert caught.value.code is ErrorCode.BUNDLE_NO_NOT_FOUND

    await void_hand(db_session, order.id, hands_seq=3)
    with pytest.raises(BusinessError) as caught:
        await register(db_session, order.id, hands_seq=3)
    assert caught.value.code is ErrorCode.BUNDLE_ALREADY_VOIDED
    assert await print_rows(db_session, order.id) == []


async def test_register_print_requires_approved_order(db_session, bundling_world):
    """草稿单不能登记打印痕迹（此时码还不存在，留痕会变成永远查无此码的假记录）。"""
    order = await prepare_order(db_session, bundling_world, XL_PLAN)
    with pytest.raises(BusinessError) as caught:
        await register(db_session, order.id, hands_seq=1)
    assert caught.value.code is ErrorCode.CUTTING_STATUS_NOT_ALLOWED


async def test_reprint_appends_row_and_writes_log(db_session, bundling_world):
    """TC-LB-04：重打**新增一行**、原行一字不改、``label_print_qty`` 累加、日志有记录。"""
    order = await approved(db_session, bundling_world)
    await register(db_session, order.id, hands_total_of_size=3)
    result = await register(
        db_session, order.id, hands_total_of_size=3, is_reprint=True, print_seq=2, printed_qty=2
    )

    rows = await print_rows(db_session, order.id)
    assert len(rows) == 2, "重打是新增一行（append-only），不是改首行"
    assert (rows[0].is_reprint, rows[0].print_seq, rows[0].printed_qty) == (False, None, 1)
    assert (rows[1].is_reprint, rows[1].print_seq, rows[1].printed_qty) == (True, 2, 2)
    assert result.printed_count == 2
    assert await label_print_qty(db_session, order.id) == 3, "首次 1 张 + 重打 2 张 = 3"

    actions = [log.action for log in await order_logs(db_session, order.id)]
    assert DocumentAction.PRINT.value in actions
    assert DocumentAction.REPRINT.value in actions
    last = (await order_logs(db_session, order.id))[-1]
    assert last.changed_fields["print_seq"] == 2
    assert last.changed_fields["label_print_qty"] == 3


async def test_duplicate_print_seq_is_rejected(db_session, bundling_world):
    """同一 ``print_seq`` 重复登记 → ``10008``；留痕行数与累计张数都不许动。"""
    order = await approved(db_session, bundling_world)
    await register(db_session, order.id, hands_total_of_size=3, is_reprint=True, print_seq=2)

    with pytest.raises(BusinessError) as caught:
        await register(db_session, order.id, hands_total_of_size=3, is_reprint=True, print_seq=2)
    assert caught.value.code is ErrorCode.ILLEGAL_OPERATION
    assert len(await print_rows(db_session, order.id)) == 1
    assert await label_print_qty(db_session, order.id) == 1


async def test_register_print_covers_hand_range(db_session, bundling_world):
    """批量登记：``from_hands..to_hands`` 逐手各留一行（哪一手印过要能逐行追溯）。"""
    order = await approved(db_session, bundling_world)
    result = await register(
        db_session, order.id, hands_seq=1, from_hands=1, to_hands=3, hands_total_of_size=3
    )
    assert [row.hands_seq for row in await print_rows(db_session, order.id)] == [1, 2, 3]
    assert len(result.print_ids) == 3
    assert result.printed_count == 3
    assert await label_print_qty(db_session, order.id) == 3


async def test_app_role_cannot_update_or_delete_label_prints(app_database_url):
    """TC-BL-08 同款：应用账号**不能** UPDATE / DELETE 打印留痕（04 §6.2.1）。

    ⚠️ 「只追加」这件事 ORM 里看不出来 —— 权限边界只有真去试才能确认。留痕一旦可被
    物理删除，「这手标签到底印过几次」就再也无从追溯，而它正是重打的对账依据。
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(app_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            for statement in (
                "UPDATE bundle_label_prints SET printed_qty = 0",
                "DELETE FROM bundle_label_prints",
            ):
                with pytest.raises((sa.exc.ProgrammingError, sa.exc.DBAPIError)) as caught:
                    await session.execute(sa.text(statement))
                await session.rollback()
                assert "permission denied" in str(caught.value).lower(), (
                    f"应用账号竟然能对打印留痕执行 {statement!r}（04 §6.2.1）"
                )
    finally:
        await engine.dispose()


async def test_register_print_validates_qty_and_unknown_order(db_session, bundling_world):
    """打印张数非正 / 单不存在 → ``10001`` / ``30001``（不能静默成功或当成空打印）。"""
    order = await approved(db_session, bundling_world)
    with pytest.raises(BusinessError) as bad_qty:
        await register(db_session, order.id, printed_qty=0)
    assert bad_qty.value.code is ErrorCode.PARAM_INVALID

    with pytest.raises(BusinessError) as missing:
        await register(db_session, uuid4())
    assert missing.value.code is ErrorCode.CUTTING_STATUS_NOT_ALLOWED


async def test_register_print_falls_back_to_authoritative_hands_total(db_session, bundling_world):
    """不传「共 M 手」也能登记：落库取**库内权威值**，不是留空（B15 要能反查标签）。"""
    order = await approved(db_session, bundling_world)
    await register(db_session, order.id)
    assert (await print_rows(db_session, order.id))[0].hands_total_of_size == 3


async def test_print_seq_lookup_tolerates_empty_selection(db_session) -> None:
    """空码集合不查库（SQL 里 ``IN ()`` 的渲染依赖版本细节，显式短路更稳）。"""
    assert (
        await existing_print_seq_hands(db_session, doc_id=uuid4(), bundle_nos=[], print_seq=1) == []
    )


def test_label_guards_reject_bad_inputs() -> None:
    """守卫边角：手号 < 1、单次手数超上限 → ``10001``（05 §2「批量上限」）。"""
    with pytest.raises(BusinessError) as low:
        assert_hand_range(0, 2)
    assert low.value.code is ErrorCode.PARAM_INVALID

    with pytest.raises(BusinessError) as too_many:
        assert_within_max_hands(MAX_LABEL_HANDS + 1)
    assert too_many.value.code is ErrorCode.PARAM_INVALID


# ------------------------------------------------------------------ 入参模型（Router 接线用）


def test_label_print_in_rejects_unknown_field_and_defaults() -> None:
    """03 §2：入参 ``extra="forbid"``；``hands_seq`` 可缺（缺了由 service 报 10002）。"""
    with pytest.raises(ValueError):
        LabelPrintIn(hands_seq=1, printed_batch=3)  # type: ignore[call-arg]

    payload = LabelPrintIn(hands_seq=2, size_code="XL")
    assert (payload.printed_qty, payload.is_reprint, payload.print_seq) == (1, False, None)
    assert payload.from_hands is None and payload.to_hands is None
