"""工资单状态机的**声明表**（T-PAY-006，``docs/08 §3.1`` 声明式定义）。

只放常量与迁移表，**不放业务逻辑**：消费方是
:class:`~app.modules.payroll.service.sheet_state_mixin.SheetStateMixin`（轻迁移编排）
与 :class:`~app.modules.payroll.service.sheet.PayrollSheetService`（``create``）。

============  ===========================  ================  ==================================
动作          迁移                         权限点            副作用
============  ===========================  ================  ==================================
``update``    DRAFT/REJECTED（原状态）      ``payroll:settle`` 白名单 ``doc_date``/``remark`` + 表头金额重算
``submit``    DRAFT/REJECTED → SUBMITTED   ``payroll:settle`` 前置：明细非空
``withdraw``  SUBMITTED → DRAFT            ``payroll:settle`` 撤回人 = 制单人
``reject``    SUBMITTED → REJECTED         ``payroll:approve`` **必填原因**（``rejected_reason``）
``cancel``    DRAFT/REJECTED → CANCELLED   ``payroll:settle`` **必填原因**（``cancelled_reason``）
============  ===========================  ================  ==================================

``approve`` / ``reverse`` / ``pay`` 归 T-PAY-007/008。``update`` 不登记在
:data:`TRANSITIONS`：它的目标状态 = 原状态，另由 ``_assert_updatable`` 判定。
"""

from __future__ import annotations

from typing import Final

from app.common.enums import DocumentStatus

#: ``document_logs.doc_type``（08 §1.1 的单据类型名）。**只有这里写这个字面量**。
DOC_TYPE_PAYROLL_SHEET: Final[str] = "PayrollSheet"
#: 工资单号前缀（09 §2.1：``PR-{YYYYMMDD}-{6 位}``）。
SHEET_DOC_PREFIX: Final[str] = "PR"

SETTLE_PERMISSION: Final[str] = "payroll:settle"
APPROVE_PERMISSION: Final[str] = "payroll:approve"

#: 动作 → 权限点（05 §4.2 / §10）。
PERMISSION_BY_ACTION: Final[dict[str, str]] = {
    "create": SETTLE_PERMISSION,
    "update": SETTLE_PERMISSION,
    "submit": SETTLE_PERMISSION,
    "withdraw": SETTLE_PERMISSION,
    "reject": APPROVE_PERMISSION,
    "cancel": SETTLE_PERMISSION,
}

#: 迁移表：**按动作**登记 ``(允许的起始状态集, 目标状态)``（08 §3.1 / §1.2 R1）。
TRANSITIONS: Final[dict[str, tuple[frozenset[DocumentStatus], DocumentStatus]]] = {
    "submit": (
        frozenset({DocumentStatus.DRAFT, DocumentStatus.REJECTED}),
        DocumentStatus.SUBMITTED,
    ),
    "withdraw": (frozenset({DocumentStatus.SUBMITTED}), DocumentStatus.DRAFT),
    "reject": (frozenset({DocumentStatus.SUBMITTED}), DocumentStatus.REJECTED),
    "cancel": (
        frozenset({DocumentStatus.DRAFT, DocumentStatus.REJECTED}),
        DocumentStatus.CANCELLED,
    ),
}

#: 可编辑状态（``update``）：``DRAFT`` / ``REJECTED``。
EDITABLE_STATUSES: Final[frozenset[DocumentStatus]] = frozenset(
    {DocumentStatus.DRAFT, DocumentStatus.REJECTED}
)

__all__ = [
    "APPROVE_PERMISSION",
    "DOC_TYPE_PAYROLL_SHEET",
    "EDITABLE_STATUSES",
    "PERMISSION_BY_ACTION",
    "SETTLE_PERMISSION",
    "SHEET_DOC_PREFIX",
    "TRANSITIONS",
]
