"""工资单状态机的**声明表**（T-PAY-006，``docs/08 §3.1`` 声明式定义）。

只放常量与迁移表，**不放业务逻辑**：消费方是
:class:`~app.modules.payroll.service.sheet_state_mixin.SheetStateMixin`（轻迁移编排）
与 :class:`~app.modules.payroll.service.sheet.PayrollSheetService`（``create``）。

============  ===========================  =================  ==================================
动作          迁移                         权限点             副作用
============  ===========================  =================  ==================================
``update``    DRAFT/REJECTED（原状态）      ``payroll:settle`` 白名单 ``doc_date``/``remark`` + 表头金额重算
``submit``    DRAFT/REJECTED → SUBMITTED   ``payroll:settle`` 前置：明细非空
``withdraw``  SUBMITTED → DRAFT            ``payroll:settle`` 撤回人 = 制单人
``reject``    SUBMITTED → REJECTED         ``payroll:approve`` **必填原因**（``rejected_reason``）
``cancel``    DRAFT/REJECTED → CANCELLED   ``payroll:settle`` **必填原因**（``cancelled_reason``）
``approve``   SUBMITTED → APPROVED         ``payroll:approve`` 计提凭证（``VoucherPort``，T-PAY-007）
``reverse``   APPROVED → SUBMITTED         ``payroll:reverse`` **必填原因** + 红冲计提（T-PAY-007）
============  ===========================  =================  ==================================

``approve`` / ``reverse`` 的业务编排（含凭证副作用）归 T-PAY-007，见
:mod:`~app.modules.payroll.service.sheet_approve`；本表只声明它们的合法性。
``pay`` 归 T-PAY-008。``update`` 不登记在 :data:`TRANSITIONS`：它的目标状态 = 原状态，
另由 ``_assert_updatable`` 判定。
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
REVERSE_PERMISSION: Final[str] = "payroll:reverse"
#: 发放（``pay``，05 §4.2）与代发导出（``GET .../bank-file``）的权限点。
#: 二者都已在 ``07 §2.2`` / ``05 §10`` 登记，本卡只声明、不新增。
PAY_PERMISSION: Final[str] = "payroll:pay"
EXPORT_PERMISSION: Final[str] = "payroll:export"

#: 动作 → 权限点（05 §4.2 / §10）。
PERMISSION_BY_ACTION: Final[dict[str, str]] = {
    "create": SETTLE_PERMISSION,
    "update": SETTLE_PERMISSION,
    "submit": SETTLE_PERMISSION,
    "withdraw": SETTLE_PERMISSION,
    "reject": APPROVE_PERMISSION,
    "cancel": SETTLE_PERMISSION,
    "approve": APPROVE_PERMISSION,
    "reverse": REVERSE_PERMISSION,
    "pay": PAY_PERMISSION,
    "export": EXPORT_PERMISSION,
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
    "approve": (frozenset({DocumentStatus.SUBMITTED}), DocumentStatus.APPROVED),
    # ⚠️ reverse 只允许从 APPROVED 出发；PAID 由 sheet_approve._assert_not_paid 先拦成 33001
    # （PAID 不在本表里，若先走状态机会报 10007，语义不对）。
    "reverse": (frozenset({DocumentStatus.APPROVED}), DocumentStatus.SUBMITTED),
    # 发放：APPROVED → PAID（05 §4.2）。PAID 后再 pay 由 pay._assert_not_already_paid
    # 先拦成 33001（PAID 不在起始集里，先走状态机会报 10007，语义不对，同 reverse 口径）。
    "pay": (frozenset({DocumentStatus.APPROVED}), DocumentStatus.PAID),
}

#: 可编辑状态（``update``）：``DRAFT`` / ``REJECTED``。
EDITABLE_STATUSES: Final[frozenset[DocumentStatus]] = frozenset(
    {DocumentStatus.DRAFT, DocumentStatus.REJECTED}
)

__all__ = [
    "APPROVE_PERMISSION",
    "DOC_TYPE_PAYROLL_SHEET",
    "EDITABLE_STATUSES",
    "EXPORT_PERMISSION",
    "PAY_PERMISSION",
    "PERMISSION_BY_ACTION",
    "REVERSE_PERMISSION",
    "SETTLE_PERMISSION",
    "SHEET_DOC_PREFIX",
    "TRANSITIONS",
]
