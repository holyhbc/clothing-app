"""打菲单**不生成码**的四个状态动作（T-BUND-005a）。

严格照 `docs/08 §1.1` 通用动作表 + `docs/08 §2.2` 打菲单，**不新增/不改名动作**。

| 动作 | 迁移 | 副作用（与状态变更**同事务**，08 R4） |
| --- | --- | --- |
| `submit` | DRAFT/REJECTED → SUBMITTED | **预占** `cutting_outputs.reserved_qty`（条件 UPDATE）+ 快照 `available_qty_before` |
| `reject` | SUBMITTED → REJECTED | **释放** `reserved_qty` + 落 `rejected_reason` |
| `withdraw` | SUBMITTED → DRAFT | **释放** `reserved_qty`（撤回人 = 制单人） |
| `cancel` | DRAFT/REJECTED → CANCELLED | **无码无库存副作用**（为什么无需释放见下） |

## 成对副作用（AGENTS 硬要求：漏一条就数据错）

```
submit   → reserved_qty += 本单计划件数（条件 UPDATE available >= qty，rowcount==0 → 30002）
reject   → reserved_qty -= 同一笔（条件 UPDATE reserved_qty >= qty，不满足 → 30002）
withdraw → reserved_qty -= 同一笔（同上）
cancel   → 无：DRAFT 从未提交；REJECTED 的预占已在 reject 时释放
approve  → 释放预占 + 结转 bundled_qty（T-BUND-005b）
```

`cancel` 的「无副作用」**不是遗漏**：它只能从 DRAFT/REJECTED 进（08 §1.1），这两个
状态下本单手里没有任何预占。反过来若开 `APPROVED → cancel`，就会漏掉「approve 结转
的 ``bundled_qty`` 要减回」那一半 —— 所以本模块**不**登记这条迁移（见 `_TRANSITIONS`）。

## 铁律（08 §1.2）

R1 统一入口 + `_assert_transition`；R2 每次迁移写 `document_logs`；
R3 `WHERE id=? AND version=? AND status=?` + `rowcount==0 → 10003`；
R4 审核 = 生效（本卡**没有审核**）；R7 状态只由 `_apply_status` 写

## 加锁顺序（modules/03 §7）

先 `cutting_outputs` → `bundling_orders`。所以 :meth:`StateMixin._lock_for_transition`
第一步**不是**锁表头，而是先**不加锁**读一次表头（只为算出要锁哪些结转行），按
(色码, 尺码) 升序锁结转行，再锁表头、锁明细；反过来会在「本单提交」与「另一张单
提交 / 裁剪反审核」并发时互等。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Final
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import DocumentStatus
from app.core.db import unit_of_work
from app.core.errors import BusinessError, ErrorCode
from app.core.permissions import AuthContext
from app.core.scope import assert_in_scope
from app.modules.base.models import Operation, Style
from app.modules.bundling.models import BundlingOrder, BundlingOrderLine
from app.modules.bundling.repository import get_lines_for_update, get_order_with_lines
from app.modules.bundling.schemas import CancelIn, RejectIn
from app.modules.bundling.service.state_guard import (
    StateGuardMixin,
    output_keys,
    require_reason,
)
from app.modules.bundling.state_repository import (
    OutputKey,
    lock_cutting_outputs,
    lock_order_for_transition,
    snapshot_available_qty_before,
)
from app.modules.cutting.models import CuttingOrder, CuttingOutput

logger = logging.getLogger("app.bundling")

#: 08 §1.1 动作表的迁移关系：**按动作**登记 ``(允许的起始状态, 目标状态)``。
#:
#: ⚠️ **必须按动作登记，不能只登记「from → 允许的 to」**：`submit` 与 `reverse` 的
#: 目标都是 ``SUBMITTED``，而 ``reverse`` 的起始是 ``APPROVED`` —— 写成集合就会把
#: ``APPROVED → submit``（03 §4.1 明列为非法迁移）判成合法。反审核上线后这条会直接
#: 变成「已审核的单又提交一次」，症状是预占加两次而界面看不出异常。
#:
#: ⚠️ ``update`` 不在这里：它不改状态，走 :meth:`CommonMixin._editable_order` 的字段
#: 白名单，不经过状态迁移入口。
#: ⚠️ **没有** ``cancel`` 的 ``APPROVED → CANCELLED``：§1.1 动作表把 ``cancel`` 起始
#: 限定为 DRAFT/REJECTED，而同节迁移图注写着「反审核也可直接作废」—— 两处矛盾，本实现
#: 按**动作表**（表 > 图），差异登记在 docs/12 §5 遗留清单。开了它就会漏掉
#: 「approve 结转的 ``bundled_qty`` 要减回」那一半副作用（08 R5）。
_TRANSITION_BY_ACTION: Final[dict[str, tuple[frozenset[DocumentStatus], DocumentStatus]]] = {
    "submit": (
        frozenset({DocumentStatus.DRAFT, DocumentStatus.REJECTED}),
        DocumentStatus.SUBMITTED,
    ),
    "approve": (frozenset({DocumentStatus.SUBMITTED}), DocumentStatus.APPROVED),
    "reject": (frozenset({DocumentStatus.SUBMITTED}), DocumentStatus.REJECTED),
    "withdraw": (frozenset({DocumentStatus.SUBMITTED}), DocumentStatus.DRAFT),
    "reverse": (frozenset({DocumentStatus.APPROVED}), DocumentStatus.SUBMITTED),
    "cancel": (
        frozenset({DocumentStatus.DRAFT, DocumentStatus.REJECTED}),
        DocumentStatus.CANCELLED,
    ),
    # ⚠️ 目标状态**也是** ``APPROVED``：增手不改状态（03 §4.1「同（不改状态）」、ADR-0033），
    #    但仍登记在这里 —— 好处是「非 APPROVED 一律 30001」由状态机统一判、报错里能列出
    #    该动作允许的起始状态，而不是在 service 里另写一遍 if。
    "hand-increment": (frozenset({DocumentStatus.APPROVED}), DocumentStatus.APPROVED),
}

#: ``_lock_for_transition`` 的返回值：已锁表头 / 已锁明细 / 已锁结转行。
type LockedScope = tuple[BundlingOrder, list[BundlingOrderLine], Mapping[OutputKey, CuttingOutput]]


class StateMixin(StateGuardMixin):
    """打菲单状态机动作（由 :class:`BundlingOrderService` 组装）。"""

    session: AsyncSession

    if TYPE_CHECKING:
        # 交叉调用 :class:`~app.modules.bundling.service.common.CommonMixin` 的私有助手
        # （照抄 cutting 的 ``order_mixin`` 写法）：运行期**不** import 兄弟 Mixin，
        # 只在类型检查时声明形状 —— 少一层 import 就少一个环。

        async def _assert_style_usable(self, style_no: str) -> Style: ...
        async def _assert_operation_usable(self, operation_no: str) -> Operation: ...
        async def _assert_cutting_approved(self, cutting_order_id: UUID) -> CuttingOrder: ...
        async def _reloaded(self, order_id: UUID) -> BundlingOrder: ...
        async def _write_log(
            self,
            order: BundlingOrder,
            operator_id: UUID,
            action: str,
            from_status: str | None,
            to_status: str,
            reason: str | None = None,
        ) -> None: ...

    # ------------------------------------------------------------------ 铁律

    def _assert_transition(
        self, action: str, from_status: DocumentStatus, to_status: DocumentStatus
    ) -> None:
        """08 R1：迁移合法性的**唯一**判定口。非法 → ``30001``。

        :param action: 动作名（键见 :data:`_TRANSITION_BY_ACTION`）。⚠️ 这个参数不是
            「为了报错好看」：``submit`` 与 ``reverse`` 的目标状态相同，只按 (from, to)
            判定会让 ``APPROVED → submit`` 混过去。
        :raises BusinessError: ``30001``；``details`` 带上**该动作**允许的起始状态，
            前端据此提示「这单现在不能做什么」。
        """
        allowed_from, target = _TRANSITION_BY_ACTION[action]
        if from_status in allowed_from and to_status is target:
            return
        raise BusinessError(
            ErrorCode.CUTTING_STATUS_NOT_ALLOWED,
            f"当前状态 {from_status.value} 不能执行 {action}"
            + (
                "（单据已作废，是终态）"
                if not allowed_from
                else f"（该动作只允许 {'/'.join(s.value for s in sorted(allowed_from, key=lambda x: x.value))} 发起）"
            ),
            details={
                "action": action,
                "from_status": from_status.value,
                "to_status": to_status.value,
                "allowed_from": sorted(item.value for item in allowed_from),
            },
        )

    async def _apply_status(
        self,
        order: BundlingOrder,
        *,
        to_status: DocumentStatus,
        operator_id: UUID,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        """08 R3 + R7：写状态的**唯一**入口（条件 UPDATE 式乐观锁）。

        ⚠️ ``WHERE id=? AND version=? AND status=?`` 三条件齐全：只按 id 更新的话，
        并发的第二个请求会在第一个提交之后**再次**写下去（症状是「提交了两次却只预占
        一次」，因为条件 UPDATE 的第二次 rowcount==0 被漏判）。
        """
        doc_no = order.doc_no  # 先取出来：Core UPDATE 会 expire 那一行
        result = await self.session.execute(
            update(BundlingOrder)
            .where(
                BundlingOrder.id == order.id,
                BundlingOrder.version == order.version,
                BundlingOrder.status == order.status,
                BundlingOrder.deleted_at.is_(None),
            )
            .values(
                status=to_status,
                version=BundlingOrder.version + 1,
                updated_by=operator_id,
                **dict(extra or {}),
            )
            .returning(BundlingOrder.id)
        )
        if len(result.all()) == 0:
            raise BusinessError(
                ErrorCode.OPTIMISTIC_LOCK_CONFLICT,
                f"打菲单 {doc_no} 已被他人改动（状态或版本已变化），请刷新后重试",
                details={"doc_no": doc_no, "to_status": to_status.value},
            )

    # ------------------------------------------------------------------ 加锁

    async def _lock_for_transition(
        self, order_id: UUID, ctx: AuthContext, *, with_outputs: bool
    ) -> LockedScope:
        """按 03 §7 的锁序取「已锁表头 + 已锁明细（+ 已锁结转行）」，并校验存在性与数据范围。

        :param with_outputs: ``True`` 时**先**锁本单涉及的 ``cutting_outputs`` 结转行
            （提交预占 / 驳回撤回释放都要动它）；``False`` 时完全不碰结转表 ——
            作废无库存副作用，不该去占别人的行锁（那会平白把并发提交者堵在门外）。
        :returns: ``(表头, 明细, 结转行)``。明细走
            :func:`~app.modules.bundling.repository.get_lines_for_update` 而不是表头的
            ``selectin`` 关系：那个关系**不过滤软删行**（过滤在 repository 的
            ``with_loader_criteria`` 上），而 ``PUT /lines`` 是全量替换语义 ——
            把软删旧行算进汇总会让预占算成两倍。
        :raises BusinessError: ``30001`` 不存在；``12002`` 越权；``10003`` 加锁期间明细
            被换过（此时按的是另一批结转行，宁可让用户刷新重来）。
        """
        view = await get_order_with_lines(self.session, order_id)
        if view is None:
            raise BusinessError(ErrorCode.CUTTING_STATUS_NOT_ALLOWED, "打菲单不存在或已删除")
        assert_in_scope(view, ctx)  # 铁律 2：越权与不存在报**不同的码**

        keys = output_keys(view.lines)
        outputs: Mapping[OutputKey, CuttingOutput] = {}
        if with_outputs:
            outputs = await lock_cutting_outputs(self.session, style_no=view.style_no, keys=keys)
        order = await lock_order_for_transition(self.session, order_id)
        if order is None:  # pragma: no cover —— 刚校验过存在
            raise BusinessError(ErrorCode.CUTTING_STATUS_NOT_ALLOWED, "打菲单不存在或已删除")
        lines = await get_lines_for_update(self.session, order_id)
        if with_outputs and output_keys(lines) != keys:
            raise BusinessError(
                ErrorCode.OPTIMISTIC_LOCK_CONFLICT,
                "打菲单明细在本次操作期间被改动，请刷新后重试",
                details={"doc_no": order.doc_no},
            )
        return order, lines, outputs

    # ------------------------------------------------------------------ 动作

    async def submit(self, order_id: UUID, operator_id: UUID, ctx: AuthContext) -> BundlingOrder:
        """提交（``DRAFT``/``REJECTED`` → ``SUBMITTED``）：§2.2 提交校验 + **预占**。

        校验顺序（modules/03 §4）：款号/工序存在可用 → 来源裁剪单 ``APPROVED`` →
        引用的裁剪尺码明细行存在且尺码匹配 → 每手件数 > 0 → **码数 = 手数**（``31004``）
        → 手序号不冲突（``31005``）→ ``Σ planned_qty ≤ 可用量``（``30002``，
        条件 UPDATE 兜底）。

        ⚠️ **本动作不生成码**：码是审核（T-BUND-005b）的事。所以「重复提交」在状态上
        就是非法的（``SUBMITTED → SUBMITTED`` 不在迁移表里），不需要额外幂等键。
        """
        async with unit_of_work(self.session):
            order, lines, outputs = await self._lock_for_transition(
                order_id, ctx, with_outputs=True
            )
            self._assert_transition("submit", order.status, DocumentStatus.SUBMITTED)
            from_status = order.status.value

            await self._assert_style_usable(order.style_no)  # ① 款号 + 工序存在可用
            await self._assert_operation_usable(order.operation_no)
            await self._assert_cutting_approved(order.source_cutting_order_id)  # ②
            size_lines = await self._size_lines(lines)  # ③⑦
            await self._assert_hands_match(lines, size_lines)  # ⑤
            await self._assert_no_hand_conflict(order, lines, size_lines)  # ⑥
            plans = await self._reserve(lines, size_lines, outputs, operator_id)  # ④
            for plan in plans:
                await snapshot_available_qty_before(
                    self.session,
                    doc_id=order.id,
                    color_code=plan.color_code,
                    size_code=plan.size_code,
                    available_qty=plan.available_before,
                    operator_id=operator_id,
                )

            # ⚠️ 日志**先写、状态后迁**：Core UPDATE 会 expire 表头那一行，之后再读
            # order.doc_no 就是同步 IO → async 下 MissingGreenlet。两者同事务。
            await self._write_log(
                order, operator_id, "SUBMIT", from_status, DocumentStatus.SUBMITTED.value
            )
            await self._apply_status(
                order, to_status=DocumentStatus.SUBMITTED, operator_id=operator_id
            )
            fresh = await self._reloaded(order_id)
        logger.info(
            "打菲单已提交",
            extra={"doc_no": fresh.doc_no, "reserved": str(sum(p.qty for p in plans))},
        )
        return fresh

    async def reject(
        self, order_id: UUID, payload: RejectIn, operator_id: UUID, ctx: AuthContext
    ) -> BundlingOrder:
        """驳回（``SUBMITTED`` → ``REJECTED``）：**必填原因** + **释放预占**。

        ⚠️ 释放与状态变更**同事务**（08 R4/R5 的反向要求）：只改状态不释放，这张单会
        永久占着裁剪余量，而界面上它已经是「被打回的草稿」，谁都看不出余量去哪了。
        """
        reason = require_reason(payload.reason, action="驳回", field="reason")
        async with unit_of_work(self.session):
            order, lines, outputs = await self._lock_for_transition(
                order_id, ctx, with_outputs=True
            )
            self._assert_transition("reject", order.status, DocumentStatus.REJECTED)
            from_status = order.status.value
            await self._release(lines, await self._size_lines(lines), outputs, operator_id)
            await self._write_log(
                order, operator_id, "REJECT", from_status, DocumentStatus.REJECTED.value, reason
            )
            await self._apply_status(
                order,
                to_status=DocumentStatus.REJECTED,
                operator_id=operator_id,
                extra={"rejected_reason": reason},
            )
            fresh = await self._reloaded(order_id)
        return fresh

    async def withdraw(self, order_id: UUID, operator_id: UUID, ctx: AuthContext) -> BundlingOrder:
        """撤回（``SUBMITTED`` → ``DRAFT``）：**撤回人 = 制单人** + **释放预占**。

        ⚠️ 权限点复用 ``bundling:update``（08 §1.1 原表如此规定），不另设
        ``bundling:withdraw`` 校验 —— 路由层按 ``bundling:update`` 声明即可。
        """
        async with unit_of_work(self.session):
            order, lines, outputs = await self._lock_for_transition(
                order_id, ctx, with_outputs=True
            )
            self._assert_transition("withdraw", order.status, DocumentStatus.DRAFT)
            self._assert_creator(order, ctx)
            from_status = order.status.value
            await self._release(lines, await self._size_lines(lines), outputs, operator_id)
            await self._write_log(
                order, operator_id, "WITHDRAW", from_status, DocumentStatus.DRAFT.value
            )
            await self._apply_status(order, to_status=DocumentStatus.DRAFT, operator_id=operator_id)
            fresh = await self._reloaded(order_id)
        return fresh

    async def cancel(
        self, order_id: UUID, payload: CancelIn, operator_id: UUID, ctx: AuthContext
    ) -> BundlingOrder:
        """作废（``DRAFT``/``REJECTED`` → ``CANCELLED``）：必填原因，**无库存副作用**。

        ⚠️ 刻意**不锁** ``cutting_outputs``：走到这一步时本单手里没有预占（DRAFT 从未
        提交；REJECTED 已在 reject 时释放），去锁结转行只会平白堵住并发提交者。
        """
        reason = require_reason(payload.cancelled_reason, action="作废", field="cancelled_reason")
        async with unit_of_work(self.session):
            order, _lines, _outputs = await self._lock_for_transition(
                order_id, ctx, with_outputs=False
            )
            self._assert_transition("cancel", order.status, DocumentStatus.CANCELLED)
            from_status = order.status.value
            await self._write_log(
                order, operator_id, "CANCEL", from_status, DocumentStatus.CANCELLED.value, reason
            )
            await self._apply_status(
                order,
                to_status=DocumentStatus.CANCELLED,
                operator_id=operator_id,
                extra={"cancelled_reason": reason},
            )
            fresh = await self._reloaded(order_id)
        return fresh


__all__ = ["StateMixin"]
