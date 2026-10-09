"""打菲单**审核后增手**（T-BUND-011 / ADR-0033，端点 ``POST /{id}/hand-increments``）。

落 ADR-0033：**审核后的打菲单只可增手，减手必须走 ``reverse``**。所以本 Mixin 的入参在
结构上就只容得下「某个尺码 + 加几手」，而 ``delta_hands ≤ 0`` 在 schema 层（``10001``）
就被挡下——**减手没有第二个实现**，否则同一件事（废掉超出新 N 的那几手）会有两份代码，
迟早分叉，而 ``reverse`` 那份已经把「未打印软删重建 / 已打印置 ``VOIDED``」做对并测过。

## 增手是纯增量，但它触到三条不变式，每条都要**重新**验一遍

1. **防超打（``31004``）不自动成立** —— ``hands ≤ 裁剪可打手数`` 是在 ``approve`` 时验的，
   裁剪侧可能在这中间增了产量，也可能没增：所以这里**重跑** :meth:`_assert_hands_match`
   （与审核同一份口径、同一份函数），而不是假设它还成立。
2. **手号连续（Q-B13）** —— 新增手号接在**原最大手号**之后（:func:`max_hand_seqs`），
   **不是**「按 delta 从 1 重编」。写错会让标签上的「第 N 手」重复，而标签已在车间流通、
   员工按 ``bundle_no`` 扫码 —— 重复的 ``bundle_no`` = **重复计件**。
3. **三层防线（03 §7）** —— 乐观锁 ``version``（本方法的入参）+ ``uq_bundles_hand`` +
   ``uq_bundles_no``。本方法在**锁住表头之后**才读最大手号，20 个并发事务因此拿到 20 个
   递增的起点，而不是全部抢第 ``N+1`` 手（那会整单回滚成「19 次 409 全败」）。

## 锁序与审核一致

用的是 :meth:`StateMixin._lock_for_transition`（``cutting_outputs`` → ``bundling_orders``
→ 明细），与 ``approve`` / ``reverse`` **同一份**：锁序不一致就是与并发审核互等死锁
（03 §7）。码行**只读不锁** —— 要锁的只有「表头 → 码」这一条边，而 ``reverse`` 是
「表头 → 码 → 结转」；少一条边不会造成环。

## 成对副作用（AGENTS 硬要求：漏一条就数据错）

```
approve  → bundles 0 → Σhands（一码一手）  ；bundled_qty += ；reserved_qty 释放
reverse  → bundles 全部 VOIDED（行保留，B12）；bundled_qty -= ；reserved_qty 重新预占
增手     → bundles += delta（一码一手，手号接续）；bundled_qty += delta × qty_per_hand
           ⚠️ **不碰任何已存在的码** —— 不作废、不重印、不动任何 counted_at 非空的码，
              不重冲结转（ADR-0033 逐项风险表）。减手那一半留给 reverse。
```
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import DocumentStatus
from app.core.db import unit_of_work
from app.core.errors import BusinessError, ErrorCode
from app.core.permissions import AuthContext
from app.modules.bundling.approve_repository import insert_bundle_series, max_hand_seqs
from app.modules.bundling.models import BundlingOrder, BundlingOrderLine
from app.modules.bundling.schemas import HandIncrementIn
from app.modules.bundling.service.numbering import (
    DEFAULT_ITEM_SEQ,
    HANDS_SEQ_DIGITS,
    ITEM_SEQ_DIGITS,
)
from app.modules.bundling.service.split import SizeLineSplit, split_size_line
from app.modules.bundling.service.state_mixin import StateMixin
from app.modules.bundling.state_repository import OutputKey, add_bundled_output_qty
from app.modules.cutting.models import CuttingOrderSizeLine, CuttingOutput

logger = logging.getLogger("app.bundling")

#: :data:`~app.modules.bundling.service.state_mixin._TRANSITION_BY_ACTION` 的动作名。
#: ⚠️ 目标状态**也是** ``APPROVED``：增手不改状态（03 §4.1「同（不改状态）」），但仍走
#: 状态机入口 —— 非 ``APPROVED`` 一律 ``30001``，且报错里能列出该动作允许的起始状态。
ACTION = "hand-increment"


class HandIncrementMixin(StateMixin):
    """``hand-increments``（由 :class:`BundlingOrderService` 组装）。"""

    session: AsyncSession

    if TYPE_CHECKING:
        # 交叉调用兄弟 Mixin 的私有助手（照抄 ApproveMixin 的写法）：运行期不 import
        # 兄弟 Mixin，只在类型检查时声明形状。

        async def _reloaded(self, order_id: UUID) -> BundlingOrder: ...
        async def _bump_header(
            self,
            order_id: UUID,
            expected_version: int,
            operator_id: UUID,
            extra: dict[str, Any] | None = None,
        ) -> None: ...
        async def _write_log(
            self,
            order: BundlingOrder,
            operator_id: UUID,
            action: str,
            from_status: str | None,
            to_status: str,
            reason: str | None = None,
            changed_fields: Mapping[str, Any] | None = None,
        ) -> None: ...

        @classmethod
        def _raise_hand_conflict(cls, exc: IntegrityError, doc_no: str) -> None: ...

    async def increment_hands(
        self, order_id: UUID, payload: HandIncrementIn, operator_id: UUID, ctx: AuthContext
    ) -> BundlingOrder:
        """审核后增手（``APPROVED`` → ``APPROVED``，**不改状态**）。

        顺序（每一步的「为什么」见模块 docstring）：

        ```
        ① 锁表头 + 结转行（与 approve 同一条锁序）→ 非 APPROVED → 30001
        ② version 过期 → 10003（挡「用旧版本号覆盖别人刚做的增手」）
        ③ 定位明细行（定位不到 / 不唯一 → 10001，不猜）
        ④ 内存里把 delta 加到该行，再**重跑** ③ 防超打 → 31004；顺带验 99 手上限 → 10001
        ⑤ 手号接原最大号展开（`MAX_HAND_SEQ` 越界 → 10001，整件口径 → 31003）
        ⑥ 批量生成新增那几手的码（撞唯一索引 → 31005）
        ⑦ 结转 bundled_qty += delta × qty_per_hand（不碰 reserved_qty；越界 → 30002）
        ⑧ 回写表头汇总（只加 delta，**不重算**）+ 写 document_logs
        ```

        :raises BusinessError: ``30001`` 非 ``APPROVED``；``10003`` 版本过期；
            ``10001`` 定位不到 / 不唯一 / 超出 99 手上限；``31004`` 超打；
            ``31005`` 手号被占；``30002`` 可打菲余量不足；``12001`` / ``12002`` 权限与范围。
        """
        async with unit_of_work(self.session):
            order, lines, outputs = await self._lock_for_transition(
                order_id, ctx, with_outputs=True
            )
            self._assert_transition(ACTION, order.status, DocumentStatus.APPROVED)
            self._assert_version(order, payload.version)
            line = self._target_line(lines, payload)
            size_lines = await self._size_lines(lines)

            # ④ ⚠️ **先在内存里加 delta、再重跑不变式**：这样 31004 比的就是「增手之后」的
            #    手数（不假设「approve 时验过就一直成立」）。失败即整事务回滚，脏值不落库。
            hands_before = line.hands
            line.hands = hands_before + payload.delta_hands
            await self._assert_hands_match(lines, size_lines)

            split = await self._plan_increment(order, line, size_lines, payload.delta_hands)
            delta_qty = split.bundle_qty * payload.delta_hands
            line.planned_qty = Decimal(line.planned_qty) + delta_qty
            line.version += 1
            line.updated_by = operator_id
            # ⚠️ **必须显式 flush**：会话是 ``autoflush=False``（core/db.py），而上面四处
            #    是纯内存脏值。``_reloaded`` 那次 SELECT 不触发 flush → 响应里的
            #    ``lines[].hands`` 会是增手**之前**的旧值（症状是「表头 5 手、明细 3 手」）。
            await self.session.flush()

            # ⑥⑦ 写。⚠️ 手号读在**锁住表头之后**：并发事务因此拿到互不相同的起点。
            codes = await self._insert_increment_series(order, line, split, operator_id)
            await self._carry_increment(
                outputs.get((line.color_code, line.size_code)),
                (line.color_code, line.size_code),
                delta_qty,
                operator_id,
            )

            # ⑧ ⚠️ **只加 delta、不重算汇总**（重算要读全部明细，并发下会读到别的事务
            #    未提交的中间态 —— 与 T-BUND-007a 修的 put_lines 缺陷同类）。
            totals = {
                "hands_total": order.hands_total + payload.delta_hands,
                "output_qty": Decimal(order.output_qty) + delta_qty,
            }
            # ⚠️ 日志**先写、表头后改**：`_bump_header` 是 Core UPDATE，会 expire 表头那一行，
            #    之后再读 `order.doc_no` 就是同步 IO → async 下 MissingGreenlet。
            await self._write_log(
                order,
                operator_id,
                "HAND_INCREMENT",
                order.status.value,
                DocumentStatus.APPROVED.value,
                None,
                changed_fields={
                    "line_id": str(line.id),
                    "line_no": line.line_no,
                    "size_code": line.size_code,
                    "delta_hands": payload.delta_hands,
                    "hands_before": hands_before,
                    "hands_after": line.hands,
                    "hands_total_before": order.hands_total,
                    "hands_total_after": totals["hands_total"],
                    "bundled_delta": str(delta_qty),
                    # ⚠️ 「新增手号区间」不可省：手号是**标签上印的**，车间里的旧标签已经
                    #    贴上去了，只记「加了 2 手」的话事后没人说得清新码是哪两号。
                    "new_hands_range": (f"{split.bundles[0].hands}..{split.bundles[-1].hands}"),
                    "codes": codes,
                },
            )
            await self._bump_header(order.id, payload.version, operator_id, totals)
            fresh = await self._reloaded(order_id)
        logger.info(
            "打菲单已增手",
            extra={
                "doc_no": fresh.doc_no,
                "size_code": split.size_code,
                "delta_hands": payload.delta_hands,
                "new_hands_range": f"{split.bundles[0].hands}..{split.bundles[-1].hands}",
                "codes": codes,
                "bundled_delta": str(delta_qty),
            },
        )
        return fresh

    # ================================================================== ② ③ 前置校验

    @staticmethod
    def _assert_version(order: BundlingOrder, version: int) -> None:
        """``version`` 过期 → ``10003``（05 §4：乐观锁冲突，刷新后重试）。

        ⚠️ 与 :meth:`~app.modules.bundling.service.common.CommonMixin._editable_order` 同一
        口径（**锁住行之后**再比），而写那一步仍走 ``_bump_header`` 的条件 UPDATE ——
        「先比」是为了给出可读文案与当前版本号，不是替代。
        """
        if order.version == version:
            return
        raise BusinessError(
            ErrorCode.OPTIMISTIC_LOCK_CONFLICT,
            f"打菲单已被他人改动（当前版本 {order.version}），请刷新后重试",
            details={"expected": version, "current": order.version},
        )

    @staticmethod
    def _target_line(
        lines: Sequence[BundlingOrderLine], payload: HandIncrementIn
    ) -> BundlingOrderLine:
        """把入参定位成**一条**明细行（定位不到 / 不唯一都 ``10001``，**不猜**）。

        ⚠️ **不唯一时报错而不是「全都加」**：同尺码跨布批的多行引的是**不同的**裁剪尺码
        明细行（每手件数、余量、防超打基准都按行不同），「给尺码加 2 手」该加在哪一行是
        业务决定（Q-B19 那一类），本模块不发明这条规则。
        """
        if payload.line_id is not None:
            for line in lines:
                if line.id == payload.line_id:
                    return line
            raise BusinessError(
                ErrorCode.PARAM_INVALID,
                f"打菲单里没有明细行 {payload.line_id}（明细可能已被整单替换，请刷新后重试）",
                details={"line_id": str(payload.line_id)},
            )
        size_code = (payload.size_code or "").strip().upper()
        matched = [line for line in lines if line.size_code.strip().upper() == size_code]
        if not matched:
            raise BusinessError(
                ErrorCode.PARAM_INVALID,
                f"本单没有尺码 {size_code} 的明细行，请核对后重试",
                details={"size_code": size_code},
            )
        if len(matched) > 1:
            raise BusinessError(
                ErrorCode.PARAM_INVALID,
                f"尺码 {size_code} 在本单有 {len(matched)} 行（跨布批），"
                "请用 line_id 指定要给哪一行增手",
                details={
                    "size_code": size_code,
                    "line_nos": [line.line_no for line in matched],
                },
            )
        return matched[0]

    # ================================================================== ⑤ 展开

    async def _plan_increment(
        self,
        order: BundlingOrder,
        line: BundlingOrderLine,
        size_lines: Mapping[UUID, CuttingOrderSizeLine],
        delta_hands: int,
    ) -> SizeLineSplit:
        """新增那几手的展开（纯函数，起始手号 = **原最大手号 + 1**，Q-B13）。

        ⚠️ **手号必须接在原最大号之后**：同 (色, 码) 手号全局连续，而标签上印的是「第 N 手」
        且已在车间流通 —— 从 1 重编会让同一个「第 N 手」出现两次，员工按 ``bundle_no``
        扫码即**重复计件**。

        ⚠️ 展开走 :func:`~.split.split_size_line`（与 ``approve`` / ``POST /split`` 同一份
        算法），顺带拿到两样**不能自己重写**的校验：手序号不超 2 位可表达范围
        （``MAX_HAND_SEQ`` → ``10001``）与每手件数的整件口径（``31003``）。

        ⚠️ 读最大手号**在锁住表头之后**（本方法的调用点），且 :func:`max_hand_seqs`
        连 ``VOIDED`` 一起算（作废的码仍占着手号）。
        """
        existing = await max_hand_seqs(self.session, order.id)
        size_code = line.size_code.strip().upper()
        return split_size_line(
            doc_no=order.doc_no,
            color_code=line.color_code,
            size_code=size_code,
            hands=delta_hands,
            qty_per_hand=size_lines[line.cutting_size_line_id].qty_per_hand,
            cutting_size_line_id=line.cutting_size_line_id,
            start_hand_seq=existing.get((line.color_code, size_code), 0) + 1,
        )

    # ================================================================== ⑥ 批量生成

    async def _insert_increment_series(
        self,
        order: BundlingOrder,
        line: BundlingOrderLine,
        split: SizeLineSplit,
        operator_id: UUID,
    ) -> int:
        """⑥ 复用 005b 的 ``INSERT ... SELECT ... generate_series()`` 批量路径。

        ⚠️ **禁止逐条 INSERT / ORM 循环**（03 §5.3）：语句数 = 1，与 ``delta_hands``
        无关；逐条插在 2C VPS 上会把行锁持有时间拖到秒级。
        ⚠️ 前缀与件序号后缀**照抄审核**：同一张单的码格式必须一个模子，否则扫码枪按
        ``bundle_no`` 解析时新老两种格式并存。
        """
        suffix = f"-{DEFAULT_ITEM_SEQ:0{ITEM_SEQ_DIGITS}d}"
        try:
            return await insert_bundle_series(
                self.session,
                doc_id=order.id,
                line_id=line.id,
                bundle_no_prefix=f"{order.doc_no}-{split.size_code}",
                hands_seq_digits=HANDS_SEQ_DIGITS,
                item_suffix=suffix,
                start_hand_seq=split.start_hand_seq,
                hands=split.hands,
                style_no=order.style_no,
                color_code=split.color_code,
                size_code=split.size_code,
                operation_no=line.operation_no,
                cutting_size_line_id=split.cutting_size_line_id,
                bundle_qty=split.bundle_qty,
                operator_id=operator_id,
            )
        except IntegrityError as exc:
            self._raise_hand_conflict(exc, order.doc_no)
            raise  # pragma: no cover —— _raise_hand_conflict 必抛（否则是别的完整性错误）

    # ================================================================== ⑦ 结转

    async def _carry_increment(
        self,
        output: CuttingOutput | None,
        key: OutputKey,
        qty: Decimal,
        operator_id: UUID,
    ) -> None:
        """⑦ 结转：``bundled_qty += delta × qty_per_hand``，**不碰** ``reserved_qty``。

        ⚠️ 与 ``approve`` 的结转**不是同一件事**：审核是「预占转结转」（两半同一条
        UPDATE），而增手时那份预占早已释放 —— 照抄 ``carry_over_output_qty`` 会把**别人**
        的预占减掉（余量凭空虚增，INV-6）。

        ⚠️ **有结转行却条件 UPDATE 未命中**与「压根没有结转行」是两种故障，措辞分开
        （与 :meth:`~app.modules.bundling.service.approve_assert.ApproveAssertMixin._shift`
        同一口径）：前者是数据被改坏，后者是这张单引用了不存在的来源。
        """
        balance = (
            await add_bundled_output_qty(
                self.session, output_id=output.id, qty=qty, operator_id=operator_id
            )
            if output is not None
            else None
        )
        if balance is not None:
            return
        tail = (
            f"可打菲余量不足（需 {qty} 件，当前 {key[0]}/{key[1]} 已被其他打菲单占用或用尽）"
            if output is not None
            else f"没有裁剪结转行，无法结转 {qty} 件"
        )
        raise BusinessError(
            ErrorCode.CUTTING_QTY_CONFLICT,
            f"{key[0]}/{key[1]}{tail}",
            details={"color_code": key[0], "size_code": key[1], "required": str(qty)},
        )


__all__ = ["ACTION", "HandIncrementMixin"]
