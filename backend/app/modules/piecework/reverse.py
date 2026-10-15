"""计件**写路径**：红冲（``docs/modules/04-计件.md`` §5.3 流程 C）。

作为 :class:`PieceworkService` 的 **mixin**（``PieceworkService(ReverseMixin, ManualMixin)``）。

## 为什么单独一个 mixin，不塞进 ``service.py``

``service.py`` 已是 349 行（链路 A 扫码）。红冲 + 补录各约 120 行，全塞进去
顶破 400 行硬线（ADR-0030）。红冲与补录 / 手工补录各自的校验 / 幂等 / 回写独立，
所以拆成 ``reverse.py``（本文件）与 ``manual.py``，共享的锁码助手在
:mod:`.write_base`。

## 红冲的三条不变量（每条都有测试）

1. **``FOR UPDATE`` 锁码**（§7.3 / TC-50）：红冲清空 ``counted_*`` 与「他人扫同码」
   并发时，不锁就是两头各写各的 —— 红冲清空那一瞬别人扫进来撞空，``counted_qty``
   终值无法保证唯一。
2. **append-only**：红冲是**新增**一条负值 ``REVERSAL``，绝不 ``UPDATE`` 原流水
   （ADR-0005）。
3. **``counted_*`` 清空是「回到未计件」的唯一真相**（``counted_at IS NULL`）——
   清空后他人能重扫，靠的是它，不是「删了原流水」。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import unit_of_work
from app.core.errors import BusinessError, ErrorCode
from app.core.numbering import BUSINESS_TZ
from app.core.permissions import AuthContext
from app.modules.piecework.audit import write_document_log
from app.modules.piecework.models import PieceworkLog, PieceworkLogType
from app.modules.piecework.repository import (
    build_insert_log_stmt,
    build_reset_counted_stmt,
)
from app.modules.piecework.schemas import PieceworkReverseIn, ReverseOut
from app.modules.piecework.write_base import WriteBaseMixin


class ReverseMixin(WriteBaseMixin):
    """红冲。``self.session`` 由 ``PieceworkService`` 提供。"""

    session: AsyncSession

    async def reverse_log(
        self,
        log_id: UUID,
        payload: PieceworkReverseIn,
        operator_id: UUID,
        ctx: AuthContext,
    ) -> ReverseOut:
        """§5.3 步骤 3：原流水 → ``REVERSAL`` + 同事务清空 ``counted_*``。

        ⚠️ 顺序不可调换：先锁原流水（能判越权 / 已结算 / 已红冲），再锁码（§7.3），
        再写红冲，再清码。
        """
        async with unit_of_work(self.session):
            log = await self._locked_original(log_id, ctx)

            if log["payroll_id"] is not None:
                raise BusinessError(
                    ErrorCode.PIECEWORK_SETTLED,
                    "该流水已结算，不可红冲；请联系计件员走结算前撤销",
                    details={"log_id": str(log_id)},
                )

            existing = await self._existing_reversal(log_id)
            if existing is not None:
                return ReverseOut(
                    reversal_log_id=existing.id,
                    bundle_no=existing.bundle_no,
                    style_no=existing.style_no,
                    operation_no=existing.operation_no,
                    reversed_qty=existing.qty,
                    reversed_amount=existing.amount,
                    bundle_counted_reset=await self._bundle_is_uncounted(existing.bundle_id),
                )

            # ⚠️ 自审（08 §4）：红冲人 ≠ 原流水创建人。沿用打菲的做法——
            #    不实现 super_admin + force 例外（approve_assert.py 注释即理由）。
            if log["created_by"] == operator_id:
                raise BusinessError(
                    ErrorCode.SELF_APPROVAL_FORBIDDEN,
                    "红冲人不能是原流水的创建人，请其他计件员操作",
                    details={"created_by": str(log["created_by"])},
                )

            bundle = await self._lock_bundle(log["bundle_id"])
            # ⚠️ 并发校验（§7.3 / TC-50）：他人已重新计了这手，清空会抹掉别人的劳动。
            if (
                bundle is not None
                and bundle["counted_at"] is not None
                and Decimal(bundle["counted_qty"]) != Decimal(log["qty"])
            ):
                raise BusinessError(
                    ErrorCode.ILLEGAL_OPERATION,
                    "这手码已被重新计件（数量与原流水不一致），先撤销那笔再红冲",
                    details={
                        "counted_qty": str(bundle["counted_qty"]),
                        "original_qty": str(log["qty"]),
                    },
                )

            reversal = PieceworkLog(
                id=uuid4(),
                bundle_no=log["bundle_no"],
                style_no=log["style_no"],
                operation_no=log["operation_no"],
                employee_id=log["employee_id"],
                workshop_id=log["workshop_id"],
                group_no=log["group_no"],
                work_date=log["work_date"],
                bundle_id=log["bundle_id"],
                qty=-log["qty"],
                unit_price=log["unit_price"],
                amount=-log["amount"],
                log_type=PieceworkLogType.REVERSAL,
                scanned_at=datetime.now(tz=UTC),
                reversal_of_id=log["id"],
                remark=payload.reason,
                created_by=operator_id,
                updated_by=operator_id,
            )
            async with self.session.begin_nested():
                await self.session.execute(build_insert_log_stmt(reversal))

            bundle_reset = False
            if log["bundle_id"] is not None:
                await self.session.execute(build_reset_counted_stmt(log["bundle_id"], operator_id))
                bundle_reset = True

            await write_document_log(
                self.session,
                log_id=reversal.id,
                # ⚠️ 审计的 `hands` / `bundle_qty` 是**码的上下文**（第几手 / 这手几件），
                #    从锁到的 bundle 取；bundle 可能不存在（bundle_id 空的历史行）→ 兜底。
                bundle={
                    "bundle_no": log["bundle_no"],
                    "hands": bundle["hands"] if bundle is not None else None,
                    "bundle_qty": bundle["bundle_qty"] if bundle is not None else log["qty"],
                },
                operator_id=operator_id,
                employee_id=log["employee_id"],
                qty=reversal.qty,
                amount=reversal.amount,
                action="REVERSE",
                to_status="REVERSED",
            )
            return ReverseOut(
                reversal_log_id=reversal.id,
                bundle_no=log["bundle_no"],
                style_no=log["style_no"],
                operation_no=log["operation_no"],
                reversed_qty=reversal.qty,
                reversed_amount=reversal.amount,
                bundle_counted_reset=bundle_reset,
            )


def _business_today() -> date:
    return datetime.now(tz=BUSINESS_TZ).date()
