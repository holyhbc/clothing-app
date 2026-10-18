"""计件结算单**审核 / 反审核**用例（T-PAY-005；``modules/05 §11`` TC-03/17/26/34）。

================  ====================================================================
TC-03             审核回写全部 ``payroll_id`` / 反审核全部清空；单据与行**保留**（R5）
TC-26             ``payroll_id IS NULL`` 保证不重复结算（占用后取不到 + 唯一索引兜底）
TC-PAY-A01        制单人自审 → ``10005``
TC-PAY-A02        下游工资单 ``APPROVED``/``PAID`` 时反审核 → ``33001``
TC-PAY-A03        并发 approve vs reverse → 一成一败 ``10003``，无中间态
—                 ``rowcount != line_count`` 整体回滚；单头与行汇总不符时拒绝
================  ====================================================================

## 副作用对称性的断言方式

不比对 ORM 属性（Core UPDATE 后 identity map 是旧值），而是**直接 SELECT
``piecework_logs.payroll_id`` 的列集合**：审核后应全部等于本单 id、反审核后应全部回 ``None``
（``08 §5`` 第 6 条要求的「副作用快照对比」，入口是 ``payroll_ids_of_logs``）。

## 并发用例为什么用「事件门控」

``reverse`` 的合法起始态是 ``APPROVED``，``approve`` 的起始态是 ``SUBMITTED`` —— 两者不可能在
**同一瞬间**都合法。TC-17/TC-34 的「一成一败 ``10003``」指的是：approve 先提交成功后，反向操作
**带着同一个 ``expected_version``** 进来被乐观锁挡下（若它先于 approve 读，报的是 ``10007`` 非法
迁移，那是另一条合法防线）。故用 ``asyncio.Event`` 固定这个顺序，断言才确定、不靠调度运气。

## 本文件刻意不走 HTTP

本卡**没有 router**（归 T-PAY-009），故直接调 service；鉴权用构造的 ``AuthContext``。
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.common.enums import DocumentStatus
from app.common.models import DocumentLog
from app.core.errors import BusinessError, ErrorCode
from app.modules.payroll.models import PieceworkSettlement
from app.modules.payroll.service import PieceworkSettlementService
from app.modules.piecework.models import PieceworkLog
from tests.factories.payroll import (
    seed_period,
    seed_piecework_log,
    settlement_line_row,
    settlement_row,
    sheet_row,
)
from tests.factories.payroll_approve import (
    DOC_TYPE,
    REVIEWER_ID,
    payroll_ids_of_logs,
    purge_committed_settlement,
    reviewer_ctx,
    seed_approvable_settlement,
    settlement_lines_of,
    settlement_logs_of,
)

# ============================================================== TC-03 副作用对称


async def test_tc03_approve_occupies_and_reverse_releases(db_session) -> None:
    settlement, logs, _, _ = await seed_approvable_settlement(db_session, log_count=3)
    log_ids = [log.id for log in logs]
    assert set((await payroll_ids_of_logs(db_session, log_ids)).values()) == {None}, (
        "审核前应全未结算"
    )

    approved = await PieceworkSettlementService(db_session).approve(
        settlement.id,
        expected_version=settlement.version,
        operator_id=REVIEWER_ID,
        ctx=reviewer_ctx(),
    )
    assert approved.status is DocumentStatus.APPROVED
    assert approved.approved_by == REVIEWER_ID
    assert approved.approved_at is not None
    assert set((await payroll_ids_of_logs(db_session, log_ids)).values()) == {settlement.id}, (
        "审核应回写本单"
    )

    approve_logs = [
        log
        for log in await settlement_logs_of(db_session, settlement.id)
        if log.action == "APPROVE"
    ]
    assert len(approve_logs) == 1
    assert approve_logs[0].changed_fields == {"occupied_log_count": 3}

    reversed_ = await PieceworkSettlementService(db_session).reverse(
        settlement.id,
        reason="车间报错，重算",
        expected_version=approved.version,
        operator_id=REVIEWER_ID,
        ctx=reviewer_ctx(),
    )
    assert reversed_.status is DocumentStatus.SUBMITTED
    assert reversed_.approved_by is None
    assert set((await payroll_ids_of_logs(db_session, log_ids)).values()) == {None}, (
        "反审核应全部释放"
    )

    # R5：不删行、不删单
    assert len(await settlement_lines_of(db_session, settlement.id)) == 3
    reverse_logs = [
        log
        for log in await settlement_logs_of(db_session, settlement.id)
        if log.action == "REVERSE"
    ]
    assert len(reverse_logs) == 1
    assert reverse_logs[0].reason == "车间报错，重算"
    assert reverse_logs[0].changed_fields == {"released_log_count": 3}
    assert (reverse_logs[0].from_status, reverse_logs[0].to_status) == ("APPROVED", "SUBMITTED")


# ============================================================== TC-26 不重复结算


async def test_tc26_occupied_log_not_settled_again(db_session) -> None:
    settlement, logs, period, workshop = await seed_approvable_settlement(db_session, log_count=2)
    await PieceworkSettlementService(db_session).approve(
        settlement.id,
        expected_version=settlement.version,
        operator_id=REVIEWER_ID,
        ctx=reviewer_ctx(),
    )
    # 已被占用的流水取不到（payroll_id IS NULL 是权威判据，BR-PR-02）
    service = PieceworkSettlementService(db_session)
    assert await service._fetch_unsettled_logs(period, workshop) == []

    # 唯一索引兜底：把已占用的流水塞进另一张单的行 → IntegrityError
    other = settlement_row(period.id, uuid4(), doc_no=f"PW-20260131-{uuid4().hex[:6]}")
    db_session.add(other)
    await db_session.flush()
    db_session.add(settlement_line_row(other.id, logs[0].id, line_no=1))
    with pytest.raises(Exception) as exc:
        await db_session.flush()
    assert "uq_piecework_settlement_lines_piecework_log_id" in str(exc.value)


# ============================================================== A01 自审 / A02 下游


async def test_tc_pay_a01_self_approval_10005(db_session) -> None:
    settlement, logs, _, _ = await seed_approvable_settlement(
        db_session, log_count=1, created_by=REVIEWER_ID
    )
    with pytest.raises(BusinessError) as exc:
        await PieceworkSettlementService(db_session).approve(
            settlement.id,
            expected_version=settlement.version,
            operator_id=REVIEWER_ID,
            ctx=reviewer_ctx(),
        )
    assert exc.value.code == ErrorCode.SELF_APPROVAL_FORBIDDEN
    refreshed = await db_session.get(PieceworkSettlement, settlement.id)
    assert refreshed is not None and refreshed.status is DocumentStatus.SUBMITTED
    assert set((await payroll_ids_of_logs(db_session, [log.id for log in logs])).values()) == {None}


@pytest.mark.parametrize("sheet_status", [DocumentStatus.APPROVED, DocumentStatus.PAID])
async def test_tc_pay_a02_reverse_blocked_by_downstream_33001(
    db_session, sheet_status: DocumentStatus
) -> None:
    settlement, _, _, _ = await seed_approvable_settlement(
        db_session, log_count=1, status=DocumentStatus.APPROVED
    )
    db_session.add(sheet_row(settlement, status=sheet_status))
    await db_session.flush()
    with pytest.raises(BusinessError) as exc:
        await PieceworkSettlementService(db_session).reverse(
            settlement.id,
            reason="下游已审核，试反审核",
            expected_version=settlement.version,
            operator_id=REVIEWER_ID,
            ctx=reviewer_ctx(),
        )
    assert exc.value.code == ErrorCode.PAYROLL_SHEET_ALREADY_PAID
    refreshed = await db_session.get(PieceworkSettlement, settlement.id)
    assert refreshed is not None and refreshed.status is DocumentStatus.APPROVED


# ============================================================== A03 并发


async def test_tc_pay_a03_concurrent_approve_reverse_one_wins(
    app_database_url, migration_url
) -> None:
    engine = create_async_engine(app_database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    # 唯一日期区间 + 唯一单号：并发用例**真提交**，靠 key 唯一避免与残留 / 重启撞车
    start = date(2099, 1, 1) + timedelta(days=uuid4().int % 2000)
    end = start + timedelta(days=20)
    settlement_id: UUID | None = None
    period_id: UUID | None = None
    log_ids: list[UUID] = []
    try:
        async with factory() as session:
            period = await seed_period(
                session, period_code=f"A03-{uuid4().hex[:8]}", start_date=start, end_date=end
            )
            workshop = uuid4()
            settlement = settlement_row(
                period.id,
                workshop,
                doc_no=f"PW-{uuid4().hex[:12]}",
                status=DocumentStatus.SUBMITTED,
                line_count=2,
                total_qty=Decimal("120"),
                total_amount=Decimal("42.0000"),
            )
            session.add(settlement)
            await session.flush()
            logs = [
                await seed_piecework_log(
                    session, work_date=start, workshop_id=workshop, group_no="G1"
                )
                for _ in range(2)
            ]
            for index, log in enumerate(logs, start=1):
                session.add(
                    settlement_line_row(
                        settlement.id,
                        log.id,
                        line_no=index,
                        qty=log.qty,
                        unit_price=log.unit_price,
                        amount=log.amount,
                        group_no="G1",
                        work_date=start,
                    )
                )
            await session.commit()
            settlement_id, period_id = settlement.id, period.id
            log_ids = [log.id for log in logs]
            version = settlement.version

        approve_done = asyncio.Event()

        async def do_approve() -> ErrorCode | None:
            async with factory() as session:
                try:
                    await PieceworkSettlementService(session).approve(
                        settlement_id,
                        expected_version=version,
                        operator_id=REVIEWER_ID,
                        ctx=reviewer_ctx(),
                    )
                    await session.commit()
                    return None
                except BusinessError as exc:
                    await session.rollback()
                    return exc.code
                finally:
                    approve_done.set()

        async def do_reverse() -> ErrorCode | None:
            await approve_done.wait()
            async with factory() as session:
                try:
                    await PieceworkSettlementService(session).reverse(
                        settlement_id,
                        reason="并发反审核",
                        expected_version=version,
                        operator_id=REVIEWER_ID,
                        ctx=reviewer_ctx(),
                    )
                    await session.commit()
                    return None
                except BusinessError as exc:
                    await session.rollback()
                    return exc.code

        approve_code, reverse_code = await asyncio.gather(do_approve(), do_reverse())
        assert approve_code is None, f"approve 应成功：{approve_code}"
        assert reverse_code is ErrorCode.OPTIMISTIC_LOCK_CONFLICT, (
            f"reverse 应报 10003：{reverse_code}"
        )

        async with factory() as session:
            fresh = await session.get(PieceworkSettlement, settlement_id)
            assert fresh is not None
            assert fresh.status is DocumentStatus.APPROVED
            assert fresh.version == version + 1, "version 只应 +1 一次"
            assert set((await payroll_ids_of_logs(session, log_ids)).values()) == {settlement_id}, (
                "反审核失败必须整体回滚，流水仍占用（无中间态）"
            )
            actions = list(
                await session.scalars(
                    select(DocumentLog.action).where(
                        DocumentLog.doc_type == DOC_TYPE, DocumentLog.doc_id == settlement_id
                    )
                )
            )
            assert actions.count("APPROVE") == 1
            assert "REVERSE" not in actions, "失败的 reverse 不应留下日志"
    finally:
        await engine.dispose()
        if settlement_id is not None and period_id is not None:
            await purge_committed_settlement(migration_url, settlement_id, period_id, log_ids)


# ============================================================== 兜底 / 前置


async def test_approve_rowcount_mismatch_rolls_back(db_session, monkeypatch) -> None:
    """条件 UPDATE 命中数 < 行数（另一单抢先占用）→ ``33002`` 整体回滚，不 commit 半个。"""
    settlement, logs, period, _ = await seed_approvable_settlement(db_session, log_count=2)
    intruder = settlement_row(period.id, uuid4(), doc_no=f"PW-20260131-{uuid4().hex[:6]}")
    db_session.add(intruder)
    await db_session.flush()
    log_ids = [log.id for log in logs]
    await db_session.execute(
        update(PieceworkLog).where(PieceworkLog.id == log_ids[0]).values(payroll_id=intruder.id)
    )

    # 让预检落空，强制走条件 UPDATE 的命中数分支（模拟预检与 UPDATE 之间的并发）
    async def _skip(_self: Any, _log_ids: list[UUID]) -> None:
        return None

    monkeypatch.setattr(PieceworkSettlementService, "_assert_logs_unoccupied", _skip)
    with pytest.raises(BusinessError) as exc:
        await PieceworkSettlementService(db_session).approve(
            settlement.id,
            expected_version=settlement.version,
            operator_id=REVIEWER_ID,
            ctx=reviewer_ctx(),
        )
    assert exc.value.code == ErrorCode.SETTLEMENT_PERIOD_DUPLICATED
    refreshed = await db_session.get(PieceworkSettlement, settlement.id)
    assert refreshed is not None and refreshed.status is DocumentStatus.SUBMITTED
    ids = await payroll_ids_of_logs(db_session, log_ids)
    assert ids[log_ids[0]] == intruder.id, "他人占用不受影响"
    assert ids[log_ids[1]] is None, "本单的占用已被整体回滚"


async def test_approve_totals_mismatch_rejected(db_session) -> None:
    settlement, logs, _, _ = await seed_approvable_settlement(
        db_session, log_count=1, total_amount=Decimal("999.0000")
    )
    with pytest.raises(BusinessError) as exc:
        await PieceworkSettlementService(db_session).approve(
            settlement.id,
            expected_version=settlement.version,
            operator_id=REVIEWER_ID,
            ctx=reviewer_ctx(),
        )
    assert exc.value.code == ErrorCode.SETTLEMENT_PERIOD_DUPLICATED
    assert set((await payroll_ids_of_logs(db_session, [log.id for log in logs])).values()) == {None}


async def test_reverse_requires_reason_10006(db_session) -> None:
    settlement, _, _, _ = await seed_approvable_settlement(
        db_session, log_count=1, status=DocumentStatus.APPROVED
    )
    with pytest.raises(BusinessError) as exc:
        await PieceworkSettlementService(db_session).reverse(
            settlement.id,
            reason="   ",
            expected_version=settlement.version,
            operator_id=REVIEWER_ID,
            ctx=reviewer_ctx(),
        )
    assert exc.value.code == ErrorCode.REASON_REQUIRED
    refreshed = await db_session.get(PieceworkSettlement, settlement.id)
    assert refreshed is not None and refreshed.status is DocumentStatus.APPROVED


async def test_approve_requires_permission_12001(db_session) -> None:
    settlement, _, _, _ = await seed_approvable_settlement(db_session, log_count=1)
    with pytest.raises(BusinessError) as exc:
        await PieceworkSettlementService(db_session).approve(
            settlement.id,
            expected_version=settlement.version,
            operator_id=REVIEWER_ID,
            ctx=reviewer_ctx(permissions=frozenset()),
        )
    assert exc.value.code == ErrorCode.PERMISSION_DENIED
