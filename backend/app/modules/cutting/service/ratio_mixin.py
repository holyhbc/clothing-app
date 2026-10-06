"""比例带出与录入模式切换（原 service.py 386-480、719-765）。

:class:`RatioMixin` 承载 ``suggest_lines``（比例带出 + 快照）与
``switch_entry_mode``，以及它们的只读助手。交叉调用用 ``TYPE_CHECKING``
前置声明（照抄 base ``style_child_mixin.py``），运行期不 import 兄弟 Mixin。
"""

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import unit_of_work
from app.core.errors import BusinessError, ErrorCode
from app.core.numbering import BUSINESS_TZ
from app.core.permissions import AuthContext
from app.modules.base.models import StyleColorSizeRatio, StyleSize
from app.modules.cutting.models import (
    CuttingEntryMode,
    CuttingOrder,
    CuttingOrderLineColor,
)
from app.modules.cutting.repository import get_line_color_for_update
from app.modules.cutting.schemas import (
    EntryModeSwitchIn,
    SuggestLinesOut,
    SuggestSizeLineOut,
)

from .common import ZERO, _current_version, _trim_zeros
from .recalc import recalc_color


class RatioMixin:
    """比例带出、快照写入与颜色级录入模式切换。"""

    session: AsyncSession

    if TYPE_CHECKING:

        async def _readable_order(self, order_id: UUID, ctx: AuthContext) -> CuttingOrder: ...

        async def _editable_order(
            self, order_id: UUID, version: int, ctx: AuthContext
        ) -> CuttingOrder: ...

        async def _bump_header(
            self,
            order_id: UUID,
            expected_version: int,
            operator_id: UUID,
            extra: dict[str, Any] | None = None,
        ) -> None: ...

        async def _recalc_all(self, order: CuttingOrder) -> None: ...

    async def suggest_lines(
        self,
        order_id: UUID,
        style_no: str,
        color_code: str,
        operator_id: UUID,
        ctx: AuthContext,
    ) -> SuggestLinesOut:
        """按比例带出手数建议，并**在同一事务内**写下 ``ratio_snapshot``。

        ⚠️ **快照必须同一事务写入**（``modules/02 §7``）：比例主数据随时可能被改，
        隔一个请求再快照，拍下来的就可能是**另一份**比例 —— 而快照的全部意义就是
        「事后能回答当时为什么这么裁」（C29）。

        ⚠️ **绝不写比例主数据**（C29 铁律）：本方法只**读** ``style_color_size_ratios``。

        C19 三条口径：
        - 该 ``(style_no, color_code)`` **完全没有**比例行 → ``20006``（连建议都没有，属漏配）
        - **部分**尺码缺配 → **不拦**，返回 ``missing_size_codes`` 仅提示
        - 比例里出现款号颜色组内**没有**的尺码 → ``20007``（主数据脏数据）
        """
        async with unit_of_work(self.session):
            order = await self._readable_order(order_id, ctx)
            ratios = await self._load_ratios(style_no, color_code)
            if not ratios:
                raise BusinessError(
                    ErrorCode.SIZE_RATIO_INCOMPLETE,
                    f"款号 {style_no} 的颜色 {color_code} 完全没有配尺码比例，请先到基础资料里补上",
                    details={"style_no": style_no, "color_code": color_code},
                )
            defined_sizes = await self._style_size_codes(style_no)
            known = set(defined_sizes)
            unknown = sorted({row.size_code for row in ratios} - known)
            if unknown:
                raise BusinessError(
                    ErrorCode.SIZE_RATIO_SIZE_MISMATCH,
                    f"比例表里有款号 {style_no} 未定义的尺码：{'、'.join(unknown)}，请先修主数据",
                    details={"style_no": style_no, "unknown_size_codes": unknown},
                )
            snapshot = {row.size_code: str(_trim_zeros(row.ratio)) for row in ratios}
            await self._write_ratio_snapshot(order, style_no, color_code, snapshot, operator_id)
            await self._bump_header(order_id, _current_version(order), operator_id)
            return SuggestLinesOut(
                items=[
                    SuggestSizeLineOut(size_code=row.size_code, ratio=str(_trim_zeros(row.ratio)))
                    for row in sorted(ratios, key=lambda r: r.size_code)
                ],
                hands_total=str(_trim_zeros(sum((row.ratio for row in ratios), start=ZERO))),
                missing_size_codes=sorted(known - {row.size_code for row in ratios}),
                ratio_snapshot=snapshot,
            )

    async def switch_entry_mode(
        self, order_id: UUID, payload: EntryModeSwitchIn, operator_id: UUID, ctx: AuthContext
    ) -> CuttingOrderLineColor:
        """切换**颜色级**录入模式（C26 / C27）。

        ⚠️ 从 ``MASTER`` 切走会**清掉该颜色现有的尺码明细 ``hands``** —— 所以
        ``confirm`` 必须为 ``True``，服务端不接受 ``False``。理由：那个「用户可能
        没看见弹窗」的场景，代价是用户精心填的 N 行手数被静默清空。
        """
        async with unit_of_work(self.session):
            order = await self._editable_order(order_id, payload.version, ctx)
            color = await get_line_color_for_update(self.session, payload.line_color_id)
            if color is None or color.line_id not in {row.id for row in order.lines}:
                raise BusinessError(ErrorCode.PARAM_INVALID, "该行内颜色不存在或已删除")
            if color.entry_mode == payload.mode:
                raise BusinessError(
                    ErrorCode.PARAM_INVALID, f"该颜色已经是 {payload.mode.value} 模式，无需切换"
                )
            if color.entry_mode is CuttingEntryMode.MASTER and not payload.confirm:
                raise BusinessError(
                    ErrorCode.PARAM_INVALID,
                    "从 MASTER 切走会清空该颜色现有的尺码明细手数，必须二次确认（confirm=true）",
                    details={"line_color_id": str(color.id)},
                )
            if color.entry_mode is CuttingEntryMode.MASTER:
                # ⚠️ 清 `hands` 而不是删行：行还在（用户可能切回来），
                #    但没有手数就不能参与汇总 —— `hands` 是 CHECK > 0 的 NOT NULL，
                #    所以「清空」只能靠 `qty_per_hand`/`hands` 归零做不到，
                #    **只能删行**。这就是为什么这里删明细行而不是改值。
                for row in list(color.size_lines):
                    row.deleted_at = datetime.now(tz=BUSINESS_TZ)
                    row.version = row.version + 1
                    row.updated_by = operator_id
                color.ratio_snapshot = None
            color.entry_mode = payload.mode
            color.entry_mode_changed_at = datetime.now(tz=BUSINESS_TZ)
            color.entry_mode_changed_by = operator_id
            color.version = color.version + 1
            color.updated_by = operator_id
            recalc_color(color, [r for r in color.size_lines if r.deleted_at is None])
            await self._recalc_all(order)
            await self._bump_header(order_id, payload.version, operator_id)
        return color

    async def _load_ratios(self, style_no: str, color_code: str) -> list[StyleColorSizeRatio]:
        """读该 ``(款号, 颜色)`` 的比例（**只读**，C29 铁律）。"""
        stmt = (
            select(StyleColorSizeRatio)
            .where(
                StyleColorSizeRatio.style_no == style_no,
                StyleColorSizeRatio.color_code == color_code,
                StyleColorSizeRatio.deleted_at.is_(None),
            )
            .order_by(StyleColorSizeRatio.size_code)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def _style_size_codes(self, style_no: str) -> list[str]:
        """该款已定义的尺码码集合（``style_sizes``）。

        ⚠️ 用来判 C19③：比例里出现**这里没有**的尺码就是主数据脏数据（``20007``）。
        只在比例里存在、款号上没定义的尺码，带出来会让人录进一个不存在的尺码。
        """
        stmt = select(StyleSize.size_code).where(
            StyleSize.style_no == style_no, StyleSize.deleted_at.is_(None)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def _write_ratio_snapshot(
        self,
        order: CuttingOrder,
        style_no: str,
        color_code: str,
        snapshot: dict[str, str],
        operator_id: UUID,
    ) -> None:
        """把比例快照写到**该单每一个含此颜色的行内颜色**上。

        ⚠️ 遍历全部行而不是「当前操作的那一行」：``suggest-lines`` 的入参是
        ``style_no + color_code``（``modules/02 §6``），**不带 line_id** ——
        因为一次带出往往要写进好几行（一匹布上两个颜色都要用比例）。
        """
        for line in order.lines:
            if line.deleted_at is not None:
                continue
            for color in line.colors:
                if color.deleted_at is not None or color.color_code != color_code:
                    continue
                color.ratio_snapshot = dict(snapshot)
                color.version = color.version + 1
                color.updated_by = operator_id
