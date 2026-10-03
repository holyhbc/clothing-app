"""结构化日志测试（docs/11-部署运维与发布规范.md §8.1）。

重点验证两件事：
    1. 输出是**单行 JSON**，字段用 ``extra=`` 传而不是拼进消息字符串
    2. 敏感字段名命中即替换为 ``***``（最后一道防线）
"""

import io
import json
import logging

import pytest

from app.core.logging import JsonFormatter, configure_logging, get_logger


@pytest.fixture
def sink() -> io.StringIO:
    """收集日志输出的内存流。"""
    return io.StringIO()


def _emit(sink: io.StringIO, level: int = logging.INFO, **extra: object) -> dict[str, object]:
    """配置日志到 sink，发一条带 extra 的记录，返回解析后的 JSON。"""
    configure_logging(level="DEBUG", json_output=True, stream=sink)
    logger = get_logger("app.test")
    logger.log(level, "事件标题", extra=extra)
    handler = logging.getLogger().handlers[-1]
    assert isinstance(handler.formatter, JsonFormatter)
    handler.flush()
    return json.loads(sink.getvalue().strip().splitlines()[-1])


def test_output_is_single_line_json(sink: io.StringIO) -> None:
    """一条记录 = 一行 JSON，便于 docker logs / journald 采集。"""
    payload = _emit(sink, bundle_no="BD-20261018-000031-XL02-0001")
    assert payload["msg"] == "事件标题"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert payload["bundle_no"] == "BD-20261018-000031-XL02-0001"
    assert "\n" not in json.dumps(payload)


def test_timestamp_is_iso8601_with_timezone(sink: io.StringIO) -> None:
    """时间带时区（docs/03 §1.1 第 9 条）。"""
    payload = _emit(sink)
    assert payload["ts"].endswith("+00:00") or payload["ts"].endswith("Z")


def test_extra_fields_become_top_level_keys(sink: io.StringIO) -> None:
    """extra 字段应成为 JSON 顶层键，便于日志检索。"""
    payload = _emit(sink, request_id="01JBABCDEFGHJKMNPQ", qty=3)
    assert payload["request_id"] == "01JBABCDEFGHJKMNPQ"
    assert payload["qty"] == 3


def test_sensitive_keys_are_redacted(sink: io.StringIO) -> None:
    """命中敏感键名的字段一律替换为 ***（docs/11 §8.1 禁止打印密码/token）。"""
    payload = _emit(
        sink,
        password="hunter2",
        db_password="hunter3",
        jwt_secret="s3cr3t",
        access_token="eyJhbGciOi",
        authorization="Bearer x",
        safe_field="可见",
    )
    for key in ("password", "db_password", "jwt_secret", "access_token", "authorization"):
        assert payload[key] == "***", f"{key} 未被脱敏"
    assert payload["safe_field"] == "可见"


def test_nested_dict_secrets_are_redacted(sink: io.StringIO) -> None:
    """嵌套结构里的敏感键同样脱敏。"""
    payload = _emit(sink, user={"name": "张三", "password": "hunter2"})
    assert payload["user"] == {"name": "张三", "password": "***"}


def test_exception_traceback_only_in_logs(sink: io.StringIO) -> None:
    """异常栈只进日志，不进响应体（docs/06 §5）。"""
    configure_logging(level="DEBUG", json_output=True, stream=sink)
    logger = get_logger("app.test")
    try:
        raise ValueError("内部细节")
    except ValueError:
        logger.exception("出错了")
    logging.getLogger().handlers[-1].flush()

    payload = json.loads(sink.getvalue().strip().splitlines()[-1])
    assert "ValueError: 内部细节" in payload["exc"]


def test_configure_logging_is_idempotent(sink: io.StringIO) -> None:
    """重复调用不得叠加 handler，否则日志会重复输出。"""
    configure_logging(stream=sink)
    configure_logging(stream=sink)
    configure_logging(stream=sink)
    logger = get_logger("app.test")
    logger.info("只应出现一次")
    logging.getLogger().handlers[-1].flush()

    lines = [line for line in sink.getvalue().strip().splitlines() if line]
    assert len(lines) == 1, f"handler 叠加导致重复输出：{lines}"


def test_text_mode_is_human_readable() -> None:
    """本地调试可用非 JSON 模式（docs/11 §8.1 允许按环境切换）。"""
    buffer = io.StringIO()
    configure_logging(level="DEBUG", json_output=False, stream=buffer)
    get_logger("app.test").info("可读模式")
    logging.getLogger().handlers[-1].flush()
    assert "可读模式" in buffer.getvalue()
    assert "INFO" in buffer.getvalue()


def test_get_logger_returns_named_logger() -> None:
    """统一入口，避免调用方直接拼消息字符串。"""
    assert get_logger("app.modules.cutting").name == "app.modules.cutting"
