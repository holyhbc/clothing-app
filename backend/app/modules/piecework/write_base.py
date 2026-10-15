"""红冲 / 补录 / 手工补录**共用的基础 mixin**（`WriteBaseMixin`）。

## 为什么单独一个文件

``reverse.py``（红冲）与 ``manual.py``（补录）都要：锁原流水、锁码、查绑定、
声明 ``session``。这四个是**共享的**，各写一份就会漂移（比如锁码的方式改了
只改一处，另一处还在用旧的）—— 所以抽成这个 base，两个 mixin 都继承它。

⚠️ ``session: AsyncSession`` 声明放在这里：mypy 不知道这些 mixin 会被
``PieceworkService`` 继承，不声明就在每个用 ``self.session`` 的地方报 attr-defined。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessError, ErrorCode
from app.core.numbering import business_today
from app.core.permissions import AuthContext
from app.core.scope import apply_data_scope
from app.modules.piecework.guard import LockedBundle
from app.modules.piecework.models import PieceworkLog, PieceworkLogType
from app.modules.piecework.repository import (
    build_bound_stmt,
    build_lock_bundle_by_id_stmt,
    build_lock_bundle_stmt,
    build_log_for_update_stmt,
)


class WriteBaseMixin:
    """红冲 / 补录共用的锁与校验助手。``self.session`` 由 ``PieceworkService`` 提供。"""

    session: AsyncSession

    async def _locked_original(self, log_id: UUID, ctx: AuthContext) -> LockedBundle:
        """按 id 锁原流水（能判定越权 / 已结算 / 已红冲）。

        ⚠️ ``apply_data_scope`` 在这里进 SQL：红冲不能「查到了但越权」—— 越权必须
        与「不存在」一样回 ``12002``（不泄漏存在性）。
        """
        stmt = apply_data_scope(build_log_for_update_stmt(log_id), PieceworkLog, ctx)
        row = (await self.session.execute(stmt)).mappings().one_or_none()
        if row is None:
            raise BusinessError(ErrorCode.DATA_SCOPE_DENIED, "计件流水不存在或超出你的数据范围")
        if row["log_type"] == PieceworkLogType.REVERSAL:
            raise BusinessError(
                ErrorCode.ILLEGAL_OPERATION, "红冲流水不能再被红冲；请补录一条新流水"
            )
        return dict(row)

    async def _lock_bundle(self, bundle_id: UUID | None) -> LockedBundle | None:
        """按码 id 锁码（§7.3：红冲 / 补录都必须 ``FOR UPDATE``）。"""
        if bundle_id is None:
            return None
        row = (
            (await self.session.execute(build_lock_bundle_by_id_stmt(bundle_id))).mappings().first()
        )
        return dict(row) if row else None

    async def _lock_bundle_by_no(self, bundle_no: str) -> LockedBundle:
        """按码号锁码（补录入口用）。不存在 → ``31001``。"""
        row = (await self.session.execute(build_lock_bundle_stmt(bundle_no))).mappings().first()
        if row is None:
            raise BusinessError(ErrorCode.BUNDLE_NO_NOT_FOUND, f"打菲号 {bundle_no} 不存在")
        return dict(row)

    async def _is_bound(self, ctx: AuthContext, bundle: LockedBundle) -> bool:
        """员工当日是否已绑定该款号工序（BR-PW-04：绑定当天有效）。"""
        today = business_today()
        return bool(
            (
                await self.session.execute(
                    build_bound_stmt(ctx.user_id, bundle["style_no"], bundle["operation_no"], today)
                )
            ).scalar_one()
        )

    async def _existing_reversal(self, original_id: UUID) -> PieceworkLog | None:
        """幂等：同 ``reversal_of_id`` 已有 ``REVERSAL`` → 返回既有（§6.3）。"""
        return (
            await self.session.execute(
                select(PieceworkLog).where(
                    PieceworkLog.reversal_of_id == original_id,
                    PieceworkLog.log_type == PieceworkLogType.REVERSAL,
                    PieceworkLog.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()

    async def _bundle_is_uncounted(self, bundle_id: UUID | None) -> bool:
        """这手码当前是否「未计件」（判据 ``counted_at IS NULL``）。"""
        if bundle_id is None:
            return True
        bundle = await self._lock_bundle(bundle_id)
        return bundle is None or bundle["counted_at"] is None
