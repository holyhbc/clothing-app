"""错误码与 docs/05 §4 的一致性（docs/12-文档与变更归档规范.md §9 检查项 3）。

这是**规范一致性闸门**：``ErrorCode`` 的取值必须是 docs/05 §4 已登记的码，
且**已实现段**不允许缺实现。防止 AI 自行编造错误码。

⚠️ **REV-2026-10 这条守卫原来有个洞，T-CUT-001b 补上**：原文的
``test_generic_segments_fully_implemented`` 只 ``parametrize`` 了
``["10", "11", "12"]`` 三个段，于是 **20xxx / 30xxx / 40xxx 在 ``05 §4`` 里登记了
却一个都没实现、也没有任何守卫会报** —— 事实是 ``30002`` / ``30006`` / ``40001`` /
``40006`` 一直缺实现，而裁剪单的错误码全在这个范围里。
本卡补实现的同时把守卫扩成 :data:`IMPLEMENTED_SEGMENTS`，
并加 :func:`test_unimplemented_segments_have_no_codes` 反向守住另一头。
"""

import re
from enum import IntEnum
from pathlib import Path

import pytest

from app.core.errors import HTTP_STATUS_BY_CODE, BusinessError, ErrorCode

DOC_PATH = Path(__file__).resolve().parents[2] / "docs" / "05-接口设计规范.md"

#: docs/05 §4 表格里登记的 HTTP 状态（节选自该表，用于交叉校验）
DOCUMENTED_HTTP: dict[int, int] = {
    10001: 422,
    10002: 400,
    10003: 409,
    10004: 429,
    10005: 409,
    10006: 422,
    10007: 409,
    10008: 409,
    10009: 413,
    11001: 401,
    11002: 401,
    11003: 401,
    11004: 403,
    11007: 422,
    11008: 422,
    11011: 422,
    12001: 403,
    12002: 403,
    20001: 404,
    20002: 409,
    20003: 409,
    20004: 404,
    20005: 422,
    20006: 422,
    20007: 422,
    99999: 500,
}


def _parse_documented_codes() -> set[int]:
    """解析 docs/05 §4 表格第一列，取出去重后的错误码集合。"""
    text = DOC_PATH.read_text(encoding="utf-8")
    section = text.split("**完整清单**")[1].split("**错误响应**")[0]
    found = re.findall(r"^\|\s*`(\d+)`\s*\|", section, re.MULTILINE)
    assert found, "docs/05 §4 未解析到任何错误码，表格结构可能变了"
    return {int(code) for code in found}


DOCUMENTED_CODES = _parse_documented_codes()

#: **特殊码**：不属于任何业务段，不能用「前两位 = 段」那套逻辑去卡它们。
#: ``0`` 是 OK（成功响应，不是错误）；``99999`` 是 INTERNAL（docs/05 §4 的兜底段）。
SPECIAL_CODES: frozenset[int] = frozenset({0, 99999})

#: **已登记但本阶段刻意不实现**的错误码 → 归属卡。
#:
#: ⚠️ 为什么要显式列出来，而不是把段整个排除：段内部常常**横跨阶段**。
#: 真实例子是 ``40xxx`` —— ``40001``（库存不足）与 ``40006``（布批可用量不足）
#: 裁剪单立刻就要用（T-CUT-001b-2 的 C38），而 ``40002``~``40005`` / ``40007``
#: 分别是「批次成本锁定」「库存与台账不平」「整件入库」「采购批次已被消耗」
#: 「成衣入库与 WIP 不符」—— **全部要等库存业务（`00 §7` 的 P3）**，
#: 而库存业务一张表都还没建。按段整体排除会让 ``40006`` 一起失去守卫，
#: 按段整体纳入会让这条守卫天天红。**所以按码列。**
#:
#: ⚠️ 方向是「实现了就删」。留着一条已实现的码在这里不是无害的 ——
#: :func:`test_deferred_codes_are_really_deferred` 会立刻报出来。
DEFERRED_CODES: dict[int, str] = {
    # ---- 32xxx 计件（P2；T-BUND-007b 随「单码作废 / 反审核不可碰已计件码」登记了 32003）----
    32001: "P2 计件：同一打菲号本工序已被他人计件（由 04-计件 触发）",
    32002: "P2 计件：该员工未绑定此工序",
    32004: "P2 计件：同一打菲号同一工序多人重复计件（Q-B12 与 32001 语义重叠，待 05 §4 定论）",
    32005: "P2 计件：数量必须为整件（尾数不计件、不入库）",
    32006: "P2 计件：计件数量超过该手件数（超产，ADR-0016 §4）",
    # ---- 31xxx 补码（打菲；2026-10-08 由「已实现」退回「推迟」）----
    # 31006 曾以「已计件的码不可改手数 → 31006」登记在 B23，但 **B23 第八批把该条款作废了**：
    # 审核后只可**增手**（增手只补生成新码、**不碰任何已存在的码**，物理上无需拦截），
    # **减手走 `reverse`** 而 `reverse` 早已用 32003 拦截「任一码已计件」。
    # 所以这条规则**物理上不可能有抛出点** —— 与其留一个永不触发的码，
    # 不如退回来明说「留给 Q-B19（裁剪改单 → 打菲联动）」。
    31006: "Q-B19（裁剪改单 → 打菲码联动）未单开卡前不需要；B23 第八批已作废其抛出场景",
    # ---- 40xxx 库存业务（P3，00 §7 方案 A 的「库存业务」阶段）----
    40002: "P3 批次成本锁定（ADR-0011 改价走红冲）",
    40003: "P3 库存与台账对账（v_stock_reconciliation 恒 0 行）",
    40004: "P3 完工入库整件校验（依赖 finished_goods_stocks）",
    40005: "P3 采购批次已被下游裁剪消耗（依赖 purchase_orders）",
    40007: "P3 成衣入库与 WIP 结存比对（依赖 wip_stocks 全链路）",
}

#: **本仓当前声称「已实现」的错误码段**（前两位）。
#:
#: ⚠️ 这份名单的**方向是「实现了就加进来」**，加进来就意味着
#: 「``05 §4`` 登记的每一个码都必须有实现（``DEFERRED_CODES`` 里显式推迟的除外）」。
#: 往没在名单里的段里塞一个码是**静默**的，而那个码一旦进了
#: ``HTTP_STATUS_BY_CODE`` 就会**看起来**已经支持了 —— 读代码的人不会去查
#: 它属于哪个阶段。由 :func:`test_unimplemented_segments_have_no_codes` 反向守住。
#:
#: 未进名单的段与它们的归属卡：
#: - ``32`` 计件 → P2
#: - ``33`` 工资 → P2
#: - ``50`` 采购 / 销售 / 财务往来 → P4 / P5
#: - ``60`` 报表与导出 → P6
#: ``32`` 进名单是 T-BUND-007b 干的：单码作废与反审核要报 ``32003``（已计件不可动），
#: 而同段其余五个码全部归 P2 计件模块，逐条列进 :data:`DEFERRED_CODES`（按码列，
#: 理由同 40xxx 那段：段内部横跨阶段，按段整体纳入会让守卫天天红）。
#: ``31`` 进名单是 T-BUND-004 干的：拆分算法要报 ``31003``（非整件）与 ``31005``
#: （手序号重复），而这条守卫明确要求「往一个段里加码就要把整段补齐」—— 否则
#: ``31001``~``31007`` 会停在「docs/05 §4 已登记但没人实现」的状态，而守卫正是为了防它。
#: 多数码由 T-BUND-005a / 005b / 006 / 007 触发，本卡实际只用 ``31003``。
IMPLEMENTED_SEGMENTS: tuple[str, ...] = ("10", "11", "12", "20", "30", "31", "32", "40")


def _segment_of(code: int) -> str | None:
    """码所属的段（前两位）；特殊码返回 ``None``。"""
    return None if code in SPECIAL_CODES else str(code)[:2]


def test_errorcode_values_are_all_documented() -> None:
    """TC-I04：ErrorCode 的每个取值都必须在 docs/05 §4 登记过。"""
    implemented = {int(code) for code in ErrorCode}
    undocumented = implemented - DOCUMENTED_CODES
    assert not undocumented, f"以下错误码未在 docs/05 §4 登记，禁止临时编造：{sorted(undocumented)}"


@pytest.mark.parametrize(
    ("code", "expected_http"),
    sorted(DOCUMENTED_HTTP.items()),
)
def test_http_status_matches_document(code: int, expected_http: int) -> None:
    """TC-I08：错误码 → HTTP 状态码必须与 docs/05 §4 表格一致。"""
    error_code = ErrorCode(code)
    assert HTTP_STATUS_BY_CODE[error_code] == expected_http


@pytest.mark.parametrize("segment", IMPLEMENTED_SEGMENTS)
def test_implemented_segments_are_fully_implemented(segment: str) -> None:
    """已实现段里 ``docs/05 §4`` 登记的码必须全部有实现，不允许缺。

    ``DEFERRED_CODES`` 里显式推迟的除外 —— 它们要么删掉、要么补实现，没有第三种状态。

    ⚠️ 这条与 :func:`test_unimplemented_segments_have_no_codes`、
    :func:`test_deferred_codes_are_really_deferred` **成对**：只守正向的话，
    「往未实现段里偷偷加一个码」是静默的，而「推迟一个已经实现的码」也是静默的。
    """
    documented = {code for code in DOCUMENTED_CODES if _segment_of(code) == segment}
    implemented = {int(code) for code in ErrorCode}
    missing = documented - implemented - set(DEFERRED_CODES)
    assert not missing, f"docs/05 §4 已登记但未实现的 {segment}xxx 错误码：{sorted(missing)}"


def test_unimplemented_segments_have_no_codes() -> None:
    """**未**列入 :data:`IMPLEMENTED_SEGMENTS` 的段，必须一个码都没实现。

    ⚠️ 反向守卫。作用是让「这个码属于哪个阶段」变成**机器可查**的事实，
    而不是靠读代码时心里默认。往未实现段里加码时必须同时把该段加进
    :data:`IMPLEMENTED_SEGMENTS` —— 那一步会让正向守卫报出该段还缺哪些码，
    也就是强制你把那一段补齐或明确推迟。
    """
    pending = {
        segment
        for segment in {_segment_of(code) for code in DOCUMENTED_CODES} - {None}
        if segment not in IMPLEMENTED_SEGMENTS
    }
    implemented = {int(code) for code in ErrorCode}
    smuggled = sorted(
        code for code in implemented if (seg := _segment_of(code)) is not None and seg in pending
    )
    assert not smuggled, (
        f"这些错误码落在「未实现段」{sorted(pending)} 里：{smuggled}。"
        f"要么把该段加进 IMPLEMENTED_SEGMENTS（并补齐该段其余已登记的码），"
        f"要么先确认这个码的归属阶段 —— 混进去的后果是它看起来已经支持了"
    )


def test_deferred_codes_are_really_deferred() -> None:
    """:data:`DEFERRED_CODES` 里的每一条都必须**真的还没实现、且真的在 §4 登记过**。

    ⚠️ 双向守。这份名单烂掉的两种方式都很便宜、后果都很贵：

    - **条目已实现却没删** → 那条码其实已经可用了，而清单说它没有。
      有人按清单去「重新实现」一遍，或者反过来因为「它还没实现」而不去处理它。
    - **条目不在 §4** → 清单里躺着一个谁也没登记过的码，等于给未来的自己
      发一张可以「按惯例实现」的假通行证。
    """
    implemented = {int(code) for code in ErrorCode}
    already_done = sorted(set(DEFERRED_CODES) & implemented)
    assert not already_done, f"DEFERRED_CODES 里的 {already_done} 已经实现了 —— 从清单删掉"
    unknown = sorted(set(DEFERRED_CODES) - DOCUMENTED_CODES)
    assert not unknown, f"DEFERRED_CODES 里的 {unknown} 不在 docs/05 §4 登记过"


def test_http_status_mapping_is_total() -> None:
    """每个 ErrorCode 都必须有 HTTP 状态映射，否则会 KeyError。"""
    for code in ErrorCode:
        assert code in HTTP_STATUS_BY_CODE, f"{code} 缺少 HTTP 状态映射"


def test_business_error_carries_details_and_status() -> None:
    """details 透传；http_status 取自映射表；repr 便于日志排查。"""
    err = BusinessError(ErrorCode.BASE_DATA_NOT_FOUND, "颜色不存在", {"color_code": "WHT"})
    assert err.details == {"color_code": "WHT"}
    assert err.http_status == 404
    assert "颜色不存在" in repr(err)


def test_business_error_details_default_empty_dict() -> None:
    """不传 details 时是空 dict 而不是 None，避免调用方到处判空。"""
    err = BusinessError(ErrorCode.PARAM_INVALID, "参数不合法")
    assert err.details == {}
    assert err.http_status == 422


def test_errorcode_is_int_enum() -> None:
    """ErrorCode 必须是 IntEnum：JSON 序列化后要能直接变成数字 code。"""
    assert issubclass(ErrorCode, IntEnum)
    assert int(ErrorCode.OK) == 0
