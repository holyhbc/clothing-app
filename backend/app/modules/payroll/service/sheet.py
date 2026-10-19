"""工资单生成（T-PAY-006）。

由**已审核结算单**按 ``(employee_id, style_no, operation_no)`` 汇总生成工资单行，
快照单价与银行资料；轻状态迁移见
:mod:`~app.modules.payroll.service.sheet_state_mixin`。

设计权威：``docs/modules/05-工资.md`` §2（BR-PR-05/06/22）、§3.4/§3.5、§4.2、§5.3、
§6.3、§8（S5~S7）、§11（TC-04/05）；``docs/08 §2.4``。

## 单价口径（Q-PL13）

``05 §5.3`` 明写「``unit_price`` 取结算行明细值，**不重读基础资料**」。同一
``(员工, 款号, 工序)`` 的多条结算行因**不同日期调价**可能单价不一（结算行本身是逐条
快照，``§3.3``）。规范未定义多值取舍，本卡取 **加权平均**
``unit_price = Σamount / Σqty``（六位小数，``ROUND_HALF_UP``）：它保证
``qty × unit_price ≈ gross_amount``，比「取首条」在工资条 / 对账上不自相矛盾；
``Σqty == 0``（全额红冲净额为 0）时退回首条快照值。已登记
``docs/modules/05-工资.md §12`` 的 **Q-PL13** 待规范确认。

## 幂等

``settlement_id`` 业务唯一（``uq_payroll_sheets_settlement_id``，阶段一 1:1）命中 →
返回既有单（``code=0``，05 §6.3）；并发下唯一索引兜底，命中同样返回既有单而不报错。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Final
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import DataScope, DocumentAction, DocumentStatus
from app.core.db import unit_of_work
from app.core.errors import BusinessError, ErrorCode
from app.core.numbering import business_today, quantize_price, take_doc_no
from app.core.permissions import AuthContext
from app.core.scope import visible_workshops
from app.modules.auth.models import User
from app.modules.payroll.models import (
    PayrollDetail,
    PayrollSheet,
    PieceworkSettlement,
    PieceworkSettlementLine,
)
from app.modules.payroll.service.pay import PayrollPayMixin
from app.modules.payroll.service.sheet_approve import SheetApproveMixin
from app.modules.payroll.service.sheet_state import SHEET_DOC_PREFIX
from app.modules.payroll.service.sheet_state_mixin import SheetStateMixin
from app.modules.payroll.voucher_port import NullVoucherGateway, VoucherPort

logger = logging.getLogger("app.payroll")

#: 银行 / 身份证快照字段（``05 §3.5`` 从 ``users`` 落到行上）。
_BANK_FIELDS: Final[tuple[str, ...]] = ("id_card", "bank_name", "bank_account")
#: 员工不存在或字段缺失时的空快照（缺失**不阻断生成**，发放时才拦 ``10002``）。
_EMPTY_BANK_SNAPSHOT: Final[dict[str, str | None]] = {
    "id_card": None,
    "bank_name": None,
    "bank_account": None,
}


@dataclass(slots=True)
class _GroupAgg:
    """一个 ``(员工, 款号, 工序)`` 分组的汇总中间态。"""

    qty: Decimal
    amount: Decimal
    first_unit_price: Decimal
    mixed: bool = False


class PayrollSheetService(PayrollPayMixin, SheetApproveMixin, SheetStateMixin):
    """工资单 service：``create`` 生成 + 轻状态迁移 + 审核计提（T-PAY-007）+ 发放（T-PAY-008）。

    ⚠️ state 的持久化助手（``_apply_status`` / ``_write_log`` / ``_get_sheet`` 等）
    在 :class:`~.sheet_state_support.SheetStoreMixin`，经 :class:`SheetStateMixin` 继承；
    ``pay`` / 代发导出在 :class:`~.pay.PayrollPayMixin`，``approve`` / ``reverse`` 在
    :class:`~.sheet_approve.SheetApproveMixin`。三者共用唯一一份状态机与日志助手。
    """

    def __init__(self, session: AsyncSession, *, voucher_port: VoucherPort | None = None) -> None:
        self.session = session
        self.voucher_port: VoucherPort = (
            voucher_port if voucher_port is not None else NullVoucherGateway()
        )

    # ================================================================== 生成

    async def create(
        self,
        *,
        settlement_id: UUID,
        ctx: AuthContext,
        pay_date: date | None = None,
        doc_date: date | None = None,
    ) -> PayrollSheet:
        """由已审核结算单生成工资单（05 §4.2 / §5.3）。

        步骤：① 来源结算单 ``APPROVED``（否则 ``33003``）且周期仍 ``OPEN``（``33003``）；
        ② ``settlement_id`` 命中 → 幂等返回既有单；③ 按 ``(员工, 款号, 工序)`` 汇总
        结算行（``qty=Σqty``、``unit_price`` 取快照、``gross=Σamount``、扣款恒 0）；
        ④ 快照 ``users`` 银行资料到行上；⑤ 写单头（``total_*`` 由 service 重算）+ 行；
        ⑥ 写 ``document_logs``。

        :param doc_date: 单据日期，默认业务今天（测试显式传入以免跨零点失败）。
        :raises BusinessError: ``12001`` 无权限；``12002`` 车间越权；``20001``
            结算单/周期不存在；``33003`` 来源未审核或周期已关闭；``10003`` 乐观锁。
        """
        self._assert_can("create", ctx)
        async with unit_of_work(self.session):
            settlement = await self._get_settlement(settlement_id)
            self._assert_workshop_in_scope(settlement.workshop_id, ctx)
            self._assert_settlement_approved(settlement)
            period = await self._locked_period(settlement.period_id)
            self._assert_period_open(period)

            existing = await self._find_existing(settlement_id)
            if existing is not None:
                logger.info(
                    "工资单幂等命中，返回既有单",
                    extra={"doc_no": existing.doc_no, "settlement_id": str(settlement_id)},
                )
                return existing

            lines = await self._load_settlement_lines(settlement.id)
            groups = self._group_lines(lines)
            self._log_mixed_prices(groups)
            snapshot = await self._load_bank_snapshot({key[0] for key in groups})
            resolved_date = doc_date if doc_date is not None else business_today()
            doc_no = await take_doc_no(
                self.session, prefix=SHEET_DOC_PREFIX, doc_date=resolved_date
            )
            header = self._build_header(
                settlement, groups, doc_no, resolved_date, pay_date, ctx.user_id
            )
            if await self._insert(header, groups, snapshot, ctx.user_id) is None:
                return await self._resolve_conflict(settlement_id)

            await self._write_log(
                header,
                ctx.user_id,
                DocumentAction.CREATE.value,
                None,
                DocumentStatus.DRAFT.value,
            )
            logger.info(
                "工资单已生成",
                extra={
                    "doc_no": doc_no,
                    "settlement_id": str(settlement_id),
                    "detail_count": header.detail_count,
                    "total_gross": str(header.total_gross),
                },
            )
            return await self._reloaded(header.id)

    # ================================================================== 取数

    async def _get_settlement(self, settlement_id: UUID) -> PieceworkSettlement:
        """按 id 取结算单。不存在 / 软删 → ``20001``。"""
        settlement = (
            await self.session.execute(
                select(PieceworkSettlement).where(
                    PieceworkSettlement.id == settlement_id,
                    PieceworkSettlement.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if settlement is None:
            raise BusinessError(
                ErrorCode.BASE_DATA_NOT_FOUND,
                "结算单不存在或已删除",
                details={"settlement_id": str(settlement_id)},
            )
        return settlement

    async def _load_settlement_lines(self, settlement_id: UUID) -> list[PieceworkSettlementLine]:
        """本结算单全部有效行，按分组键 + ``line_no`` 排序（``line_no`` 稳定可复现）。"""
        rows = await self.session.execute(
            select(PieceworkSettlementLine)
            .where(
                PieceworkSettlementLine.doc_id == settlement_id,
                PieceworkSettlementLine.deleted_at.is_(None),
            )
            .order_by(
                PieceworkSettlementLine.employee_id,
                PieceworkSettlementLine.style_no,
                PieceworkSettlementLine.operation_no,
                PieceworkSettlementLine.line_no,
            )
        )
        return list(rows.scalars().all())

    async def _find_existing(self, settlement_id: UUID) -> PayrollSheet | None:
        """业务唯一键 ``settlement_id`` 查既有单（幂等命中返回它，不报错）。"""
        return (
            await self.session.execute(
                select(PayrollSheet).where(
                    PayrollSheet.settlement_id == settlement_id,
                    PayrollSheet.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()

    async def _load_bank_snapshot(
        self, employee_ids: set[UUID]
    ) -> dict[UUID, dict[str, str | None]]:
        """从 ``users`` 快照银行资料（缺失 = ``None``，**不阻断生成**）。"""
        if not employee_ids:
            return {}
        users = (
            (await self.session.execute(select(User).where(User.id.in_(employee_ids))))
            .scalars()
            .all()
        )
        return {user.id: _bank_snapshot(user) for user in users}

    # ================================================================== 汇总 / 组装

    def _group_lines(
        self, lines: list[PieceworkSettlementLine]
    ) -> dict[tuple[UUID, str, str], _GroupAgg]:
        """按 ``(employee_id, style_no, operation_no)`` 汇总（BR-PR-05）。"""
        groups: dict[tuple[UUID, str, str], _GroupAgg] = {}
        for line in lines:
            key = (line.employee_id, line.style_no, line.operation_no)
            agg = groups.get(key)
            if agg is None:
                groups[key] = _GroupAgg(
                    qty=line.qty, amount=line.amount, first_unit_price=line.unit_price
                )
                continue
            if line.unit_price != agg.first_unit_price:
                agg.mixed = True
            agg.qty += line.qty
            agg.amount += line.amount
        return groups

    def _log_mixed_prices(self, groups: dict[tuple[UUID, str, str], _GroupAgg]) -> None:
        """同一分组出现多个单价时记警告（Q-PL13 的口径可追溯）。"""
        if any(agg.mixed for agg in groups.values()):
            logger.warning(
                "工资单汇总存在同一分组多条不同单价，unit_price 取加权平均（Q-PL13）",
                extra={"mixed_group_count": sum(1 for a in groups.values() if a.mixed)},
            )

    def _build_header(
        self,
        settlement: PieceworkSettlement,
        groups: dict[tuple[UUID, str, str], _GroupAgg],
        doc_no: str,
        doc_date: date,
        pay_date: date | None,
        operator_id: UUID,
    ) -> PayrollSheet:
        """建单头；``total_*`` 由 service **重算**（扣款恒 0，Q-PL01）。"""
        total_gross = sum((agg.amount for agg in groups.values()), start=Decimal("0"))
        return PayrollSheet(
            doc_no=doc_no,
            status=DocumentStatus.DRAFT,
            doc_date=doc_date,
            period_id=settlement.period_id,
            period_code=settlement.period_code,
            workshop_id=settlement.workshop_id,
            pay_date=pay_date,
            employee_count=len({key[0] for key in groups}),
            detail_count=len(groups),
            total_gross=total_gross,
            total_deduction=Decimal("0"),
            total_net=total_gross,
            settlement_id=settlement.id,
            created_by=operator_id,
            updated_by=operator_id,
        )

    def _build_details(
        self,
        doc_id: UUID,
        groups: dict[tuple[UUID, str, str], _GroupAgg],
        snapshot: dict[UUID, dict[str, str | None]],
        operator_id: UUID,
    ) -> list[PayrollDetail]:
        """每人每（款号 × 工序）一行；``net = gross - deduction``（BR-PR-06）。"""
        ordered = sorted(groups.items(), key=lambda item: (str(item[0][0]), item[0][1], item[0][2]))
        details: list[PayrollDetail] = []
        for index, (key, agg) in enumerate(ordered, start=1):
            employee_id, style_no, operation_no = key
            bank = snapshot.get(employee_id, _EMPTY_BANK_SNAPSHOT)
            details.append(
                PayrollDetail(
                    doc_id=doc_id,
                    line_no=index,
                    employee_id=employee_id,
                    style_no=style_no,
                    operation_no=operation_no,
                    qty=agg.qty,
                    unit_price=_resolve_unit_price(agg),
                    gross_amount=agg.amount,
                    deduction_amount=Decimal("0"),
                    net_amount=agg.amount,
                    deduction_type=None,
                    id_card=bank["id_card"],
                    bank_name=bank["bank_name"],
                    bank_account=bank["bank_account"],
                    created_by=operator_id,
                    updated_by=operator_id,
                )
            )
        return details

    async def _insert(
        self,
        header: PayrollSheet,
        groups: dict[tuple[UUID, str, str], _GroupAgg],
        snapshot: dict[UUID, dict[str, str | None]],
        operator_id: UUID,
    ) -> PayrollSheet | None:
        """同事务写单头 + 行。撞唯一约束（并发）返回 ``None``，由调用方转幂等。"""
        try:
            async with self.session.begin_nested():
                self.session.add(header)
                await self.session.flush()
                self.session.add_all(self._build_details(header.id, groups, snapshot, operator_id))
                await self.session.flush()
        except IntegrityError:
            return None
        return header

    async def _resolve_conflict(self, settlement_id: UUID) -> PayrollSheet:
        """并发唯一约束命中后重查既有单；查不到说明是别的唯一冲突 → ``33002``。"""
        existing = await self._find_existing(settlement_id)
        if existing is not None:
            logger.info(
                "工资单并发幂等命中（唯一约束兜底）",
                extra={"doc_no": existing.doc_no, "settlement_id": str(settlement_id)},
            )
            return existing
        raise BusinessError(
            ErrorCode.SETTLEMENT_PERIOD_DUPLICATED,
            "同一结算单已有工资单，或工资单号冲突，请刷新后查看",
            details={"settlement_id": str(settlement_id)},
        )

    # ================================================================== 校验

    def _assert_settlement_approved(self, settlement: PieceworkSettlement) -> None:
        """来源结算单必须 ``APPROVED``，否则不能生成工资单（``05 §4.2``）。

        ⚠️ ``05 §4.2`` 把「来源未审核」与「周期已关闭」并列为 ``33003``，故此处复用
        同一码；``details.reason`` 区分两种触发，前端可给出不同文案。
        """
        if settlement.status is DocumentStatus.APPROVED:
            return
        raise BusinessError(
            ErrorCode.PAYROLL_PERIOD_CLOSED,
            f"来源结算单 {settlement.doc_no} 尚未审核通过，不能生成工资单",
            details={
                "settlement_id": str(settlement.id),
                "status": settlement.status.value,
                "reason": "settlement_not_approved",
            },
        )

    def _assert_workshop_in_scope(self, workshop_id: UUID, ctx: AuthContext) -> None:
        """结算车间必须在数据范围内（越权 → ``12002``）。"""
        if ctx.data_scope == DataScope.FACTORY or workshop_id in visible_workshops(ctx):
            return
        raise BusinessError(
            ErrorCode.DATA_SCOPE_DENIED,
            "该车间超出你的数据范围，如需生成请联系管理员",
            details={"workshop_id": str(workshop_id)},
        )


def _resolve_unit_price(agg: _GroupAgg) -> Decimal:
    """分组单价：加权平均 ``Σamount / Σqty``；``Σqty == 0`` 退回首条快照值（Q-PL13）。"""
    if agg.qty != 0:
        return quantize_price(agg.amount / agg.qty, 6)
    return agg.first_unit_price


def _bank_snapshot(user: User | None) -> dict[str, str | None]:
    """读用户上的银行 / 身份证字段；字段不存在或非字符串一律 ``None``。"""
    return {name: _str_attr(user, name) for name in _BANK_FIELDS}


def _str_attr(user: User | None, name: str) -> str | None:
    if user is None:
        return None
    value = getattr(user, name, None)
    return value if isinstance(value, str) else None


__all__ = ["PayrollSheetService"]
