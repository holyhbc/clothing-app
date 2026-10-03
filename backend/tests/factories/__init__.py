"""数据工厂（docs/10-测试规范.md §7）。

规则：
    - 工厂集中在 ``tests/factories/``，统一 ``factory.FooFactory().create()``
    - **禁止在测试里手写 uuid / 时间 / 一堆字段的字典**
    - 时间必须可固定，避免跨月跨年失败（docs/10 §2.1）

P0 阶段库里只有 ``document_logs`` 一张业务表，因此本包目前只提供它的工厂；
后续模块的工厂随模块一起加（``user.py`` / ``style.py`` / ``piecework.py`` …）。
"""

from datetime import UTC, datetime
from typing import Any, ClassVar
from uuid import uuid4

__all__ = ["FIXED_DATE", "FIXED_DATETIME", "DocumentLogFactory", "new_id"]

#: 全局固定时间：所有需要时间的工厂默认值都用它，保证用例可重复
FIXED_DATETIME = datetime(2026, 8, 15, 10, 30, 0, tzinfo=UTC)
FIXED_DATE = FIXED_DATETIME.date()


def new_id() -> str:
    """生成一个 uuid 字符串。**只允许工厂内部调用**，测试里不要直接用。"""
    return str(uuid4())


class BaseDataFactory:
    """极简工厂基类：按默认值建 dict，再叠加调用方覆盖项。

    不引第三方库：P0 阶段只有一张表，factory_boy 的收益不足以抵消依赖成本；
    等工厂数量超过 5 个再评估切换（登记在 T-AUTH-001 的依赖清单里）。
    """

    #: 子类覆盖：字段默认值
    defaults: ClassVar[dict[str, Any]] = {}

    @classmethod
    def build(cls, **overrides: Any) -> dict[str, Any]:
        """构造字典但不落库。"""
        return {**cls.defaults, **overrides}

    @classmethod
    async def create(cls, session: Any, **overrides: Any) -> Any:
        """构造并落库，返回 ORM 实体。"""
        from sqlalchemy import insert

        payload = cls.build(**overrides)
        result = await session.execute(insert(cls.table).values(**payload))  # type: ignore[attr-defined]
        return result


class DocumentLogFactory(BaseDataFactory):
    """``document_logs`` 工厂（docs/04 §7.9）。"""

    defaults: ClassVar[dict[str, Any]] = {
        "id": new_id(),
        "doc_type": "Probe",
        "doc_id": new_id(),
        "doc_no": "PR-20260815-000001",
        "action": "CREATE",
        "from_status": None,
        "to_status": None,
        "operator_id": new_id(),
        "operator_name": "测试操作员",
        "reason": None,
        "changed_fields": None,
        "created_at": FIXED_DATETIME,
    }
