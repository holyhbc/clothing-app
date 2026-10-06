"""字典资源 PATCH 字段过滤与唯一冲突翻译助手（原 service.py 431-454）。"""

from typing import Any

from sqlalchemy.exc import IntegrityError

from app.core.errors import BusinessError, ErrorCode
from app.modules.base.resources import DictResource

from .common import logger


def payload_dict_changes(payload: dict[str, Any], code_column: str) -> dict[str, Any]:
    """从 PATCH 体里取出真正要改的字段。

    两个要点：
    - 过滤 ``None``：``{"remark": None}`` 表示"不改备注"，不是"把备注清空"
    - 剔除 ``version``（它自己进 WHERE，不进 SET）与**编码列**：
      §4.4 明确"编码字段不可改"，而工序更严格 —— 引用方是字符串冗余，
      改号会让历史单据指向一个不存在的工序
    """
    skip = {code_column, "version", "items"}
    return {key: value for key, value in payload.items() if key not in skip and value is not None}


def _unique_violation(exc: IntegrityError, resource: DictResource) -> BusinessError:
    """把唯一索引冲突翻译成 ``10001``。

    ⚠️ 不暴露数据库错误文案 —— 里面带表名与约束名（docs/06 §5）。
    """
    logger.info("唯一约束冲突", extra={"resource": resource.key})
    return BusinessError(
        ErrorCode.PARAM_INVALID,
        f"{resource.code_column} 已存在，请换一个编码",
        details={"field": resource.code_column},
    )
