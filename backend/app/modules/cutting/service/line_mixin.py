"""三层明细批量替换与全单重算（原 service.py 266-382、587-670）。

:class:`LineMixin` 承载「三条 PUT 全量替换」与它们共用的 ``_soft_delete_subtree`` /
``_recalc_all``。交叉调用用 ``TYPE_CHECKING`` 前置声明（照抄 base
``style_child_mixin.py``），运行期不 import 兄弟 Mixin，避免环。
"""

from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import unit_of_work
from app.core.errors import BusinessError, ErrorCode
from app.core.numbering import BUSINESS_TZ
from app.core.permissions import AuthContext
from app.modules.cutting.models import (
    CuttingOrder,
    CuttingOrderLine,
    CuttingOrderLineColor,
    CuttingOrderSizeLine,
)
from app.modules.cutting.repository import get_line_color_for_update, get_lines_for_update
from app.modules.cutting.schemas import (
    OrderLineIn,
    PutColorsIn,
    PutLinesIn,
    PutSizeLinesIn,
)

from .recalc import recalc_color, recalc_line, recalc_order


class LineMixin:
    """三层明细全量替换 + 软删子树 + 全单重算。"""

    session: AsyncSession

    if TYPE_CHECKING:

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

        async def _reloaded(self, order_id: UUID) -> CuttingOrder: ...

        async def _locked_graph(self, order_id: UUID) -> list[CuttingOrderLine]: ...

        async def _insert_lines(
            self, order: CuttingOrder, lines: Sequence[OrderLineIn], operator_id: UUID
        ) -> list[tuple[CuttingOrderLine, list[CuttingOrderLineColor]]]: ...

        async def _insert_color(
            self, line: CuttingOrderLine, color_in: object, operator_id: UUID
        ) -> CuttingOrderLineColor: ...

        async def _insert_size_line(
            self,
            color: CuttingOrderLineColor,
            draft: object,
            operator_id: UUID,
            already_created: Sequence[CuttingOrderSizeLine],
        ) -> CuttingOrderSizeLine: ...

    # ---------------------------------------------------------------- 写：三层批量替换

    async def put_lines(
        self, order_id: UUID, payload: PutLinesIn, operator_id: UUID, ctx: AuthContext
    ) -> CuttingOrder:
        """布批行**全量替换**（``PUT /cutting-orders/{id}/lines``）。

        ⚠️ **全量替换 = 软删旧行 + 插新行**，不是物理删：应用账号对四张表
        **无 DELETE 权限**（``04 §6.2.1``），真删会撞 ``permission denied``。

        ⚠️ **每一行都要重算，包括没被改动的那几行**：只重算「本次碰过的行」是最
        容易犯的错 —— 别的行的汇总会停在旧值上，而单头是全表的和，于是对不上。
        """
        async with unit_of_work(self.session):
            order = await self._editable_order(order_id, payload.version, ctx)
            old_lines = await self._locked_graph(order_id)
            await self._soft_delete_subtree(old_lines, operator_id)
            # ⚠️ **软删后必须先 flush**，再插新行。
            #    同一次 flush 里 PostgreSQL 按**执行顺序**判索引 —— 而 SQLAlchemy
            #    会把 UPDATE（软删）与 INSERT（新行）按表依赖排序，可能让 INSERT
            #    先跑，于是部分唯一索引 ``WHERE deleted_at IS NULL`` 仍能看见
            #    旧行 → 撞 duplicate key（实测过一次）。
            await self.session.flush()
            await self._insert_lines(order, payload.items, operator_id)
            await self._recalc_all(order)
            await self._bump_header(order_id, payload.version, operator_id)
            # ⚠️ ★ **必须重读**（T-CUT-001c-4 的 E2E 抓到的 500）：
            #   `_bump_header` 是 Core `UPDATE`，它会把 identity map 里那一行
            #   **expire** —— 于是返回的 `order` 对象上 `updated_at` / `version`
            #   一被访问就触发惰性刷新。而 **Router 里的 Pydantic 序列化是同步上下文**
            #   （FastAPI 在 greenlet 之外跑它），惰性刷新在那里直接抛
            #   `MissingGreenlet: greenlet_spawn has not been called` → 整个接口 500。
            #
            #   为什么 service 单测全绿：那里属性访问发生在 **async** 函数里，
            #   greenlet 还在，惰性刷新**能**成功 —— 于是「重读没生效」这件事
            #   在 service 层完全测不出来，只有走 HTTP 才暴露。
            #   同一段代码在 `patch()` 里早就有了（那里返回的就是重读结果）。
            order = await self._reloaded(order_id)
        return order

    async def put_colors(
        self,
        order_id: UUID,
        line_id: UUID,
        payload: PutColorsIn,
        operator_id: UUID,
        ctx: AuthContext,
    ) -> CuttingOrder:
        """行内颜色**全量替换**（``PUT /lines/{line_id}/colors``）。"""
        async with unit_of_work(self.session):
            order = await self._editable_order(order_id, payload.version, ctx)
            line = next((row for row in order.lines if row.id == line_id), None)
            if line is None:
                raise BusinessError(
                    ErrorCode.PARAM_INVALID, f"第 {line_id} 行不属于这张裁剪单或已删除"
                )
            # ⚠️ 先锁该行：§7 的固定锁序 `doc_id` → 行 → 颜色 → 明细
            await self.session.execute(
                select(CuttingOrderLine).where(CuttingOrderLine.id == line_id).with_for_update()
            )
            for color in list(line.colors):
                await self._soft_delete_subtree([line], operator_id, only_colors={color.id})
            await self.session.flush()  # ⚠️ 同 put_lines：先落软删，再插新行
            for color_in in payload.items:
                await self._insert_color(line, color_in, operator_id)
            await self._recalc_all(order)
            await self._bump_header(order_id, payload.version, operator_id)
            # ⚠️ ★ **必须重读**（T-CUT-001c-4 的 E2E 抓到的 500）：
            #   `_bump_header` 是 Core `UPDATE`，它会把 identity map 里那一行
            #   **expire** —— 于是返回的 `order` 对象上 `updated_at` / `version`
            #   一被访问就触发惰性刷新。而 **Router 里的 Pydantic 序列化是同步上下文**
            #   （FastAPI 在 greenlet 之外跑它），惰性刷新在那里直接抛
            #   `MissingGreenlet: greenlet_spawn has not been called` → 整个接口 500。
            #
            #   为什么 service 单测全绿：那里属性访问发生在 **async** 函数里，
            #   greenlet 还在，惰性刷新**能**成功 —— 于是「重读没生效」这件事
            #   在 service 层完全测不出来，只有走 HTTP 才暴露。
            #   同一段代码在 `patch()` 里早就有了（那里返回的就是重读结果）。
            order = await self._reloaded(order_id)
        return order

    async def put_size_lines(
        self, order_id: UUID, payload: PutSizeLinesIn, operator_id: UUID, ctx: AuthContext
    ) -> CuttingOrder:
        """尺码明细**全量替换**（``PUT /size-lines``）。

        ⚠️ ``size_line_no`` **省略时由服务端分配**（``max(现存) + 1``，见
        :func:`_next_size_line_no`），因为「先 select 后插」在并发下会发两次号
        （``modules/02 §7``）。同尺码可重复，所以分配不能按 ``count``。
        """
        async with unit_of_work(self.session):
            order = await self._editable_order(order_id, payload.version, ctx)
            color = await get_line_color_for_update(self.session, payload.line_color_id)
            if color is None or color.line_id not in {row.id for row in order.lines}:
                raise BusinessError(ErrorCode.PARAM_INVALID, "该行内颜色不属于这张裁剪单或已删除")
            for row in list(color.size_lines):
                row.deleted_at = datetime.now(tz=BUSINESS_TZ)
                row.version = row.version + 1
                row.updated_by = operator_id
            await self.session.flush()
            created: list[CuttingOrderSizeLine] = []
            for draft in payload.items:
                created.append(await self._insert_size_line(color, draft, operator_id, created))
            recalc_color(color, created)
            await self._recalc_all(order)
            await self._bump_header(order_id, payload.version, operator_id)
            # ⚠️ ★ **必须重读**（T-CUT-001c-4 的 E2E 抓到的 500）：
            #   `_bump_header` 是 Core `UPDATE`，它会把 identity map 里那一行
            #   **expire** —— 于是返回的 `order` 对象上 `updated_at` / `version`
            #   一被访问就触发惰性刷新。而 **Router 里的 Pydantic 序列化是同步上下文**
            #   （FastAPI 在 greenlet 之外跑它），惰性刷新在那里直接抛
            #   `MissingGreenlet: greenlet_spawn has not been called` → 整个接口 500。
            #
            #   为什么 service 单测全绿：那里属性访问发生在 **async** 函数里，
            #   greenlet 还在，惰性刷新**能**成功 —— 于是「重读没生效」这件事
            #   在 service 层完全测不出来，只有走 HTTP 才暴露。
            #   同一段代码在 `patch()` 里早就有了（那里返回的就是重读结果）。
            order = await self._reloaded(order_id)
        return order

    async def _soft_delete_subtree(
        self,
        lines: Sequence[CuttingOrderLine],
        operator_id: UUID,
        *,
        only_colors: set[UUID] | None = None,
    ) -> None:
        """三层级联软删，**先子后父**（§7 的锁序）。

        :param only_colors: 只软删这些颜色及其明细（``PUT /colors`` 用；
            那一行本身不删）。``None`` = 整棵子树。
        """
        now = datetime.now(tz=BUSINESS_TZ)
        for line in lines:
            colors = [c for c in line.colors if only_colors is None or c.id in only_colors]
            for color in colors:
                for row in color.size_lines:
                    if row.deleted_at is None:
                        row.deleted_at = now
                        row.version = row.version + 1
                        row.updated_by = operator_id
            for color in colors:
                if color.deleted_at is None:
                    color.deleted_at = now
                    color.version = color.version + 1
                    color.updated_by = operator_id
            if only_colors is None and line.deleted_at is None:
                line.deleted_at = now
                line.version = line.version + 1
                line.updated_by = operator_id

    async def _recalc_all(self, order: CuttingOrder) -> None:
        """**全单**重算：每行 → 表头。

        ⚠️ **重新加载整棵三层图**（``get_lines_for_update``），而不是读
        ``order.lines``。踩过一次的坑：``order.lines`` 是进入本方法**之前**
        加载的图，**不包含**本次替换新建的行 —— 于是重算只看得到刚被软删的旧行
        （过滤掉之后是空的），表头全部归 0。症状是「替换成功但单头汇总变成 0」，
        而行本身在库里是对的。

        ⚠️ 重算**必须全单**：单头是所有行的和，只重算本次碰过的行会让单头与
        逐行相加对不上，而这种错在界面上完全看不出来（表头与列表是同一份错数据）。

        ⚠️ 过滤软删是关键：软删的行不参与任何汇总 —— 忘了过滤的话，一张删过行的
        单据会把已删行的耗料算进表头。
        """
        # ⚠️ **必须先 flush**：测试夹具的 session 是 `autoflush=False`
        #    （生产也是 —— 见 conftest），所以 `select()` **看不到本次事务里
        #    尚未落库的 INSERT**。踩过一次的症状是「重算全得 0」：新建的明细行
        #    还没 flush，紧接着的 SELECT 查不到它们，于是被过滤成空集。
        await self.session.flush()
        lines = await get_lines_for_update(self.session, order.id)
        for line in lines:
            live_colors = [c for c in line.colors if c.deleted_at is None]
            for color in live_colors:
                recalc_color(
                    color,
                    [r for r in color.size_lines if r.deleted_at is None],
                )
            recalc_line(line, live_colors)
        # ⚠️ **不要在这里动 ``order.version``**（T-CUT-001c-4 的 E2E 抓到）：
        #   版本号的**唯一归属**是 `_bump_header` 的条件 UPDATE
        #   （`WHERE version = :expected` + `version = version + 1`）—— 它是乐观锁的实现，
        #   也是“并发只能有一个成功”的唯一保证。
        #   在这里再 +1 会形成**一次写推两次版本**：
        #     ·我们新增的 flush 把内存里的 2 写进库，
        #     ·无条件的 UPDATE 再加 1 变 3，而下一个请求拿着旧版本来就收 `10003`。
        #   之前不觡这个问题，是因为 Core UPDATE 把行 expire 了 —— 内存里那个 +1
        #   根本没被写进库（它被当成了需要重新加载），而整体的汇总也一起丢了。
        colors_by_line = {
            line.id: [c for c in line.colors if c.deleted_at is None] for line in lines
        }
        recalc_order(order, lines, lambda line: colors_by_line.get(line.id, []))
        # ⚠️ ★ **必须在这里 flush**（T-CUT-001c-4 的 E2E 抓到的「汇总只算在内存里」）：
        #   调用方紧接着会调 `_bump_header`，而它是一条 **Core `UPDATE`** ——
        #   Core UPDATE 对 identity map 里的行是 `synchronize_session='fetch'`，
        #   会把这一行 **expire**。被 expire 的行在提交时**不会**把上面刚算出的
        #   耗料 / 出数 / 裁损 / 尾数 / 手数写回库（它已被标记成「要重新加载」），
        #   于是库里留着**旧值**，而内存对象是对的。
        #
        #   为什么一直没发现：service 单测断言的是**返回对象**（内存，正确），
        #   集成测试断言三层**行数**，HTTP 层的响应又是重读来的 —— 只有直接查库
        #   才看得见（`test_put_lines_persists_header_aggregates`）。
        await self.session.flush()
