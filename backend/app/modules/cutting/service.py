"""裁剪单的**业务逻辑**与**事务边界**（docs/03 §1.3：service 是唯一的核心）。

## 本文件只做「草稿态」（T-CUT-001b-1）

**不做**状态机的任何动作（``submit`` / ``approve`` / …）。原因不是省事，而是
那些动作**依赖四张还不存在的表**（详见任务卡）：审核第 ① 步要写
``stock_ledger_lines``（**连字段表都没有**）、第 ④ 步要 ``bom_items``、
第 ⑦ 步要 ``cutting_outputs``、反审核要 ``bundles.counted_at``。

## 三条贯穿全文件的口径

### ① 三级汇总一律重算，**从不信任前端**（C6）

```
cutting_order_size_lines.output_qty     ← 唯一的出数权威来源
    ↓ Σ
cutting_order_line_colors.output_qty_total / balance_qty_total / hands_total
    ↓ Σ
cutting_order_lines.output_qty(用户录入的估算值) / balance_qty / fabric_qty / waste_qty
    ↓ Σ
cutting_orders.fabric_qty / output_qty / cut_waste_qty / balance_qty / hands_total
```

⚠️ **表头的五列在入参 Schema 里连字段都没有**（``CuttingOrderCreateIn``）——
传了会被 ``extra="forbid"`` 报 ``10001``，而不是「悄悄被忽略」。
**能被忽略的入参是最坏的一种**：前端以为设的值生效了。

### ② 取整口径：整数精确乘法，**不 floor**（C13 / C21 / ADR-0020）

``output_qty = hands × qty_per_hand``，两个都是 ``int``，所以**乘法本身就没有
小数**，``floor`` / ``round`` 在这里是**多余的代码**。⚠️ 一旦引入 ``floor``，
就等于允许 ``1.5 手`` 这种非法值悄悄通过（P0 的错误码 ``30006`` 就是为它准备的）。

### ③ 行余量：正向录入值 - 明细合计（C34 口径 A，业务确认 2026-10-04）

```
行 balance_qty = 行 output_qty（用户按铺布实耗正向录入）
                 - Σ(颜色 Σ尺码 output_qty)
```

⚠️ **不要**把行 ``output_qty`` 覆盖成明细合计 —— 那会让 ``balance_qty`` 恒为 0，
「行余量」这个概念整个消失。这正是本次修掉的原缺陷（``modules/02 §6`` ①
原写「服务端算」）。为负 → ``30002``。
"""

from collections.abc import Callable, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import DocumentStatus
from app.core.db import unit_of_work
from app.core.errors import BusinessError, ErrorCode
from app.core.numbering import BUSINESS_TZ, DOC_PREFIX_CUTTING, take_doc_no
from app.core.permissions import AuthContext
from app.core.scope import assert_in_scope
from app.modules.base.models import (
    MaterialStock,
    Style,
    StyleColorSizeRatio,
    StyleSize,
)
from app.modules.cutting.models import (
    CuttingEntryMode,
    CuttingOrder,
    CuttingOrderLine,
    CuttingOrderLineColor,
    CuttingOrderSizeLine,
)
from app.modules.cutting.repository import (
    OrderListQuery,
    count_orders,
    get_line_color_for_update,
    get_lines_for_update,
    get_order_three_levels,
    list_orders,
)
from app.modules.cutting.schemas import (
    CuttingOrderCreateIn,
    CuttingOrderPatchIn,
    EntryModeSwitchIn,
    OrderLineIn,
    PutColorsIn,
    PutLinesIn,
    PutSizeLinesIn,
    SuggestLinesOut,
    SuggestSizeLineOut,
)

#: 数量零值。**用常量而不是字面量 ``0``**：三级汇总里 ``0`` 出现十几次，
#: 而写成常量后「这一处的 0 是件数还是米数」一眼可辨。
ZERO = Decimal("0")

__all__ = ["CuttingOrderService", "recalc_color", "recalc_line", "recalc_order"]


def _current_version(order: CuttingOrder) -> int:
    """取单据当前版本号（读接口没有 ``version`` 入参时用它当 expected）。"""
    return int(order.version)


def _trim_zeros(value: Decimal) -> Decimal:
    """去掉小数**末尾**的零，但保留整数形态（``3.0000`` → ``3``，``1.50`` → ``1.5``）。

    ⚠️ **不用 ``Decimal.normalize()``**：它对 ``Decimal("100")`` 返回
    ``Decimal("1E+2")`` —— 建议手数 100 手显示成 ``1E+2`` 是不可接受的。
    所以显式换算到目标刻度再 ``quantize``。

    ⚠️ 为什么需要它：比例列是 ``numeric(14,4)``，所以 ``1.0 + 2.0`` 得到
    ``3.0000``。而「手数」是给人看的计数，显示成 ``3.0000`` 会让人怀疑
    「是不是录错了小数」。真实的 1.5 手必须保留（ADR-0013 允许）。
    """
    normalized = value.normalize()
    # ⚠️ `as_tuple().exponent` 的类型标注是 `int | Literal["n","N","F"]`（后三个是
    #    NaN / sNaN / Infinity 的标记），而比例列是 `numeric(14,4)` NOT NULL CHECK >= 0，
    #    **不可能**是非有限值 —— 但 mypy 不知道这件事，所以显式收窄。
    #    不用 `assert`：那是「运行到这里说明数据库坏了」的信号，而真出现时
    #    `quantize` 自己会抛 `InvalidOperation`，比断言消息更准确。
    exponent: int = normalized.as_tuple().exponent  # type: ignore[assignment]
    if exponent > 0:
        return normalized.quantize(Decimal("1"))
    return normalized


class CuttingOrderService:
    """裁剪单服务。**事务边界唯一入口**（docs/03 §1.4）。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

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
        return order

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
        return order

    # ---------------------------------------------------------------- 写：比例与模式

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

    # ---------------------------------------------------------------- 私有

    async def _bump_header(
        self,
        order_id: UUID,
        expected_version: int,
        operator_id: UUID,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """**条件 UPDATE** 式乐观锁：``WHERE version = :expected`` + ``rowcount == 0`` 判冲突。

        ⚠️⚠️ **这是本卡最关键的一处修正**。原来的写法是「先 ``SELECT`` 读出版本、
        在 Python 里比、再 ``session.commit()``」—— 那是**先读后写**，两个并发事务
        可以在对方提交前都读到 ``version = 1``、都通过检查、都写 ``version = 2``，
        **两个都成功**（后写的覆盖先写的）。

        踩过一次：并发用例断言「5 个里只应有 1 个成功」，实际是
        ``['ok', 'CONFLICT', 'CONFLICT', 'ok', 'CONFLICT']`` —— **两个 ok**。
        `docs/10 §5.4` 那句「**只靠业务层检查 = 未通过**」说的就是这个。

        ⚠️ 必须走 ``04 §2`` 的写法（条件 UPDATE + ``rowcount == 0``）而不是 ORM 属性
        赋值：``session.commit()`` 发出的 UPDATE **不带 version 谓词**，所以它在
        数据库层根本没有锁。
        """
        values: dict[str, Any] = {
            "version": CuttingOrder.version + 1,
            "updated_by": operator_id,
            **(extra or {}),
        }
        result = await self.session.execute(
            update(CuttingOrder)
            .where(CuttingOrder.id == order_id, CuttingOrder.version == expected_version)
            .values(**values)
            .returning(CuttingOrder.id)
        )
        # ⚠️ 用 `RETURNING id` 的行数判命中，而不是 `result.rowcount` ——
        #    mypy 的 `AsyncSession.execute` 返回类型不含 `rowcount`，而实际是有的；
        #    写成 `returning` 之后「有没有命中」就是结果集的长度，类型也干净
        if len(result.all()) == 0:
            current = await self.session.get(CuttingOrder, order_id)
            raise BusinessError(
                ErrorCode.OPTIMISTIC_LOCK_CONFLICT,
                f"裁剪单已被他人修改（当前版本 {current.version if current else '已删除'}）"
                f"，请刷新后重试",
                details={
                    "expected": expected_version,
                    "current": current.version if current else None,
                },
            )

    async def _editable_order(self, order_id: UUID, version: int, ctx: AuthContext) -> CuttingOrder:
        """取可编辑的单据：存在 + 在数据范围内 + 版本对 + 状态可改。

        ⚠️ 四道检查的**顺序**是刻意的，每一步都对应一种不同的用户处境，报的码
        也必须不同 —— 全报同一个码的话，用户只会看到「操作失败」而不知道该做什么：
        1. 存在性 → ``30001``（顺带覆盖「已软删」）
        2. 数据范围 → ``12002``（越权；docs/07 §3.2 铁律 2）
        3. 乐观锁 → ``10003``（他人已改，**必须在动手之前**拒）
        4. 状态 → ``30001``（C14：``SUBMITTED`` 及以后只读）

        ⚠️ 三层明细一并加载：改完之后要重算全单汇总，而重算需要**全部**行
        （只重算本次碰过的行是最容易犯的错）。
        """
        order = await get_order_three_levels(self.session, order_id)
        if order is None:
            raise BusinessError(ErrorCode.CUTTING_STATUS_NOT_ALLOWED, "裁剪单不存在或已删除")
        assert_in_scope(order, ctx)
        if order.version != version:
            raise BusinessError(
                ErrorCode.OPTIMISTIC_LOCK_CONFLICT,
                f"裁剪单已被他人修改（当前版本 {order.version}），请刷新后重试",
                details={"expected": version, "current": order.version},
            )
        if order.status not in (DocumentStatus.DRAFT, DocumentStatus.REJECTED):
            raise BusinessError(
                ErrorCode.CUTTING_STATUS_NOT_ALLOWED,
                f"当前状态 {order.status.value} 不允许修改（只有草稿与已驳回可改，08 §1.1）",
                details={"status": order.status.value},
            )
        return order

    async def _readable_order(self, order_id: UUID, ctx: AuthContext) -> CuttingOrder:
        """取可读的单据（三层），**不校验版本与状态** —— 读操作不该有乐观锁。"""
        order = await get_order_three_levels(self.session, order_id)
        if order is None:
            raise BusinessError(ErrorCode.CUTTING_STATUS_NOT_ALLOWED, "裁剪单不存在或已删除")
        assert_in_scope(order, ctx)
        return order

    async def _locked_graph(self, order_id: UUID) -> list[CuttingOrderLine]:
        """锁住三层并返回布批行（软删与替换都要用）。"""
        return await get_lines_for_update(self.session, order_id)

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
        order.version = order.version + 1
        colors_by_line = {
            line.id: [c for c in line.colors if c.deleted_at is None] for line in lines
        }
        recalc_order(order, lines, lambda line: colors_by_line.get(line.id, []))

    async def _insert_size_line(
        self,
        color: CuttingOrderLineColor,
        draft: object,
        operator_id: UUID,
        already_created: Sequence[CuttingOrderSizeLine],
    ) -> CuttingOrderSizeLine:
        """插一条尺码明细，行号省略时由服务端分配。"""
        computed = draft.hands * draft.qty_per_hand  # type: ignore[attr-defined]
        manual = draft.output_qty is not None and draft.output_qty != computed  # type: ignore[attr-defined]
        row = CuttingOrderSizeLine(
            line_color_id=color.id,
            # ⚠️ **冗余外键**：打菲 / 计件按布批反查（04 §5 要求它建索引）
            line_id=color.line_id,
            size_line_no=draft.size_line_no  # type: ignore[attr-defined]
            or self._next_size_line_no(color, already_created),
            size_code=draft.size_code,  # type: ignore[attr-defined]
            hands=draft.hands,  # type: ignore[attr-defined]
            qty_per_hand=draft.qty_per_hand,  # type: ignore[attr-defined]
            output_qty=draft.output_qty if manual else computed,  # type: ignore[attr-defined]
            output_qty_manual=manual,
            balance_qty=max(computed - draft.output_qty, 0) if manual else 0,  # type: ignore[attr-defined]
            hands_seq=draft.hands_seq,  # type: ignore[attr-defined]
            remark=draft.remark,  # type: ignore[attr-defined]
            created_by=operator_id,
            updated_by=operator_id,
        )
        self.session.add(row)
        return row

    @staticmethod
    def _next_size_line_no(
        color: CuttingOrderLineColor, already_created: Sequence[CuttingOrderSizeLine]
    ) -> int:
        """分配尺码明细行号 = **max(现存与本次新建) + 1**。

        ⚠️ **必须是 max + 1 而不是 count + 1**：软删的行不计入 ``count``，
        所以一张「原本 3 行、删了 2 行、现在还有 1 行」的明细表，``count`` 是 1、
        下一个号该是 4 —— 用 ``count + 1`` 会发出 2，与软删行撞号。

        ⚠️ 锁由调用方负责（``get_line_color_for_update`` 已锁住该颜色行）——
        ``modules/02 §7``「在锁住该颜色行的前提下分配」正是防两个并发请求拿到同一个号。
        """
        candidates = [row.size_line_no for row in color.size_lines if row.deleted_at is None]
        candidates += [row.size_line_no for row in already_created]
        return (max(candidates) if candidates else 0) + 1

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

    async def _insert_lines(
        self, order: CuttingOrder, lines: Sequence[OrderLineIn], operator_id: UUID
    ) -> list[tuple[CuttingOrderLine, list[CuttingOrderLineColor]]]:
        """落库三层明细，返回 ``[(行, 该行的颜色)]``。

        ⚠️ **返回对象图而不是 id**，是为了让汇总层（:func:`recalc_line` /
        :func:`recalc_order`）不必回查数据库。写入路径上刚 ``add`` 的行还没进过
        任何 SELECT，依赖 lazy load 会多出三次查询，而「汇总算错」是这一层
        最贵的 bug —— 让它能被单独测比让它跑得快重要。
        """
        built: list[tuple[CuttingOrderLine, list[CuttingOrderLineColor]]] = []
        for line_in in lines:
            stock = await self._resolve_stock(line_in.stock_id)
            self._assert_fabric_within_available(stock, line_in.fabric_qty)
            line = CuttingOrderLine(
                doc_id=order.id,
                line_no=line_in.line_no,
                stock_id=stock.id,
                # ⚠️ 快照列**从 stock 反查**，不接受前端传 —— 两个来源会互相矛盾
                supplier_id=stock.supplier_id,
                material_id=stock.material_id,
                style_no=order.style_no,
                dye_lot_no=stock.dye_lot_no,
                bolt_no=stock.bolt_no,
                color_plan=line_in.color_plan,
                width_cm=line_in.width_cm if line_in.width_cm is not None else stock.width_cm,
                fabric_qty=line_in.fabric_qty,
                waste_qty=line_in.waste_qty,
                # ★ 正向录入的估算值，**不覆盖**（C34 口径 A）
                output_qty=line_in.output_qty,
                remark=line_in.remark,
                created_by=operator_id,
                updated_by=operator_id,
            )
            self.session.add(line)
            await self.session.flush()
            colors = [
                await self._insert_color(line, color_in, operator_id) for color_in in line_in.colors
            ]
            await self.session.flush()
            built.append((line, colors))
        return built

    @staticmethod
    def _assert_fabric_within_available(stock: MaterialStock, fabric_qty: Decimal) -> None:
        """行耗料不得超过该布批的可用量（C38 / ADR-0022 → ``40006``）。

        ⚠️ **按 ``available_qty = stock_qty - locked_qty`` 判**，不是 ``stock_qty``
        （C16 明确）。差额就是「已被别处锁住的那部分」—— 按 ``stock_qty`` 判会让
        用户以为能裁一匹已经被另一张单预留的布，而真正扣料时才发现超了。

        ⚠️ **这里只校验单行**。跨行 / 跨单的**累计**占用（C36：同一缸布可被多行
        占用，但累加不得超可用量）必须等审核时的条件 UPDATE 才能判 ——
        那时才是真正扣料的地方，也是 b-3 的活。草稿态只看单行，是为了让用户
        在录入时就发现「这匹布不够」而不是填完一整张单。

        ⚠️ 边界是**严格大于**：``fabric_qty == available_qty`` 要放行。
        """
        available = (stock.stock_qty or ZERO) - (stock.locked_qty or ZERO)
        if fabric_qty > available:
            raise BusinessError(
                ErrorCode.BATCH_STOCK_INSUFFICIENT,
                f"缸号 {stock.dye_lot_no} 匹号 {stock.bolt_no} 可用 {available} 米，"
                f"本次登记耗料 {fabric_qty} 米，不够。请换一匹布或调整耗料。",
                details={
                    "stock_id": str(stock.id),
                    "dye_lot_no": stock.dye_lot_no,
                    "bolt_no": stock.bolt_no,
                    "available_qty": str(available),
                    "fabric_qty": str(fabric_qty),
                },
            )

    async def _resolve_stock(self, stock_id: UUID) -> MaterialStock:
        """按 ``stock_id`` 取布批行（ADR-0022：级联选料，不允许自由输入缸号）。

        ⚠️ 这一条就是「级联选料」的落地点：请求里**只有** ``stock_id``，
        缸号 / 匹号 / 物料 / 供应商 / 门幅全部从它反查 ——
        所以「缸号与匹号对不上」「门幅填了别的批次的」这类错误**结构上不可能发生**。
        """
        stock = await self.session.get(MaterialStock, stock_id)
        if stock is None or stock.deleted_at is not None:
            raise BusinessError(ErrorCode.BASE_DATA_NOT_FOUND, "所选布批不存在或已停用，请重新选批")
        return stock

    async def _insert_color(
        self, line: CuttingOrderLine, color_in: object, operator_id: UUID
    ) -> CuttingOrderLineColor:
        """落库行内颜色 + 其尺码明细（ADR-0017 第 2 层）。

        ⚠️ ``entry_mode_changed_*`` **不在这里写**：那是「模式被切换」时的留痕
        （C27 / C28），而建单时的初值不是「切换」。初始就是 ``entry_mode`` 本身。
        """
        color = CuttingOrderLineColor(
            line_id=line.id,
            color_code=color_in.color_code,  # type: ignore[attr-defined]
            entry_mode=color_in.entry_mode,  # type: ignore[attr-defined]
            qty_per_hand=color_in.qty_per_hand,  # type: ignore[attr-defined]
            uniform_qty=color_in.uniform_qty,  # type: ignore[attr-defined]
            # ⚠️ ratio_snapshot **不在建单时写** —— 那是「按比例带出」那条路径
            #    （suggest-lines）的事，且 C29 铁律：改行绝不写回比例主数据。
            #    建单时无条件写空快照会让人误以为「快照了但没内容」
            hands_total=ZERO,
            output_qty_total=ZERO,
            balance_qty_total=ZERO,
            created_by=operator_id,
            updated_by=operator_id,
        )
        self.session.add(color)
        await self.session.flush()
        # ⚠️ **显式收集**建好的尺码明细，不靠 ``color.size_lines`` 回读 ——
        #    写入路径上依赖 lazy load 会多一次 SELECT，而汇总逻辑是最该能
        #    单独测的部分（见 recalc_color 的 docstring）
        created_size_lines: list[CuttingOrderSizeLine] = []
        for size_in in color_in.size_lines:  # type: ignore[attr-defined]
            computed = size_in.hands * size_in.qty_per_hand
            manual = size_in.output_qty is not None and size_in.output_qty != computed
            row = CuttingOrderSizeLine(
                line_color_id=color.id,
                line_id=line.id,
                size_line_no=size_in.size_line_no,
                size_code=size_in.size_code,
                hands=size_in.hands,
                qty_per_hand=size_in.qty_per_hand,
                output_qty=size_in.output_qty if manual else computed,
                output_qty_manual=manual,
                # ⚠️ 差额只在**人工指定**时产生，且符号固定为「算出来的 - 人工给的」。
                #    正数 = 裁多了（零头），进 cut_waste_qty；人工给得比算出来多时
                #    取 0（多出来的那部分是**超出铺布能力**的，按 C34 由行余量去管）
                balance_qty=max(computed - size_in.output_qty, 0) if manual else 0,
                hands_seq=size_in.hands_seq,
                remark=size_in.remark,
                created_by=operator_id,
                updated_by=operator_id,
            )
            created_size_lines.append(row)
            self.session.add(row)
        await self.session.flush()
        recalc_color(color, created_size_lines)
        # ⚠️ C28：人工指定过出数的颜色**自动转 MANUAL** —— 否则它在 MASTER 模式下
        #    会显得「按比例带出来的」，而下一次编辑时比例会覆盖人工的判断
        if any(row.output_qty_manual for row in created_size_lines) and (
            color.entry_mode != CuttingEntryMode.MANUAL
        ):
            color.entry_mode = CuttingEntryMode.MANUAL
        return color


# ---------------------------------------------------------------- 三级汇总（纯函数）


def recalc_color(color: CuttingOrderLineColor, size_lines: Sequence[CuttingOrderSizeLine]) -> None:
    """重算**颜色**层三个汇总 —— **纯内存函数，不碰 DB**。

    ⚠️ 子行**由调用方传入**而不是从 ``color.size_lines`` 取。理由：写入路径上
    刚 ``add`` 的行还没进过任何 SELECT，靠关系回读就多一次查询；而
    「汇总是最该能单独测的逻辑」—— 传进来之后它连 ORM session 都不需要。

    ⚠️ 三个 ``sum`` 都给 ``start=ZERO``：空列表时 ``sum()`` 返回 **``int`` 0**，
    而写成 ``sum(...) or ZERO`` 的类型是 ``int | Decimal``，mypy 会拦；
    更糟的是「空列表返回 int 0」会让该列的类型在有数据 / 无数据之间摇摆。
    """
    color.hands_total = sum((Decimal(row.hands) for row in size_lines), start=ZERO)
    color.output_qty_total = sum((Decimal(row.output_qty) for row in size_lines), start=ZERO)
    color.balance_qty_total = sum((Decimal(row.balance_qty) for row in size_lines), start=ZERO)


def recalc_line(line: CuttingOrderLine, colors: Sequence[CuttingOrderLineColor]) -> None:
    """重算**行**层的 ``balance_qty``（耗料与行出数**不重算**）。

    ⚠️ **只有 ``balance_qty`` 是算出来的**。``fabric_qty`` / ``waste_qty`` /
    ``output_qty`` 三列都是用户按铺布实耗正向录入的，服务端**不覆盖**
    （C34 口径 A / C35）。所以本函数的全部内容就是那一个减法 —— 而它的
    **负数检查是整张单最重要的一道业务校验**：它拦的是「你登记的可出件数
    比实际裁出来的件数还少」，那会让 ``cut_waste_qty`` 变成负数、
    损耗率报表彻底失去意义。
    """
    size_line_sum = sum((c.output_qty_total for c in colors), start=ZERO)
    line.balance_qty = line.output_qty - size_line_sum
    if line.balance_qty < 0:
        raise BusinessError(
            ErrorCode.CUTTING_QTY_CONFLICT,
            f"第 {line.line_no} 行：可出件数 {line.output_qty} 件装不下 "
            f"尺码明细合计 {size_line_sum} 件，请调整行可出件数或明细",
            details={
                "line_no": line.line_no,
                "line_output_qty": str(line.output_qty),
                "size_line_sum_qty": str(size_line_sum),
            },
        )


def recalc_order(
    order: CuttingOrder,
    lines: Sequence[CuttingOrderLine],
    colors_of: Callable[[CuttingOrderLine], Sequence[CuttingOrderLineColor]],
) -> None:
    """重算**表头**五列 + ``color_codes``（C6）。

    ⚠️ ``cut_waste_qty = Σ行 waste_qty + balance_qty``（C13「裁损**含**尾数」）。
    余量**不得再单独扣一次** —— 那是 C5 明确点名的经典错误。

    ⚠️ ``colors_of`` 是回调而不是直接读 ``line.colors``：见 :func:`recalc_color`
    的同款理由（写入路径上子集合未必已加载）。

    ⚠️ ``hands_total`` 是 ``int``（表头列是 ``integer``），所以逐项 ``int()``：
    ``hands_total`` 在颜色层是 ``numeric(14,4)``（比例之和**可以是小数**，
    ADR-0013 允许 1.5 手的建议值），而表头是整数 —— 直接把 ``Decimal``
    赋给 ``Mapped[int]`` 时 SQLAlchemy 不会替你转，它会把 ``Decimal('6')``
    塞进 ``integer`` 列，Postgres 接受（能隐式转），但**列的值与 Python 侧
    不一致**，后面比较就出鬼。
    """
    order.fabric_qty = sum((line.fabric_qty for line in lines), start=ZERO)
    order.output_qty = sum((line.output_qty for line in lines), start=ZERO)
    order.balance_qty = sum((line.balance_qty for line in lines), start=ZERO)
    order.cut_waste_qty = sum((line.waste_qty for line in lines), start=ZERO) + order.balance_qty
    order.hands_total = sum((int(c.hands_total) for line in lines for c in colors_of(line)), 0)
    order.color_codes = ",".join(sorted({c.color_code for line in lines for c in colors_of(line)}))
