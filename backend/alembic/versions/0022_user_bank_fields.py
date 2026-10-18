"""0022 users 补身份证与银行卡三列（T-BASE-012；L-128 闭环）。

权威来源：``docs/04 §7.13.1``（``users`` DDL）、``docs/modules/05-工资 §3.5/§5.5``
（代发快照口径）、任务卡 ``T-BASE-012``。

只做一件事：给 ``users`` 加三列 —— ``id_card`` / ``bank_name`` / ``bank_account``，
均**可空**、无唯一 / 非空约束（不发工资的人如财务 / 仓管无需银行卡）。

⚠️ **为什么必须有这三列**（``docs/12`` L-128）：``05 §3.5`` / ``04 §7.6.5`` 要求
工资单生成时把三字段**从 ``users`` 快照**到 ``payroll_details`` 行上，而 ``users``
（迁移 0002 建）**从来没有**这三列 —— 结果 T-PAY-006 的快照恒为 ``None``，
T-PAY-008 的发放（要求 ``bank_account`` 非空否则 ``10002``）与代发 CSV 必然失败。

⚠️ 三列**可空**、无数据迁移：存量行三列保持 ``NULL``（缺失不阻断工资单生成，
发放时才拦 ``10002``，与 ``05 §5.5`` 一致）。列长与 ``04 §7.6.5`` 的快照列**逐字对齐**
（``id_card`` 32 / ``bank_name`` 128 / ``bank_account`` 64），避免快照时截断。

⚠️ **不做**独立加密 / 脱敏存储（阶段一按普通字段落库，task §范围「不做」）；
PII 仅经 ``system:user:manage`` 保护的用户管理接口暴露（``docs/07 §5``）。

⚠️ ``erp_app`` 无硬删权限已由 init 脚本的 ``ALTER DEFAULT PRIVILEGES`` 覆盖新列 ——
``ADD COLUMN`` 不改变表级 ``REVOKE DELETE``（``04 §6.2.1``），故本迁移无授权语句。

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: （列名, 类型, 注释）。顺序即建列顺序，downgrade 反序删。
_COLUMNS: tuple[tuple[str, sa.String, str], ...] = (
    ("id_card", sa.String(32), "身份证号（代发快照来源）"),
    ("bank_name", sa.String(128), "开户行（代发快照来源）"),
    ("bank_account", sa.String(64), "银行卡号（代发快照来源）"),
)


def upgrade() -> None:
    for name, type_, comment in _COLUMNS:
        op.add_column("users", sa.Column(name, type_, nullable=True, comment=comment))


def downgrade() -> None:
    # ⚠️ 反序删列，干净回退到迁移 0021 的 schema（无索引 / 约束需单独处理）。
    for name, _type, _comment in reversed(_COLUMNS):
        op.drop_column("users", name)
