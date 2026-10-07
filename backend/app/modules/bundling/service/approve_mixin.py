"""打菲单 ``approve``（审核六步）与 ``reverse``（反审核）（T-BUND-005b）。

## 审核六步**顺序不可调换**（03 §4.1 / 08 §2.2）

```
① 按手生成 bundle_no（一码一手，手序号内嵌）—— 展开在 :mod:`.split`，**纯函数**
② 每手件数 = 裁剪尺码明细的 qty_per_hand（直接取，不 floor；Q-B15）
③ 打菲手数不得超打裁剪手数，否则 31004（少打允许，B21）
④ 手序号重复 → 31005
⑤ UNIQUE(doc_id, color_code, size_code, hands) 兜底（DB 唯一索引）
⑥ 更新裁剪结转：bundled_qty += 本单件数，reserved_qty 释放
```

①② 只在**内存里算**（:func:`~.split.preview_order`，与 ``POST /split`` 同一份算法），
③④ 是校验（见 :mod:`.approve_assert`），⑤⑥ 才是真正的写。所以「顺序」在代码里的落点是
**先算 → 再校 → 后写**：③ 必须跑在 ⑤⑥ 之前 —— 超打的单据若先把码插进去再报错，事务虽会
回滚，却白占了一遍唯一索引与行锁，而那段锁还挡着并发的裁剪反审核。

## 成对副作用（AGENTS 硬要求：漏一条就数据错）

``approve``：``bundles`` 0 → Σ明细 hands（一码一手，批量 INSERT）；结转 ``bundled_qty``
并释放预占。``reverse``：``bundles`` 行保留、全部 ``VOIDED``（B12 不可恢复）；结转
``bundled_qty`` 减回并重新预占。

## 铁律（08 §1.2）

R1 统一入口 + ``_assert_transition``（继承自 :class:`~.state_mixin.StateMixin`）；R2 每次
迁移写 ``document_logs``；R3 ``WHERE id/version/status`` + rowcount==0 → ``10003``；R4 审核 =
生效（码与结转同一事务）；R5 反审核 = 全链路反向；R7 状态只由 ``_apply_status`` 写。
**不接受前端传入 ``hands``**：手序号一律服务端 ``generate_series`` 展开（03 §7 防线之二）。

## 幂等

``Idempotency-Key`` 的读取 / 登记在 Router 层（``app/core/idempotency.py``，05 §5），
T-BUND-006/007 接线。本 Mixin 接受 ``idempotency_key`` 并写进 ``document_logs.changed_fields``
使重复审核可追；service 层的兜底是**状态机本身** —— 第二次 approve 撞非法迁移报 ``30001``。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import DocumentStatus
from app.core.db import unit_of_work
from app.core.errors import BusinessError, ErrorCode
from app.core.permissions import AuthContext
from app.modules.bundling.approve_repository import (
    insert_bundle_series,
    sum_bundle_qty_by_key,
    void_active_bundles,
)
from app.modules.bundling.models import BundlingOrder, BundlingOrderLine
from app.modules.bundling.repository import list_active_bundle_hands
from app.modules.bundling.service.approve_assert import ApproveAssertMixin
from app.modules.bundling.service.numbering import (
    DEFAULT_ITEM_SEQ,
    HANDS_SEQ_DIGITS,
    ITEM_SEQ_DIGITS,
)
from app.modules.bundling.service.split import OrderSplitPreview, SplitLineInput, preview_order
from app.modules.bundling.service.state_guard import planned_quantities, require_reason
from app.modules.bundling.service.state_mixin import StateMixin
from app.modules.bundling.state_repository import OutputBalance, OutputKey
from app.modules.cutting.models import CuttingOrderSizeLine, CuttingOutput

logger = logging.getLogger("app.bundling")

#: 唯一索引冲突时翻译成 ``31005``（B22 / 03 §7 第五层防线）。
#:
#: ⚠️ **必须精确匹配约束名**，不能「捕获所有 IntegrityError 都当 31005」：
#: ``fk_bundles_line``（明细行不存在）也是 IntegrityError，把它报成「手序号重复」会让用户
#: 去核对根本不相关的手号，而真正的原因是数据被并发删了。
HAND_CONFLICT_CONSTRAINTS: Final[frozenset[str]] = frozenset({"uq_bundles_hand", "uq_bundles_no"})

#: PG 的完整性错误文案里约束名的位置（``... violates unique constraint "uq_xxx"``）。
_CONSTRAINT_IN_MESSAGE: Final[re.Pattern[str]] = re.compile(r'constraint "([^"]+)"')


class ApproveMixin(ApproveAssertMixin, StateMixin):
    """``approve`` / ``reverse``（由 :class:`BundlingOrderService` 组装）。"""

    session: AsyncSession

    if TYPE_CHECKING:
        # 交叉调用 CommonMixin 的私有助手（照抄 StateMixin 的写法）：运行期不 import 兄弟
        # Mixin，只在类型检查时声明形状 —— 少一层 import 就少一个环。

        async def _reloaded(self, order_id: UUID) -> BundlingOrder: ...
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

    # ================================================================== approve

    async def approve(
        self,
        order_id: UUID,
        operator_id: UUID,
        ctx: AuthContext,
        *,
        remark: str | None = None,
        idempotency_key: str | None = None,
    ) -> BundlingOrder:
        """审核（``SUBMITTED`` → ``APPROVED``）：03 §4.1 **六步** + §5.3 **四条断言**。

        :param remark: 可选审核备注（03 §6 ``approvals`` 的请求要点）。
        :param idempotency_key: ``Idempotency-Key``（05 §5）。不传也能安全重入 —— 状态机
            保证第二次 approve 报 ``30001``，不会生成第二批码。
        :raises BusinessError: ``10005`` 制单人自审；``30001`` 状态不允许；``31004`` 超打 /
            码数与手数不符；``31005`` 手序号重复或不连续；``30002`` 结转失败 / 余量为负。
        """
        async with unit_of_work(self.session):
            order, lines, outputs = await self._lock_for_transition(
                order_id, ctx, with_outputs=True
            )
            self._assert_transition("approve", order.status, DocumentStatus.APPROVED)
            self._assert_approver(order, ctx)
            from_status = order.status.value

            # ①② 展开（纯函数，与 POST /split 同一份算法）
            size_lines = await self._size_lines(lines)
            plan = await self._plan_bundles(order, lines, size_lines)
            # ③ 防超打（与 submit 同一口径，少打允许）④ 手序号冲突
            await self._assert_hands_match(lines, size_lines)
            self._assert_no_conflict(plan)
            # ⑤ 批量生成码 ⑥ 结转
            codes = await self._insert_bundle_series(order, lines, plan, operator_id)
            balances = await self._carry_over(lines, size_lines, outputs, operator_id)
            await self._assert_generated(order, plan, balances)
            totals = self._write_back_totals(order, lines, plan, operator_id)

            await self._write_log(
                order,
                operator_id,
                "APPROVE",
                from_status,
                DocumentStatus.APPROVED.value,
                remark,
                changed_fields={**totals, "codes": codes, "idempotency_key": idempotency_key},
            )
            await self._apply_status(
                order,
                to_status=DocumentStatus.APPROVED,
                operator_id=operator_id,
                extra={
                    "hands_total": plan.hands_total,
                    "output_qty": plan.planned_qty,
                    "balance_qty": plan.balance_qty,
                    "approved_by": operator_id,
                },
            )
            fresh = await self._reloaded(order_id)
        logger.info(
            "打菲单已审核",
            extra={
                "doc_no": fresh.doc_no,
                "codes": codes,
                "planned_qty": str(plan.planned_qty),
                "balance_qty": str(plan.balance_qty),
            },
        )
        return fresh

    # ================================================================== reverse

    async def reverse(
        self, order_id: UUID, reason: str, operator_id: UUID, ctx: AuthContext
    ) -> BundlingOrder:
        """反审核（``APPROVED`` → ``SUBMITTED``）：**必填原因** + 与 approve 严格对称。

        ① 本单全部 ACTIVE 码置 ``VOIDED`` + ``voided_at`` + ``void_reason``（**行保留**，
        B12 不可恢复）；② ``bundled_qty`` 减回、``reserved_qty`` 重新预占；③ 写
        ``document_logs``（``REVERSE`` + 原因）。

        :raises BusinessError: ``10002`` 未填原因；``32003`` 已有计件（P2 落地后）；
            ``30001`` 状态不允许，或本单没有任何可作废的码。
        """
        cleaned = require_reason(reason, action="反审核", field="reason")
        async with unit_of_work(self.session):
            order, _lines, outputs = await self._lock_for_transition(
                order_id, ctx, with_outputs=True
            )
            self._assert_transition("reverse", order.status, DocumentStatus.SUBMITTED)
            from_status = order.status.value

            await self._assert_not_counted(order)
            voided = await void_active_bundles(
                self.session, doc_id=order.id, reason=cleaned, operator_id=operator_id
            )
            self._assert_voided(order, voided)
            restored = await self._restore_reservation(order, outputs, operator_id)

            await self._write_log(
                order,
                operator_id,
                "REVERSE",
                from_status,
                DocumentStatus.SUBMITTED.value,
                cleaned,
                changed_fields={
                    "voided_codes": voided,
                    "restored": {f"{c}/{s}": str(q) for (c, s), q in restored.items()},
                },
            )
            await self._apply_status(
                order, to_status=DocumentStatus.SUBMITTED, operator_id=operator_id
            )
            fresh = await self._reloaded(order_id)
        logger.info(
            "打菲单已反审核",
            extra={"doc_no": fresh.doc_no, "voided_codes": voided, "reason": cleaned},
        )
        return fresh

    # ================================================================== ①② 展开

    async def _plan_bundles(
        self,
        order: BundlingOrder,
        lines: list[BundlingOrderLine],
        size_lines: dict[UUID, CuttingOrderSizeLine],
    ) -> OrderSplitPreview:
        """按手展开本单全部明细（①②）。

        ⚠️ **算法在 :mod:`.split` 里，审核不重写一遍**：预演（``POST /split``）与审核算的是
        同一件事，写两份的表现是「预演说 6 个码、审核生成 5 个」，而用户只会认为审核算错了。

        ⚠️ 每手件数取**裁剪尺码明细**的 ``qty_per_hand``（B20 / Q-B15），不是本行的
        ``planned_qty`` 反算 —— 反算等于又引入一份「除不尽」口径。``output_qty``
        **刻意不传裁剪侧的值**：那是按裁剪行自己的 ``hands`` 算的，拿它当基准会把「还没打的
        裁剪余量」误报成打菲余数；出数基准就是 ``hands × qty_per_hand``，余数恒为 0（09 §4.2）。

        ⚠️ **必须传 ``existing_hands``**，否则 :attr:`OrderSplitPreview.conflicts` 恒为空、
        ④ 的预检形同虚设（只能等 ``uq_bundles_hand`` 在插入时才报）。查库方式与 ``submit``
        的 ⑥ 完全一致（同一个 :func:`list_active_bundle_hands`），免得两处口径分叉。
        """
        existing = await list_active_bundle_hands(self.session, order.id)
        return preview_order(
            doc_no=order.doc_no,
            existing_hands={(h.color_code, h.size_code, h.hands): h.bundle_no for h in existing},
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
        )

    # ================================================================== ⑤ 批量生成

    async def _insert_bundle_series(
        self,
        order: BundlingOrder,
        lines: list[BundlingOrderLine],
        plan: OrderSplitPreview,
        operator_id: UUID,
    ) -> int:
        """⑤ 逐条明细一行一条 ``INSERT ... SELECT ... generate_series()``（03 §5.3）。

        ⚠️ **禁止逐条 INSERT / ORM 循环**：2000 手的大单逐条插就是 2000 次往返，行锁持有
        时间在 2C VPS 上会到秒级。语句数 = 明细行数，与手数无关。

        :returns: 实际插入的码数（写入 ``document_logs``）。
        :raises BusinessError: ``31005`` 命中 ``uq_bundles_hand`` / ``uq_bundles_no``。
        """
        suffix = f"-{DEFAULT_ITEM_SEQ:0{ITEM_SEQ_DIGITS}d}"
        inserted = 0
        try:
            for line, split in zip(lines, plan.lines, strict=True):
                # ⚠️ 前缀必须**逐行**拼且**不带尾部分隔符**：尺码码与手序号之间**没有分隔符**
                # （``XL01`` 而非 ``XL-01``）—— 加了分隔符码仍能过 DB CHECK、却与
                # :func:`parse_bundle_no` 的「掐掉后 2 位是手序号」口径对不上，
                # 扫出来是「尺码 XL 的第 0 手」。
                prefix = f"{order.doc_no}-{split.size_code}"
                inserted += await insert_bundle_series(
                    self.session,
                    doc_id=order.id,
                    line_id=line.id,
                    bundle_no_prefix=prefix,
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
        return inserted

    @staticmethod
    def _constraint_name(exc: IntegrityError) -> str | None:
        """从 PG 的完整性错误里取出**约束名**。

        ⚠️ **不能读 ``exc.orig.constraint_name``**：那是 asyncpg 原生异常的属性，而 SQLAlchemy
        的 asyncpg 适配器把底层异常包了一层并不透传该属性（实测恒为 ``None``）。约束名只
        出现在 PG 的英文文案里，所以从文案取 —— 这是唯一可靠的位置（实测
        ``str(exc)`` 含 ``violates unique constraint "uq_bundles_hand"``）。
        """
        found = _CONSTRAINT_IN_MESSAGE.search(str(exc))
        return found.group(1) if found else None

    @classmethod
    def _raise_hand_conflict(cls, exc: IntegrityError, doc_no: str) -> None:
        """唯一索引冲突 → ``31005``；**其它完整性错误原样抛出**（不吞、不误报）。"""
        constraint = cls._constraint_name(exc)
        if constraint in HAND_CONFLICT_CONSTRAINTS:
            raise BusinessError(
                ErrorCode.BUNDLE_HAND_DUPLICATED,
                f"打菲单 {doc_no} 有手号已被占用（同单同色同尺码同手号已存在），请核对后重试",
                details={"doc_no": doc_no, "constraint": constraint},
            ) from exc
        raise exc

    # ================================================================== ⑥ 结转

    async def _carry_over(
        self,
        lines: list[BundlingOrderLine],
        size_lines: dict[UUID, CuttingOrderSizeLine],
        outputs: Mapping[OutputKey, CuttingOutput],
        operator_id: UUID,
    ) -> dict[OutputKey, OutputBalance]:
        """⑥ 释放 ``submit`` 的预占并结转成 ``bundled_qty``（一条 UPDATE 做完两半）。

        ⚠️ 件数口径复用 :func:`~.state_guard.planned_quantities` —— 与 ``submit`` 预占
        **逐分对齐**，一分为二就是超发（INV-6）。

        :returns: 结转后的四列（断言 ④ 的数据源）。
        """
        balances: dict[OutputKey, OutputBalance] = {}
        for key, qty in sorted(planned_quantities(lines, size_lines).items()):
            balance = await self._shift(
                outputs.get(key), key, qty, operator_id, verb="结转", forward=True
            )
            balances[key] = balance
        return balances

    async def _restore_reservation(
        self,
        order: BundlingOrder,
        outputs: Mapping[OutputKey, CuttingOutput],
        operator_id: UUID,
    ) -> dict[OutputKey, Decimal]:
        """反审核把结转**减回**、并把 ``reserved_qty`` **重新预占**（与 approve 成对）。

        ⚠️ 减回的件数取**当初写下的码件数合计**（:func:`sum_bundle_qty_by_key`），**不是**重算
        ``Σ(hands × qty_per_hand)``：裁剪侧可能在审核之后改过 ``qty_per_hand``，重算的数与
        当初加上去的不是同一个，结转再也回不到原点。码是 ``VOIDED`` 但**行还在**，这个合计
        因此仍读得到 —— 这正是「反审核只置状态、不删行」（B12）派上用场的地方。
        """
        restored: dict[OutputKey, Decimal] = {}
        for key, qty in sorted((await sum_bundle_qty_by_key(self.session, order.id)).items()):
            await self._shift(outputs.get(key), key, qty, operator_id, verb="归还", forward=False)
            restored[key] = qty
        return restored


__all__ = ["HAND_CONFLICT_CONSTRAINTS", "ApproveMixin"]
