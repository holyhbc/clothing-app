"""错误码与 docs/05 §4 的一致性（docs/12-文档与变更归档规范.md §9 检查项 3）。

这是**规范一致性闸门**：``ErrorCode`` 的取值必须是 docs/05 §4 已登记的码，
且通用段（10xxx / 11xxx / 12xxx）不允许缺实现。防止 AI 自行编造错误码。
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


@pytest.mark.parametrize("segment", ["10", "11", "12"])
def test_generic_segments_fully_implemented(segment: str) -> None:
    """通用段 / 认证段 / 权限段在 docs 登记的码必须全部有实现，不允许缺。"""
    documented = {code for code in DOCUMENTED_CODES if str(code).startswith(segment)}
    implemented = {int(code) for code in ErrorCode}
    missing = documented - implemented
    assert not missing, f"docs/05 §4 已登记但未实现的 {segment}xxx 错误码：{sorted(missing)}"


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
