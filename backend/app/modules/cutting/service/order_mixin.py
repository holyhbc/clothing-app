"""裁剪单表头建 / 改 / 删 / 读与列表（原 service.py 137-262、769-815）。

:class:`OrderMixin` 只承载**表头与列表**相关方法；三层明细的写操作在
:mod:`line_mixin` / :mod:`insert_mixin`。交叉调用用 ``TYPE_CHECKING`` 前置声明
（照抄 base ``style_child_mixin.py``），运行期不 import 兄弟 Mixin，避免环。
"""

from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import DocumentStatus
from app.core.db import unit_of_work
from app.core.errors import BusinessError, ErrorCode
from app.core.numbering import BUSINESS_TZ, DOC_PREFIX_CUTTING, take_doc_no
from app.core.permissions import AuthContext
from app.core.scope import assert_in_scope
from app.modules.base.models import Style
from app.modules.cutting.models import (
    CuttingOrder,
    CuttingOrderLine,
    CuttingOrderLineColor,
)
from app.modules.cutting.repository import (
    OrderListQuery,
    count_orders,
    get_order_three_levels,
    list_orders,
)
from app.modules.cutting.schemas import (
    CuttingOrderCreateIn,
    CuttingOrderPatchIn,
    OrderLineIn,
)

from .recalc import recalc_line, recalc_order


class OrderMixin:
    """表头建 / 改 / 软删 / 详情 / 列表。"""

    session: AsyncSession

    if TYPE_CHECKING:

        async def _insert_lines(
            self, order: CuttingOrder, lines: Sequence[OrderLineIn], operator_id: UUID
        ) -> list[tuple[CuttingOrderLine, list[CuttingOrderLineColor]]]: ...

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

        async def _locked_graph(self, order_id: UUID) -> list[CuttingOrderLine]: ...

        async def _soft_delete_subtree(
            self,
            lines: Sequence[CuttingOrderLine],
            operator_id: UUID,
            *,
            only_colors: set[UUID] | None = None,
        ) -> None: ...

    # ---------------------------------------------------------------- 建单

    async def create(
        self, payload: CuttingOrderCreateIn, operator_id: UUID, ctx: AuthContext | None = None
    ) -> CuttingOrder:
        """建一张**草稿态**裁剪单（含三层明细）。

        ⚠️ **取号在事务内**（C1）。放事务外的后果：号被发出后事务回滚，
        那个号就永久跳过了 —— 而 C1 要求「生成即占用、永不复用」。

        :param ctx: 传了就校验建单人落在数据范围内（车间必须在可见车间内）。
            **不传则跳过校验** —— 内部调用方（seed / 测试）没有 AuthContext，
            而 :func:`app.core.scope.assert_in_scope` 拿不到 ctx 就没法判。
            ⚠️ 这个「可跳过」是刻意的，但**Router 必须传**（INV-8）。
        """
        # ⚠️ 必须用 `unit_of_work` 而不是 `session.begin()`（docs/03 §1.1 第 5 条）：
        #    前面的 SELECT 已触发 autobegin 时 `begin()` 会抛
        #    "A transaction is already begun"（base 模块踩过，13 个用例全红）
        async with unit_of_work(self.session):
            style = await self._assert_style_usable(payload.style_id)
            # ⚠️ **先取号再插单**：C1「生成即占用」，而取号与插入必须在**同一事务**
            #    —— 放事务外的话号被发出后事务回滚，那个号就永久跳过了。
            #    ⚠️ 反过来「先校验批次再取号」是有意的：批次不可用时报错，
            #    此时号还没消耗，不留空洞
            doc_no = await take_doc_no(
                self.session, prefix=DOC_PREFIX_CUTTING, doc_date=payload.doc_date
            )
            order = CuttingOrder(
                doc_no=doc_no,
                workshop_id=payload.workshop_id,
                style_id=style.id,
                style_no=style.style_no,
                color_codes="",  # ← 汇总算完才有值（它是 Σ 各行颜色）
                doc_date=payload.doc_date,
                delivery_date=payload.delivery_date,
                ply_count=payload.ply_count,
                entry_mode_default=payload.entry_mode_default,
                status=DocumentStatus.DRAFT,
                remark_source=payload.remark_source,
                remark=payload.remark,
                created_by=operator_id,
                updated_by=operator_id,
            )
            self.session.add(order)
            await self.session.flush()

            built = await self._insert_lines(order, payload.lines, operator_id)
            # ⚠️ 汇总在**全部明细落库之后**算，且**自底向上**（颜色 → 行 → 头）：
            #    先算头等于算了三次空值，而 `recalc_order` 依赖行的 `balance_qty`
            for line, colors in built:
                recalc_line(line, colors)
            colors_by_line = {line.id: colors for line, colors in built}
            recalc_order(
                order,
                [line for line, _ in built],
                lambda line: colors_by_line[line.id],
            )
            # ⚠️ 同 _recalc_all：算完立刻落库，后面的重载才是「刷新」而不是「回滚」
            await self.session.flush()
            # ⚠️ **重载三层再返回**。create 建的三层是 `session.add` 进去的，
            #   从未挂到 `order.lines` 上；而 `lines` 是 `lazy="selectin"`，
            #   所以调用方（Router 的 `model_validate`）一访问就触发**惰性加载** ——
            #   那在 Pydantic 的**同步**上下文里会抛
            #   `MissingGreenlet: greenlet_spawn has not been called`，
            #   报错完全看不出根因是「service 返回的对象没加载完」。
            #   重载还有一个好处：响应里的数字是**库里真实落下的**那些
            fresh = await get_order_three_levels(self.session, order.id)
            if fresh is None:  # pragma: no cover —— 刚建出来，行必然还在
                raise BusinessError(ErrorCode.CUTTING_STATUS_NOT_ALLOWED, "裁剪单不存在或已删除")
        return fresh

    # ---------------------------------------------------------------- 写：表头 / 软删

    async def patch(
        self, order_id: UUID, payload: CuttingOrderPatchIn, operator_id: UUID, ctx: AuthContext
    ) -> CuttingOrder:
        """改表头（仅 ``DRAFT`` / ``REJECTED``，C14）。

        ⚠️ **只改表头**：三层明细要走三条 PUT（各自带锁、各自重算）。混进来的话
        「改个备注」也要锁住整张单的行与明细，而那会让页面上每一次自动保存都
        变成重量级操作。

        ⚠️ ``version`` 不符 → ``10003``，且**在动手之前**就拒 —— 落到最后一步才发现
        等于白算一遍汇总。
        """
        async with unit_of_work(self.session):
            await self._editable_order(order_id, payload.version, ctx)
            extra: dict[str, Any] = {}
            if payload.doc_date is not None:
                extra["doc_date"] = payload.doc_date
            if payload.delivery_date is not None:
                extra["delivery_date"] = payload.delivery_date
            if payload.ply_count is not None:
                extra["ply_count"] = payload.ply_count
            if payload.remark_source is not None:
                extra["remark_source"] = payload.remark_source
            if payload.remark is not None:
                extra["remark"] = payload.remark
            await self._bump_header(order_id, payload.version, operator_id, extra)
            # ⚠️ 条件 UPDATE 之后必须**重读**：ORM 对象的 `version` 还是旧值
            #   （UPDATE 走的是 Core 语句，不经过 identity map 的过期标记）
            order = await get_order_three_levels(self.session, order_id)
            if order is None:  # pragma: no cover —— 刚 bump 过，行必然还在
                raise BusinessError(ErrorCode.CUTTING_STATUS_NOT_ALLOWED, "裁剪单不存在或已删除")
        return order

    async def delete(
        self, order_id: UUID, version: int, operator_id: UUID, ctx: AuthContext
    ) -> None:
        """软删单据，**三层级联软删**（modules/02 §6 DELETE 行）。

        ⚠️ **级联是必须的**：三层都靠「父软删了它就该不可见」维持一致性，而
        ``apply_data_scope`` 只在**查询起点**加 ``deleted_at IS NULL`` —— 子表查询
        若不加，详情页仍会把已删单据的尺码明细显示出来。

        ⚠️ **先子后父**（§7 的锁序）：锁 ``lines`` → ``line_colors`` → ``size_lines``。
        反过来会在并发删两单时死锁。

        ⚠️ 只有 ``DRAFT`` 能删：``SUBMITTED`` 及以后是只读的（08 §1.1），
        已审核的必须走 ``reverse`` / ``cancel``（b-3）。
        """
        async with unit_of_work(self.session):
            await self._editable_order(order_id, version, ctx)
            lines = await self._locked_graph(order_id)
            await self._soft_delete_subtree(lines, operator_id)
            await self._bump_header(
                order_id, version, operator_id, {"deleted_at": datetime.now(tz=BUSINESS_TZ)}
            )

    # ---------------------------------------------------------------- 读

    async def get(self, order_id: UUID, ctx: AuthContext) -> CuttingOrder:
        """单据详情（三层结构）。

        ⚠️ ``apply_data_scope`` 在 repository 里，这里只做
        :func:`assert_in_scope` —— **详情按 ID 直查是越权的经典入口**
        （docs/07 §3.2 铁律 2）。顺序刻意是「先查存在性再判范围」：
        越权与不存在要报**不同的码**（``12002`` vs ``30001``），
        否则攻击者能靠错误码探测单据是否存在。
        """
        order = await get_order_three_levels(self.session, order_id)
        if order is None:
            raise BusinessError(ErrorCode.CUTTING_STATUS_NOT_ALLOWED, "裁剪单不存在或已删除")
        assert_in_scope(order, ctx)
        return order

    async def list_orders(
        self, query: OrderListQuery, ctx: AuthContext
    ) -> tuple[list[CuttingOrder], int]:
        """单据列表。**导出必须复用本方法**（docs/07 §3.2 铁律 3）。"""
        query.validate()
        return (
            await list_orders(self.session, ctx, query),
            await count_orders(self.session, ctx, query),
        )

    # ---------------------------------------------------------------- 私有

    async def _assert_style_usable(self, style_id: UUID) -> Style:
        """款号必须存在且启用（``08 §2.1`` 提交校验的第一条，草稿态就拦）。

        ⚠️ **在草稿态就拦**，而不是留到 ``submit``：让用户建完一整张单才发现
        款号被停用，是最难解释的一种失败 —— 而拦它的成本只是一次查询。
        """
        style = await self.session.get(Style, style_id)
        if style is None:
            raise BusinessError(ErrorCode.BASE_DATA_NOT_FOUND, "款号不存在，请先建款号档案")
        if not style.is_active:
            raise BusinessError(
                ErrorCode.BASE_DATA_REFERENCED, f"款号 {style.style_no} 已停用，不能开裁剪单"
            )
        if style.category_id is None:
            # ADR-0020 / C37：款号必须有商品分类，分类是取价的前提
            raise BusinessError(
                ErrorCode.PARAM_INVALID,
                f"款号 {style.style_no} 还没选商品分类，请先在款号档案里补上",
            )
        return style
