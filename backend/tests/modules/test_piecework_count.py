"""扫码计件的规则用例（``04-计件 §5.1`` ①~⑪、§6.1）。

======================================  ==========================================
TC-PW-01                             ① 不存在的码 → ``31001``
TC-PW-02                             ① 已作废的码 → ``31002``
TC-PW-03                             ② 款号没配这个计件工序 → ``20001``
TC-PW-04                             ③ 当日没绑定 → ``32002``；昨天的绑定今天也 ``32002``
TC-PW-05                             ④ 没设单价 → ``20004``（**不回退 0、不用旧价**）
TC-PW-06                             ⑤ 不传 ``qty`` = 该手全件（**不是 1**，ADR-0016）
TC-PW-07                             ⑤ 部分生产 ``qty=28`` → ``counted_qty=28``，仍算已计件
TC-PW-08                             ⑤ 超产 ``qty > bundle_qty`` → ``32006``
TC-PW-09                             ⑤ 非整件 ``qty=28.5`` → ``32005``
TC-PW-10                             ⑤ ``qty <= 0`` → ``10001``
TC-PW-11                             ⑥ 金额 = 件数 × 单价，全程 Decimal（0.42 × 60）
TC-PW-12                             ⑦ 同员工重试 → ``idempotent=true``，**不重复计数**
TC-PW-13                             ★ 取价用的是**码上的**工序，不是配置列的 ``bundle_qty``
TC-PW-14                             ⑨ 回写 ``bundles.counted_*`` + ``version`` +1
TC-PW-15                             ⑪ ``document_logs`` 记**操作员**与**计件人**两个 id
TC-PW-16                             ⑧ ``log_type=SCAN`` + ``payroll_id IS NULL`` + ``group_no`` 冗余
TC-PW-17                             ⑥ 同员工第二手 → ``today_hands`` / ``today_qty`` 累加
TC-PW-18                             权限：没有 ``piecework:count`` → ``12001``
======================================  ==========================================

## TC-PW-06 与 TC-PW-13 是本卡最容易被「改对了一半」的用例

``qty`` 的默认值是 ``bundles.bundle_qty``，而这个款号的 ``style_operations.bundle_qty``
**故意设成 1**（见 :mod:`tests.factories.piecework_world`）。所以：

- 取错列 → 落 ``qty=1``（TC-PW-06 失败）
- 硬编码 1 → TC-PW-06 失败
- 超产校验用错列 → 60 件的手被判成「超产」（TC-PW-08 的错误码变了）

三个症状指向同一行代码。这是有意为之：Q-PW13 说的「同名不同义」只有把两列
设成不同值才测得出来，都设成 60 的话取错列与取对列结果一模一样。
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select, update

from app.common.models import DocumentLog
from app.core.errors import BusinessError, ErrorCode
from app.core.numbering import business_today
from app.modules.bundling.models import Bundle, BundleStatus
from app.modules.piecework.audit import DOC_TYPE_PIECEWORK
from app.modules.piecework.guard import resolve_qty
from app.modules.piecework.models import PieceworkLog, PieceworkLogType
from app.modules.piecework.service import PieceworkService
from tests.factories.piecework_world import BUNDLE_QTY, UNIT_PRICE, ctx_for, scanned_at

API = "/api/v1/piecework/count"


async def _count(
    db_session: Any,
    world: dict[str, Any],
    *,
    bundle_no: str | None = None,
    employee: Any = None,
    qty: Decimal | None = None,
    now: datetime | None = None,
) -> Any:
    """走 service 直调（不走 HTTP）：这里要断言的是**事务内部的规则**，
    HTTP 层的校验与包装另有 ``test_piecework_router`` 覆盖。"""
    return await PieceworkService(db_session).count_piecework(
        bundle_no=bundle_no or world["bundle_no"],
        employee_id=employee.id if employee else world["employee"].id,
        scanned_at=scanned_at(now),
        qty=qty,
        operator_id=ctx_for().user_id,
        ctx=ctx_for(),
    )


async def _logs(db_session: Any, bundle_no: str) -> list[PieceworkLog]:
    return list(
        (
            await db_session.execute(
                select(PieceworkLog)
                .where(PieceworkLog.bundle_no == bundle_no, PieceworkLog.deleted_at.is_(None))
                .order_by(PieceworkLog.created_at)
            )
        )
        .scalars()
        .all()
    )


# ============================================================== ① 锁码与作废


async def test_tc_pw_01_unknown_bundle_raises_31001(db_session, piecework_world) -> None:
    with pytest.raises(BusinessError) as exc:
        await _count(db_session, piecework_world, bundle_no=f"BD-NOPE-{uuid4().hex[:8].upper()}")
    assert exc.value.code == ErrorCode.BUNDLE_NO_NOT_FOUND


async def test_tc_pw_02_voided_bundle_raises_31002(db_session, piecework_world) -> None:
    await db_session.execute(
        update(Bundle)
        .where(Bundle.id == piecework_world["bundle"].id)
        .values(status=BundleStatus.VOIDED)
    )
    with pytest.raises(BusinessError) as exc:
        await _count(db_session, piecework_world)
    assert exc.value.code == ErrorCode.BUNDLE_ALREADY_VOIDED


# ============================================================== ②④ 基础资料


async def test_tc_pw_03_operation_not_configured_raises_20001(db_session, piecework_world) -> None:
    """把码上的工序号改成一个**款号没配**的工序 → ``20001``。

    ⚠️ 改的是 ``bundles.operation_no`` 而不是 ``style_operations``：码与工序强绑定
    （ADR-0016），而删掉配置行会被外键挡住。"""
    other_op = f"OP-X{uuid4().hex[:6].upper()}"
    await db_session.execute(
        update(Bundle)
        .where(Bundle.id == piecework_world["bundle"].id)
        .values(operation_no=other_op)
    )
    with pytest.raises(BusinessError) as exc:
        await _count(db_session, piecework_world)
    assert exc.value.code == ErrorCode.BASE_DATA_NOT_FOUND


async def test_tc_pw_05_no_rate_raises_20004(db_session, piecework_world) -> None:
    """把生效期挪到明天 → 今天取不到价 → ``20004``。

    ⚠️ **不回退旧价也不取 0**：若实现里写了「命中不到就拿最近的一条」，
    这里会变成成功计件、金额还是那个价 —— 而 20004 的全部意义就是拦住它。
    """
    from app.modules.base.models import OperationRate

    await db_session.execute(
        update(OperationRate)
        .where(OperationRate.style_no == piecework_world["style_no"])
        .values(effective_from=business_today() + timedelta(days=1))
    )
    with pytest.raises(BusinessError) as exc:
        await _count(db_session, piecework_world)
    assert exc.value.code == ErrorCode.OPERATION_RATE_NOT_SET


# ============================================================== ③ 绑定


async def test_tc_pw_04a_without_binding_raises_32002(db_session, piecework_world) -> None:
    from app.modules.piecework.models import PieceworkBinding

    await db_session.execute(
        update(PieceworkBinding)
        .where(PieceworkBinding.user_id == piecework_world["employee"].id)
        .values(deleted_at=datetime.now(tz=UTC))
    )
    with pytest.raises(BusinessError) as exc:
        await _count(db_session, piecework_world)
    assert exc.value.code == ErrorCode.PIECEWORK_NOT_BOUND


async def test_tc_pw_04b_yesterdays_binding_is_expired(db_session, piecework_world) -> None:
    """BR-PW-04：**绑定当天有效，次日需重扫**。昨天的绑定今天必须 ``32002``。"""
    from app.modules.piecework.models import PieceworkBinding

    yesterday = business_today() - timedelta(days=1)
    await db_session.execute(
        update(PieceworkBinding)
        .where(PieceworkBinding.user_id == piecework_world["employee"].id)
        .values(work_date=yesterday)
    )
    with pytest.raises(BusinessError) as exc:
        await _count(db_session, piecework_world)
    assert exc.value.code == ErrorCode.PIECEWORK_NOT_BOUND


# ============================================================== ⑤⑥ 件数与金额


async def test_tc_pw_06_default_qty_is_bundle_qty_not_one(db_session, piecework_world) -> None:
    out = await _count(db_session, piecework_world)
    assert out.qty == "60.000", (
        "默认件数必须取 bundles.bundle_qty（numeric(14,3) 的列标度），不能是 1"
    )
    log = (await _logs(db_session, piecework_world["bundle_no"]))[0]
    assert log.qty == BUNDLE_QTY


async def test_tc_pw_07_partial_production(db_session, piecework_world) -> None:
    """BR-PW-27：28/60 件。⚠️ **仍算已计件**（``counted_at`` 非空）——
    判「未计件」看 ``counted_at`` 不看 ``counted_qty``（03-打菲 TC-30-04 同一口径）。"""
    out = await _count(db_session, piecework_world, qty=Decimal(28))
    assert (out.qty, out.bundle_qty) == ("28.000", "60.000")
    bundle = await db_session.get(Bundle, piecework_world["bundle"].id)
    assert bundle is not None
    assert bundle.counted_qty == Decimal(28)
    assert bundle.counted_at is not None


async def test_tc_pw_08_over_production_raises_32006(db_session, piecework_world) -> None:
    with pytest.raises(BusinessError) as exc:
        await _count(db_session, piecework_world, qty=BUNDLE_QTY + 1)
    assert exc.value.code == ErrorCode.PIECEWORK_OVER_PRODUCTION
    assert exc.value.details is not None
    assert exc.value.details["bundle_qty"] == "60.000"


async def test_tc_pw_09_fractional_qty_raises_32005(db_session, piecework_world) -> None:
    with pytest.raises(BusinessError) as exc:
        await _count(db_session, piecework_world, qty=Decimal("28.5"))
    assert exc.value.code == ErrorCode.PIECEWORK_QTY_NOT_INTEGER


async def test_tc_pw_10_non_positive_qty_raises_10001(db_session, piecework_world) -> None:
    with pytest.raises(BusinessError) as exc:
        await _count(db_session, piecework_world, qty=Decimal(0))
    assert exc.value.code == ErrorCode.PARAM_INVALID


async def test_tc_pw_11_amount_is_decimal(db_session, piecework_world) -> None:
    """``0.42 × 60 = 25.2``。⚠️ 选 0.42 就是因为它在二进制浮点下乘不出 25.2
    （``25.199999999999996``）—— 用 0.5 就测不出「全程 Decimal」。"""
    out = await _count(db_session, piecework_world)
    assert out.unit_price == "0.420000", "单价按 numeric(12,6) 的列标度"
    assert out.amount == "25.2000", "0.42 × 60 必须精确落在 numeric(18,4)"


def test_resolve_qty_is_pure() -> None:
    """``resolve_qty`` 是纯函数，直接断言判定顺序（整件 → 超产 → 正数）。

    ⚠️ 60.5 件**同时**非整件与超产，按顺序必须报 ``32005``：报成超产会让人
    以为该去追加手，白多裁一批货。"""
    with pytest.raises(BusinessError) as exc:
        resolve_qty(Decimal("60.5"), BUNDLE_QTY)
    assert exc.value.code == ErrorCode.PIECEWORK_QTY_NOT_INTEGER
    assert resolve_qty(None, BUNDLE_QTY) == BUNDLE_QTY
    assert resolve_qty(Decimal(1), BUNDLE_QTY) == Decimal(1)


# ============================================================== ⑦⑭⑬ 回写与留痕


async def test_tc_pw_12_same_employee_retry_is_idempotent(db_session, piecework_world) -> None:
    """BR-PW-16 ①：同员工重试 → ``code=0`` + ``idempotent=true``，**永不返回 32001**。

    ⚠️ 还要断言 ``log_id`` 相同：**返回首次那条**（``05 §5``），而不是新建一条的
    id —— 前端拿到新 id 会以为这是「第二件活」，今日本站的金额也会累加两次。
    """
    first = await _count(db_session, piecework_world)
    second = await _count(db_session, piecework_world)
    assert first.idempotent is False and second.idempotent is True
    assert first.log_id == second.log_id, "幂等命中必须回首次那条流水，不是新建一条的 id"
    assert len(await _logs(db_session, piecework_world["bundle_no"])) == 1
    assert second.today_hands == 1, "幂等命中不能再 +1 手"
    assert second.today_amount == first.today_amount


async def test_tc_pw_14_mark_counted_and_version_bump(db_session, piecework_world) -> None:
    before = piecework_world["bundle"].version
    await _count(db_session, piecework_world)
    await db_session.refresh(piecework_world["bundle"])
    bundle = piecework_world["bundle"]
    assert bundle.counted_qty == BUNDLE_QTY
    assert bundle.counted_by == piecework_world["employee"].id, "counted_by 是员工不是操作员"
    assert bundle.counted_at is not None
    assert bundle.version == before + 1, "码的并发改动要走乐观锁"


async def test_tc_pw_15_document_log_records_operator_and_employee(
    db_session, piecework_world
) -> None:
    """⑪ 留痕记**两个 id**：``operator_id`` 是工位机上的组长，
    ``piecework_logs.employee_id`` 是干活的员工。⚠️ 这是本卡最容易省掉的一条 ——
    只记一个的话，「这条流水是谁扫的」与「这活是谁做的」永远分不开，
    出了纠纷（同一台机子两个人用）无从查证。"""
    out = await _count(db_session, piecework_world)
    row = (
        await db_session.execute(
            select(DocumentLog).where(
                DocumentLog.doc_type == DOC_TYPE_PIECEWORK,
                DocumentLog.doc_id == out.log_id,
            )
        )
    ).scalar_one()
    assert row.operator_id == ctx_for().user_id
    assert row.operator_id != out.log_id  # 组长 ≠ 流水
    assert row.changed_fields is not None
    assert row.changed_fields["employee_id"] == str(piecework_world["employee"].id)
    assert row.action == "COUNT"


async def test_tc_pw_16_log_row_shape(db_session, piecework_world) -> None:
    """⑧ 落库的形状：``SCAN`` / ``payroll_id IS NULL`` / ``group_no`` 行级冗余 /
    ``unit_price`` 是快照（改基础资料不影响历史，09 §4.1）。"""
    out = await _count(db_session, piecework_world)
    log = (await _logs(db_session, piecework_world["bundle_no"]))[0]
    assert log.log_type == PieceworkLogType.SCAN
    assert log.payroll_id is None
    assert log.group_no == piecework_world["employee"].group_no
    assert log.unit_price == UNIT_PRICE, "单价必须快照，改基础资料不能改历史"
    assert log.amount == Decimal(out.amount)
    assert log.bundle_id == piecework_world["bundle"].id
    assert log.reversal_of_id is None


async def test_tc_pw_17_today_totals_accumulate(db_session, piecework_world) -> None:
    """同一员工第二手：``today_hands`` **+1**（主计数是手，ADR-0016 §6），
    ``today_qty`` 累加件数。⚠️ 断言的是 +1 而不是 +60。"""
    await _count(db_session, piecework_world)
    second_bundle_no = await _second_bundle_no(db_session, piecework_world)
    out = await _count(db_session, piecework_world, bundle_no=second_bundle_no)
    assert out.today_hands == 2
    assert out.today_qty == "120.000"
    assert out.hands == 2 and out.hands_total_of_size == 3, "世界铺了 3 手"


async def _second_bundle_no(db_session: Any, world: dict[str, Any]) -> str:
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
    return rows[1]
