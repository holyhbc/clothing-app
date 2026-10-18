"""计件结算单的状态迁移表（声明式，``docs/08 §3.1``）。

结算单 ``PieceworkSettlement`` 的**状态合法性**只在这一处声明，由
:class:`~app.modules.payroll.service.settlement_state.SettlementStateMixin` 消费。
T-PAY-004 落地**轻迁移**（``submit`` / ``withdraw`` / ``reject`` / ``cancel``）；
T-PAY-005 追加 ``approve`` / ``reverse``（要回写 / 释放 ``piecework_logs.payroll_id``，
副作用在 :mod:`~app.modules.payroll.service.settlement_approve`）。

## 为什么按**动作**登记，而不是按「起始状态 → 允许的目标集合」

``submit`` 与未来的 ``reverse``（T-PAY-005：``APPROVED → SUBMITTED``）目标状态相同，
若只登记 ``(from, to)``，就会把 ``APPROVED → submit`` 这种非法迁移判成合法 ——
症状是「已审核的单又被提交一次」。故照 ``bundling/service/state_mixin.py`` 同款，
以**动作名**为键，值是该动作允许的起始状态集与唯一目标状态。

## 与 ``docs/08 §2.3`` / ``modules/05 §4.1`` 的对应

============  ===========================  ================  ==========
动作          迁移                         权限点            必填原因
============  ===========================  ================  ==========
``submit``    DRAFT/REJECTED → SUBMITTED   ``payroll:settle`` —
``withdraw``  SUBMITTED → DRAFT            ``payroll:settle`` —
``reject``    SUBMITTED → REJECTED         ``payroll:approve`` 是
``cancel``    DRAFT/REJECTED → CANCELLED    ``payroll:settle`` 是
``approve``   SUBMITTED → APPROVED         ``payroll:approve`` —（副作用：占用流水）
``reverse``   APPROVED → SUBMITTED         ``payroll:reverse`` 是（副作用：释放流水）
============  ===========================  ================  ==========

``CANCELLED`` 是终态：它不在任何动作的 ``allowed_from`` 里，再迁移一律被
:meth:`~app.modules.payroll.service.settlement_state.SettlementStateMixin._assert_transition`
判非法（08 R6）。
"""

from __future__ import annotations

from typing import Final

from app.common.enums import DocumentAction, DocumentStatus

#: ``document_logs.doc_type``（08 §1.1 的单据类型名）。**只有这里写这个字面量**：
#: 写日志的与将来查历史摘要的必须同值。
DOC_TYPE_PIECEWORK_SETTLEMENT: Final[str] = "PieceworkSettlement"

#: 结算单号前缀（09 §2.1：``PW-{YYYYMMDD}-{6 位}``）；取号复用
#: :func:`app.core.numbering.take_doc_no`（计数器表 ``cutting_doc_no_sequences``）。
SETTLEMENT_DOC_PREFIX: Final[str] = "PW"

#: 权限点（05 §10 / 07 §2.2）。
SETTLE_PERMISSION: Final[str] = "payroll:settle"
APPROVE_PERMISSION: Final[str] = "payroll:approve"
REVERSE_PERMISSION: Final[str] = "payroll:reverse"

#: 动作 → 权限点。``create`` 也走 ``payroll:settle``（05 §4.1 / §10）。
PERMISSION_BY_ACTION: Final[dict[str, str]] = {
    "create": SETTLE_PERMISSION,
    "submit": SETTLE_PERMISSION,
    "withdraw": SETTLE_PERMISSION,
    "reject": APPROVE_PERMISSION,
    "cancel": SETTLE_PERMISSION,
    "approve": APPROVE_PERMISSION,
    "reverse": REVERSE_PERMISSION,
}

#: 必填原因的动作（05 §4 BR-PR-11；缺 → ``10006``）。
REASON_REQUIRED_ACTIONS: Final[frozenset[str]] = frozenset({"reject", "cancel", "reverse"})

#: 动作 → ``document_logs.action``（取值来自 ``DocumentAction``，不散落字面量）。
LOG_ACTION_BY_ACTION: Final[dict[str, str]] = {
    "create": DocumentAction.CREATE.value,
    "submit": DocumentAction.SUBMIT.value,
    "withdraw": DocumentAction.WITHDRAW.value,
    "reject": DocumentAction.REJECT.value,
    "cancel": DocumentAction.CANCEL.value,
    "approve": DocumentAction.APPROVE.value,
    "reverse": DocumentAction.REVERSE.value,
}

#: 迁移表：**按动作**登记 ``(允许的起始状态集, 目标状态)``（08 §3.1 / §1.2 R1）。
SETTLEMENT_TRANSITIONS: Final[dict[str, tuple[frozenset[DocumentStatus], DocumentStatus]]] = {
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
    "reverse": (frozenset({DocumentStatus.APPROVED}), DocumentStatus.SUBMITTED),
}

__all__ = [
    "APPROVE_PERMISSION",
    "DOC_TYPE_PIECEWORK_SETTLEMENT",
    "LOG_ACTION_BY_ACTION",
    "PERMISSION_BY_ACTION",
    "REASON_REQUIRED_ACTIONS",
    "REVERSE_PERMISSION",
    "SETTLEMENT_DOC_PREFIX",
    "SETTLEMENT_TRANSITIONS",
    "SETTLE_PERMISSION",
]
