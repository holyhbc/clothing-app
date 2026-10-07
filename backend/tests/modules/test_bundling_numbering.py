"""打菲单号与 `bundle_no` 纯函数测试（T-BUND-002）。

覆盖：
- TC-BN-01: `build_bundle_no` 格式用例（L01 / XL02 / XXL01 / 3XL01）
- TC-BN-02: 尺码码 3 位 `XXL` / `3XL` 不抛错、原样嵌入
- TC-BN-03: `parse_bundle_no` 往返一致
- TC-BN-04: `next_doc_no` 20 并发无重复、无空洞
- TC-BN-05: `take_doc_no` 复用不新建表（`cutting_doc_no_sequences` 出现 `BD` 行）
- TC-BN-06: 跨天重置
"""

import asyncio
import os
from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.numbering import DOC_PREFIX_CUTTING
from app.modules.bundling.service import (
    build_bundle_no,
    is_valid_bundle_no,
    next_doc_no,
    parse_bundle_no,
)

# 复用 cutting 的测试日期常量，避免跨零点
DOC_DATE = date(2026, 10, 18)
DOC_DATE_NEXT = date(2026, 10, 19)


def _db_url() -> str:
    """取测试库连接串（CI 环境由 docker-compose.ci.yml 注入）。"""
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        pytest.skip("未配置 DATABASE_URL，跳过需独立引擎的并发测试")
    return url


# ──────────────────────────────────────────────────────────────────────────────
# 纯函数测试（不需 DB）
# ──────────────────────────────────────────────────────────────────────────────


def test_build_bundle_no_basic() -> None:
    """TC-BN-01：基础格式 `BD-YYYYMMDD-6位-尺码码手序号-件序号`。"""
    doc_no = "BD-20261018-000031"
    assert build_bundle_no(doc_no, "XL", 2) == "BD-20261018-000031-XL02-0001"
    assert build_bundle_no(doc_no, "L", 1) == "BD-20261018-000031-L01-0001"


def test_build_bundle_no_size_code_3_chars() -> None:
    """TC-BN-02：尺码码 3 位 `XXL` / `3XL` 不抛错、原样嵌入。"""
    doc_no = "BD-20261018-000031"
    assert build_bundle_no(doc_no, "XXL", 1) == "BD-20261018-000031-XXL01-0001"
    assert build_bundle_no(doc_no, "3XL", 1) == "BD-20261018-000031-3XL01-0001"
    # 大小写归一化
    assert build_bundle_no(doc_no, "xxl", 1) == "BD-20261018-000031-XXL01-0001"
    assert build_bundle_no(doc_no, "3xl", 1) == "BD-20261018-000031-3XL01-0001"


def test_build_bundle_no_item_seq_default() -> None:
    """件序号默认 0001，显式传 1 等价。"""
    doc_no = "BD-20261018-000031"
    assert build_bundle_no(doc_no, "XL", 2) == build_bundle_no(doc_no, "XL", 2, 1)
    # 预留扩展：显式传 >1（Q-B10 未闭环，但函数允许）
    assert build_bundle_no(doc_no, "XL", 2, 2) == "BD-20261018-000031-XL02-0002"


def test_build_bundle_no_invalid_args() -> None:
    """非法参数抛 ValueError。"""
    doc_no = "BD-20261018-000031"
    with pytest.raises(ValueError, match="size_code 不能为空"):
        build_bundle_no(doc_no, "", 1)
    with pytest.raises(ValueError, match="hands_seq 必须"):
        build_bundle_no(doc_no, "XL", 0)
    with pytest.raises(ValueError, match="hands_seq 必须"):
        build_bundle_no(doc_no, "XL", -1)
    with pytest.raises(ValueError, match="item_seq 必须"):
        build_bundle_no(doc_no, "XL", 1, 0)


def test_parse_bundle_no_roundtrip() -> None:
    """TC-BN-03：parse 与 build 往返一致。"""
    cases = [
        "BD-20261018-000031-L01-0001",
        "BD-20261018-000031-XL02-0001",
        "BD-20261018-000031-XXL01-0001",
        "BD-20261018-000031-3XL01-0001",
        "BD-20261018-000031-XL02-0002",  # 件序号 >1
    ]
    for bn in cases:
        parsed = parse_bundle_no(bn)
        rebuilt = build_bundle_no(
            parsed.doc_no, parsed.size_code, parsed.hands_seq, parsed.item_seq
        )
        assert rebuilt == bn, f"往返不一致: {bn} -> {parsed} -> {rebuilt}"


def test_parse_bundle_no_invalid() -> None:
    """非法格式抛 ValueError。"""
    invalid = [
        "BD-20261018-000031-XL02",  # 缺件序号
        "BD-20261018-000031-XL-0001",  # 手序号缺位
        "BD-20261018-000031-XL2-0001",  # 手序号 1 位
        "BD-20261018-000031-XL02-001",  # 件序号 3 位
        "CT-20261018-000031-XL02-0001",  # 前缀不对
        "BD-20261018-000031-xl02-0001",  # 小写（正则要求大写）
        "BD-20261018-000031-XL02-0001-extra",  # 多段
        "",  # 空串
    ]
    for bn in invalid:
        with pytest.raises(ValueError):
            parse_bundle_no(bn)


def test_is_valid_bundle_no() -> None:
    """格式校验函数。"""
    valid = [
        "BD-20261018-000031-L01-0001",
        "BD-20261018-000031-XL02-0001",
        "BD-20261018-000031-XXL01-0001",
        "BD-20261018-000031-3XL01-0001",
        "BD-20261018-000031-XL02-0002",
    ]
    invalid = [
        "BD-20261018-000031-XL02",
        "BD-20261018-000031-xl02-0001",
        "CT-20261018-000031-XL02-0001",
        "",
    ]
    for bn in valid:
        assert is_valid_bundle_no(bn), f"应合法: {bn}"
    for bn in invalid:
        assert not is_valid_bundle_no(bn), f"应非法: {bn}"


# ──────────────────────────────────────────────────────────────────────────────
# DB 相关测试：next_doc_no 并发、跨天、复用计数器表
# ──────────────────────────────────────────────────────────────────────────────


async def test_next_doc_no_format(db_session: AsyncSession) -> None:
    """单据号格式 `BD-YYYYMMDD-6位`。"""
    no = await next_doc_no(db_session, doc_date=DOC_DATE)
    assert no.startswith("BD-20261018-")
    assert len(no) == len("BD-20261018-000001")
    assert no[-6:].isdigit()


async def test_next_doc_no_sequence_increments(db_session: AsyncSession) -> None:
    """同一天同事务内连续取号递增。"""
    no1 = await next_doc_no(db_session, doc_date=DOC_DATE)
    no2 = await next_doc_no(db_session, doc_date=DOC_DATE)
    seq1 = int(no1[-6:])
    seq2 = int(no2[-6:])
    assert seq2 == seq1 + 1


async def test_concurrent_next_doc_no_unique() -> None:
    """TC-BN-04：20 并发取 `BD` 单号无重复、连续。

    必须用**独立引擎真提交**：同一连接上的语句会被 PostgreSQL 串行化，
    测出来的其实是「顺序执行」。

    ⚠️ 每次都**新建 session + 真 commit** —— 只有真提交才让下一个事务看见
    计数器递增，否则 20 个并发全都在同一份未提交的 ``next_no`` 上取号。
    """
    url = _db_url()
    engine = create_async_engine(url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:

        async def take_one() -> str:
            async with factory() as session, session.begin():
                return await next_doc_no(session, doc_date=DOC_DATE)

        numbers = await asyncio.gather(*(take_one() for _ in range(20)))
        assert len(set(numbers)) == 20, f"取号重复了：{sorted(numbers)}"
        seqs = sorted(int(no[-6:]) for no in numbers)
        assert seqs == list(range(seqs[0], seqs[0] + 20)), f"取到的号不连续：{numbers}"
        assert all(no.startswith("BD-20261018-") for no in numbers)
    finally:
        await engine.dispose()


async def test_next_doc_no_resets_each_day(db_session: AsyncSession) -> None:
    """TC-BN-06：跨天重置，新一天从 000001 开始。

    ⚠️ 只断言相对关系，不断言绝对值：并发用例用**独立引擎真提交**了
    20 个号，那些提交不在任何用例回滚范围内。
    """
    day1_first = await next_doc_no(db_session, doc_date=DOC_DATE)
    day1_second = await next_doc_no(db_session, doc_date=DOC_DATE)
    day2_first = await next_doc_no(db_session, doc_date=DOC_DATE_NEXT)

    assert int(day1_second[-6:]) == int(day1_first[-6:]) + 1, "同一天必须逐个递增"
    assert day2_first == "BD-20261019-000001", "换一天就该从头开始"
    assert day2_first != day1_second


async def test_next_doc_no_reuses_counter_table(db_session: AsyncSession) -> None:
    """TC-BN-05：复用 `cutting_doc_no_sequences` 表，**不新建表**。

    验证：插入 `prefix='BD'` 的计数器行后，同表能被 cutting 复用（prefix='CT'）。
    """
    # 先取一个 BD 号（会在 cutting_doc_no_sequences 插入 prefix='BD' 行）
    await next_doc_no(db_session, doc_date=DOC_DATE)

    # 直接查表确认行存在
    from app.modules.cutting.models import CuttingDocNoSequence

    result = await db_session.execute(
        select(CuttingDocNoSequence).where(
            CuttingDocNoSequence.prefix == "BD",
            CuttingDocNoSequence.doc_date == DOC_DATE,
        )
    )
    row = result.scalar_one_or_none()
    assert row is not None, "cutting_doc_no_sequences 应有 prefix='BD' 行"
    assert row.next_no >= 2, "计数器应已递增（下一个可用号）"

    # 再取一个 CT 号，确认同表共存不冲突
    from app.core.numbering import take_doc_no

    ct_no = await take_doc_no(db_session, prefix=DOC_PREFIX_CUTTING, doc_date=DOC_DATE)
    assert ct_no.startswith("CT-20261018-")


# ──────────────────────────────────────────────────────────────────────────────
# 导入可用性测试
# ──────────────────────────────────────────────────────────────────────────────


def test_service_exports() -> None:
    """`from app.modules.bundling.service import build_bundle_no, next_doc_no` 可用。"""
    from app.modules.bundling.service import (
        build_bundle_no as _build,
    )
    from app.modules.bundling.service import (
        is_valid_bundle_no as _valid,
    )
    from app.modules.bundling.service import (
        next_doc_no as _next,
    )
    from app.modules.bundling.service import (
        parse_bundle_no as _parse,
    )

    assert callable(_build)
    assert callable(_next)
    assert callable(_parse)
    assert callable(_valid)
