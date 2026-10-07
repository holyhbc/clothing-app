"""打菲单**提交校验**与**预占/释放**的私有助手（T-BUND-005a）。

与 :mod:`.state_mixin` 分家只有一个理由：**ADR-0030 的单文件 ≤400 行硬上限**。
分家后职责也更清：本文件回答「这份单据能不能提交 / 该预占多少」，
:mod:`.state_mixin` 只回答「从哪个状态能到哪个状态、谁干的、留什么痕」。

⚠️ 这里的错误码**不是发明的**：``31004``（码数 ≠ 手数，B21）、``31005``（手序号
冲突，B22）、``30002``（超可用量，INV-6 口径）、``10001`` / ``20001`` 全部取自
`docs/05 §4` 与 `modules/03 §9`。

⚠️ **预占/释放必须成对**（AGENTS 硬要求）：:meth:`StateGuardMixin._reserve` 加多少，
:meth:`StateGuardMixin._release` 就减多少，两者的件数口径是同一个
:func:`_planned_quantities` —— 口径分叉会让释放永远对不上账。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessError, ErrorCode
from app.core.permissions import AuthContext
from app.modules.bundling.models import BundlingOrder, BundlingOrderLine
from app.modules.bundling.repository import (
    get_cutting_size_lines_by_ids,
    list_active_bundle_hands,
)
from app.modules.bundling.schemas import MAX_REASON
from app.modules.bundling.service.split import HAND_DUPLICATED_CODE, SplitLineInput, preview_order
from app.modules.bundling.state_repository import (
    OutputKey,
    available_qty_of,
    release_output_qty,
    reserve_output_qty,
)
from app.modules.cutting.models import CuttingOrderSizeLine, CuttingOutput

ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class OutputPlan:
    """一个 (色码, 尺码) 的预占计划（提交时算好，审核前不再变）。"""

    color_code: str
    size_code: str
    output_id: UUID
    qty: Decimal
    available_before: Decimal


def require_reason(value: str, *, action: str, field: str) -> str:
    """状态动作的**必填原因**（08 §1.1）。空白也算没填 → ``10002``。

    ⚠️ 不能只靠 pydantic 的 ``min_length=1``：``"   "`` 能过，落库后单据上会出现
    「原因：一片空白」，比报错难解释得多。
    """
    reason = value.strip()
    if not reason:
        raise BusinessError(
            ErrorCode.MISSING_BUSINESS_PARAM, f"{action}必须填写原因（{field}），空白不算"
        )
    return reason[:MAX_REASON]


def output_keys(lines: Sequence[BundlingOrderLine]) -> list[OutputKey]:
    """本单明细涉及的 (色码, 尺码) —— 结转行的业务键，去重**且有序**。"""
    return sorted({(line.color_code, line.size_code) for line in lines})


def planned_quantities(
    lines: Sequence[BundlingOrderLine], size_lines: Mapping[UUID, CuttingOrderSizeLine]
) -> dict[OutputKey, Decimal]:
    """各 (色码, 尺码) 的**打菲件数** = ``Σ(hands × qty_per_hand)``。

    ⚠️ 件数取**裁剪尺码明细的 ``qty_per_hand``**（B20 / Q-B15），不是本单行的
    ``planned_qty``：审核生成码用的是同一个式子，预占必须与它**逐分对齐**，
    少预占就是超发（INV-6）。
    ⚠️ 非正的量**跳过**而不是报错：这条路径也用于**释放**，裁剪侧数据被改坏时不该把
    「驳回 / 撤回」一起堵死 —— 那会让单据永久卡在 SUBMITTED。
    """
    totals: dict[OutputKey, Decimal] = {}
    for line in lines:
        source = size_lines.get(line.cutting_size_line_id)
        if source is None:
            continue
        qty = Decimal(source.qty_per_hand) * line.hands
        if qty <= ZERO:
            continue
        key = (line.color_code, line.size_code)
        totals[key] = totals.get(key, ZERO) + qty
    return totals


class StateGuardMixin:
    """提交校验 + 预占/释放（私有助手，由 :class:`BundlingOrderService` 组装）。"""

    session: AsyncSession

    async def _size_lines(
        self, lines: Sequence[BundlingOrderLine]
    ) -> dict[UUID, CuttingOrderSizeLine]:
        """批量取本单明细引用的裁剪尺码明细行，并校验**存在 + 尺码匹配**（§4 ⑦）。

        ⚠️ 批量 IN（500 行明细 = 1 次往返）而不是逐行 ``session.get``。
        """
        session = self.session
        size_lines = await get_cutting_size_lines_by_ids(
            session, [line.cutting_size_line_id for line in lines]
        )
        for line in lines:
            source = size_lines.get(line.cutting_size_line_id)
            if source is None:
                raise BusinessError(
                    ErrorCode.BASE_DATA_NOT_FOUND,
                    f"第 {line.line_no} 行：裁剪尺码明细行不存在或已删除",
                    details={"line_no": line.line_no},
                )
            if source.size_code != line.size_code:
                raise BusinessError(
                    ErrorCode.PARAM_INVALID,
                    f"第 {line.line_no} 行：尺码 {line.size_code} 与裁剪明细行的 "
                    f"{source.size_code} 不匹配",
                    details={"line_no": line.line_no},
                )
        return size_lines

    async def _assert_hands_match(
        self, lines: Sequence[BundlingOrderLine], size_lines: Mapping[UUID, CuttingOrderSizeLine]
    ) -> None:
        """§4 ⑤ / B21：**码数 = 手数**预检，不一致 → ``31004``。

        口径：**按 (色码, 尺码) 分组**，本单该组的 ``Σ hands`` 必须等于它**所引用的**
        裁剪尺码明细行的 ``Σ hands``（少打、多打都拦），``details`` 回传两侧手数。

        ⚠️ 比的是**本单引用到的那几行**，而不是裁剪单该色该尺码的全部行：引用了哪几行
        就该打满哪几行，而同一尺码可以来自裁剪的多个布批（ADR-0017 §4）。
        """
        expected: dict[OutputKey, int] = {}
        actual: dict[OutputKey, int] = {}
        for line in lines:
            key = (line.color_code, line.size_code)
            expected[key] = expected.get(key, 0) + line.hands
            source = size_lines[line.cutting_size_line_id]
            actual[key] = actual.get(key, 0) + source.hands
        for key, planned in sorted(expected.items()):
            cutting_hands = actual[key]
            if planned != cutting_hands:
                raise BusinessError(
                    ErrorCode.BUNDLE_HANDS_MISMATCH,
                    f"{key[0]}/{key[1]}：打菲 {planned} 手与裁剪的 {cutting_hands} 手不一致"
                    "（少打或多打都不行）",
                    details={
                        "color_code": key[0],
                        "size_code": key[1],
                        "planned_hands": planned,
                        "cutting_hands": cutting_hands,
                    },
                )

    async def _assert_no_hand_conflict(
        self,
        order: BundlingOrder,
        lines: Sequence[BundlingOrderLine],
        size_lines: Mapping[UUID, CuttingOrderSizeLine],
    ) -> None:
        """§4 ⑥：展开后的手序号与库内 ACTIVE 码冲突 → ``31005``（B22）。

        ⚠️ 委托 :func:`preview_order` 而不自己再展开一遍：预演（``POST /split``）与
        提交共用同一份算法，冲突清单因此不会出现「预演说行、提交说不行」。
        """
        existing = await list_active_bundle_hands(self.session, order.id)
        preview = preview_order(
            doc_no=order.doc_no,
            lines=[
                SplitLineInput(
                    color_code=line.color_code,
                    size_code=line.size_code,
                    hands=line.hands,
                    qty_per_hand=size_lines[line.cutting_size_line_id].qty_per_hand,
                    cutting_size_line_id=line.cutting_size_line_id,
                )
                for line in lines
            ],
            existing_hands={(h.color_code, h.size_code, h.hands): h.bundle_no for h in existing},
        )
        if not preview.conflicts:
            return
        first = preview.conflicts[0]
        raise BusinessError(
            ErrorCode.BUNDLE_HAND_DUPLICATED,
            f"{first.color_code}/{first.size_code} 第 {first.hands} 手已存在打菲码 "
            f"{first.existing_bundle_no}，请核对后重试",
            details={
                "code": HAND_DUPLICATED_CODE,
                "color_code": first.color_code,
                "size_code": first.size_code,
                "hands": first.hands,
                "existing_bundle_no": first.existing_bundle_no,
                "conflict_count": len(preview.conflicts),
            },
        )

    async def _reserve(
        self,
        lines: Sequence[BundlingOrderLine],
        size_lines: Mapping[UUID, CuttingOrderSizeLine],
        outputs: Mapping[OutputKey, CuttingOutput],
        operator_id: UUID,
    ) -> list[OutputPlan]:
        """按 (色码, 尺码) **条件 UPDATE** 预占，逐键不超发（INV-6 / B7）。

        ⚠️ 两层是同一条规则，缺一不可：**先查可用量**只为给出可读文案，
        **条件 UPDATE** 才是权威 —— 查完到写之间另一个事务能插进来，两张打菲单会同时
        看到同一份余量并双双预占成功（超发的经典形态）。
        """
        session = self.session
        plans: list[OutputPlan] = []
        for key, qty in sorted(planned_quantities(lines, size_lines).items()):
            output = outputs.get(key)
            available = available_qty_of(output) if output is not None else ZERO
            if output is None or available < qty:
                raise BusinessError(
                    ErrorCode.CUTTING_QTY_CONFLICT,
                    f"{key[0]}/{key[1]} 可打菲余量不足：需要 {qty}，当前 "
                    f"{available if output is not None else '无裁剪结转行'}",
                    details={
                        "color_code": key[0],
                        "size_code": key[1],
                        "required": str(qty),
                        "available": str(available) if output is not None else None,
                    },
                )
            if not await reserve_output_qty(
                session, output_id=output.id, qty=qty, operator_id=operator_id
            ):
                raise BusinessError(
                    ErrorCode.CUTTING_QTY_CONFLICT,
                    f"{key[0]}/{key[1]} 可打菲余量刚被其他打菲单占用（需要 {qty}），请重新核对",
                    details={"color_code": key[0], "size_code": key[1], "required": str(qty)},
                )
            plans.append(
                OutputPlan(
                    color_code=key[0],
                    size_code=key[1],
                    output_id=output.id,
                    qty=qty,
                    available_before=available,
                )
            )
        return plans

    async def _release(
        self,
        lines: Sequence[BundlingOrderLine],
        size_lines: Mapping[UUID, CuttingOrderSizeLine],
        outputs: Mapping[OutputKey, CuttingOutput],
        operator_id: UUID,
    ) -> None:
        """释放本单的预占 —— 与 :meth:`_reserve` **严格成对**（`reject` / `withdraw`）。

        ⚠️ 释放失败（``reserved_qty`` 不足 / 结转行不见了）**抛错而不静默跳过**：
        无脑减会把别人的预占减成负数（凭空放大可打菲余量，INV-6）；静默跳过则让这张
        单永久占着余量，症状是「明明驳回了却还打不出菲」。
        """
        session = self.session
        for key, qty in sorted(planned_quantities(lines, size_lines).items()):
            output = outputs.get(key)
            if output is None or not await release_output_qty(
                session, output_id=output.id, qty=qty, operator_id=operator_id
            ):
                raise BusinessError(
                    ErrorCode.CUTTING_QTY_CONFLICT,
                    f"{key[0]}/{key[1]} 的预占已不在裁剪结转里（应释放 {qty}），"
                    "数据不一致，请联系管理员核查",
                    details={"color_code": key[0], "size_code": key[1], "required": str(qty)},
                )

    @staticmethod
    def _assert_creator(order: BundlingOrder, ctx: AuthContext) -> None:
        """08 §1.1：撤回人 = 制单人。不符 → ``12001``。

        ⚠️ 判 ``ctx.user_id``（**认证身份**）而不是 ``operator_id``：后者由调用方
        传入，与身份不一致说明调用方自己错了，用它判会把「传错身份」变成静默放过。
        """
        if ctx.user_id == order.created_by:
            return
        raise BusinessError(
            ErrorCode.PERMISSION_DENIED,
            "只有制单人本人可以撤回本单",
            details={"doc_no": order.doc_no},
        )


__all__ = [
    "OutputPlan",
    "StateGuardMixin",
    "output_keys",
    "planned_quantities",
    "require_reason",
]
