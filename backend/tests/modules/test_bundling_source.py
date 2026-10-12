"""新建打菲单的**来源选择**（候选裁剪单 + 来源明细行）。

覆盖 modules/03 §5.1 第 2 步与 B8：

- TC-SRC-01 候选单**只列** ``APPROVED``（``DRAFT`` / ``CANCELLED`` 不出现）
- TC-SRC-02 款号不匹配的裁剪单不出现
- TC-SRC-03 数据范围：换车间看不到别人的单（07 §3.2 铁律 1）
- TC-SRC-04 来源明细行按裁剪单 + 色码返回，且带余量
- TC-SRC-05 换一个色码 → 没有该色的尺码明细行（空列表，不是报错）
- TC-SRC-06 余量为 0 的尺码**仍在列表里**（主管要看见「一件都打不了」）
"""

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.common.enums import DataScope, DocumentStatus
from app.core.numbering import DOC_PREFIX_CUTTING, take_doc_no
from app.modules.bundling.service.source import SourceService
from app.modules.cutting.models import CuttingOrder, CuttingOutput
from tests.factories.bundling import DOC_DATE, build_world, ctx


@pytest.fixture
async def world(db_session):
    return await build_world(db_session)


def _service(db_session, workshop_id=None, scope=DataScope.FACTORY):
    return SourceService(db_session, ctx(workshop_id=workshop_id, scope=scope))


async def _make_other_status_order(db_session, world, status: DocumentStatus) -> CuttingOrder:
    """造一张**非** ``APPROVED`` 的裁剪单（同款同车间），用来验证候选列表会把它滤掉。"""
    doc_no = await take_doc_no(db_session, prefix=DOC_PREFIX_CUTTING, doc_date=DOC_DATE)
    # ⚠️ 必填字段与 `make_approved_cutting_order` 保持一致 ——
    # 手搓模型时漏一个 NOT NULL 就会报 IntegrityError，而报错指向数据库约束，
    # 离「我少写了哪个字段」十万八千里。这里对齐工厂的字段清单。
    order = CuttingOrder(
        id=uuid4(),
        doc_no=doc_no,
        workshop_id=world["workshop"].id,
        style_id=world["style"].id,
        style_no=world["style"].style_no,
        color_codes="WHT",
        doc_date=DOC_DATE,
        delivery_date=DOC_DATE,
        ply_count=100,
        entry_mode_default="MASTER",
        status=status,
        remark_source="测试",
        created_by=ctx().user_id,
        updated_by=ctx().user_id,
    )
    db_session.add(order)
    await db_session.flush()
    return order


# ------------------------------------------------------------------ 候选裁剪单


async def test_source_orders_only_list_approved(db_session, world):
    """TC-SRC-01 只列 APPROVED。"""
    service = _service(db_session)
    rows = await service.available_orders(world["style"].style_no)

    doc_nos = {r.doc_no for r in rows}
    assert world["cutting_order"].doc_no in doc_nos

    # 造一张 DRAFT 与一张 CANCELLED，同款同车间
    for status in (DocumentStatus.DRAFT, DocumentStatus.CANCELLED):
        other = await _make_other_status_order(db_session, world, status)
        assert other.doc_no not in doc_nos


async def test_source_orders_exclude_other_styles(db_session, world):
    """TC-SRC-02 款号不匹配的不出现。"""
    other = await build_world(db_session, style_no="OTHER-STYLE-0001")
    rows = await _service(db_session).available_orders(world["style"].style_no)

    ids = {r.id for r in rows}
    assert other["cutting_order"].id not in ids
    assert all(r.style_no == world["style"].style_no for r in rows)


async def test_source_orders_respect_data_scope(db_session, world):
    """TC-SRC-03 数据范围：车间主管看不到别的车间的单。"""
    other = await build_world(db_session, style_no=world["style"].style_no)
    mine = ctx(workshop_id=world["workshop"].id, scope=DataScope.WORKSHOP)

    visible = {
        r.id
        for r in await SourceService(db_session, mine).available_orders(world["style"].style_no)
    }
    assert world["cutting_order"].id in visible
    assert other["cutting_order"].id not in visible


async def test_source_orders_workshop_filter_cannot_widen_scope(db_session, world):
    """传别的车间 id **不能放大范围**（07 §3.2 铁律 1）。"""
    other = await build_world(db_session, style_no=world["style"].style_no)
    mine = ctx(workshop_id=world["workshop"].id, scope=DataScope.WORKSHOP)

    rows = await SourceService(db_session, mine).available_orders(
        world["style"].style_no, workshop_id=other["workshop"].id
    )
    assert all(r.id != other["cutting_order"].id for r in rows)


# ------------------------------------------------------------------ 来源明细行


async def test_source_lines_by_cutting_order_and_color(db_session, world):
    """TC-SRC-04 来源明细行带尺码、手数与余量。"""
    rows = await _service(db_session).available_lines(
        world["cutting_order"].id, world["style"].style_no, color_code="WHT"
    )

    assert rows, "应至少有一条来源明细行"
    for row in rows:
        assert row.size_code
        assert row.hands >= 1
        assert row.qty_per_hand > 0
        assert row.available_qty >= 0
        assert isinstance(row.available_qty, Decimal)

    ids = {r.cutting_size_line_id for r in rows}
    assert ids <= {line.id for line in world["cutting_size_lines"]}


async def test_source_lines_other_color_returns_empty_not_error(db_session, world):
    """TC-SRC-05 换色码 → 空列表，不是报错。"""
    rows = await _service(db_session).available_lines(
        world["cutting_order"].id, world["style"].style_no, color_code="BLK"
    )
    assert rows == []


async def test_source_lines_keep_zero_available_sizes(db_session, world):
    """TC-SRC-06 余量为 0 的尺码**仍在列表里**（不静默隐藏）。"""
    service = _service(db_session)

    # 先扣光一款色的可用量，再看列表
    rows = await db_session.execute(
        select(CuttingOutput).where(CuttingOutput.style_no == world["style"].style_no)
    )
    for output in rows.scalars().all():
        output.output_qty = "0"
        output.bundled_qty = "0"
        output.reserved_qty = "0"
    await db_session.flush()

    lines = await service.available_lines(
        world["cutting_order"].id, world["style"].style_no, color_code="WHT"
    )
    assert lines, "余量为 0 也要列出来，不能变成空列表"
    assert all(line.available_qty == Decimal("0") for line in lines)


async def test_source_lines_size_code_must_match_size_line(db_session, world):
    """明细行的 size_code 必须来自裁剪尺码明细行本身，不是余量表。"""
    rows = await _service(db_session).available_lines(
        world["cutting_order"].id, world["style"].style_no, color_code="WHT"
    )
    by_id = {line.id: line for line in world["cutting_size_lines"]}
    for row in rows:
        assert by_id[row.cutting_size_line_id].size_code == row.size_code


async def test_source_orders_unknown_style_returns_empty(db_session):
    """款号不存在 → 空列表，不是 500。"""
    assert await _service(db_session).available_orders("NO-SUCH-STYLE") == []
