"""打菲**码**的三个公开动作：列表、详情、单码作废（T-BUND-007b / modules/03 §6）。

## 单码作废 ≠ 整单反审核（本文件最重要的一条）

| | 单码作废（本文件 ``void_code``） | 整单反审核（``reverse``，T-BUND-005b） |
| --- | --- | --- |
| 作用对象 | **一个码** | 本单全部 ACTIVE 码 |
| ``cutting_outputs`` 结转 | **完全不动** | ``bundled_qty`` 减回 + 重新预占 |
| 单据状态 | 不变（``from_status == to_status``） | ``APPROVED`` → ``SUBMITTED`` |
| 权限点 | ``bundling:code:void`` | ``bundling:reverse`` |

混淆这两件事的后果很具体：作废一个码却去减结转，10 张单各作废 1 手之后，
``bundled_qty`` 会比实际 ACTIVE 码少 10 手，而**没有任何表能验出这个差**
（结转只对「整单审核/反审核」留痕，单码作废不留）。所以本文件**一行结转都不碰**。

## 三条顺序（每一条都有代价）

1. **先取码（不加锁）→ 锁单据 → 锁码 → 判 → 写**：锁序是 ``bundling_orders → bundles``
   （03 §7 的统一加锁顺序）。反过来先锁码再锁单据，会与并发的 ``approve`` / ``reverse``
   互等 —— 那两个动作是「锁单据 → 锁码」，顺序相反即死锁。
2. **「不存在」与「越权」报不同的码**：先查存在性（``31001``）再对父单判范围（``12002``）。
   反过来车间主管拿别人的码只会看到「查无此码」，会去核对码有没有输错。
3. **已计件 → ``32003``**，且判定复用 :meth:`_assert_hands_not_counted`（P2 建表后只改一处）。

## 列表 / 详情的数据范围

``bundles`` 没有 ``workshop_id``：列表与统计靠
:func:`~app.core.scope.apply_data_scope` 的 ``via``（经 ``bundling_orders`` 回查），
详情 / 作废靠「对父单 ``assert_in_scope``」（07 §3.2 铁律 2）。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import DocumentAction
from app.core.db import unit_of_work
from app.core.errors import BusinessError, ErrorCode
from app.core.numbering import BUSINESS_TZ
from app.core.permissions import AuthContext
from app.core.scope import assert_in_scope
from app.modules.bundling.code_repository import (
    BundleListQuery,
    BundleRow,
    count_bundles,
    get_bundle_by_no,
    list_bundles,
    lock_bundle_for_void,
    mark_bundle_voided,
)
from app.modules.bundling.label_repository import hand_total_by_size, list_print_records
from app.modules.bundling.models import BundleStatus, BundlingOrder
from app.modules.bundling.repository import get_order_for_update, get_order_with_lines
from app.modules.bundling.service.approve_assert import CountedHand
from app.modules.bundling.service.state_guard import require_reason

logger = logging.getLogger("app.bundling")


@dataclass(frozen=True, slots=True)
class VoidCodeResult:
    """一次单码作废的结果（03 §6 的响应要点）。

    ⚠️ **不含**结转数字：单码作废不动 ``cutting_outputs``，给出任何结转相关的数都会让前端
    以为结转变了（见模块 docstring 的那张对照表）。
    """

    bundle_no: str
    doc_no: str
    status: str
    voided_at: datetime
    void_reason: str
    idempotency_key: str | None = None


class CodeMixin:
    """码查询与单码作废（由 :class:`~.bundling_order_service.BundlingOrderService` 组装）。"""

    session: AsyncSession

    if TYPE_CHECKING:
        # 照抄 LabelMixin 的写法：运行期不 import 兄弟 Mixin，只在类型检查时声明形状。
        async def _write_log(
            self,
            order: BundlingOrder,
            operator_id: UUID,
            action: str,
            from_status: str | None,
            to_status: str,
            reason: str | None = None,
            changed_fields: Mapping[str, object] | None = None,
        ) -> None: ...

        async def _assert_hands_not_counted(
            self, hands: Sequence[CountedHand], *, scope: str
        ) -> None: ...

    # ================================================================== 只读

    async def list_bundles(
        self, query: BundleListQuery, ctx: AuthContext
    ) -> tuple[list[BundleRow], int]:
        """码列表（``GET /bundles``）。

        :returns: ``(行, 总数)``。每行已补上**共 M 手**（``hands_total_of_size``）：
            列表页要直接显示「第 N 手 / 共 M 手」（03 §11.5.2），补齐是**一条**带行值
            ``IN`` 的 SQL，而不是每行查一次。
        :raises BusinessError: ``10001`` 分页非法；``12002`` 由 ``apply_data_scope`` 的
            ``via`` 过滤体现（越权的码压根不进结果集）。
        """
        query.validate()
        rows = await list_bundles(self.session, ctx, query)
        total = await count_bundles(self.session, ctx, query)
        return await self._with_hand_totals(rows), total

    async def get_bundle(self, bundle_no: str, ctx: AuthContext) -> BundleRow:
        """单码详情（``GET /bundles/{bundle_no}``，modules/03 §6 + ADR-0016 §6）。

        :raises BusinessError: ``31001`` 码不存在（格式对但查无此码 / 别的厂的码）；
            ``12002`` 不在数据范围内（**先查存在性再判范围**，两个码不能混）。
        """
        row = await get_bundle_by_no(self.session, bundle_no)
        if row is None:
            raise BusinessError(
                ErrorCode.BUNDLE_NO_NOT_FOUND,
                f"打菲号 {bundle_no} 不存在，请核对是否输错或来自别的系统",
                details={"bundle_no": bundle_no},
            )
        order = await self._parent_order(row.doc_id, bundle_no)
        assert_in_scope(order, ctx)
        totals = await hand_total_by_size(
            self.session, [(row.doc_id, row.color_code, row.size_code)]
        )
        records = tuple(await list_print_records(self.session, bundle_no))
        return row._replace(
            hands_total_of_size=totals.get((row.doc_id, row.color_code, row.size_code), row.hands),
            print_records=records,
        )

    # ================================================================== 写：单码作废

    async def void_code(
        self,
        bundle_no: str,
        reason: str,
        operator_id: UUID,
        ctx: AuthContext,
        *,
        idempotency_key: str | None = None,
    ) -> VoidCodeResult:
        """作废**一个**打菲码（``POST /bundles/{bundle_no}/voids``，modules/03 §6 + 08 §2.2）。

        步骤（顺序有代价，见模块 docstring）：

        1. 取码（**不加锁**）→ 不存在报 ``31001``
        2. ``FOR UPDATE`` 锁单据 → 数据范围 ``12002``；顺带拿到 ``doc_no`` 写日志
        3. ``FOR UPDATE`` 锁码（**在单据之后**，与 approve / reverse 同序，防死锁）
        4. 已作废 → ``31002``；已计件 → ``32003``（判定共用反审核那一处）
        5. 条件 UPDATE（``version + status='ACTIVE'``）→ 落 ``VOIDED`` + ``voided_at`` +
           ``void_reason``，**行保留**（B12 不可恢复）
        6. 写 ``document_logs``（``VOID_CODE``，含原因与码号）

        :param reason: 必填原因。空白字符串由 :func:`require_reason` 报 ``10002``
            （schema 的 ``min_length=1`` 挡得住「没传」，挡不住 ``"   "``）。
        :param idempotency_key: 写进日志的 ``changed_fields``，让重复请求可追（05 §5）。
            ⚠️ **命中幂等键的短路在 Router 层**（照 ``approve`` 的写法）：命中时返回首次结果
            而不是再次执行 —— 否则第二次会撞 ``31002``，而扫码枪/连点重试不该看到错误。
        :raises BusinessError: ``31001`` / ``12002`` / ``10002`` / ``31002`` / ``32003``。
        """
        async with unit_of_work(self.session):
            # ① 不加锁地取码：只为拿到 doc_id（下一步锁单据）。加锁会与 approve 互等。
            row = await get_bundle_by_no(self.session, bundle_no)
            if row is None:
                raise BusinessError(
                    ErrorCode.BUNDLE_NO_NOT_FOUND,
                    f"打菲号 {bundle_no} 不存在，请核对是否输错或来自别的系统",
                    details={"bundle_no": bundle_no},
                )
            # ② 锁单据 + 数据范围。⚠️ 全程**不碰 cutting_outputs**（单码作废不动结转）。
            order = await get_order_for_update(self.session, row.doc_id)
            if order is None:  # pragma: no cover —— FK 保证父单在
                raise BusinessError(ErrorCode.BUNDLE_NO_NOT_FOUND, "该码所属打菲单不存在或已删除")
            assert_in_scope(order, ctx)
            # ③ 原因必填要在**动手之前**判（漏判的后果是留下一条没有原因的动作日志）
            reason = require_reason(reason, action="作废打菲码", field="void_reason")

            locked = await lock_bundle_for_void(self.session, bundle_no)
            if locked is None:  # pragma: no cover —— 并发软删，概率极低但不能崩
                raise BusinessError(ErrorCode.BUNDLE_NO_NOT_FOUND, f"打菲号 {bundle_no} 不存在")
            if locked.status == BundleStatus.VOIDED.value:
                raise BusinessError(
                    ErrorCode.BUNDLE_ALREADY_VOIDED,
                    f"打菲号 {bundle_no} 已作废，不能重复作废",
                    details={"bundle_no": bundle_no},
                )
            await self._assert_hands_not_counted(
                [CountedHand(bundle_no, locked.counted_at)],
                scope=f"打菲码 {bundle_no}",
            )
            voided_at = datetime.now(tz=BUSINESS_TZ)
            if not await mark_bundle_voided(
                self.session,
                bundle_id=locked.id,
                expected_version=locked.version,
                reason=reason,
                operator_id=operator_id,
            ):
                # 并发：另一个事务已经作废了（08 §1.2 R3 的 rowcount == 0 分支）
                raise BusinessError(
                    ErrorCode.BUNDLE_ALREADY_VOIDED,
                    f"打菲号 {bundle_no} 已被他人作废，请刷新后查看",
                    details={"bundle_no": bundle_no},
                )
            await self._write_log(
                order,
                operator_id,
                DocumentAction.VOID_CODE.value,
                order.status.value,
                order.status.value,
                reason=reason,
                changed_fields={
                    "bundle_no": bundle_no,
                    "voided_at": voided_at.isoformat(),
                    "affected_outputs": "无（单码作废不动 cutting_outputs）",
                    "idempotency_key": idempotency_key,
                },
            )
        logger.info(
            "打菲单码已作废",
            extra={
                "doc_no": order.doc_no,
                "bundle_no": bundle_no,
                "status": BundleStatus.VOIDED.value,
            },
        )
        return VoidCodeResult(
            bundle_no=bundle_no,
            doc_no=order.doc_no,
            status=BundleStatus.VOIDED.value,
            voided_at=voided_at,
            void_reason=reason,
            idempotency_key=idempotency_key,
        )

    # ================================================================== 私有助手

    async def _parent_order(self, doc_id: UUID, bundle_no: str) -> BundlingOrder:
        """取码所属单据（**详情与作废的数据范围根**）。

        ⚠️ **不带锁**：本方法只在只读路径用（``void_code`` 自己显式调
        :func:`get_order_for_update`，因为它要按「单据 → 码」的锁序走）。越权与不存在在
        调用方分岔成两个错误码，这里只负责「拿到那一张单」。
        """
        order = await get_order_with_lines(self.session, doc_id)
        if order is None:  # pragma: no cover —— FK 保证父单在
            raise BusinessError(
                ErrorCode.BUNDLE_NO_NOT_FOUND, f"打菲号 {bundle_no} 所属的打菲单不存在"
            )
        return order

    async def _with_hand_totals(self, rows: list[BundleRow]) -> list[BundleRow]:
        """给一批行补**共 M 手**（一条行值 ``IN`` 查询，不是每行一次）。"""
        if not rows:
            return rows
        totals = await hand_total_by_size(
            self.session, sorted({(r.doc_id, r.color_code, r.size_code) for r in rows})
        )
        return [
            row._replace(
                hands_total_of_size=totals.get(
                    (row.doc_id, row.color_code, row.size_code), row.hands
                )
            )
            for row in rows
        ]


__all__ = ["BundleListQuery", "CodeMixin", "VoidCodeResult"]
