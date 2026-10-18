"""工资单 ``approve``（审核计提）与 ``reverse``（反审核红冲）（T-PAY-007）。

设计权威：``docs/modules/05-工资.md`` §2（BR-PR-07/09/10）、§4.2、§5.4、§5.6、§6.1、
§9、§11（TC-06/TC-08）；``docs/08`` §1.2 R4/R5、§2.4、§3.3；``P2-工资-实施说明 §8 Q-PL09``。

## 凭证副作用：唯一收敛点在 :mod:`~app.modules.payroll.voucher_port`

``approve`` 与 ``reverse`` 都**不直接碰凭证**：它们只调
:class:`~app.modules.payroll.voucher_port.VoucherPort` 的 ``create_accrual`` /
``reverse_accrual``。M9（财务凭证）未建表前注入
:class:`~app.modules.payroll.voucher_port.NullVoucherGateway`，凭证号落 ``NULL``。
**调用与状态迁移同一事务**（``08 §1.2 R4``）：端口抛错则整体回滚，绝不留下
「状态已 APPROVED 而凭证没生成」的半个状态。

## 成对副作用（AGENTS 硬要求：漏一条就数据错）

```
approve   SUBMITTED → APPROVED   VoucherPort.create_accrual(sheet)  → 回写 accrual_voucher_no
reverse   APPROVED  → SUBMITTED  VoucherPort.reverse_accrual(sheet) → 清空 accrual_voucher_no（红冲）
```

## 前置与顺序（本卡三条）

① 状态可审核（否则 ``10007``）；② **制单人 ≠ 审核人**（``10005``，BR-PR-10）；
③ ``total_net = total_gross - total_deduction``（不符拒绝，与 DB ``ck_payroll_sheets_net`` 同口径）。
``reverse`` 额外：**必填原因**（``10006``）；``status != PAID``（已发放 → ``33001``，
BR-PR-09，只能由 M9 走红字冲销）。

⚠️ ``PAID`` 判定必须在 :meth:`_assert_transition` **之前**：``PAID`` 不在 ``reverse``
的合法起始集中，先走状态机的话会报 ``10007`` 而非业务语义正确的 ``33001``。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import DocumentAction, DocumentStatus
from app.core.db import unit_of_work
from app.core.errors import BusinessError, ErrorCode
from app.core.permissions import AuthContext
from app.modules.payroll.models import PayrollSheet
from app.modules.payroll.service.common import require_reason

if TYPE_CHECKING:
    from app.modules.payroll.voucher_port import VoucherPort

logger = logging.getLogger("app.payroll")


class SheetApproveMixin:
    """``approve`` / ``reverse``（由 :class:`~.sheet.PayrollSheetService` 组装）。

    ⚠️ 本 Mixin **不重新实现** ``_apply_status`` / ``_write_log`` / ``_assert_transition``
    / ``_assert_can``：它们唯一的一份在
    :class:`~.sheet_state_mixin.SheetStateMixin` / :class:`~.sheet_state_support.SheetStoreMixin`，
    本类只在 ``TYPE_CHECKING`` 下声明形状，组合类按 MRO 取到那一份
    （``AGENTS`` §2.1：状态字段只由状态机迁移方法写）。
    """

    session: AsyncSession
    #: 凭证端口（默认 ``NullVoucherGateway``，由 ``PayrollSheetService.__init__`` 注入）。
    voucher_port: VoucherPort

    if TYPE_CHECKING:
        # 交叉引用兄弟 Mixin 的私有助手（照抄 settlement_approve 的写法）：
        # 运行期不 import 兄弟 Mixin，只在类型检查时声明形状 —— 少一层 import 就少一个环。
        def _assert_can(self, action: str, ctx: AuthContext) -> None: ...
        def _assert_transition(
            self, action: str, from_status: DocumentStatus, to_status: DocumentStatus
        ) -> None: ...
        async def _get_sheet(self, sheet_id: UUID) -> PayrollSheet: ...
        async def _apply_status(
            self,
            sheet: PayrollSheet,
            *,
            to_status: DocumentStatus,
            expected_version: int,
            operator_id: UUID,
            extra: dict[str, Any] | None = None,
        ) -> None: ...
        async def _write_log(
            self,
            sheet: PayrollSheet,
            operator_id: UUID,
            action: str,
            from_status: str | None,
            to_status: str,
            reason: str | None = None,
            changed_fields: dict[str, Any] | None = None,
        ) -> None: ...
        async def _reloaded(self, sheet_id: UUID) -> PayrollSheet: ...

    # ================================================================== approve

    async def approve(
        self,
        sheet_id: UUID,
        *,
        expected_version: int,
        operator_id: UUID,
        ctx: AuthContext,
        remark: str | None = None,
    ) -> PayrollSheet:
        """审核（``SUBMITTED`` → ``APPROVED``）：审核 = 生成计提凭证。

        前置：① 状态可审核（否则 ``10007``）；② **制单人 ≠ 审核人**（``10005``）；
        ③ ``total_net = total_gross - total_deduction``（不符拒绝）。

        副作用（同一事务）：``VoucherPort.create_accrual(sheet)`` → 回写
        ``accrual_voucher_no``（空适配器下为 ``NULL``）；写 ``document_logs``
        （``APPROVE``，含金额与（若有的）凭证号）。

        :param remark: 可选审核备注，落 ``document_logs.reason``。
        :raises BusinessError: ``12001`` 无权限；``20001`` 单据不存在；``10007`` 非法迁移；
            ``10005`` 自审；``33001`` 已发放（不可审核）；``10003`` 乐观锁。
        """
        self._assert_can("approve", ctx)
        async with unit_of_work(self.session):
            sheet = await self._get_sheet(sheet_id)
            self._assert_transition("approve", sheet.status, DocumentStatus.APPROVED)
            self._assert_not_self_approval(sheet, ctx)
            self._assert_net_balanced(sheet)
            voucher_no = await self.voucher_port.create_accrual(sheet)
            from_status = sheet.status.value
            # ⚠️ 日志先写、状态后迁：Core UPDATE 会 expire ORM 行，之后再读 doc_no 会 MissingGreenlet。
            await self._write_log(
                sheet,
                operator_id,
                DocumentAction.APPROVE.value,
                from_status,
                DocumentStatus.APPROVED.value,
                remark,
                changed_fields=self._accrual_log_fields(sheet, voucher_no),
            )
            await self._apply_status(
                sheet,
                to_status=DocumentStatus.APPROVED,
                expected_version=expected_version,
                operator_id=operator_id,
                extra={
                    "approved_by": operator_id,
                    "approved_at": datetime.now(tz=UTC),
                    "accrual_voucher_no": voucher_no,
                },
            )
            fresh = await self._reloaded(sheet_id)
        logger.info(
            "工资单已审核（计提凭证生成点）",
            extra={
                "doc_no": fresh.doc_no,
                "accrual_voucher_no": fresh.accrual_voucher_no,
                "total_net": str(fresh.total_net),
            },
        )
        return fresh

    # ================================================================== reverse

    async def reverse(
        self,
        sheet_id: UUID,
        *,
        reason: str | None,
        expected_version: int,
        operator_id: UUID,
        ctx: AuthContext,
    ) -> PayrollSheet:
        """反审核（``APPROVED`` → ``SUBMITTED``）：**必填原因** + 与 ``approve`` 对称。

        前置：① 状态可反审核（否则 ``10007``）；② **必填原因**（``10006``，BR-PR-11）；
        ③ ``status != PAID``（已发放 → ``33001``，BR-PR-09，只能红字冲销）。

        副作用（同一事务）：``VoucherPort.reverse_accrual(sheet)``（红冲计提凭证），
        清空 ``accrual_voucher_no``；写 ``document_logs``（``REVERSE`` + 原因，含凭证号若有时）。
        **不删单、不删行**（``08 R5``）。

        :raises BusinessError: ``12001`` 无权限；``10006`` 缺原因；``20001`` 单据不存在；
            ``10007`` 非法迁移；``33001`` 已发放；``10003`` 乐观锁。
        """
        self._assert_can("reverse", ctx)
        reason_text = require_reason(reason, action="反审核工资单")
        async with unit_of_work(self.session):
            sheet = await self._get_sheet(sheet_id)
            self._assert_not_paid(sheet)
            self._assert_transition("reverse", sheet.status, DocumentStatus.SUBMITTED)
            voucher_no = await self.voucher_port.reverse_accrual(sheet)
            from_status = sheet.status.value
            await self._write_log(
                sheet,
                operator_id,
                DocumentAction.REVERSE.value,
                from_status,
                DocumentStatus.SUBMITTED.value,
                reason_text,
                changed_fields=self._reversal_log_fields(sheet, voucher_no),
            )
            await self._apply_status(
                sheet,
                to_status=DocumentStatus.SUBMITTED,
                expected_version=expected_version,
                operator_id=operator_id,
                extra={"approved_by": None, "approved_at": None, "accrual_voucher_no": None},
            )
            fresh = await self._reloaded(sheet_id)
        logger.info(
            "工资单已反审核（计提凭证红冲点）",
            extra={"doc_no": fresh.doc_no, "reason": reason_text},
        )
        return fresh

    # ================================================================== 校验

    def _assert_not_self_approval(self, sheet: PayrollSheet, ctx: AuthContext) -> None:
        """制单人 ≠ 审核人（``08 §4`` / BR-PR-10）。相等 → ``10005``。

        判 ``ctx.user_id``（认证身份）而不是 ``operator_id``：后者由调用方传入，用它判会把
        「传错身份」变成静默放过（同 :meth:`SheetStateMixin._assert_creator`）。
        """
        if ctx.user_id != sheet.created_by:
            return
        raise BusinessError(
            ErrorCode.SELF_APPROVAL_FORBIDDEN,
            "不能审核自己制作的工资单，请由他人审核",
            details={"doc_no": sheet.doc_no},
        )

    @staticmethod
    def _assert_net_balanced(sheet: PayrollSheet) -> None:
        """``total_net = total_gross - total_deduction``（BR-PR-06，与 DB CHECK 同口径）。

        ``create`` / ``update`` 已按明细重算（结构上必然一致），这里的双检是**防篡改 /
        防漂移**：账实不符时绝不带着错金额生成计提凭证。
        """
        if sheet.total_net == sheet.total_gross - sheet.total_deduction:
            return
        raise BusinessError(
            ErrorCode.ILLEGAL_OPERATION,
            "工资单实发合计与「应发 - 扣款」不一致，不能审核（数据异常，请重新生成工资单）",
            details={
                "doc_no": sheet.doc_no,
                "total_gross": str(sheet.total_gross),
                "total_deduction": str(sheet.total_deduction),
                "total_net": str(sheet.total_net),
            },
        )

    def _assert_not_paid(self, sheet: PayrollSheet) -> None:
        """已发放（``PAID``）不可反审核（BR-PR-09）。→ ``33001``，只能红字冲销。"""
        if sheet.status is not DocumentStatus.PAID:
            return
        raise BusinessError(
            ErrorCode.PAYROLL_SHEET_ALREADY_PAID,
            "工资单已发放，不可反审核；只能由财务走红字冲销",
            details={"sheet_id": str(sheet.id), "doc_no": sheet.doc_no},
        )

    @staticmethod
    def _accrual_log_fields(sheet: PayrollSheet, voucher_no: str | None) -> dict[str, Any]:
        """``approve`` 日志字段：金额 + （若有的）计提凭证号（任务卡「含金额 + 凭证号若有时」）。"""
        fields: dict[str, Any] = {
            "total_gross": str(sheet.total_gross),
            "total_deduction": str(sheet.total_deduction),
            "total_net": str(sheet.total_net),
        }
        if voucher_no is not None:
            fields["accrual_voucher_no"] = voucher_no
        return fields

    @staticmethod
    def _reversal_log_fields(sheet: PayrollSheet, voucher_no: str | None) -> dict[str, Any]:
        """``reverse`` 日志字段：被清空的原凭证号 + （若有的）红冲凭证号。"""
        fields: dict[str, Any] = {}
        if sheet.accrual_voucher_no is not None:
            fields["accrual_voucher_no"] = None
        if voucher_no is not None:
            fields["reversal_voucher_no"] = voucher_no
        return fields


__all__ = ["SheetApproveMixin"]
