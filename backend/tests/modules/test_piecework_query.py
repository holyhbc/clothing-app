"""计件只读查询用例 —— **列表 / 导出 / 数据范围**（``04-计件 §6.1``）。

======================================  ==========================================
TC-PW-Q01                             列表：默认排序、分页、总数与行数一致
TC-PW-Q01b                            列表行字段齐 + ``bundle_no`` 完整不截断
TC-PW-Q02                             ★ 列表与导出**同一筛选 → 同一批数据**（07 §3.2 铁律 3）
TC-PW-Q03                             ★ 数据范围：FACTORY / WORKSHOP / SELF 三档
TC-PW-Q05                             ★ 导出：双权限点
TC-PW-Q05b                            ★ 导出：**BOM 真在字节里** + 列顺序 + 金额原样
TC-PW-Q05c                            导出超上限**报错**而不是静默截断
======================================  ==========================================

## TC-PW-Q03 是本卡最要紧的一条

列表 / 导出 / 汇总三个端点都调 ``apply_data_scope``，但那只是**看起来**在过滤 ——
真正要验的是「换个 token 真的少了行」。而导出尤其重要：它是绕过界面直接拿数据
的地方，页面上看不见车间以外的东西、导出的 CSV 里却有，是审查看不出来的那种缺陷。
"""

from __future__ import annotations

import csv
import io

import pytest

from app.common.enums import DataScope
from tests.modules.pw_query_helpers import (
    EXPORT_PERMS,
    LOGS,
    READ_PERMS,
    hdr,
    seed,
    self_hdr,
)


async def test_tc_pw_q01_list_paginates(client, db_session, auth_headers, piecework_world) -> None:
    ids = await seed(db_session, piecework_world, count=3)
    headers = await hdr(db_session, auth_headers, piecework_world, READ_PERMS)
    first = await client.get(f"{LOGS}?page=1&size=2", headers=headers)
    assert first.status_code == 200, first.text
    page1 = first.json()["data"]
    assert page1["total"] == 3
    assert len(page1["items"]) == 2
    second = (await client.get(f"{LOGS}?page=2&size=2", headers=headers)).json()["data"]
    assert len(second["items"]) == 1
    # ⚠️ 两页的 id 合起来必须**恰好**是那 3 条、且无重复 —— 排序键不唯一时
    # 「同一行在两页各出现一次」看起来就像分页坏了
    seen = [row["log_id"] for row in page1["items"] + second["items"]]
    assert sorted(seen) == sorted(str(i) for i in ids)
    assert page1["page"] == 1 and page1["page_size"] == 2


async def test_tc_pw_q01b_list_row_shape(client, db_session, auth_headers, piecework_world) -> None:
    """列表行必须带齐 §6.1 的字段。⚠️ ``bundle_no`` **完整不截断**（BR-PW-30）——
    前端要拿它扫回那一张单，截断过的码扫不回来。"""
    await seed(db_session, piecework_world, count=1)
    headers = await hdr(db_session, auth_headers, piecework_world, READ_PERMS)
    row = (await client.get(LOGS, headers=headers)).json()["data"]["items"][0]
    for field in (
        "bundle_no",
        "style_no",
        "operation_no",
        "color_name",
        "size_name",
        "employee_no",
        "employee_name",
        "group_no",
        "work_date",
        "hands",
        "bundle_qty",
        "qty",
        "unit_price",
        "amount",
        "log_type",
        "is_settled",
    ):
        assert field in row, f"列表行缺 {field}"
    assert row["bundle_no"] == piecework_world["bundle_no"]
    assert isinstance(row["amount"], str), "金额必须是字符串（05 §3）"
    assert row["is_settled"] is False


async def test_tc_pw_q02_export_matches_list(
    client, db_session, auth_headers, piecework_world
) -> None:
    """★ 导出与列表**同一筛选 → 同一批数据**。另写一条导出查询的话，
    「列表看到的」与「导出的」会在对账时不一致 —— 而对账正是导出存在的理由。"""
    await seed(db_session, piecework_world, count=3)
    headers = await hdr(db_session, auth_headers, piecework_world, EXPORT_PERMS)
    listed = (await client.get(f"{LOGS}?size=200", headers=headers)).json()["data"]["items"]
    exported = await client.get(f"{LOGS}/export", headers=headers)
    assert exported.status_code == 200, exported.text
    rows = list(csv.DictReader(io.StringIO(exported.content.decode("utf-8-sig"))))
    assert len(rows) == len(listed)
    assert {r["bundle_no"] for r in rows} == {r["bundle_no"] for r in listed}
    filtered = await client.get(
        f"{LOGS}/export?style_no={piecework_world['style_no']}", headers=headers
    )
    assert filtered.headers["X-Row-Count"] == str(len(listed))


async def test_tc_pw_q03_data_scope(
    client, db_session, auth_headers, piecework_world, other_piecework_world
) -> None:
    """★ 三档数据范围看到的行数不同。

    ⚠️ 需要**第二个车间**才能验 WORKSHOP 的过滤：同一个车间的两个 token 看到的
    一样多，那条用例等于没验。而 SELF 的那半最容易漏 —— 员工端令牌一旦拿到
    工位机接口上，看到的就是别人的工钱。"""
    await seed(db_session, piecework_world, count=2)
    await seed(db_session, other_piecework_world, count=1)

    factory = await hdr(db_session, auth_headers, piecework_world, READ_PERMS)
    mine = await hdr(
        db_session,
        auth_headers,
        piecework_world,
        READ_PERMS,
        data_scope=DataScope.WORKSHOP,
        workshop_id=piecework_world["workshop_id"],
    )
    others = await hdr(
        db_session,
        auth_headers,
        other_piecework_world,
        READ_PERMS,
        data_scope=DataScope.WORKSHOP,
        workshop_id=other_piecework_world["workshop_id"],
    )
    self_scope = await self_hdr(db_session, auth_headers, piecework_world["employee"])

    assert len((await client.get(LOGS, headers=factory)).json()["data"]["items"]) == 3
    assert len((await client.get(LOGS, headers=mine)).json()["data"]["items"]) == 2
    assert len((await client.get(LOGS, headers=others)).json()["data"]["items"]) == 1
    assert len((await client.get(LOGS, headers=self_scope)).json()["data"]["items"]) == 2

    # ⚠️ 导出也要过滤：它是绕过界面直接拿数据的地方。
    #    导出**额外**要 piecework:export + system:export:manage，所以另发一个
    #    「别人的车间 + 导出权限」的 token。
    others_export = await hdr(
        db_session,
        auth_headers,
        other_piecework_world,
        EXPORT_PERMS,
        data_scope=DataScope.WORKSHOP,
        workshop_id=other_piecework_world["workshop_id"],
    )
    exported = await client.get(f"{LOGS}/export", headers=others_export)
    assert exported.headers["X-Row-Count"] == "1"


async def test_tc_pw_q10_detail_scope_denial(
    client, db_session, auth_headers, piecework_world, other_piecework_world
) -> None:
    """★ 查不到与越权**都**回 12002：分开就等于泄漏「这条记录确实存在」。

    ⚠️ 拿**别人车间的真实 id** 去查（不是随便编一个 uuid）：
    编一个 uuid 的话，「不存在」与「无权」两条路径会走同一段代码，
    测出来是绿的，而真·无权访问那条根本没被验到。"""
    ids = await seed(db_session, other_piecework_world, count=1)
    headers = await hdr(
        db_session,
        auth_headers,
        piecework_world,
        READ_PERMS,
        data_scope=DataScope.WORKSHOP,
        workshop_id=piecework_world["workshop_id"],
    )
    denied = await client.get(f"{LOGS}/{ids[0]}", headers=headers)
    assert denied.status_code == 403
    assert denied.json()["code"] == 12002

    from uuid import uuid4

    missing = await client.get(f"{LOGS}/{uuid4()}", headers=headers)
    assert missing.status_code == 403
    assert missing.json()["code"] == 12002


async def test_tc_pw_q05_export_needs_two_permissions(
    client, db_session, auth_headers, piecework_world
) -> None:
    """导出要 **两个**权限点。只给 ``piecework:read`` 的人能看到列表，
    但导不出来 —— 导出是绕过界面的口子，比界面更容易泄露。"""
    await seed(db_session, piecework_world, count=1)
    read_only = await hdr(db_session, auth_headers, piecework_world, READ_PERMS)
    no_global = await hdr(
        db_session, auth_headers, piecework_world, ("piecework:read", "piecework:export")
    )
    full = await hdr(db_session, auth_headers, piecework_world, EXPORT_PERMS)

    assert (await client.get(f"{LOGS}/export", headers=read_only)).status_code == 403
    assert (await client.get(f"{LOGS}/export", headers=no_global)).status_code == 403
    assert (await client.get(f"{LOGS}/export", headers=full)).status_code == 200


async def test_tc_pw_q05b_export_bom_and_columns(
    client, db_session, auth_headers, piecework_world
) -> None:
    """BOM 必须**真在字节里**（Excel 不看 charset 参数去猜编码，没 BOM 按 GBK 打开
    → 中文全乱码，而「生成成功、下载打不开」排查起来极贵）。

    ⚠️ 金额**原样**输出：转 float 再 ``str()`` 会把 ``0.42 × 60`` 变成
    ``25.199999999999996``，对账的人会以为系统算错了。"""
    await seed(db_session, piecework_world, count=1)
    headers = await hdr(db_session, auth_headers, piecework_world, EXPORT_PERMS)
    response = await client.get(f"{LOGS}/export", headers=headers)
    raw = response.content
    assert raw.startswith(b"\xef\xbb\xbf"), "缺少 UTF-8 BOM"
    text = raw.decode("utf-8-sig")
    header_line = text.splitlines()[0]
    assert header_line.split(",")[:4] == ["work_date", "workshop_name", "group_no", "style_no"]
    assert "25.2000" in text, "金额必须是原样字符串，不能是 float 化的结果"
    assert "attachment;" in response.headers["Content-Disposition"]


async def test_tc_pw_q05c_export_over_limit_is_rejected(
    client, db_session, auth_headers, piecework_world, monkeypatch: pytest.MonkeyPatch
) -> None:
    """超过上限**报错**而不是静默截断：静默截断会让人拿一份不完整的数据去对账，
    而对账「平了」的假象比报错贵得多。"""
    from app.modules.piecework import query_service

    assert query_service.EXPORT_LIMIT == 500, "先钉住默认值，免得下面的 monkeypatch 悄悄改了它"
    monkeypatch.setattr(query_service, "EXPORT_LIMIT", 1)
    await seed(db_session, piecework_world, count=3)
    headers = await hdr(db_session, auth_headers, piecework_world, EXPORT_PERMS)
    response = await client.get(f"{LOGS}/export", headers=headers)
    assert response.status_code == 422
    assert response.json()["code"] == 10001
    assert "缩小日期范围" in response.json()["message"]
