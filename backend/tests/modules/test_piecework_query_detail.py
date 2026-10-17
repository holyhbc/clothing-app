"""计件只读查询用例 —— **详情 / 汇总 / 工位机 / 结算筛选**（``04-计件 §6.1``）。

======================================  ==========================================
TC-PW-Q04                             详情：本体 + ``bundle`` 子对象
TC-PW-Q06                             ★ 汇总：**手数与件数并列**（BR-PW-31）
TC-PW-Q06b                            汇总：按款号 / 按工序分组
TC-PW-Q07                             工位机今日卡：按 ``work_date`` 而非 ``scanned_at``
TC-PW-Q07b                            工位机：只算自己的量
TC-PW-Q08                             非法 ``group_by`` → 422（路由层 pattern 挡住）
TC-PW-Q08b                            未知 ``sort`` **回落默认**，且不构成注入
TC-PW-Q09                             ``is_settled`` 按 ``payroll_id`` 判（不是布尔列）
TC-PW-Q09b                            ★ 跨零点扫码算**前一天**（BR-PW-11）
======================================  ==========================================

## TC-PW-Q09b 的两个坑

① 时间戳必须**按 ``Asia/Shanghai`` 直接构造 23:58** —— 写成 UTC 的 23:58 再
``astimezone``，换算出来是上海时间**次日** 07:58，用例想验的跨零点根本没造出来。
② 绑定是**当天的**（BR-PW-04），所以要计「昨天 23:58」的件，得有**昨天**的绑定；
而单价的生效日也得覆盖到昨天 —— 少了任一条，报错会是 32002 / 20004，
指向「绑定坏了 / 没设价」，跟「跨零点」这个被测点毫无关系。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import update

from app.core.numbering import BUSINESS_TZ, business_today
from app.modules.piecework.models import PieceworkLog
from app.modules.piecework.service import PieceworkService
from tests.factories.payroll import seed_settlement
from tests.factories.piecework_world import bind, ctx_for
from tests.modules.pw_query_helpers import (
    LOGS,
    READ_PERMS,
    STATION,
    SUMMARY,
    bundle_nos,
    hdr,
    seed,
)


async def test_tc_pw_q04_detail(client, db_session, auth_headers, piecework_world) -> None:
    ids = await seed(db_session, piecework_world, count=1)
    headers = await hdr(db_session, auth_headers, piecework_world, READ_PERMS)
    data = (await client.get(f"{LOGS}/{ids[0]}", headers=headers)).json()["data"]
    assert data["log_id"] == str(ids[0])
    assert data["reversed_by"] == [] and data["reversal_of"] is None
    assert data["bundle"] is not None
    # ⚠️ bundle 子对象必须带「这手共几件 / 已计几件 / 谁计的」——
    # 这正是「这手是不是没做完」的判据（BR-PW-32）
    assert set(data["bundle"]) >= {"hands", "bundle_qty", "counted_qty", "counted_by_name"}


async def test_tc_pw_q06_summary_hands_and_qty(
    client, db_session, auth_headers, piecework_world
) -> None:
    """★ 手数与件数**并列**（BR-PW-31、ADR-0016 §6）：车间的主计数是「手」，
    只给件数的话主管看到的是「做完了 180 件」而不是「做了 3 手」。"""
    await seed(db_session, piecework_world, count=3)
    headers = await hdr(db_session, auth_headers, piecework_world, READ_PERMS)
    rows = (await client.get(f"{SUMMARY}?group_by=employee", headers=headers)).json()["data"]
    assert len(rows) == 1
    row = rows[0]
    assert row["total_hands"] == 3, "手数必须是 COUNT(*)"
    assert Decimal(row["total_qty"]) == Decimal(180), "件数是 SUM(qty)"
    assert row["employee_name"] == piecework_world["employee"].name


async def test_tc_pw_q06b_summary_group_by_style(
    client, db_session, auth_headers, piecework_world
) -> None:
    await seed(db_session, piecework_world, count=2)
    headers = await hdr(db_session, auth_headers, piecework_world, READ_PERMS)
    by_style = (await client.get(f"{SUMMARY}?group_by=style", headers=headers)).json()["data"]
    by_op = (await client.get(f"{SUMMARY}?group_by=operation", headers=headers)).json()["data"]
    assert by_style[0]["style_no"] == piecework_world["style_no"]
    assert by_op[0]["total_hands"] == 2


async def test_tc_pw_q08_bad_group_by(client, db_session, auth_headers, piecework_world) -> None:
    """非法 ``group_by`` → 422（FastAPI 的 pattern 挡在路由层，不进 service）。"""
    headers = await hdr(db_session, auth_headers, piecework_world, READ_PERMS)
    response = await client.get(f"{SUMMARY}?group_by=workshop", headers=headers)
    assert response.status_code == 422


async def test_tc_pw_q08b_unknown_sort_falls_back(
    client, db_session, auth_headers, piecework_world
) -> None:
    """未知 ``sort`` **回落默认**而不是报错：排序是锦上添花，
    整个列表因为 ``sort=empyty_name`` 打不开是更糟的结果。

    ⚠️ 关键在于它**必须是白名单**：未知值直接拼进 ORDER BY 就是注入入口。"""
    await seed(db_session, piecework_world, count=2)
    headers = await hdr(db_session, auth_headers, piecework_world, READ_PERMS)
    evil = await client.get(f"{LOGS}?sort=);DROP TABLE piecework_logs;--", headers=headers)
    assert evil.status_code == 200, evil.text
    assert len(evil.json()["data"]["items"]) == 2


async def test_tc_pw_q07_station_today(client, db_session, auth_headers, piecework_world) -> None:
    """工位机今日卡。⚠️ **按 ``work_date`` 而非 ``scanned_at`` 的日期区间**（BR-PW-11）：
    用 ``scanned_at`` 会让「昨晚 23:58 扫的」在今天早上查不到。"""
    await seed(db_session, piecework_world, count=2, station="WS-01")
    last_no = (await bundle_nos(db_session, piecework_world, 2))[1]
    headers = await hdr(db_session, auth_headers, piecework_world, READ_PERMS)
    data = (await client.get(f"{STATION}/WS-01/today", headers=headers)).json()["data"]
    assert data["today_hands"] == 2
    assert Decimal(data["today_qty"]) == Decimal(120)
    assert data["work_date"] == business_today().isoformat()
    assert data["last_bundle_no"] == last_no, "最近一次扫码是**最后扫的那一手**，不是第一手"

    other = (await client.get(f"{STATION}/WS-99/today", headers=headers)).json()["data"]
    assert other["today_hands"] == 0 and other["last_bundle_no"] is None


async def test_tc_pw_q07b_station_counts_only_that_station(
    client, db_session, auth_headers, piecework_world
) -> None:
    """工位机的数**只算自己的** —— 两台机子各扫各的，
    工位机 A 的界面上不能出现工位机 B 的量。"""
    await seed(db_session, piecework_world, count=2, station="WS-01")
    headers = await hdr(db_session, auth_headers, piecework_world, READ_PERMS)
    a = (await client.get(f"{STATION}/WS-01/today", headers=headers)).json()["data"]
    b = (await client.get(f"{STATION}/WS-02/today", headers=headers)).json()["data"]
    assert a["today_hands"] == 2 and b["today_hands"] == 0


async def test_tc_pw_q09_is_settled_uses_payroll_id(
    client, db_session, auth_headers, piecework_world
) -> None:
    """★ ``is_settled`` 的判据是 ``payroll_id IS NULL``，**不是布尔列**（04 §7.1）：
    两个来源必然漂移，而结算回写是 service 行为、不允许手工填。

    ⚠️ 这里直接 UPDATE ``payroll_id`` 来造「已结算」—— 只造最小真实结算单满足 FK
    （迁移 0020 起 ``payroll_id`` 有外键），这条用例要验的是**筛选条件用哪一列**，
    不是结算功能。"""
    ids = await seed(db_session, piecework_world, count=3)
    settlement = await seed_settlement(db_session, workshop_id=piecework_world["workshop_id"])
    await db_session.execute(
        update(PieceworkLog).where(PieceworkLog.id == ids[0]).values(payroll_id=settlement.id)
    )
    headers = await hdr(db_session, auth_headers, piecework_world, READ_PERMS)

    settled = (await client.get(f"{LOGS}?is_settled=true", headers=headers)).json()["data"]
    assert settled["total"] == 1
    assert settled["items"][0]["log_id"] == str(ids[0])
    unsettled = (await client.get(f"{LOGS}?is_settled=false", headers=headers)).json()["data"]
    assert unsettled["total"] == 2

    summary = (await client.get(f"{SUMMARY}?group_by=employee", headers=headers)).json()["data"]
    assert summary[0]["total_hands"] == 3, "汇总按手数，结算与否不影响手数口径"


async def test_tc_pw_q09b_work_date_range(
    client, db_session, auth_headers, piecework_world
) -> None:
    """★ 跨零点扫码算**前一天**（BR-PW-11）。见模块 docstring 的两个坑。"""
    yesterday = business_today() - timedelta(days=1)
    late = datetime(yesterday.year, yesterday.month, yesterday.day, 23, 58, tzinfo=BUSINESS_TZ)
    no = (await bundle_nos(db_session, piecework_world, 1))[0]
    # ⚠️ **绑定是当天的**（BR-PW-04）：要计「昨天 23:58」的件，得有昨天的绑定。
    await bind(
        db_session,
        user=piecework_world["employee"],
        bundle=piecework_world["bundle"],
        operation_no=piecework_world["operation_no"],
        workshop_id=piecework_world["workshop_id"],
        work_date=yesterday,
    )
    await PieceworkService(db_session).count_piecework(
        bundle_no=no,
        employee_id=piecework_world["employee"].id,
        scanned_at=late,
        qty=None,
        operator_id=ctx_for().user_id,
        ctx=ctx_for(),
    )
    headers = await hdr(db_session, auth_headers, piecework_world, READ_PERMS)
    today_only = await client.get(
        f"{LOGS}?work_date_from={business_today()}&work_date_to={business_today()}", headers=headers
    )
    assert today_only.json()["data"]["total"] == 0, (
        "跨零点的扫码要算前一天，不能因为时间戳是今天就算今天"
    )
    both = await client.get(f"{LOGS}?work_date_from={yesterday}", headers=headers)
    assert both.json()["data"]["total"] == 1
    assert UUID(str(both.json()["data"]["items"][0]["log_id"])) is not None
