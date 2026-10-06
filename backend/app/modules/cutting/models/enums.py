from __future__ import annotations

from enum import StrEnum

from sqlalchemy import (
    Enum as SAEnum,
)

from app.common.enums import DocumentStatus


class CuttingEntryMode(StrEnum):
    """裁剪明细的录入模式（``docs/04 §7.7.2`` 的 ``CREATE TYPE``，ADR-0014）。

    ⚠️ **挂在行内颜色级**（``cutting_order_line_colors.entry_mode``），不是单据级 ——
    「这个颜色用哪种模式」是**这匹布上这个颜色**的属性，换一匹布就可能换模式
    （ADR-0017 修订了 ADR-0014 的单据级挂载）。

    ==================  ==================================  ==================
    值                  场景                                界面上填什么
    ==================  ==================================  ==================
    ``MASTER``         常规，尺码比例固定                  点「按比例带出」生成行
    ``UNIFORM``        所有尺码件数一样                    点「统一件数」填一个数
    ``MANUAL``         裁床个别尺码数量不一致              任意增 / 删 / 改
    ==================  ==================================  ==================

    ⚠️ 三种模式**可以在同一张单里混用**（行1 红色走 A、行2 黑色走 C）
    —— 模式是「行 × 颜色」级属性。
    """

    MASTER = "MASTER"
    UNIFORM = "UNIFORM"
    MANUAL = "MANUAL"


#: PG enum ``document_status``（``docs/04 §3`` / ``docs/08 §1.1``），由迁移 0009 建。
#:
#: ⚠️ **必须是真正的 PG enum**，不能拿 ``String`` 糊弄 —— 否则枚举值失去数据库层
#: 的约束，而 ``alembic check`` 会一直报 ``modify_type``（与 ``size_class`` 同一理由）。
#: ``values_callable`` 不给的话 SQLAlchemy 存**成员名**，恰好与 PG enum 的值一致。
#:
#: ⚠️ **下一个单据表（``bundling_orders`` / ``purchase_orders`` …）必须从这里 import**，
#: 不要重新声明一遍 —— 两次 ``SAEnum(..., name="document_status")`` 会在
#: ``Base.metadata`` 里留下两个同名类型，autogenerate 见到就报 ``duplicate object``。
DOCUMENT_STATUS = SAEnum(DocumentStatus, name="document_status", create_type=False)

#: PG enum ``cutting_entry_mode``，由迁移 0009 建。
#: ⚠️ ``create_type=False``：类型由迁移手写 ``CREATE TYPE`` 建，
#: 不加这个 SQLAlchemy 会**自己再发一条**，撞车报 ``type already exists``
#: （0004 / 0007 / 0008 各踩过一次）。
CUTTING_ENTRY_MODE = SAEnum(CuttingEntryMode, name="cutting_entry_mode", create_type=False)
