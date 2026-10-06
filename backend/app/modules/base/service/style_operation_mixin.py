"""款号工序全量替换 + 现行价（原 service.py 1641-1874）。"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import unit_of_work
from app.core.errors import BusinessError, ErrorCode
from app.core.numbering import normalize_style_no
from app.core.permissions import AuthContext
from app.core.scope import apply_data_scope
from app.modules.base.models import Operation, OperationRate, Style, StyleOperation
from app.modules.base.schemas import (
    OperationRateOut,
    StyleOperationOut,
    StyleOperationsListOut,
    StyleOperationsReplaceIn,
)

from .common import (
    ACTION_UPDATE,
    DOC_STYLE_OPERATION,
    MAX_STYLE_OPERATION_ITEMS,
    SCALE_BUNDLE_QTY,
    _duplicated,
    numeric_str,
    write_document_log,
)
from .rate_helpers import rate_out


class StyleOperationMixin:
    session: AsyncSession
    ctx: AuthContext

    if TYPE_CHECKING:

        async def get_required(self, style_no: str, *, for_update: bool = False) -> Style: ...

        def _assert_aggregate_version(self, style: Style, expected: int) -> None: ...

        async def _bump_aggregate(self, style: Style) -> None: ...

    # -------------------------------------------------------- 款号工序

    async def list_style_operations(
        self,
        style_no: str,
        *,
        is_piecework: bool | None = None,
        include_inactive: bool = False,
    ) -> list[StyleOperationOut]:
        """款号工序配置列表，按 ``sequence`` 升序。

        ``include_inactive=False`` 时过滤掉**工序字典已停用**的行，但款号自己的
        配置不删（R17：停用工序后历史款号配置照常可查）。
        """
        code = normalize_style_no(style_no)
        style = await self.get_required(code)
        stmt = apply_data_scope(
            cast("Select[Any]", select(StyleOperation, Operation.name)),
            StyleOperation,
            self.ctx,
        ).where(StyleOperation.style_no == style.style_no, StyleOperation.deleted_at.is_(None))
        if is_piecework is not None:
            stmt = stmt.where(StyleOperation.is_piecework == is_piecework)
        # ⚠️ 显式 JOIN（不靠外键推断 —— ``select(StyleOperation, Operation.name)``
        #    会生成笛卡尔积，被 filterwarnings=error 变成 500）。
        #    过滤停用工序时用 INNER JOIN，"包含停用行"时用 LEFT JOIN。
        if include_inactive:
            stmt = stmt.outerjoin(Operation, Operation.operation_no == StyleOperation.operation_no)
        else:
            stmt = stmt.join(Operation, Operation.operation_no == StyleOperation.operation_no)
            stmt = stmt.where(Operation.deleted_at.is_(None), Operation.is_active.is_(True))
        rows = (
            await self.session.execute(
                stmt.order_by(StyleOperation.sequence, StyleOperation.operation_no)
            )
        ).all()
        return [self._style_operation_out(row[0], row[1]) for row in rows]

    @staticmethod
    def _style_operation_out(row: StyleOperation, name: str | None) -> StyleOperationOut:
        return StyleOperationOut(
            id=row.id,
            version=row.version,
            remark=row.remark,
            created_at=row.created_at,
            updated_at=row.updated_at,
            style_no=row.style_no,
            operation_no=row.operation_no,
            operation_name=name,
            sequence=row.sequence,
            bundle_qty=numeric_str(row.bundle_qty, SCALE_BUNDLE_QTY),
            is_piecework=row.is_piecework,
            is_final_operation=row.is_final_operation,
        )

    async def replace_style_operations(
        self, style_no: str, payload: StyleOperationsReplaceIn
    ) -> StyleOperationsListOut:
        """按款号**全量替换**款号工序（≤500 行）。

        三条硬校验（modules/01 §6）：

        1. ``operation_no`` **存在且启用** —— 用行锁查，让"停用工序"与"建款号工序
           配置"串行化（modules/01 §7）；
        2. 同一次提交里 ``operation_no`` 不重复（DB 唯一索引兜底）；
        3. ``is_final_operation`` **至多一道** —— 业务确认所有款最后一道都是整烫，
           识别不到必须人工指定，所以既不允许两道，也不允许一道都不标。

        ⚠️ **不物理删行**（04 §6.2.1 / ADR-0025），实现口径与
        :meth:`replace_ratios` 一致：按 ``operation_no`` upsert + 多余行软删 +
        软删键复活。
        """
        code = normalize_style_no(style_no)
        style = await self.get_required(code)
        if not payload.items:
            raise BusinessError(ErrorCode.PARAM_INVALID, "款号工序不能为空，请至少配一道工序")
        if len(payload.items) > MAX_STYLE_OPERATION_ITEMS:
            raise BusinessError(
                ErrorCode.PARAM_INVALID,
                f"款号工序一次最多 {MAX_STYLE_OPERATION_ITEMS} 行，收到 {len(payload.items)} 行",
                details={
                    "max_items": MAX_STYLE_OPERATION_ITEMS,
                    "received": len(payload.items),
                },
            )
        operation_nos = [item.operation_no for item in payload.items]
        duplicates = _duplicated(operation_nos)
        if duplicates:
            raise BusinessError(
                ErrorCode.PARAM_INVALID,
                f"工序 {', '.join(duplicates)} 在同一次提交里重复了",
                details={"duplicated_operation_nos": duplicates},
            )
        final_flags = [item.operation_no for item in payload.items if item.is_final_operation]
        if len(final_flags) > 1:
            raise BusinessError(
                ErrorCode.PARAM_INVALID,
                f"最后一道工序只能有一道，收到 {len(final_flags)} 道：{', '.join(final_flags)}",
                details={"final_operation_nos": final_flags},
            )
        await self._assert_operations_active(operation_nos)

        async with unit_of_work(self.session):
            await self.session.refresh(style, with_for_update=True)
            self._assert_aggregate_version(style, payload.version)
            existing = {
                row.operation_no: row
                for row in (
                    await self.session.execute(
                        select(StyleOperation)
                        .where(StyleOperation.style_no == code)
                        .order_by(StyleOperation.operation_no)
                        .with_for_update()
                    )
                )
                .scalars()
                .all()
            }
            before = {
                row.operation_no: row.sequence
                for row in existing.values()
                if row.deleted_at is None
            }
            submitted = {item.operation_no: item for item in payload.items}
            for operation_no in sorted(submitted, key=lambda key: submitted[key].sequence):
                item = submitted[operation_no]
                row = existing.get(operation_no)
                if row is None:
                    row = StyleOperation(
                        style_id=style.id,
                        style_no=code,
                        operation_no=operation_no,
                        sequence=item.sequence,
                        bundle_qty=item.bundle_qty,
                        is_piecework=item.is_piecework,
                        is_final_operation=item.is_final_operation,
                        remark=item.remark,
                        created_by=self.ctx.user_id,
                        updated_by=self.ctx.user_id,
                    )
                    self.session.add(row)
                else:
                    # 软删行要**复活**：``uq_style_operations (style_no, operation_no)``
                    # 不是部分索引，软删行仍占着键，不复活就插不进来
                    row.sequence = item.sequence
                    row.bundle_qty = item.bundle_qty
                    row.is_piecework = item.is_piecework
                    row.is_final_operation = item.is_final_operation
                    row.remark = item.remark
                    row.deleted_at = None
                    row.updated_by = self.ctx.user_id
                    row.version = row.version + 1
            for operation_no, row in existing.items():
                if operation_no in submitted or row.deleted_at is not None:
                    continue
                row.deleted_at = datetime.now(tz=UTC)
                row.updated_by = self.ctx.user_id
                row.version = row.version + 1
            try:
                await self.session.flush()
            except IntegrityError as exc:
                raise BusinessError(
                    ErrorCode.PARAM_INVALID,
                    f"款号 {code} 的工序写入冲突，请刷新后重试",
                    details={"style_no": code},
                ) from exc
            await self._bump_aggregate(style)
            log_id = await write_document_log(
                self.session,
                self.ctx,
                doc_type=DOC_STYLE_OPERATION,
                doc_id=style.id,
                doc_no=code,
                action=ACTION_UPDATE,
                reason="全量替换款号工序",
                changed_fields={
                    "style_no": code,
                    "operation_nos": operation_nos,
                    "final_operation_nos": final_flags,
                    "before_sequences": before,
                    "after_sequences": {item.operation_no: item.sequence for item in payload.items},
                    "removed_operation_nos": sorted(before.keys() - submitted.keys()),
                },
            )
        # 重查活行再拼响应（理由同 :meth:`_load_ratios`），顺带把工序名带出来，
        # 省掉前端一次字典查询往返
        return StyleOperationsListOut(
            items=await self.list_style_operations(code, include_inactive=True),
            document_log_id=log_id,
        )

    async def _assert_operations_active(self, operation_nos: Sequence[str]) -> None:
        """工序必须存在且启用；加锁让"停用"与"建配置"串行化。"""
        rows = set(
            (
                await self.session.execute(
                    select(Operation.operation_no)
                    .where(
                        Operation.operation_no.in_(list(operation_nos)),
                        Operation.deleted_at.is_(None),
                        Operation.is_active.is_(True),
                    )
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        missing = [item for item in operation_nos if item not in rows]
        if missing:
            raise BusinessError(
                ErrorCode.PARAM_INVALID,
                f"工序 {', '.join(missing)} 不存在或已停用，请先在工序字典里启用",
                details={"invalid_operation_nos": missing},
            )

    async def _current_rates(self, style_no: str) -> list[OperationRateOut]:
        """该款各工序的**当前有效价**（款号详情页的"现行价"区块）。"""
        rows = (
            (
                await self.session.execute(
                    select(OperationRate)
                    .where(
                        OperationRate.style_no == style_no,
                        OperationRate.effective_to.is_(None),
                        OperationRate.deleted_at.is_(None),
                    )
                    .order_by(OperationRate.operation_no)
                )
            )
            .scalars()
            .all()
        )
        return [rate_out(row) for row in rows]
