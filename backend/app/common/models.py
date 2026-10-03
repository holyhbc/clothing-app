"""SQLAlchemy 声明基类与公共字段 Mixin（docs/04-数据库规范.md §2）。

⚠️ 字段定义与 04 §2 **逐字对齐**，不得增删列名：

    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid()
    created_at      timestamptz NOT NULL DEFAULT now()
    created_by      uuid        NOT NULL
    updated_at      timestamptz NOT NULL DEFAULT now()
    updated_by      uuid        NOT NULL
    deleted_at      timestamptz                -- NULL 表示未删除；软删只写这个
    version         integer     NOT NULL DEFAULT 1   -- 乐观锁，UPDATE 时 +1
    remark          text

**append-only 日志表的例外**（设计稿 §10.1 W2，需回写 04 §2）：
``document_logs`` / ``auth_login_logs`` / ``auth_refresh_tokens`` 永不修改、
永不删除，因此不带 ``version`` / ``deleted_at`` / ``updated_*``。
先例见 04 §7.9 的 ``document_logs``。这类表直接用 ``Base``，不要混入 Mixin。
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, Integer, func, text
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811 —— 别名照抄 docs/04 §2
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """全局声明基类。

    ``metadata`` 供 Alembic autogenerate 使用；新增模型必须在
    ``alembic/env.py`` 的 ``target_metadata`` 里可见（靠 import 触发注册）。
    """


class AuditMixin:
    """审计字段：谁、何时建的，谁、何时改的。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    created_by: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        onupdate=func.now(),
        nullable=False,
    )
    updated_by: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    remark: Mapped[str | None] = mapped_column(nullable=True)


class SoftDeleteMixin:
    """软删位（INV-7）。列表查询默认必须加 ``deleted_at IS NULL``。"""

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )


class VersionMixin:
    """乐观锁版本号。

    取值口径：**由应用层显式 +1**（docs/04-数据库规范.md §2）。
    原因是必须与状态迁移写在同一条 UPDATE 里（``SET status=?, version=version+1``），
    若交给数据库触发器自动 +1，会与"条件 UPDATE 未命中即冲突"的判定产生竞态。

    约束 ``ck_<table>_version_positive CHECK (version > 0)`` 由各模型自行声明
    （Alembic 生成，不在 Mixin 里拼名字）。
    """

    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")


class IdMixin:
    """UUID 主键（docs/04 §1：主键 uuid + gen_random_uuid()）。"""

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )


class BaseModel(IdMixin, AuditMixin, SoftDeleteMixin, VersionMixin, Base):
    """业务表基类：ID + 审计 + 软删 + 乐观锁。

    ⚠️ MRO 里必须把 Mixin 放在 ``Base`` 之前，否则 DeclarativeBase 的元类
    不会处理这些 mixin 声明的列。
    """

    __abstract__ = True

    def as_dict_for_log(self) -> dict[str, Any]:
        """输出可直接进 ``document_logs.changed_fields`` 的字段快照。

        只取业务列，**排除** ``updated_at`` / ``updated_by`` / ``version``
        （它们由 diff 计算过程自身产生，放进快照会污染每次变更记录）。
        """
        exclude = {"updated_at", "updated_by", "version"}
        return {
            column.name: getattr(self, column.name)
            for column in self.__table__.columns
            if column.name not in exclude
        }
