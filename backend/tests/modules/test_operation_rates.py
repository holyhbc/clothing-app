"""工序单价的设价 / 调价 / 取价测试（T-BASE-002，ADR-0026 三档）。

覆盖：

======================  =====================================================
TC-B14                  调价后**历史区间 ``unit_price`` 未变**（INV-3 / INV-P0-3）
TC-B15                  生效区间重叠 → 冲突码 + ``details`` 含冲突区间；旧行未动
TC-B16                  未来生效价**不影响**当日取价（R19）
TC-B17                  未设价 → ``20004`` + ``details`` 含款号 / 工序
TC-B26                  三档各命中正确 ``rate_source``
TC-B27                  款号有专用价时**分类价不生效**（被遮蔽）
TC-B28                  ``NULLS NOT DISTINCT`` 拦截重复通用价行
TC-B29                  ``style_no`` 与 ``product_category_id`` 同时给 → ``10001``
CC-4                    20 并发调同一三元组同一生效日 → 至多 1 成功
CC-7                    20 并发插"同款号同工序同日"档位 1 → 至多 1 行
Q8                      取价 SQL ``EXPLAIN`` 命中 ``idx_operation_rates_lookup``
R20                     调价必填 ``reason`` → ``10006``
权限                    缺 ``piecework:rate:manage`` → ``12001``
数据范围                跟单（``SELF``）查他人款号单价 → ``12002``
======================  =====================================================

⚠️ 全部断言落在**数字字符串**上（docs/05 §3：金额 / 单价响应一律字符串），
且价格一律写死日期（docs/10 §10：测试里写死日期导致跨月失败）。
"""

import asyncio
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.common.enums import DataScope, RateSource
from app.core.errors import BusinessError, ErrorCode
from app.core.permissions import AuthContext
from app.modules.base.models import (
    Operation,
    OperationRate,
    ProductCategory,
    Style,
)
from app.modules.base.schemas import (
    OperationRateCreate,
)
from app.modules.base.service import (
    RateQuery,
    RateService,
)
from tests.factories.user import OPERATOR_ID, UserFactory

CONCURRENCY = 20

# ⚠️ 固定业务日期：docs/10 §10「测试里写死日期导致跨月失败」
D_START = date(2026, 8, 1)
D_SPLIT = date(2026, 8, 16)
D_NEXT = date(2026, 9, 1)
D_BEFORE = date(2026, 7, 31)
D_MID = date(2026, 8, 10)


def _uniq(prefix: str) -> str:
    return f"{prefix}{uuid4().hex[:6].upper()}"


def _ctx(data_scope: DataScope = DataScope.FACTORY, *, user_id=OPERATOR_ID) -> AuthContext:
    return AuthContext(
        user_id=user_id,
        name="测试操作员",
        employee_no="TEST01",
        workshop_id=None,
        group_no=None,
        permissions=frozenset({"*"}),
        data_scope=data_scope,
    )


def _rates(session) -> RateService:
    return RateService(session, _ctx())


async def _category(session) -> ProductCategory:
    row = ProductCategory(
        code=_uniq("CAT"),
        name="分类",
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    session.add(row)
    await session.flush()
    return row


async def _operation(session, operation_no: str | None = None) -> Operation:
    resolved = operation_no or _uniq("OP")
    row = Operation(
        operation_no=resolved,
        name=f"工序{resolved}",
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    session.add(row)
    await session.flush()
    return row


async def _style(session, *, category: ProductCategory | None = None) -> Style:
    row = Style(
        style_no=_uniq("HB"),
        name="款",
        category_id=(category or await _category(session)).id,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    session.add(row)
    await session.flush()
    return row


# ------------------------------------------------------------------ TC-B14


async def test_tc_b14_reprice_never_updates_history_unit_price(db_session):
    """TC-B14：调价后历史区间 ``unit_price`` 未变（INV-3 / INV-P0-3）。

    这是整套单价的**核心不变量**：调价只能「关旧区间 + 插新区间」，
    绝不 ``UPDATE unit_price``。已落库的计件流水拿的是快照，历史价被改就是
    "工资算错了"。
    """
    style = await _style(db_session)
    op = await _operation(db_session)
    service = _rates(db_session)

    first = await service.set_rate(
        OperationRateCreate(
            operation_no=op.operation_no,
            style_no=style.style_no,
            unit_price=Decimal("0.350000"),
            effective_from=D_START,
            reason="首次设价",
        )
    )
    assert first.closed_rates == []

    second = await service.set_rate(
        OperationRateCreate(
            operation_no=op.operation_no,
            style_no=style.style_no,
            unit_price=Decimal("0.380000"),
            effective_from=D_SPLIT,
            reason="8 月中调价",
        )
    )
    # 旧行只被**关区间**，价格原封不动
    assert len(second.closed_rates) == 1
    assert second.closed_rates[0].unit_price == "0.350000"
    assert second.closed_rates[0].effective_to == D_SPLIT

    rows = (
        (
            await db_session.execute(
                select(OperationRate)
                .where(OperationRate.style_no == style.style_no)
                .order_by(OperationRate.effective_from)
            )
        )
        .scalars()
        .all()
    )
    assert [str(row.unit_price) for row in rows] == ["0.350000", "0.380000"]
    assert rows[0].effective_to == D_SPLIT
    assert rows[1].effective_to is None


async def test_tc_b14_resolve_returns_old_price_before_split(db_session):
    """TC-B14 配套：按 ``work_date`` 命中历史区间（旧价仍然是旧价）。"""
    style = await _style(db_session)
    op = await _operation(db_session)
    service = _rates(db_session)
    for effective_from, price in ((D_START, "0.350000"), (D_SPLIT, "0.380000")):
        await service.set_rate(
            OperationRateCreate(
                operation_no=op.operation_no,
                style_no=style.style_no,
                unit_price=Decimal(price),
                effective_from=effective_from,
                reason="调价",
            )
        )
    assert (await service.resolve(style.style_no, op.operation_no, D_MID)).unit_price == "0.350000"
    assert (await service.resolve(style.style_no, op.operation_no, D_NEXT)).unit_price == "0.380000"


# ------------------------------------------------------------------ TC-B15


async def test_tc_b15_overlapping_interval_is_rejected_with_conflict_details(db_session):
    """TC-B15：生效区间重叠 → 冲突码 + ``details`` 含冲突区间；旧行未被改动。"""
    style = await _style(db_session)
    op = await _operation(db_session)
    service = _rates(db_session)
    await service.set_rate(
        OperationRateCreate(
            operation_no=op.operation_no,
            style_no=style.style_no,
            unit_price=Decimal("0.380000"),
            effective_from=D_SPLIT,
        )
    )
    with pytest.raises(BusinessError) as exc:
        # 区间 ``[08-01, 08-20)`` 与既有的 ``[08-16, ∞)`` 相交
        await service.set_rate(
            OperationRateCreate(
                operation_no=op.operation_no,
                style_no=style.style_no,
                unit_price=Decimal("0.400000"),
                effective_from=D_START,
                effective_to=date(2026, 8, 20),
                reason="回溯调价",
            )
        )
    assert exc.value.code == ErrorCode.RATE_RANGE_OVERLAP
    conflicts = exc.value.details["conflicts"]
    assert conflicts[0]["effective_from"] == D_SPLIT.isoformat()
    assert conflicts[0]["unit_price"] == "0.380000"
    assert exc.value.details["new_interval"]["effective_from"] == D_START.isoformat()

    rows = (
        (
            await db_session.execute(
                select(OperationRate).where(OperationRate.style_no == style.style_no)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert str(rows[0].unit_price) == "0.380000"
    assert rows[0].effective_to is None


async def test_non_overlapping_intervals_are_accepted(db_session):
    """区间**首尾相接**不算重叠（``[a, b)`` 与 ``[b, c)``）—— 那是正常调价。"""
    style = await _style(db_session)
    op = await _operation(db_session)
    service = _rates(db_session)
    await service.set_rate(
        OperationRateCreate(
            operation_no=op.operation_no,
            style_no=style.style_no,
            unit_price=Decimal("0.350000"),
            effective_from=D_START,
            effective_to=D_SPLIT,
        )
    )
    result = await service.set_rate(
        OperationRateCreate(
            operation_no=op.operation_no,
            style_no=style.style_no,
            unit_price=Decimal("0.380000"),
            effective_from=D_SPLIT,
            reason="接续调价",
        )
    )
    assert result.closed_rates == []
    assert result.rate.effective_from == D_SPLIT


async def test_same_effective_from_twice_returns_20002(db_session):
    """同一生效日已有行 → ``20002``。

    追加式模型下"改今天的价"必须换一个生效日（R11）。静默覆盖会让"今天之前算过
    的钱"与"今天算的钱"对不上，而流水是事后才核对的。
    """
    style = await _style(db_session)
    op = await _operation(db_session)
    service = _rates(db_session)
    await service.set_rate(
        OperationRateCreate(
            operation_no=op.operation_no,
            style_no=style.style_no,
            unit_price=Decimal("0.350000"),
            effective_from=D_START,
        )
    )
    with pytest.raises(BusinessError) as exc:
        await service.set_rate(
            OperationRateCreate(
                operation_no=op.operation_no,
                style_no=style.style_no,
                unit_price=Decimal("0.360000"),
                effective_from=D_START,
                reason="想直接改",
            )
        )
    assert exc.value.code == ErrorCode.STYLE_ALREADY_EXISTS
    assert exc.value.details["existing_unit_price"] == "0.350000"


# ------------------------------------------------------------------ TC-B16


async def test_tc_b16_future_rate_does_not_affect_today(db_session):
    """TC-B16 / R19：提前公告的未来价**不参与**当日取价。"""
    style = await _style(db_session)
    op = await _operation(db_session)
    service = _rates(db_session)
    await service.set_rate(
        OperationRateCreate(
            operation_no=op.operation_no,
            style_no=style.style_no,
            unit_price=Decimal("0.350000"),
            effective_from=D_START,
        )
    )
    await service.set_rate(
        OperationRateCreate(
            operation_no=op.operation_no,
            style_no=style.style_no,
            unit_price=Decimal("0.400000"),
            effective_from=D_NEXT,
            reason="9 月预告价",
        )
    )
    assert (await service.resolve(style.style_no, op.operation_no, D_MID)).unit_price == "0.350000"
    assert (await service.resolve(style.style_no, op.operation_no, D_NEXT)).unit_price == "0.400000"


# ------------------------------------------------------------------ TC-B17


async def test_tc_b17_unset_rate_returns_20004_with_details(db_session):
    """TC-B17：未设价 → ``20004`` + ``details`` 含款号 / 工序（前端提示去设价）。"""
    style = await _style(db_session)
    op = await _operation(db_session)
    with pytest.raises(BusinessError) as exc:
        await _rates(db_session).resolve(style.style_no, op.operation_no, D_MID)
    assert exc.value.code == ErrorCode.OPERATION_RATE_NOT_SET
    assert exc.value.details["style_no"] == style.style_no
    assert exc.value.details["operation_no"] == op.operation_no
    assert "请先设价" in exc.value.message


async def test_tc_b17_rate_not_effective_on_that_date_returns_20004(db_session):
    """有行但当日无生效区间 → 同样 ``20004``（口径是"当日没有生效单价"）。"""
    style = await _style(db_session)
    op = await _operation(db_session)
    service = _rates(db_session)
    await service.set_rate(
        OperationRateCreate(
            operation_no=op.operation_no,
            style_no=style.style_no,
            unit_price=Decimal("0.350000"),
            effective_from=D_START,
            effective_to=D_SPLIT,
        )
    )
    with pytest.raises(BusinessError) as exc:
        await service.resolve(style.style_no, op.operation_no, D_NEXT)
    assert exc.value.code == ErrorCode.OPERATION_RATE_NOT_SET


# ------------------------------------------------------------------ TC-B26/27


async def _three_tiers(session) -> tuple[Style, Operation, ProductCategory]:
    category = await _category(session)
    style = await _style(session, category=category)
    op = await _operation(session)
    return style, op, category


async def test_tc_b26_style_tier_wins(db_session):
    """TC-B26 档位 1：款号专用价命中 → ``rate_source = STYLE``。"""
    style, op, category = await _three_tiers(db_session)
    service = _rates(db_session)
    await service.set_rate(
        OperationRateCreate(
            operation_no=op.operation_no,
            product_category_id=category.id,
            unit_price=Decimal("0.500000"),
            effective_from=D_START,
        )
    )
    await service.set_rate(
        OperationRateCreate(
            operation_no=op.operation_no,
            product_category_id=None,
            unit_price=Decimal("0.300000"),
            effective_from=D_START,
        )
    )
    await service.set_rate(
        OperationRateCreate(
            operation_no=op.operation_no,
            style_no=style.style_no,
            unit_price=Decimal("0.350000"),
            effective_from=D_START,
        )
    )
    hit = await service.resolve(style.style_no, op.operation_no, D_MID)
    assert hit.unit_price == "0.350000"
    assert hit.rate_source == RateSource.STYLE


async def test_tc_b27_category_tier_used_when_no_style_rate(db_session):
    """TC-B26/27 档位 2：没有款号专用价时命中分类价 → ``rate_source = CATEGORY``。"""
    style, op, category = await _three_tiers(db_session)
    service = _rates(db_session)
    await service.set_rate(
        OperationRateCreate(
            operation_no=op.operation_no,
            product_category_id=category.id,
            unit_price=Decimal("0.500000"),
            effective_from=D_START,
        )
    )
    await service.set_rate(
        OperationRateCreate(
            operation_no=op.operation_no,
            product_category_id=None,
            unit_price=Decimal("0.300000"),
            effective_from=D_START,
        )
    )
    hit = await service.resolve(style.style_no, op.operation_no, D_MID)
    assert hit.unit_price == "0.500000"
    assert hit.rate_source == RateSource.CATEGORY
    assert hit.product_category_id == category.id


async def test_tc_b26_operation_tier_is_the_lowest(db_session):
    """TC-B26 档位 3：只剩全厂同工序统一价 → ``rate_source = OPERATION``。

    ⚠️ 这是业务方 2026-10-03 明确确认存在的第三档（ADR-0026 状态栏"方案 a：
    完整三档"）。若把它禁掉，工厂按工序统一定价的场景就没法维护，
    而 ADR-0020 的 ``rate_source=OPERATION`` 也会永远填不出来。
    """
    style, op, _category = await _three_tiers(db_session)
    service = _rates(db_session)
    await service.set_rate(
        OperationRateCreate(
            operation_no=op.operation_no,
            product_category_id=None,
            unit_price=Decimal("0.300000"),
            effective_from=D_START,
        )
    )
    hit = await service.resolve(style.style_no, op.operation_no, D_MID)
    assert hit.unit_price == "0.300000"
    assert hit.rate_source == RateSource.OPERATION
    assert hit.product_category_id is None


async def test_tc_b27_style_rate_shadows_category_rate(db_session):
    """TC-B27：款号有专用价时**分类价对此款不生效**（被遮蔽，取价永远优先档位 1）。"""
    style, op, category = await _three_tiers(db_session)
    service = _rates(db_session)
    await service.set_rate(
        OperationRateCreate(
            operation_no=op.operation_no,
            product_category_id=category.id,
            unit_price=Decimal("0.500000"),
            effective_from=D_SPLIT,
        )
    )
    await service.set_rate(
        OperationRateCreate(
            operation_no=op.operation_no,
            style_no=style.style_no,
            unit_price=Decimal("0.350000"),
            effective_from=D_START,
        )
    )
    # 分类价生效日更晚也一样：档位优先于生效日
    hit = await service.resolve(style.style_no, op.operation_no, D_NEXT)
    assert hit.unit_price == "0.350000"
    assert hit.rate_source == RateSource.STYLE


async def test_tc_b26_newer_row_wins_within_same_tier(db_session):
    """同档位内取**最新生效**的那一行（``ORDER BY effective_from DESC``）。"""
    style, op, _category = await _three_tiers(db_session)
    service = _rates(db_session)
    await service.set_rate(
        OperationRateCreate(
            operation_no=op.operation_no,
            style_no=style.style_no,
            unit_price=Decimal("0.350000"),
            effective_from=D_START,
        )
    )
    await service.set_rate(
        OperationRateCreate(
            operation_no=op.operation_no,
            style_no=style.style_no,
            unit_price=Decimal("0.360000"),
            effective_from=D_SPLIT,
            reason="调价",
        )
    )
    assert (await service.resolve(style.style_no, op.operation_no, D_MID)).unit_price == "0.350000"
    assert (await service.resolve(style.style_no, op.operation_no, D_NEXT)).unit_price == "0.360000"


# ------------------------------------------------------------------ TC-B29


async def test_tc_b29_style_no_and_category_together_returns_10001(db_session):
    """TC-B29：一个价**不能同时**限款号又限分类 → ``10001``（Schema 层就拒）。

    ⚠️ 任务卡原文写的是「``style_no`` 与 ``product_category_id`` **同时为空** →
    ``10001``」，但那与 ADR-0026 §2 的档位 3（全厂同工序统一价，业务方 2026-10-03
    明确确认存在）直接冲突，也与迁移 0005 已按 L-030 修正的
    ``ck_operation_rates_target``（"不能**同时**有值"）相反。
    这里按 ADR-0026 §1 / §2 实现，把**同时有值**拦成 ``10001``。
    """
    from pydantic import ValidationError

    style = await _style(db_session)
    category = await _category(db_session)
    with pytest.raises(ValidationError):
        OperationRateCreate(
            operation_no="01",
            style_no=style.style_no,
            product_category_id=category.id,
            unit_price=Decimal("0.3"),
            effective_from=D_START,
        )
    # service 层再兜一道（防绕过 Schema 的直调）
    payload = OperationRateCreate(
        operation_no="01",
        style_no=style.style_no,
        unit_price=Decimal("0.3"),
        effective_from=D_START,
    )
    object.__setattr__(payload, "product_category_id", category.id)
    with pytest.raises(BusinessError) as exc:
        await _rates(db_session).set_rate(payload)
    assert exc.value.code == ErrorCode.PARAM_INVALID


# ------------------------------------------------------------------ R20


async def test_reprice_without_reason_returns_10006(db_session):
    """R20：调价必填 ``reason`` → ``10006``，且旧区间**没有被关闭**。"""
    style = await _style(db_session)
    op = await _operation(db_session)
    service = _rates(db_session)
    await service.set_rate(
        OperationRateCreate(
            operation_no=op.operation_no,
            style_no=style.style_no,
            unit_price=Decimal("0.350000"),
            effective_from=D_START,
        )
    )
    with pytest.raises(BusinessError) as exc:
        await service.set_rate(
            OperationRateCreate(
                operation_no=op.operation_no,
                style_no=style.style_no,
                unit_price=Decimal("0.380000"),
                effective_from=D_SPLIT,
            )
        )
    assert exc.value.code == ErrorCode.REASON_REQUIRED
    rows = (
        (
            await db_session.execute(
                select(OperationRate).where(OperationRate.style_no == style.style_no)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].effective_to is None


async def test_free_operation_zero_price_still_needs_reason(db_session):
    """R20：``unit_price = 0``（免费工序）同样要 ``reason``。"""
    style = await _style(db_session)
    op = await _operation(db_session)
    service = _rates(db_session)
    await service.set_rate(
        OperationRateCreate(
            operation_no=op.operation_no,
            style_no=style.style_no,
            unit_price=Decimal("0.100000"),
            effective_from=D_START,
        )
    )
    with pytest.raises(BusinessError) as exc:
        await service.set_rate(
            OperationRateCreate(
                operation_no=op.operation_no,
                style_no=style.style_no,
                unit_price=Decimal("0"),
                effective_from=D_SPLIT,
            )
        )
    assert exc.value.code == ErrorCode.REASON_REQUIRED


async def test_first_price_does_not_require_reason(db_session):
    """首次设价 ``reason`` 可空（modules/01 §3.4「首次设价可空」）。"""
    style = await _style(db_session)
    op = await _operation(db_session)
    result = await _rates(db_session).set_rate(
        OperationRateCreate(
            operation_no=op.operation_no,
            style_no=style.style_no,
            unit_price=Decimal("0.350000"),
            effective_from=D_START,
        )
    )
    assert result.rate.unit_price == "0.350000"
    assert result.rate.is_current is True
    assert result.rate.rate_source == RateSource.STYLE


# ------------------------------------------------------------------ 列表


async def test_list_rates_includes_history_and_derives_is_current(db_session):
    """列表**含历史区间**并派生 ``is_current``（04 §7.8.3：``is_current`` 不入库）。"""
    style = await _style(db_session)
    op = await _operation(db_session)
    service = _rates(db_session)
    await service.set_rate(
        OperationRateCreate(
            operation_no=op.operation_no,
            style_no=style.style_no,
            unit_price=Decimal("0.350000"),
            effective_from=D_START,
        )
    )
    await service.set_rate(
        OperationRateCreate(
            operation_no=op.operation_no,
            style_no=style.style_no,
            unit_price=Decimal("0.380000"),
            effective_from=D_SPLIT,
            reason="调价",
        )
    )
    items, total = await service.list_rates(RateQuery(style_no=style.style_no))
    assert total == 2
    # 默认按 effective_from DESC
    assert [item.unit_price for item in items] == ["0.380000", "0.350000"]
    assert [item.is_current for item in items] == [True, False]


async def test_list_rates_without_style_no_returns_factory_tiers(db_session):
    """不传 ``style_no`` 能查到档位 2 / 3 —— 它们本来就没有款号。"""
    category = await _category(db_session)
    operation_no = _uniq("OP")
    await _operation(db_session, operation_no)
    service = _rates(db_session)
    await service.set_rate(
        OperationRateCreate(
            operation_no=operation_no,
            product_category_id=category.id,
            unit_price=Decimal("0.500000"),
            effective_from=D_START,
        )
    )
    await service.set_rate(
        OperationRateCreate(
            operation_no=operation_no,
            product_category_id=None,
            unit_price=Decimal("0.300000"),
            effective_from=D_START,
        )
    )
    items, total = await service.list_rates(RateQuery(operation_no=operation_no))
    assert total == 2
    assert {item.rate_source for item in items} == {RateSource.CATEGORY, RateSource.OPERATION}


async def test_list_rates_rejects_unknown_sort_field(db_session):
    """``sort_by`` 白名单之外一律拒绝（禁止把用户输入拼进 SQL）。"""
    with pytest.raises(BusinessError) as exc:
        await _rates(db_session).list_rates(RateQuery(sort_by="unit_price; DROP TABLE"))
    assert exc.value.code == ErrorCode.PARAM_INVALID


# ------------------------------------------------------------------ 数据范围


async def test_merchandiser_cannot_read_other_style_rate(db_session):
    """跟单（``SELF``）查他人款号单价 → ``12002``。"""
    other = await UserFactory.create(db_session, employee_no=_uniq("E"), data_scope=DataScope.SELF)
    mine = await UserFactory.create(db_session, employee_no=_uniq("E"), data_scope=DataScope.SELF)
    style = await _style(db_session)
    service = RateService(db_session, _ctx(DataScope.SELF, user_id=mine.id))
    with pytest.raises(BusinessError) as exc:
        await service.resolve(style.style_no, "01", D_MID)
    assert exc.value.code == ErrorCode.DATA_SCOPE_DENIED
    assert other.id != mine.id


async def test_merchandiser_list_rates_is_limited_to_own_styles(db_session):
    """不传款号时，``SELF`` 用户只看得到「自己款号的价 + 全厂口径的价」。"""
    mine = await UserFactory.create(db_session, employee_no=_uniq("E"), data_scope=DataScope.SELF)
    mine_style = await _style(db_session)
    mine_style.merchandiser_id = mine.id
    await db_session.flush()
    other_style = await _style(db_session)
    await db_session.flush()
    service = _rates(db_session)
    op = await _operation(db_session)
    for style_no in (mine_style.style_no, other_style.style_no):
        await service.set_rate(
            OperationRateCreate(
                operation_no=op.operation_no,
                style_no=style_no,
                unit_price=Decimal("0.350000"),
                effective_from=D_START,
            )
        )
    await service.set_rate(
        OperationRateCreate(
            operation_no=op.operation_no,
            product_category_id=None,
            unit_price=Decimal("0.300000"),
            effective_from=D_START,
        )
    )
    items, _total = await RateService(db_session, _ctx(DataScope.SELF, user_id=mine.id)).list_rates(
        RateQuery()
    )
    seen = {item.style_no for item in items}
    assert mine_style.style_no in seen
    assert other_style.style_no not in seen
    assert None in seen  # 全厂口径行


# ------------------------------------------------------------------ HTTP 端到端


async def test_http_resolve_requires_permission(client, auth_headers):
    """缺 ``base:read`` → ``12001``，且报出缺哪个权限点（前端据此隐藏入口）。"""
    headers = await auth_headers(role="custom", permissions=("base:update",))
    resp = await client.get(
        "/api/v1/operation-rates/resolve",
        params={"style_no": "HB-2026-0001", "operation_no": "01"},
        headers=headers,
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["code"] == int(ErrorCode.PERMISSION_DENIED)
    assert body["details"]["required_permission"] == "base:read"


async def test_http_set_rate_requires_rate_manage_permission(client, auth_headers):
    """缺 ``piecework:rate:manage`` → ``12001``，记录不变。

    单价维护复用计件域已登记的权限点，**不新造**（modules/01 §6 末条）。
    """
    headers = await auth_headers(role="custom", permissions=("base:read", "base:update"))
    resp = await client.post(
        "/api/v1/operation-rates",
        json={
            "operation_no": "01",
            "unit_price": "0.350000",
            "effective_from": D_START.isoformat(),
        },
        headers=headers,
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == int(ErrorCode.PERMISSION_DENIED)


async def test_http_resolve_requires_work_date_params(client, auth_headers):
    """``style_no`` / ``operation_no`` 必填 → 缺一个就是 ``10001``。"""
    headers = await auth_headers(role="super_admin")
    resp = await client.get("/api/v1/operation-rates/resolve", headers=headers)
    assert resp.status_code == 422
    assert resp.json()["code"] == int(ErrorCode.PARAM_INVALID)


# ------------------------------------------------------------------ 守卫


async def test_unknown_operation_no_is_blocked_by_foreign_key(ddl_session):
    """工序号必须存在：``fk_operation_rates_operations`` 兜底。

    直接打数据库绕过应用层，证明拦住"给一个不存在的工序号设价"的是**外键**。
    """
    from sqlalchemy.exc import IntegrityError

    ddl_session.add(
        OperationRate(
            operation_no="NO-SUCH-OP",
            product_category_id=None,
            effective_from=D_START,
            unit_price=Decimal("0.3"),
            created_by=OPERATOR_ID,
            updated_by=OPERATOR_ID,
        )
    )
    with pytest.raises(IntegrityError):
        await ddl_session.commit()
    await ddl_session.rollback()


async def test_rate_for_unknown_style_returns_20001(db_session):
    """款号不存在 → ``20001``（不是 ``20004``：连款号都没有就没资格谈单价）。"""
    op = await _operation(db_session)
    with pytest.raises(BusinessError) as exc:
        await _rates(db_session).resolve(_uniq("NOPE"), op.operation_no, D_MID)
    assert exc.value.code == ErrorCode.BASE_DATA_NOT_FOUND


# ------------------------------------------------------------------ CC-4 / CC-7


#: 并发用例里造数的**专属前缀**。清理逻辑按前缀删，只删本文件造的行。
#:
#: ⚠️ 为什么要清理：并发用例必须**真提交**（不提交的话同一个 session 上的语句
#: 会被 PostgreSQL 串行化，测出来的是顺序执行，而顺序执行永远不出问题）。
#: 真提交就意味着数据留在库里，不清理的话 ``test_seed_cli`` 的"内置分类数量
#: == 6"、``test_scope`` 的"通用工序只有一条"这类**全库计数**断言会随机失败 ——
#: 而且失败原因离真正的元凶十万八千里。
CONC_CATEGORY_PREFIX = "CCCAT"
CONC_STYLE_PREFIX = "CCHB"
CONC_OPERATION_PREFIX = "CCOP"
CONC_CUSTOMER_PREFIX = "CCCUS"


@pytest.fixture
async def concurrent_sessions(app_database_url: str, migration_url: str):
    """每个并发任务一条**独立连接**，并在结束后清掉造的数据。

    ⚠️ 不能共用 session：同一连接上的语句会被 PostgreSQL 串行化，那样测出来的
    是"顺序执行"，而顺序执行永远不会有并发问题。

    :param migration_url: 清理要用**迁移账号** —— ``erp_app`` 被 REVOKE 了全部
        DELETE（04 §6.2.1），而下面要删的正是业务数据。
    """
    engine = create_async_engine(app_database_url, pool_pre_ping=True, pool_size=CONCURRENCY + 2)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    sessions = [factory() for _ in range(CONCURRENCY + 4)]
    try:
        yield sessions
    finally:
        for session in sessions:
            await session.close()
        await engine.dispose()
        await _purge_concurrency_rows(migration_url)


async def _purge_concurrency_rows(migration_url: str) -> None:
    """删除本文件并发用例造出来的行（按专属前缀，依赖顺序从子表到父表）。

    ⚠️ 全部用**绑定参数**，不拼字符串：``docs/03 §1.5`` 禁止把变量拼进 SQL，
    哪怕这个变量是模块常量 —— 今天的"安全"取决于没人后来把它改成用户输入。
    """
    style_pattern = f"{CONC_STYLE_PREFIX}%"
    operation_pattern = f"{CONC_OPERATION_PREFIX}%"
    child_tables = ("style_operations", "style_color_size_ratios", "style_sizes", "style_colors")
    statements = [
        (
            "DELETE FROM operation_rates WHERE style_no LIKE :style "
            "OR operation_no LIKE :operation",
            {"style": style_pattern, "operation": operation_pattern},
        ),
        # 表名是模块内写死的字面量集合（不是输入），值一律走绑定参数
        *[
            (f"DELETE FROM {table} WHERE style_no LIKE :style", {"style": style_pattern})  # noqa: S608
            for table in child_tables
        ],
        ("DELETE FROM styles WHERE style_no LIKE :style", {"style": style_pattern}),
        (
            "DELETE FROM operations WHERE operation_no LIKE :operation",
            {"operation": operation_pattern},
        ),
        (
            "DELETE FROM product_categories WHERE code LIKE :category",
            {"category": f"{CONC_CATEGORY_PREFIX}%"},
        ),
        (
            "DELETE FROM customers WHERE code LIKE :customer",
            {"customer": f"{CONC_CUSTOMER_PREFIX}%"},
        ),
        ("DELETE FROM style_no_sequences", {}),
    ]
    engine = create_async_engine(migration_url, pool_pre_ping=True)
    try:
        async with engine.begin() as conn:
            for sql, params in statements:
                await conn.execute(text(sql).bindparams(**params))
    finally:
        await engine.dispose()


async def test_concurrent_set_rate_same_triple_same_day_yields_one_row(
    concurrent_sessions,
):
    """真并发：``CONCURRENCY`` 个独立连接对同一 ``(款号, 工序, 生效日)`` 设价 →
    恰好 1 行，其余 ``20002``（CC-4 / modules/01 §7、TC-27）。

    全部请求在 ``SELECT ... FOR UPDATE`` 时都还看不到对方的未提交行，因此都走到
    插入；最后由 ``uq_operation_rates``（四列 + ``NULLS NOT DISTINCT``，ADR-0026）
    兜底，冲突被翻成 ``20002``（``STYLE_ALREADY_EXISTS``）。同一生效日**不是**
    区间重叠，所以**不是** ``20005``。
    """
    style_no = f"{CONC_STYLE_PREFIX}{uuid4().hex[:6].upper()}"
    operation_no = f"{CONC_OPERATION_PREFIX}{uuid4().hex[:6].upper()}"
    setup = concurrent_sessions[0]
    category = ProductCategory(
        code=f"{CONC_CATEGORY_PREFIX}{uuid4().hex[:6].upper()}",
        name="并发分类",
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    setup.add(category)
    await setup.flush()
    setup.add(
        Style(
            style_no=style_no,
            name="并发单价款",
            category_id=category.id,
            created_by=OPERATOR_ID,
            updated_by=OPERATOR_ID,
        )
    )
    setup.add(
        Operation(
            operation_no=operation_no,
            name="并发工序",
            created_by=OPERATOR_ID,
            updated_by=OPERATOR_ID,
        )
    )
    await setup.commit()

    barrier = asyncio.Barrier(CONCURRENCY)

    async def _set_rate(index: int) -> str:
        session = concurrent_sessions[index]
        await barrier.wait()
        try:
            await RateService(session, _ctx()).set_rate(
                OperationRateCreate(
                    operation_no=operation_no,
                    style_no=style_no,
                    unit_price=Decimal("0.350000"),
                    effective_from=D_START,
                )
            )
            return "ok"
        except BusinessError as exc:
            return f"rejected:{int(exc.code)}"

    results = await asyncio.gather(*(_set_rate(index) for index in range(CONCURRENCY)))
    assert results.count("ok") == 1, results
    assert results.count(f"rejected:{int(ErrorCode.STYLE_ALREADY_EXISTS)}") == CONCURRENCY - 1, (
        f"同日冲突应全部是 20002，实际 {results}"
    )

    async with concurrent_sessions[CONCURRENCY + 1] as verify:
        rows = int(
            await verify.scalar(
                select(func.count())
                .select_from(OperationRate)
                .where(
                    OperationRate.style_no == style_no,
                    OperationRate.operation_no == operation_no,
                    OperationRate.effective_from == D_START,
                    OperationRate.deleted_at.is_(None),
                )
            )
        )
    assert rows == 1, f"该三元组应恰好 1 行，实际 {rows} 行"


# ------------------------------------------------ 剩余边界与守卫


async def test_list_rates_can_filter_by_product_category_id(db_session):
    """档位 2 行只能用 ``product_category_id`` 找得到（它没有款号）。"""
    category = await _category(db_session)
    other = await _category(db_session)
    operation_no = _uniq("OP")
    await _operation(db_session, operation_no)
    service = _rates(db_session)
    for cat in (category, other):
        await service.set_rate(
            OperationRateCreate(
                operation_no=operation_no,
                product_category_id=cat.id,
                unit_price=Decimal("0.5"),
                effective_from=D_START,
            )
        )
    items, total = await service.list_rates(RateQuery(product_category_id=category.id))
    assert total == 1
    assert items[0].rate_source == RateSource.CATEGORY


async def test_list_rates_filters_by_effective_from_range(db_session):
    """``effective_from`` / ``effective_to`` 过滤的是**生效起始日**所在的区间。"""
    style = await _style(db_session)
    op = await _operation(db_session)
    service = _rates(db_session)
    await service.set_rate(
        OperationRateCreate(
            operation_no=op.operation_no,
            style_no=style.style_no,
            unit_price=Decimal("0.350000"),
            effective_from=D_START,
        )
    )
    await service.set_rate(
        OperationRateCreate(
            operation_no=op.operation_no,
            style_no=style.style_no,
            unit_price=Decimal("0.380000"),
            effective_from=D_SPLIT,
            reason="调价",
        )
    )
    _items, in_range = await service.list_rates(
        RateQuery(style_no=style.style_no, effective_from=D_SPLIT)
    )
    assert in_range == 1
    with pytest.raises(BusinessError) as exc:
        await service.list_rates(
            RateQuery(style_no=style.style_no, effective_from=D_NEXT, effective_to=D_START)
        )
    assert exc.value.code == ErrorCode.PARAM_INVALID


async def test_list_rates_rejects_oversize_page(db_session):
    with pytest.raises(BusinessError) as exc:
        await _rates(db_session).list_rates(RateQuery(size=500))
    assert exc.value.code == ErrorCode.PARAM_INVALID


async def test_set_rate_for_unknown_style_returns_20001(db_session):
    """设价时款号不存在 → ``20001``（外键也会兜住，但先给友好文案）。"""
    op = await _operation(db_session)
    with pytest.raises(BusinessError) as exc:
        await _rates(db_session).set_rate(
            OperationRateCreate(
                operation_no=op.operation_no,
                style_no=_uniq("NOPE"),
                unit_price=Decimal("0.3"),
                effective_from=D_START,
            )
        )
    assert exc.value.code == ErrorCode.BASE_DATA_NOT_FOUND


async def test_set_rate_for_unknown_category_returns_20001(db_session):
    with pytest.raises(BusinessError) as exc:
        await _rates(db_session).set_rate(
            OperationRateCreate(
                operation_no=_uniq("OP"),
                product_category_id=uuid4(),
                unit_price=Decimal("0.3"),
                effective_from=D_START,
            )
        )
    assert exc.value.code == ErrorCode.BASE_DATA_NOT_FOUND


async def test_rate_range_validator_rejects_empty_interval():
    """``effective_to <= effective_from`` → Schema 就拒（区间不能为空）。"""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        OperationRateCreate(
            operation_no="01",
            product_category_id=None,
            unit_price=Decimal("0.3"),
            effective_from=D_START,
            effective_to=D_START,
        )


async def test_set_rate_writes_document_log_with_old_and_new_price(db_session):
    """调价日志必须记下**新旧两个价**与生效日（docs/04 §7.9 变更留痕）。"""
    style = await _style(db_session)
    op = await _operation(db_session)
    service = _rates(db_session)
    first = await service.set_rate(
        OperationRateCreate(
            operation_no=op.operation_no,
            style_no=style.style_no,
            unit_price=Decimal("0.350000"),
            effective_from=D_START,
        )
    )
    second = await service.set_rate(
        OperationRateCreate(
            operation_no=op.operation_no,
            style_no=style.style_no,
            unit_price=Decimal("0.380000"),
            effective_from=D_SPLIT,
            reason="8 月中调价",
        )
    )
    from app.common.models import DocumentLog

    logs = (
        (
            await db_session.execute(
                select(DocumentLog)
                .where(
                    DocumentLog.doc_type == "OperationRate", DocumentLog.doc_id == second.rate.id
                )
                .order_by(DocumentLog.created_at)
            )
        )
        .scalars()
        .all()
    )
    assert len(logs) == 1
    changed = logs[0].changed_fields or {}
    assert changed["unit_price"] == "0.380000"
    assert changed["rate_source"] == "STYLE"
    assert changed["closed"][0]["unit_price"] == "0.350000"
    assert changed["closed"][0]["effective_to"] == D_SPLIT.isoformat()
    assert first.document_log_id is not None


async def test_set_rate_response_shows_closed_interval(db_session):
    """调价响应回显被关闭的旧区间（旧价原样，用户可自行核对）。"""
    style = await _style(db_session)
    op = await _operation(db_session)
    service = _rates(db_session)
    await service.set_rate(
        OperationRateCreate(
            operation_no=op.operation_no,
            style_no=style.style_no,
            unit_price=Decimal("0.350000"),
            effective_from=D_START,
        )
    )
    result = await service.set_rate(
        OperationRateCreate(
            operation_no=op.operation_no,
            style_no=style.style_no,
            unit_price=Decimal("0.380000"),
            effective_from=D_SPLIT,
            reason="调价",
        )
    )
    assert [item.unit_price for item in result.closed_rates] == ["0.350000"]
    assert result.closed_rates[0].is_current is False
    assert result.rate.is_current is True


# ------------------------------------------------------------------ 导出


async def test_http_export_rates_requires_two_permissions(client, auth_headers):
    """导出需要 ``base:export`` **且** ``system:export:manage``，少一个都拒（ADR 决议）。"""
    headers = await auth_headers(role="custom", permissions=("base:read", "base:export"))
    resp = await client.get(
        "/api/v1/operation-rates/exports",
        params={"operation_no": "NOPE"},
        headers=headers,
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["code"] == int(ErrorCode.PERMISSION_DENIED)
    assert "system:export:manage" in body["message"]


async def test_http_export_rates_uses_same_filters_as_list(client, auth_headers, db_session):
    """导出与列表**同一套筛选**（docs/07 §3.2 铁律 3）：行数必须一致。"""
    style = await _style(db_session)
    op = await _operation(db_session)
    await _rates(db_session).set_rate(
        OperationRateCreate(
            operation_no=op.operation_no,
            style_no=style.style_no,
            unit_price=Decimal("0.350000"),
            effective_from=D_START,
        )
    )
    await _rates(db_session).set_rate(
        OperationRateCreate(
            operation_no=op.operation_no,
            product_category_id=None,
            unit_price=Decimal("0.300000"),
            effective_from=D_START,
        )
    )
    headers = await auth_headers(role="super_admin")
    listed = await client.get(
        "/api/v1/operation-rates",
        params={"operation_no": op.operation_no},
        headers=headers,
    )
    exported = await client.get(
        "/api/v1/operation-rates/exports",
        params={"operation_no": op.operation_no},
        headers=headers,
    )
    assert exported.status_code == 200
    assert exported.headers["X-Row-Count"] == str(listed.json()["data"]["total"])
    assert exported.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument"
    )
    assert "operation-rates-" in exported.headers["content-disposition"]
