"""计件**写路径**：手工补录 / 返工补录（``docs/modules/04-计件.md`` §5.3 步骤 4 + §5.4 流程 D）。

作为 :class:`PieceworkService` 的 **mixin**（``PieceworkService(ReverseMixin, ManualMixin)``）。

## 为什么单独一个 mixin，不塞进 ``reverse.py``

``reverse.py``（红冲）与这里的「补录 / 手工补录」各自约 120 行，合起来 240 行 +
共享助手会顶破 400 行硬线（ADR-0030）。两者的**共同点**只有「锁码」—— 已在
:mod:`.write_base` 抽成 ``WriteBaseMixin``；各自独立的校验 / 幂等 / 回写留在这里。

## 补录的两条不变量（每条都有测试）

1. **``qty ≤ bundle_qty`` 在补录 / 手工补录路径同样强制**（``32006``，TC-46）：
   否则「补录改数绕过超产」就是给返工开的后门。
2. **append-only**：只 INSERT（``SUPPLEMENT`` 正值 / ``MANUAL`` 正值），
   绝不 ``UPDATE`` 原流水的 ``qty`` / ``amount`` / ``bundle_no``（ADR-0005）。

## ⚠️ 一个未定口径（不自行发明，见 :meth:`ManualMixin._credit_for`）

「补录算给谁」：SUPPLEMENT 一定算给**被红冲的原流水那个员工**（§5.3：L3 是对 L1 的
纠正）。而 MANUAL（漏扫码）的请求体**故意不带 employee_id**（§6.1 没有这一项），
漏扫码的码又是未计件状态（``counted_by`` 空）—— 所以 MANUAL 算给谁**规范没写**。
这里按 §5.4 标题「计件员对漏扫码补录」**算给操作员**，并留 ``TODO(业务待确认)``。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.models import DocumentLog
from app.core.db import unit_of_work
from app.core.errors import BusinessError, ErrorCode
from app.core.numbering import BUSINESS_TZ
from app.core.permissions import AuthContext
from app.modules.piecework.audit import DOC_TYPE_PIECEWORK
from app.modules.piecework.guard import LockedBundle, guard_bundle, resolve_qty
from app.modules.piecework.models import PieceworkLog, PieceworkLogType
from app.modules.piecework.pricing import resolve_price
from app.modules.piecework.repository import (
    build_insert_log_stmt,
    build_mark_counted_stmt,
)
from app.modules.piecework.schemas import PieceworkLogIn, PieceworkLogWriteOut
from app.modules.piecework.write_base import WriteBaseMixin

#: 金额精度（numeric(18,4)，与 service 的 _MONEY_SCALE 一致）。
_MONEY = Decimal("0.0001")


class ManualMixin(WriteBaseMixin):
    """补录 / 手工补录。``self.session`` 由 ``PieceworkService`` 提供。"""

    session: AsyncSession

    async def write_log(
        self, payload: PieceworkLogIn, operator_id: UUID, ctx: AuthContext
    ) -> PieceworkLogWriteOut:
        """§5.3 步骤 4（SUPPLEMENT）+ §5.4 流程 D（MANUAL）。

        校验链（§5.4 ③ 逐条）：码有效 → 绑定/权限 → 他人未计（32001）→ 取价（20004）
        → qty 正整数（32005）→ qty ≤ bundle_qty（32006）。
        """
        if payload.log_type not in (PieceworkLogType.MANUAL, PieceworkLogType.SUPPLEMENT):
            raise BusinessError(
                ErrorCode.PARAM_INVALID,
                f"log_type 只能是 MANUAL 或 SUPPLEMENT，收到 {payload.log_type!r}",
            )
        # ⚠️ ``remark`` 必填（§9：手工补录未填 remark → 10002）。
        #     schema 层把 ``min_length=1`` 拿掉：空串由 service 统一回 ``10002``
        #     （与红冲的 ``reason`` 同一口径），而不是 422。
        if not (payload.remark or "").strip():
            raise BusinessError(ErrorCode.MISSING_BUSINESS_PARAM, "补录必须填 remark（写清依据）")

        async with unit_of_work(self.session):
            bundle = await self._lock_bundle_by_no(payload.bundle_no)
            guard_bundle(bundle, operator_id)  # 不存在 31001 / 作废 31002 / 他人已计 32001

            bound = await self._is_bound(ctx, bundle)
            if not bound and not ctx.has("piecework:manual"):
                raise BusinessError(
                    ErrorCode.PIECEWORK_NOT_BOUND,
                    f"员工未绑定 {bundle['style_no']} 的 {bundle['operation_no']} 工序",
                )

            if payload.log_type == PieceworkLogType.SUPPLEMENT and payload.reversal_of_id is None:
                raise BusinessError(
                    ErrorCode.MISSING_BUSINESS_PARAM,
                    "SUPPLEMENT 必须提供 reversal_of_id（指回被红冲的原流水）",
                )
            if payload.log_type == PieceworkLogType.MANUAL and payload.reversal_of_id is not None:
                raise BusinessError(
                    ErrorCode.PARAM_INVALID, "MANUAL 不允许 reversal_of_id（漏扫码没有原流水）"
                )

            # ⚠️ **算给谁**：见模块 docstring 的「未定口径」。
            credited_employee, credited_group = await self._credit_for(payload, bundle, operator_id)
            work_date = _business_today()
            unit_price = await resolve_price(
                self.session, bundle["style_no"], bundle["operation_no"], work_date
            )
            qty = resolve_qty(payload.qty, bundle["bundle_qty"])
            amount = (qty * unit_price).quantize(_MONEY, rounding=ROUND_HALF_UP)

            log = PieceworkLog(
                id=uuid4(),
                bundle_no=bundle["bundle_no"],
                style_no=bundle["style_no"],
                operation_no=bundle["operation_no"],
                employee_id=credited_employee,
                workshop_id=bundle["workshop_id"],
                group_no=credited_group,
                work_date=work_date,
                bundle_id=bundle["id"],
                qty=qty,
                unit_price=unit_price,
                amount=amount,
                log_type=payload.log_type,
                scanned_at=datetime.now(tz=UTC),
                reversal_of_id=payload.reversal_of_id,
                remark=payload.remark,
                created_by=operator_id,
                updated_by=operator_id,
            )
            try:
                async with self.session.begin_nested():
                    await self.session.execute(build_insert_log_stmt(log))
            except IntegrityError:
                # 唯一键兜底：同 (bundle_no, operation_no, employee_id, work_date, log_type)
                # 已存在 → 幂等返回既有（§6.3）。
                existing = await self._same_key_log(bundle, work_date, payload.log_type)
                if existing is not None:
                    return self._write_out(existing, bundle, existing.amount, work_date)
                raise

            await self._rewrite_counted(bundle, qty, credited_employee, operator_id)
            doc_id = await self._write_create_log(log, operator_id, remark=payload.remark)
            return PieceworkLogWriteOut(
                log_id=log.id,
                document_log_id=doc_id,
                bundle_no=bundle["bundle_no"],
                style_no=bundle["style_no"],
                operation_no=bundle["operation_no"],
                qty=_qty_str(qty),
                bundle_qty=_qty_str(bundle["bundle_qty"]),
                unit_price=_price_str(unit_price),
                amount=_money_str(amount),
                log_type=payload.log_type,
                work_date=work_date,
            )

    # ================================================================== 内部

    async def _credit_for(
        self, payload: PieceworkLogIn, bundle: LockedBundle, operator_id: UUID
    ) -> tuple[UUID, str | None]:
        """这条补录流水**算给谁**（及其组别）。

        - SUPPLEMENT → 被红冲的原流水那个员工（§5.3：L3 是对 L1 的纠正，责任人是 L1 的员工）
        - MANUAL → 操作员（§5.4 标题「计件员对漏扫码补录」；**待业务方确认**）

        ⚠️ 组别快照的是**员工当时的组别**（04 §7.1：历史流水属当时的组），
        所以这里查 ``users.group_no`` 而不是复用 bundle 上任何东西。
        """
        from app.modules.auth.models import User

        if payload.log_type == PieceworkLogType.SUPPLEMENT:
            if payload.reversal_of_id is None:  # 已在上面校验过，这里防呆
                raise BusinessError(
                    ErrorCode.MISSING_BUSINESS_PARAM, "SUPPLEMENT 需要 reversal_of_id"
                )
            original = (
                await self.session.execute(
                    select(PieceworkLog.employee_id).where(
                        PieceworkLog.id == payload.reversal_of_id,
                        PieceworkLog.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if original is None:
                raise BusinessError(
                    ErrorCode.DATA_SCOPE_DENIED,
                    "reversal_of_id 对应的原流水不存在，无法补录",
                    details={"reversal_of_id": str(payload.reversal_of_id)},
                )
            employee_id = original
        else:  # MANUAL
            employee_id = operator_id
        group_no = (
            await self.session.execute(select(User.group_no).where(User.id == employee_id))
        ).scalar_one_or_none()
        return employee_id, group_no

    async def _rewrite_counted(
        self, bundle: LockedBundle, qty: Decimal, employee_id: UUID, operator_id: UUID
    ) -> None:
        """补录 / 手工补录后**重写** `counted_*`。

        ⚠️ ``employee_id`` 是**被计件的人**（SUPPLEMENT 的原员工 / MANUAL 的操作员），
        不是操作员 —— 与链路 A 的 `counted_by = employee_id` 同一口径（ADR-0016 决策 3）。
        """
        await self.session.execute(
            build_mark_counted_stmt(
                bundle["id"], qty, employee_id, operator_id, datetime.now(tz=UTC)
            )
        )

    async def _same_key_log(
        self, bundle: LockedBundle, work_date: date, log_type: PieceworkLogType
    ) -> PieceworkLog | None:
        return (
            await self.session.execute(
                select(PieceworkLog).where(
                    PieceworkLog.bundle_no == bundle["bundle_no"],
                    PieceworkLog.operation_no == bundle["operation_no"],
                    PieceworkLog.employee_id == (bundle["counted_by"] or None),
                    PieceworkLog.work_date == work_date,
                    PieceworkLog.log_type == log_type,
                    PieceworkLog.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()

    async def _write_create_log(self, log: PieceworkLog, operator_id: UUID, *, remark: str) -> UUID:
        entry = DocumentLog(
            doc_type=DOC_TYPE_PIECEWORK,
            doc_id=log.id,
            doc_no=log.bundle_no,
            action="CREATE",
            from_status=None,
            to_status="LOGGED",
            operator_id=operator_id,
            operator_name="系统",
            reason=remark,
            changed_fields={"log_type": log.log_type.value, "qty": str(log.qty)},
        )
        self.session.add(entry)
        await self.session.flush()
        return entry.id

    def _write_out(
        self, log: PieceworkLog, bundle: LockedBundle, amount: Decimal, work_date: date
    ) -> PieceworkLogWriteOut:
        """幂等命中的响应。⚠️ ``document_log_id`` 用 ``log.id``（它本就是合法 UUID，
        而这条流水已经落过库、留过痕 —— 这次响应只是「你重复提交了」的提示，
        不必再去查那条 CREATE 日志）。"""
        return PieceworkLogWriteOut(
            log_id=log.id,
            document_log_id=log.id,
            bundle_no=bundle["bundle_no"],
            style_no=bundle["style_no"],
            operation_no=bundle["operation_no"],
            qty=log.qty,
            bundle_qty=bundle["bundle_qty"],
            unit_price=log.unit_price,
            amount=amount,
            log_type=log.log_type,
            work_date=work_date,
        )


def _business_today() -> date:
    return datetime.now(tz=BUSINESS_TZ).date()


def _qty_str(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.001"))


def _price_str(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.000001"))


def _money_str(value: Decimal) -> Decimal:
    return value.quantize(_MONEY)
