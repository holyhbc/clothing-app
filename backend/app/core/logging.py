"""结构化日志（docs/11-部署运维与发布规范.md §8.1）。

口径：
    - 输出 JSON 到 **stdout**（容器里 `docker logs` 直接可读），不写文件
    - 用 ``extra={...}`` 附加结构化字段，禁止把字段拼进消息字符串
    - **绝不打印密码、token、完整身份证/银行卡号**（docs/11 §8.1）
    - 未捕获异常走 ``logger.exception``，栈只进服务端日志

JSON 字段：``ts`` ``level`` ``logger`` ``msg`` + 调用方传入的 extra。
时间统一带时区并转 UTC 序列化为 ISO8601（docs/03 §1.1 第 9 条）。
"""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any, TextIO

#: 日志里**永远不允许**出现的键名（子串匹配，大小写不敏感）
FORBIDDEN_KEY_PARTS: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "token",
    "authorization",
    "cookie",
    "id_card",
    "bank_card",
)

#: 标准日志属性，不算业务 extra
_RESERVED_ATTRS = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys() | {"asctime", "message", "taskName"}
)


class JsonFormatter(logging.Formatter):
    """把 LogRecord 渲染成单行 JSON。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            # 栈只进日志，不进响应体（docs/06 §5）
            payload["exc"] = self.formatException(record.exc_info)

        for key, value in record.__dict__.items():
            if key in _RESERVED_ATTRS or key.startswith("_"):
                continue
            payload[key] = _redact(key, value)

        return json.dumps(payload, ensure_ascii=False, default=str)


def _redact(key: str, value: object) -> object:
    """命中敏感键名一律替换为 ``***``。

    这是**最后一道防线**：调用方即使误传也不会泄露。真正的规范是调用方不要传
    （docs/11 §8.1），但审核不可能穷尽，所以这里兜底。
    """
    lowered = key.lower()
    if any(part in lowered for part in FORBIDDEN_KEY_PARTS):
        return "***"
    if isinstance(value, dict):
        return {k: _redact(k, v) for k, v in value.items()}
    return value


def configure_logging(
    *,
    level: str = "INFO",
    json_output: bool = True,
    stream: TextIO | None = None,
) -> None:
    """配置根 logger。可重复调用（幂等），不叠加 handler。

    :param level: 日志级别名（DEBUG / INFO / WARNING / ERROR）
    :param json_output: True 输出 JSON（生产）；False 输出可读文本（本地调试）
    :param stream: 输出流，默认 stdout
    """
    root = logging.getLogger()
    root.setLevel(level.upper())

    # 幂等：先清掉本函数装过的 handler，避免重复输出
    for handler in list(root.handlers):
        if getattr(handler, "_garment_erp", False):
            root.removeHandler(handler)

    handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    handler._garment_erp = True  # type: ignore[attr-defined]
    if json_output:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-5s [%(name)s] %(message)s")
        )
    root.addHandler(handler)

    # SQLAlchemy 默认会打 WARNING 级的连接池噪声，生产环境压到 ERROR
    logging.getLogger("sqlalchemy.engine").setLevel(logging.ERROR)
    logging.getLogger("sqlalchemy.pool").setLevel(logging.ERROR)


def get_logger(name: str) -> logging.Logger:
    """取 logger。禁止直接用 ``logging.getLogger`` 拼消息字符串。"""
    return logging.getLogger(name)
