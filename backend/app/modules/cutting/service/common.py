"""cutting service 拆分后的共享常量、取单与乐观锁助手（原 service.py 94-126、482-585）。

本模块**不 import 任何 mixin / 组合类**，只依赖 common / core / models /
repository 层，以切断循环导入（设计稿 §2.3）。:class:`CommonMixin` 承载被多方
调用的 ``_bump_header``（条件 UPDATE 乐观锁）/ ``_editable_order`` /
``_readable_order`` / ``_reloaded`` / ``_locked_graph``，与 base 把共享助手归
``common`` 同一口径。
"""

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import DocumentStatus
from app.core.errors import BusinessError, ErrorCode
from app.core.permissions import AuthContext
from app.core.scope import assert_in_scope
from app.modules.cutting.models import CuttingOrder, CuttingOrderLine
from app.modules.cutting.repository import get_lines_for_update, get_order_three_levels

#: 数量零值。**用常量而不是字面量 ``0``**：三级汇总里 ``0`` 出现十几次，
#: 而写成常量后「这一处的 0 是件数还是米数」一眼可辨。
ZERO = Decimal("0")


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


class CommonMixin:
    """公共取单 / 软删锁图 / 条件 UPDATE 乐观锁（被各 Mixin 交叉调用）。"""

    session: AsyncSession

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

    async def _reloaded(self, order_id: UUID) -> CuttingOrder:
        """重读整棵三层树（``populate_existing`` 由 repository 负责）。

        ⚠️ **每一次写之后都要走这里**，理由见 :meth:`put_lines` 里那段注释：
        Core ``UPDATE`` 会 ``expire`` identity map 里的行，而 Router 的 Pydantic
        序列化在同步上下文里，访问被 expire 的列就是 ``MissingGreenlet`` 500。
        """
        order = await get_order_three_levels(self.session, order_id)
        if order is None:  # pragma: no cover —— 刚写过，行必然还在
            raise BusinessError(ErrorCode.CUTTING_STATUS_NOT_ALLOWED, "裁剪单不存在或已删除")
        return order

    async def _locked_graph(self, order_id: UUID) -> list[CuttingOrderLine]:
        """锁住三层并返回布批行（软删与替换都要用）。"""
        return await get_lines_for_update(self.session, order_id)
