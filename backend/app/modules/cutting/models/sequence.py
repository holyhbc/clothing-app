from __future__ import annotations

from datetime import date

from sqlalchemy import CheckConstraint, Date, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.common.models import Base, IdMixin


class CuttingDocNoSequence(IdMixin, Base):
    """裁剪单号按天计数器（**纯计数表**，C1）。

    ⚠️ **豁免 ``04 §2`` 公共字段**，理由与 ``style_no_sequences`` 完全一样：
    纯计数表没有「谁改的」的概念 —— 它由取号逻辑在事务内
    ``UPDATE ... SET next_no = next_no + 1 RETURNING next_no - 1`` 递增，
    而 ``created_by`` / ``updated_by`` 是 ``NOT NULL`` 的**操作人**字段；
    计数行的「创建者」只能是系统，那才是谎话。

    ⚠️ **为什么不用 PG ``SEQUENCE``**：C1 的单号**按天重置**，而 ``setval`` 做不了
    「新的一天自动从头来」（要么在应用里判断日期、要么跑 DDL）。``(doc_date, prefix)``
    作唯一键的话，新的一天自然就是新的一行。

    ⚠️ **回滚会不会导致重号**：会 —— 事务回滚时 ``next_no`` 一起退回。但那个号
    **从未出现在任何响应里**（单据没建成），业务上不可观测；真正保证「发出即唯一」
    的是 ``uq_cutting_orders_doc_no``。
    """

    __tablename__ = "cutting_doc_no_sequences"

    doc_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        comment="按天分组：单据号 CT-{YYYYMMDD}-{6 位} 的 YYYYMMDD 部分",
    )
    prefix: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="CT",
        server_default=text("'CT'"),
        comment="单据前缀（09 §2.1：裁剪 = CT）；写成一列是为了将来分单据类型时不必改表",
    )
    next_no: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
        comment="下一个可用序号，从 1 开始（09 §2.1：6 位）",
    )

    __table_args__ = (
        UniqueConstraint("doc_date", "prefix", name="uq_cutting_doc_no_sequences_date_prefix"),
        CheckConstraint("next_no >= 1", name="ck_cutting_doc_no_sequences_next_no"),
        {"comment": "裁剪单号按天计数器（纯计数表，豁免 04 §2 公共字段，同 style_no_sequences）"},
    )
