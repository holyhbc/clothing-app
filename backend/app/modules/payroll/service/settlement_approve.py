"""计件结算单 ``approve``（审核即占用）与 ``reverse``（反审核释放）（T-PAY-005）。

设计权威：``docs/modules/05-工资.md`` §2（BR-PR-03/04/13）、§4.1 / §4.4、§5.2 / §5.6 / §5.7、
§7（P2/P3）、§9、§11（TC-03/17/26/34）；``docs/08`` §1.2 R4/R5、§2.3。

## 成对副作用（AGENTS 硬要求：漏一条就数据错）

```
approve   SUBMITTED → APPROVED   UPDATE piecework_logs SET payroll_id=本单 WHERE id IN(行) AND payroll_id IS NULL
reverse   APPROVED  → SUBMITTED   UPDATE piecework_logs SET payroll_id=NULL   WHERE id IN(行) AND payroll_id=本单
```

两条 UPDATE 与状态迁移**同一事务**（08 §1.2 R4）：任一失败整体回滚，绝不留下
「单已 APPROVED 而流水没占用」或「流水已释放而单还 APPROVED」的半个状态。

## ``approve`` 的 ``rowcount`` 核对

``WHERE payroll_id IS NULL`` 是 P3 的兜底口径（04 §7.1）。命中行数必须 **恰好等于本单行数** ——
少一条就说明有流水被另一张单抢先占用（唯一索引 ``uq_piecework_settlement_lines_piecework_log_id``
在「两单同时把同一流水写成自己的行」时兜底，但「另一单先审核占用」只能靠这里的条件 UPDATE 发现）。
不等 → ``33002`` 整体回滚（**不 commit 半个**）。

⚠️ 计数用 ``.returning(...)`` 的行数而不是 ``result.rowcount``：mypy 的
``AsyncSession.execute`` 返回类型不含 ``rowcount``（同 ``cutting/service/common.py`` 的做法）。

## ``reverse`` 的前置（下游校验）

工资单（``T-PAY-006``）由已审核结算单生成。只要下游存在 ``APPROVED`` / ``PAID`` 的工资单，
结算单就**不能**反审核（否则工资单会挂在一张已回到 ``SUBMITTED`` 的结算单上）。命中 → ``33001``
（05 §4.4：结算反审核时下游工资已审核/已发放，复用「工资单已发放不可反审核」的门户码）。

⚠️ R5：反审核**不删行、不删单**，只清 ``payroll_id`` —— 行与单是「不重复计入」的追溯依据。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import DocumentStatus
from app.core.db import unit_of_work
from app.core.errors import BusinessError, ErrorCode
from app.core.permissions import AuthContext
from app.modules.payroll.models import PayrollSheet, PieceworkSettlement, PieceworkSettlementLine
from app.modules.payroll.service.common import require_reason
from app.modules.payroll.state import LOG_ACTION_BY_ACTION
from app.modules.piecework.models import PieceworkLog

logger = logging.getLogger("app.payroll")


class SettlementApproveMixin:
    """``approve`` / ``reverse``（由 :class:`~.settlement.PieceworkSettlementService` 组装）。

    ⚠️ 本 Mixin **不重新实现** ``_apply_status`` / ``_write_log``：它们唯一的一份在
    :class:`~.settlement_state.SettlementStateMixin`，本类只在 ``TYPE_CHECKING`` 下声明形状，
    组合类按 MRO 取到那一份（``AGENTS`` §2.1：状态字段只由状态机迁移方法写）。
    """

    session: AsyncSession

    if TYPE_CHECKING:
        # 交叉引用 SettlementStateMixin 的私有助手（照抄 bundling ApproveMixin 的写法）：
        # 运行期不 import 兄弟 Mixin，只在类型检查时声明形状 —— 少一层 import 就少一个环。
        def _assert_can(self, action: str, ctx: AuthContext) -> None: ...
        def _assert_transition(
            self, action: str, from_status: DocumentStatus, to_status: DocumentStatus
        ) -> None: ...
        async def _get_settlement(self, settlement_id: UUID) -> PieceworkSettlement: ...
        async def _apply_status(
            self,
            settlement: PieceworkSettlement,
            *,
            to_status: DocumentStatus,
            expected_version: int,
            operator_id: UUID,
            extra: dict[str, Any] | None = None,
        ) -> None: ...
        async def _write_log(
            self,
            settlement: PieceworkSettlement,
            operator_id: UUID,
            action: str,
            from_status: str | None,
            to_status: str,
            reason: str | None = None,
            changed_fields: dict[str, Any] | None = None,
        ) -> None: ...
        async def _reloaded(self, settlement_id: UUID) -> PieceworkSettlement: ...

    # ================================================================== approve

    async def approve(
        self,
        settlement_id: UUID,
        *,
        expected_version: int,
        operator_id: UUID,
        ctx: AuthContext,
        remark: str | None = None,
    ) -> PieceworkSettlement:
        """审核（``SUBMITTED`` → ``APPROVED``）：审核即**占用**全部行对应的计件流水。

        前置：① 状态可审核（否则 ``10007``）；② **制单人 ≠ 审核人**（``10005``，BR-PR-10）；
        ③ 重算 ``total_qty`` / ``total_amount`` / ``line_count`` 与行汇总一致（不一致 → ``33002``，
        数据异常，绝不静默放行）；④ 行对应流水未被其他结算单占用（``33002``，唯一索引是最终兜底）。

        副作用（同一事务）：``UPDATE piecework_logs SET payroll_id=本单 WHERE id IN(本单行)
        AND payroll_id IS NULL``；命中数必须 == 本单行数，否则整体回滚。写 ``document_logs``
        （``APPROVE``，含 ``occupied_log_count``）。

        :param remark: 可选审核备注，落 ``document_logs.reason``。
        :raises BusinessError: ``12001`` 无权限；``20001`` 单据不存在；``10007`` 非法迁移；
            ``10005`` 自审；``33002`` 汇总不符 / 流水被占用；``10003`` 乐观锁。
        """
        self._assert_can("approve", ctx)
        async with unit_of_work(self.session):
            settlement = await self._get_settlement(settlement_id)
            self._assert_transition("approve", settlement.status, DocumentStatus.APPROVED)
            self._assert_not_self_approval(settlement, ctx)
            lines = await self._load_lines(settlement.id)
            self._assert_totals_match(settlement, lines)
            log_ids = [line.piecework_log_id for line in lines]
            await self._assert_logs_unoccupied(log_ids)
            occupied = await self._occupy_logs(settlement.id, log_ids)
            if occupied != len(log_ids):
                raise BusinessError(
                    ErrorCode.SETTLEMENT_PERIOD_DUPLICATED,
                    "结算行对应的计件流水已被其他结算单占用，本次审核整体回滚",
                    details={
                        "doc_no": settlement.doc_no,
                        "expected_log_count": len(log_ids),
                        "occupied_log_count": occupied,
                    },
                )
            from_status = settlement.status.value
            # ⚠️ 日志先写、状态后迁：Core UPDATE 会 expire ORM 行，之后再读 doc_no 会 MissingGreenlet。
            await self._write_log(
                settlement,
                operator_id,
                LOG_ACTION_BY_ACTION["approve"],
                from_status,
                DocumentStatus.APPROVED.value,
                remark,
                changed_fields={"occupied_log_count": occupied},
            )
            await self._apply_status(
                settlement,
                to_status=DocumentStatus.APPROVED,
                expected_version=expected_version,
                operator_id=operator_id,
                extra={"approved_by": operator_id, "approved_at": datetime.now(tz=UTC)},
            )
            fresh = await self._reloaded(settlement_id)
        logger.info(
            "计件结算单已审核（已占用流水）",
            extra={"doc_no": fresh.doc_no, "occupied_log_count": occupied},
        )
        return fresh

    # ================================================================== reverse

    async def reverse(
        self,
        settlement_id: UUID,
        *,
        reason: str | None,
        expected_version: int,
        operator_id: UUID,
        ctx: AuthContext,
    ) -> PieceworkSettlement:
        """反审核（``APPROVED`` → ``SUBMITTED``）：**必填原因** + 与 ``approve`` 严格对称。

        前置：① 状态可反审核（否则 ``10007``）；② **必填原因**（``10006``，BR-PR-11）；
        ③ 下游无 ``APPROVED`` / ``PAID`` 工资单（有 → ``33001``，05 §4.4 下游校验）。

        副作用（同一事务）：清空本单全部行对应流水的 ``payroll_id``（流水重新可结算）。
        **不删行、不删单**（08 R5）；写 ``document_logs``（``REVERSE`` + 原因，
        含 ``released_log_count``）。

        :raises BusinessError: ``12001`` 无权限；``10006`` 缺原因；``20001`` 单据不存在；
            ``10007`` 非法迁移；``33001`` 下游工资已审核/已发放；``10003`` 乐观锁。
        """
        self._assert_can("reverse", ctx)
        reason_text = require_reason(reason, action="反审核结算单")
        async with unit_of_work(self.session):
            settlement = await self._get_settlement(settlement_id)
            self._assert_transition("reverse", settlement.status, DocumentStatus.SUBMITTED)
            await self._assert_no_downstream_sheets(settlement.id)
            lines = await self._load_lines(settlement.id)
            log_ids = [line.piecework_log_id for line in lines]
            released = await self._release_logs(settlement.id, log_ids)
            from_status = settlement.status.value
            await self._write_log(
                settlement,
                operator_id,
                LOG_ACTION_BY_ACTION["reverse"],
                from_status,
                DocumentStatus.SUBMITTED.value,
                reason_text,
                changed_fields={"released_log_count": released},
            )
            await self._apply_status(
                settlement,
                to_status=DocumentStatus.SUBMITTED,
                expected_version=expected_version,
                operator_id=operator_id,
                extra={"approved_by": None, "approved_at": None},
            )
            fresh = await self._reloaded(settlement_id)
        logger.info(
            "计件结算单已反审核（已释放流水）",
            extra={
                "doc_no": fresh.doc_no,
                "released_log_count": released,
                "reason": reason_text,
            },
        )
        return fresh

    # ================================================================== 校验

    def _assert_not_self_approval(self, settlement: PieceworkSettlement, ctx: AuthContext) -> None:
        """制单人 ≠ 审核人（08 §4 / BR-PR-10）。相等 → ``10005``。

        判 ``ctx.user_id``（认证身份）而不是 ``operator_id``：后者由调用方传入，用它判会把
        「传错身份」变成静默放过（同 :meth:`SettlementStateMixin._assert_creator`）。
        """
        if ctx.user_id != settlement.created_by:
            return
        raise BusinessError(
            ErrorCode.SELF_APPROVAL_FORBIDDEN,
            "不能审核自己制作的结算单，请由他人审核",
            details={"doc_no": settlement.doc_no},
        )

    @staticmethod
    def _assert_totals_match(
        settlement: PieceworkSettlement, lines: list[PieceworkSettlementLine]
    ) -> None:
        """单头合计必须 == 行汇总（BR-PR-03 / §5.7 三方对账的下界）。

        ``create`` 时单头由 service 重算（结构上必然一致），这里的重算是**防篡改 / 防漂移**：
        行被外部改过、或单头被手改过时，绝不能带着错的金额进入工资计算。
        """
        total_qty = sum((line.qty for line in lines), start=Decimal("0"))
        total_amount = sum((line.amount for line in lines), start=Decimal("0"))
        if (
            total_qty == settlement.total_qty
            and total_amount == settlement.total_amount
            and len(lines) == settlement.line_count
        ):
            return
        raise BusinessError(
            ErrorCode.SETTLEMENT_PERIOD_DUPLICATED,
            "结算单汇总与行明细不一致，不能审核（数据异常，请重建结算单）",
            details={
                "doc_no": settlement.doc_no,
                "header": {
                    "total_qty": str(settlement.total_qty),
                    "total_amount": str(settlement.total_amount),
                    "line_count": settlement.line_count,
                },
                "lines": {
                    "total_qty": str(total_qty),
                    "total_amount": str(total_amount),
                    "line_count": len(lines),
                },
            },
        )

    async def _assert_logs_unoccupied(self, log_ids: list[UUID]) -> None:
        """行对应流水不得被其他结算单占用（P3 预检；最终兜底是唯一索引）。占用 → ``33002``。"""
        if not log_ids:
            return
        occupied = await self.session.scalar(
            select(func.count())
            .select_from(PieceworkLog)
            .where(
                PieceworkLog.id.in_(log_ids),
                PieceworkLog.payroll_id.is_not(None),
                PieceworkLog.deleted_at.is_(None),
            )
        )
        if occupied:
            raise BusinessError(
                ErrorCode.SETTLEMENT_PERIOD_DUPLICATED,
                "有计件流水已被其他结算单占用，不能重复结算",
                details={"occupied_log_count": int(occupied)},
            )

    async def _assert_no_downstream_sheets(self, settlement_id: UUID) -> None:
        """下游工资单已 ``APPROVED`` / ``PAID`` 时禁止反审核（05 §4.4 / §5.6）。→ ``33001``。"""
        downstream = await self.session.scalar(
            select(PayrollSheet.id)
            .where(
                PayrollSheet.settlement_id == settlement_id,
                PayrollSheet.status.in_([DocumentStatus.APPROVED, DocumentStatus.PAID]),
                PayrollSheet.deleted_at.is_(None),
            )
            .limit(1)
        )
        if downstream is not None:
            raise BusinessError(
                ErrorCode.PAYROLL_SHEET_ALREADY_PAID,
                "来源结算单的工资单已审核 / 已发放，不能反审核结算单；请先反审核工资单",
                details={"settlement_id": str(settlement_id)},
            )

    # ================================================================== 取数 / 写

    async def _load_lines(self, settlement_id: UUID) -> list[PieceworkSettlementLine]:
        """本单全部有效行（按 ``line_no``）。"""
        rows = await self.session.execute(
            select(PieceworkSettlementLine)
            .where(
                PieceworkSettlementLine.doc_id == settlement_id,
                PieceworkSettlementLine.deleted_at.is_(None),
            )
            .order_by(PieceworkSettlementLine.line_no)
        )
        return list(rows.scalars().all())

    async def _occupy_logs(self, settlement_id: UUID, log_ids: list[UUID]) -> int:
        """审核副作用：条件 UPDATE 回写 ``payroll_id``（``WHERE payroll_id IS NULL``，P3）。

        :returns: 实际命中（占用）的流水条数。
        """
        if not log_ids:
            return 0
        result = await self.session.execute(
            update(PieceworkLog)
            .where(PieceworkLog.id.in_(log_ids), PieceworkLog.payroll_id.is_(None))
            .values(payroll_id=settlement_id)
            .returning(PieceworkLog.id)
        )
        return len(result.all())

    async def _release_logs(self, settlement_id: UUID, log_ids: list[UUID]) -> int:
        """反审核副作用：只清**本单占用**的流水的 ``payroll_id``（与 ``approve`` 严格对称）。"""
        if not log_ids:
            return 0
        result = await self.session.execute(
            update(PieceworkLog)
            .where(
                PieceworkLog.id.in_(log_ids),
                PieceworkLog.payroll_id == settlement_id,
            )
            .values(payroll_id=None)
            .returning(PieceworkLog.id)
        )
        return len(result.all())


__all__ = ["SettlementApproveMixin"]
