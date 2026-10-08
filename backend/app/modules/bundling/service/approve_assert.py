"""审核 / 反审核的**断言与前置校验**（T-BUND-005b 的第 ③④ 步与 §5.3 四条断言）。

与 :mod:`.approve_mixin` 分家只有一个理由：**ADR-0030 的单文件 ≤400 行硬上限**。分家后
职责也更清：本文件回答「这份单据能不能审核 / 审核完的数据自洽吗」，:mod:`.approve_mixin`
回答「审核要写什么、怎么写」。

## 本文件**只做断言，不做写入**

⚠️ 这条边界是刻意的：断言一旦和写路径混在一个方法里，下次有人图省事把断言挪到写之前 /
写之后，两者的差别（哪些必须同事务、哪些可以事后补）就看不出来了。

## 六步里的位置

```
③ 防超打（31004）→ :meth:`ApproveAssertMixin._assert_hands_match`（继承自 StateGuardMixin，
    **与 submit 同一份口径**，不重写）
④ 手序号重复（31005）→ :meth:`ApproveAssertMixin._assert_no_conflict`（预检）+ ``uq_bundles_hand``
    （兜底，在 :mod:`.approve_mixin` 的 INSERT 那一层）
四条断言（§5.3）    → :meth:`ApproveAssertMixin._assert_generated`
制单人 ≠ 审核人      → :meth:`ApproveAssertMixin._assert_approver`
已计件 → 32003      → :meth:`ApproveAssertMixin._assert_not_counted`（P2 建表后只改这一处）
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Final
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessError, ErrorCode
from app.core.permissions import AuthContext
from app.modules.bundling.approve_repository import (
    count_doc_bundles,
    hand_spans,
    lock_active_bundle_hands,
    sum_doc_bundle_qty,
)
from app.modules.bundling.models import BundlingOrder, BundlingOrderLine
from app.modules.bundling.service.split import OrderSplitPreview
from app.modules.bundling.service.state_guard import StateGuardMixin
from app.modules.bundling.state_repository import (
    OutputBalance,
    OutputKey,
    carry_over_output_qty,
    roll_back_output_qty,
)
from app.modules.cutting.models import CuttingOutput

logger = logging.getLogger("app.bundling")

ZERO: Final[Decimal] = Decimal("0")


@dataclass(frozen=True, slots=True)
class CountedHand:
    """判「已计件」所需的**最小**信息。

    ⚠️ 刻意只带这两列：反审核判的是**一批**码（整单），单码作废判的是**一个**码，
    而这两条都必须走同一个判定（否则 P2 建表后要改两处，而漏掉一处就是「作废掉已计件
    的码」—— 数据错了且不可恢复，B12）。
    """

    bundle_no: str
    counted_at: datetime | None


class ApproveAssertMixin(StateGuardMixin):
    """审核 / 反审核的断言（由 :class:`BundlingOrderService` 组装）。"""

    session: AsyncSession

    # ================================================================== ③ 前置校验

    @staticmethod
    def _assert_approver(order: BundlingOrder, ctx: AuthContext) -> None:
        """08 §1.1 / §4：**制单人 ≠ 审核人** → ``10005``。

        ⚠️ 判 ``ctx.user_id``（**认证身份**）而不是 ``operator_id``：后者由调用方传入，
        与身份不一致说明调用方自己错了，用它判会把「传错身份」变成静默放过。

        ⚠️ **不实现 ``super_admin`` + ``force=true`` 的例外**（08 §4）：那需要一个「强制审核」
        入参，而 ``POST /approvals`` 的入参里没有它（03 §6）。凭空发明一个入参等于绕过这条
        铁律，登记在 docs/12 §5 待排期。
        """
        if ctx.user_id != order.created_by:
            return
        raise BusinessError(
            ErrorCode.SELF_APPROVAL_FORBIDDEN,
            f"制单人不能审核自己开的打菲单（{order.doc_no}），请换他人审核",
            details={"doc_no": order.doc_no, "created_by": str(order.created_by)},
        )

    @staticmethod
    def _assert_no_conflict(plan: OrderSplitPreview) -> None:
        """④ 展开结果与库内已有 ACTIVE 码手号冲突 → ``31005``（B22）。

        ⚠️ 这一层**在插入之前**：``uq_bundles_hand`` 只在真的插进去时才报，而等到那时整批
        INSERT 已经把 ``bundle_no`` 占掉了，报错回滚的代价是一次无谓的批量写。
        """
        if not plan.conflicts:
            return
        first = plan.conflicts[0]
        raise BusinessError(
            ErrorCode.BUNDLE_HAND_DUPLICATED,
            f"{first.color_code}/{first.size_code} 第 {first.hands} 手已存在打菲码 "
            f"{first.existing_bundle_no}，请核对后重试",
            details={
                "color_code": first.color_code,
                "size_code": first.size_code,
                "hands": first.hands,
                "existing_bundle_no": first.existing_bundle_no,
                "conflict_count": len(plan.conflicts),
            },
        )

    # ================================================================== 03 §5.3 四条断言
    #
    # ⚠️ 本 Mixin **只做断言**，不做任何写入 —— 「校验」与「落库」分家是刻意的：断言一旦
    # 和写路径混在一个方法里，下次有人图省事把断言挪到写之前/之后，两者的差别就看不出来了。

    async def _assert_generated(
        self,
        order: BundlingOrder,
        plan: OrderSplitPreview,
        balances: dict[OutputKey, OutputBalance],
    ) -> None:
        """审核事务内的 **4 条断言**，任一不过整事务回滚（03 §5.3 / 08 R4）。

        ```
        1. count(bundles WHERE doc_id=?) == Σ 明细 hands       ← 码数 = 手数
        2. Σ(bundles.bundle_qty) + balance_qty == 本单件数      ← 余数归位
        3. 每个 (color_code, size_code) 内 hands 恰为 1..N 无缺号
        4. 结转后 output_qty - bundled_qty - reserved_qty >= 0  ← INV-6
        ```
        """
        await self._assert_code_count(order, plan)
        await self._assert_remainder(order, plan)
        await self._assert_hand_continuity(order, plan)
        self._assert_within_available(order, plan, balances)

    async def _assert_code_count(self, order: BundlingOrder, plan: OrderSplitPreview) -> None:
        """断言 ①：码数 = 手数（少打允许，故只查「生成的 ≠ 计划的」）。"""
        actual = await count_doc_bundles(self.session, order.id)
        if actual == plan.hands_total:
            return
        raise BusinessError(
            ErrorCode.BUNDLE_HANDS_MISMATCH,
            f"打菲单 {order.doc_no} 应生成 {plan.hands_total} 个码，实际 {actual} 个",
            details={
                "doc_no": order.doc_no,
                "planned_hands": plan.hands_total,
                "actual_codes": actual,
            },
        )

    async def _assert_remainder(self, order: BundlingOrder, plan: OrderSplitPreview) -> None:
        """断言 ②：Σ码件数 + 余数 = 本单件数（**余数归 ``balance_qty``**，09 §4.2）。"""
        total = await sum_doc_bundle_qty(self.session, order.id)
        if total + plan.balance_qty == plan.planned_qty:
            return
        raise BusinessError(
            ErrorCode.BUNDLE_HANDS_MISMATCH,
            f"打菲单 {order.doc_no} 的码件数合计与本单件数不符",
            details={
                "doc_no": order.doc_no,
                "code_qty": str(total),
                "balance_qty": str(plan.balance_qty),
                "planned_qty": str(plan.planned_qty),
            },
        )

    async def _assert_hand_continuity(self, order: BundlingOrder, plan: OrderSplitPreview) -> None:
        """断言 ③：每个 (色码, 尺码) 的手号恰为 ``1..N``，无缺号、无重号（B22）。

        ⚠️ 三个数都要对：只看 ``min==1 and max==N and count==N`` 时，``1,1,3`` 能蒙混过关
        （min=1、max=3、count=3）—— 所以额外比 ``count(distinct hands)``。
        """
        expected = {(s.color_code, s.size_code): s.hands for s in plan.lines}
        for span in await hand_spans(self.session, order.id):
            key = (span.color_code, span.size_code)
            want = expected.get(key)
            if want is not None and span.min_seq == 1 and span.max_seq == want == span.distinct_seq:
                continue
            raise BusinessError(
                ErrorCode.BUNDLE_HAND_DUPLICATED,
                f"{key[0]}/{key[1]} 的手号不连续（应为 1..{want}，实际 {span.min_seq}.."
                f"{span.max_seq} 共 {span.span_count} 个，其中不同手号 {span.distinct_seq} 个）",
                details={
                    "color_code": key[0],
                    "size_code": key[1],
                    "expected_hands": want,
                    "min_seq": span.min_seq,
                    "max_seq": span.max_seq,
                    "distinct_seq": span.distinct_seq,
                    "codes": span.span_count,
                },
            )

    def _assert_within_available(
        self,
        order: BundlingOrder,
        plan: OrderSplitPreview,
        balances: dict[OutputKey, OutputBalance],
    ) -> None:
        """断言 ④：本单件数 ≤ 各 (色码, 尺码) 的可用量（INV-6 / B7）。

        ⚠️ 判的是**结转之后**的 ``output_qty - bundled_qty - reserved_qty`` —— 那才是
        「审核完还剩多少可打」的真值。用结转前的数判会漏掉「结转把余量吃掉」这一种，而它
        恰恰是超发的唯一形态。

        ⚠️ 数据源是结转那条 UPDATE 的 ``RETURNING``（:class:`OutputBalance`）而不是回读 ORM
        对象：Core UPDATE 后 identity map 里那行是旧值，回读会让这条断言恒真。
        """
        for split in plan.lines:
            key = (split.color_code, split.size_code)
            balance = balances.get(key)
            if balance is None:
                continue
            remaining = balance.output_qty - balance.bundled_qty - balance.reserved_qty
            if remaining >= ZERO:
                continue
            raise BusinessError(
                ErrorCode.CUTTING_QTY_CONFLICT,
                f"{key[0]}/{key[1]} 可打菲余量被本次审核打成负数（{remaining}）",
                details={
                    "color_code": key[0],
                    "size_code": key[1],
                    "output_qty": str(balance.output_qty),
                    "bundled_qty": str(balance.bundled_qty),
                    "reserved_qty": str(balance.reserved_qty),
                },
            )

    async def _shift(
        self,
        output: CuttingOutput | None,
        key: OutputKey,
        qty: Decimal,
        operator_id: UUID,
        *,
        verb: str,
        forward: bool,
    ) -> OutputBalance:
        """结转 / 归还的公共入口（含失败分支的两种措辞）。"""
        move = carry_over_output_qty if forward else roll_back_output_qty
        balance = (
            await move(self.session, output_id=output.id, qty=qty, operator_id=operator_id)
            if output is not None
            else None
        )
        if balance is not None:
            return balance
        # ⚠️ **有结转行却条件 UPDATE 未命中**与「压根没有结转行」是两种故障，措辞必须分开：
        # 前者是数据被改坏（预占或结转被别处动过），后者是这张单引用了不存在的来源。
        tail = (
            f"的数量已不在裁剪结转里（应{verb} {qty} 件），数据不一致，请联系管理员核查"
            if output is not None
            else f"没有裁剪结转行，无法{verb} {qty} 件"
        )
        raise BusinessError(
            ErrorCode.CUTTING_QTY_CONFLICT,
            f"{key[0]}/{key[1]}{tail}",
            details={"color_code": key[0], "size_code": key[1], "required": str(qty)},
        )

    # ================================================================== 回写汇总

    def _write_back_totals(
        self,
        order: BundlingOrder,
        lines: list[BundlingOrderLine],
        plan: OrderSplitPreview,
        operator_id: UUID,
    ) -> dict[str, object]:
        """回写明细 ``planned_qty``；表头三列由 ``_apply_status`` 的**同一条 UPDATE** 写。

        ⚠️ 表头汇总**不单独 UPDATE**：状态迁移与汇总同语句（08 R4），免得出现「状态已
        ``APPROVED`` 而 ``hands_total`` 还是草稿值」的半成品。
        ⚠️ 明细**逐行比对后才写**：建单时写下的值通常已经是对的（两者都取裁剪尺码明细的
        ``qty_per_hand``），500 行全量回写就是 500 条无谓的 UPDATE。
        """
        for line, split in zip(lines, plan.lines, strict=True):
            expected = split.bundle_qty * split.hands
            if Decimal(line.planned_qty) == expected:
                continue
            line.planned_qty = expected
            line.version += 1
            line.updated_by = operator_id
        return {
            "hands_total": plan.hands_total,
            "planned_qty": str(plan.planned_qty),
            "balance_qty": str(plan.balance_qty),
        }

    # ================================================================== 反审核校验

    async def _assert_not_counted(self, order: BundlingOrder) -> None:
        """08 §2.2 反审核第 ① 条：**已有计件流水 → 拒绝**（``32003``）。

        反审核**先锁码再判**（:func:`lock_active_bundle_hands` 带 ``FOR UPDATE``）与计件侧串行
        （03 §7）：否则会出现「判完没计件、紧接着另一个事务扫码写上 ``counted_at``、本事务把码
        作废」。判定本身委托 :meth:`_assert_hands_not_counted`（**全仓唯一**那一处）。
        """
        hands = await lock_active_bundle_hands(self.session, order.id)
        await self._assert_hands_not_counted(
            [CountedHand(hand.bundle_no, hand.counted_at) for hand in hands],
            scope=f"打菲单 {order.doc_no}",
        )

    async def _assert_hands_not_counted(self, hands: Sequence[CountedHand], *, scope: str) -> None:
        """**全仓唯一**的「这批码是不是已计件」判定点（``32003``）。

        调用方两条：反审核（整单码集，:meth:`_assert_not_counted`）与**单码作废**
        （T-BUND-007b 的 ``void_code``）。它们必须共用同一个方法 —— P2 建表后只改这里一处。

        ⚠️ **当前恒放行，且这是刻意的**：``piecework_logs`` 尚未建立（属计件模块 P2，L-096），
        所以**无流水可查**。判据本应是「有没有未红冲的计件流水」，而 ``bundles.counted_at``
        由计件模块回写、当前恒为 ``NULL``；拿它当判据等于一个「看起来在拦、实际拦不住任何东西」
        的假阳性守卫 —— 宁可明确留空并登记，也不要那样写。

        两条判据（**都在这一个方法里**，P2 落地后也只改这里）：

        1. ``bundles.counted_at`` 非空 → ``32003``（``03 §7``「作废 / 反审核 / 改手数
           都要先 ``FOR UPDATE`` 锁码再判 ``counted_at``」、``03 §9``、TC-08 / TC-10）。
           ⚠️ **这一条现在就能判、也必须判**：它是库里唯一的计件痕迹，
           而「已计件的码被作废 / 被反审核掉」之后**不可恢复**（B12）—— 工资已经按它算过。
        2. 未红冲的**计件流水** → 同样是 ``32003``，但 ``piecework_logs`` 属 P2、尚未建表
           （L-096），当前**无表可查 → 放行**。红冲后计件模块会清空 ``counted_at``，
           所以第 1 条判不出来的那部分（红冲过又重新计件的流水）只能等 P2。

        :param scope: 报错文案里的定位（「打菲单 BD-… 」或「打菲码 BD-…-XL01-0001」），
            让用户知道该去红冲哪一个。
        :raises BusinessError: ``32003``（任一判据命中）。
        """
        for hand in hands:
            if hand.counted_at is not None:
                raise BusinessError(
                    ErrorCode.PIECEWORK_SETTLED,
                    f"{scope} 里的打菲码 {hand.bundle_no} 已经计件"
                    f"（{hand.counted_at:%Y-%m-%d %H:%M}），不可作废 / 反审核；"
                    "请先在计件模块红冲并补录",
                    details={
                        "bundle_no": hand.bundle_no,
                        "counted_at": hand.counted_at.isoformat(),
                    },
                )
        # TODO(P2: piecework_logs 建表，L-096) 查这些码未红冲的计件流水，有则抛 32003

    @staticmethod
    def _assert_voided(order: BundlingOrder, voided: int) -> None:
        """反审核后码必须**全部**转 ``VOIDED``（行保留，不删）。

        ⚠️ ``voided == 0`` 说明审核时一码都没生成 —— 那种单据不该能被审核通过，走到这里说明
        有人绕过 :meth:`approve` 直接改了状态。
        """
        if voided > 0:
            return
        raise BusinessError(
            ErrorCode.CUTTING_STATUS_NOT_ALLOWED,
            f"打菲单 {order.doc_no} 没有任何可作废的打菲码，数据不一致，请联系管理员核查",
            details={"doc_no": order.doc_no, "voided_codes": voided},
        )


__all__ = ["ApproveAssertMixin", "CountedHand"]
