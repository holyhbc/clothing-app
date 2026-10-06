"""九个字典资源的统一 CRUD（原 service.py 132-429）。"""

from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import delete, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.models import DocumentLog
from app.core.db import unit_of_work
from app.core.errors import BusinessError, ErrorCode
from app.core.permissions import AuthContext
from app.modules.auth.models import User
from app.modules.base.repository import DictRepository, ListQuery, Row
from app.modules.base.resources import DictResource, ref_count_expression
from app.modules.base.schemas import DictRow

from .common import ACTION_CREATE, ACTION_DELETE, ACTION_UPDATE, logger
from .dict_helpers import _unique_violation, payload_dict_changes


class DictService:
    """九个资源的统一 CRUD。"""

    def __init__(self, session: AsyncSession, resource: DictResource, ctx: AuthContext) -> None:
        self.session = session
        self.resource = resource
        self.ctx = ctx
        self.repo = DictRepository(session, resource)

    # ------------------------------------------------------------ 读方法

    async def list_rows(self, query: ListQuery) -> tuple[list[DictRow], int]:
        """分页列表，每行带 ``ref_count``（§4.4）。"""
        query.validate(self.resource)
        rows, total = await self.repo.list_rows(self.ctx, query)
        counts = await self.repo.ref_counts(rows)
        items = [
            DictRow.model_validate(row).model_copy(
                update={"ref_count": counts.get(row.id) if self.resource.ref_checkers else None}
            )
            for row in rows
        ]
        return items, total

    async def options(self, keyword: str | None, size: int, offset: int = 0) -> list[Any]:
        """候选下拉。"""
        return await self.repo.options(self.ctx, keyword, size, offset)

    async def export_rows(self, query: ListQuery) -> list[Row]:
        """导出行：**与列表同一套筛选**（docs/07 §3.2 铁律 3）。"""
        query.validate(self.resource)
        return await self.repo.iter_export_rows(self.ctx, query)

    async def get_one(self, code: str) -> Row:
        """按业务编码取单条。不存在 → ``20001``。"""
        obj = await self.repo.find_by_code(code)
        if obj is None:
            raise BusinessError(
                ErrorCode.BASE_DATA_NOT_FOUND,
                f"{self.resource.doc_type} {code} 不存在或已删除",
                details={"code": code},
            )
        return obj

    async def _reload(self, obj: Row) -> Row:
        """写操作后重新加载实体。

        ⚠️ **这一步不能省**。session 配了 ``expire_on_commit=False``（docs/03 §1.4，
        为了接口层少一次查询），于是 UPDATE 提交后 identity map 里那份对象**仍是旧值**；
        紧接着的 ``get_one`` 会命中缓存返回旧值，调用方拿到的就是"改了但没改"的
        实体。这类 bug 在开发时表现为"接口返回 200 但界面没变"，极难定位。
        """
        await self.session.refresh(obj)
        return obj

    # ------------------------------------------------------------ 写方法

    async def create(self, payload: dict[str, Any]) -> Row:
        """新建。编码重复 → ``10001``。"""
        values = {
            **payload,
            self.resource.code_column: str(payload[self.resource.code_column]).strip(),
        }
        if await self.repo.exists_by_code(values[self.resource.code_column]):
            raise BusinessError(
                ErrorCode.PARAM_INVALID,
                f"编码 {values[self.resource.code_column]} 已存在，请换一个",
                details={"code": values[self.resource.code_column]},
            )
        async with unit_of_work(self.session):
            obj = self.resource.model(
                **values,
                created_by=self.ctx.user_id,
                updated_by=self.ctx.user_id,
            )
            self.session.add(obj)
            try:
                await self.session.flush()
            except IntegrityError as exc:
                # 唯一索引兜底（并发下两个请求同时通过上面的 exists 检查）
                raise _unique_violation(exc, self.resource) from exc
            await self._write_log(ACTION_CREATE, obj, None, "新建")
        return obj

    async def patch(self, code: str, payload: dict[str, Any]) -> Row:
        """部分更新。``version`` 必传且必须匹配，否则 ``10003``。"""
        obj = await self.get_one(code)
        version = payload.get("version")
        if version is None:
            raise BusinessError(ErrorCode.PARAM_INVALID, "缺少 version（乐观锁必填）")
        changes = payload_dict_changes(payload, self.resource.code_column)
        if not changes:
            raise BusinessError(ErrorCode.PARAM_INVALID, "没有需要更新的字段")

        async with unit_of_work(self.session):
            stmt = (
                update(self.resource.model)
                .where(
                    self.resource.model.id == obj.id,
                    self.resource.model.version == version,
                    self.resource.model.deleted_at.is_(None),
                )
                .values(
                    **changes,
                    version=self.resource.model.version + 1,
                    updated_by=self.ctx.user_id,
                )
                .execution_options(synchronize_session=False)
            )
            result = await self.session.execute(stmt)
            if cast("CursorResult[Any]", result).rowcount == 0:
                raise BusinessError(
                    ErrorCode.OPTIMISTIC_LOCK_CONFLICT,
                    "数据已被他人修改，请刷新后重试",
                    details={"expected_version": version},
                )
            await self._write_log(ACTION_UPDATE, obj, changes, "修改")
        return await self._reload(obj)

    async def disable(self, code: str, reason: str) -> Row:
        """停用。``reason`` 必填（§4.4）。"""
        if self.resource.is_active_column is None:
            raise BusinessError(
                ErrorCode.ILLEGAL_OPERATION, f"{self.resource.doc_type} 没有启用/停用概念"
            )
        if not reason or not reason.strip():
            raise BusinessError(ErrorCode.MISSING_BUSINESS_PARAM, "停用必须填写原因（reason）")
        obj = await self.get_one(code)
        if not getattr(obj, self.resource.is_active_column):
            raise BusinessError(ErrorCode.ILLEGAL_OPERATION, "已经是停用状态，无需重复停用")

        async with unit_of_work(self.session):
            await self.session.execute(
                update(self.resource.model)
                .where(self.resource.model.id == obj.id)
                .values(
                    **{self.resource.is_active_column: False},
                    remark=reason[:500],
                    version=self.resource.model.version + 1,
                    updated_by=self.ctx.user_id,
                )
            )
            await self._write_log(
                ACTION_UPDATE, obj, {"is_active": False, "reason": reason}, "停用"
            )
        return await self.get_one(code)

    async def delete(self, code: str) -> int:
        """删除。返回级联删除的明细行数。

        两分支（ADR-0025）：
            - ``allow_physical_delete=False``（纯软删表）→ 只写 ``deleted_at``
            - ``allow_physical_delete=True``（字典表）→ 引用为 0 时**真删**，
              否则 ``20003`` + 引用明细
        """
        obj = await self.get_one(code)
        if not self.resource.allow_physical_delete:
            async with unit_of_work(self.session):
                await self.session.execute(
                    update(self.resource.model)
                    .where(self.resource.model.id == obj.id)
                    .values(
                        deleted_at=datetime.now(tz=UTC),
                        version=self.resource.model.version + 1,
                        updated_by=self.ctx.user_id,
                    )
                )
                await self._write_log(ACTION_DELETE, obj, None, "软删除")
            return 0

        references = await self.repo.references_of(obj)
        if references:
            raise BusinessError(
                ErrorCode.BASE_DATA_REFERENCED,
                f"{self.resource.doc_type} {code} 已被引用，不能删除；请改为停用",
                details={
                    "code": code,
                    "ref_count": sum(item["count"] for item in references),
                    "references": references,
                },
            )

        try:
            return await self._delete_physical(obj, code)
        except IntegrityError as exc:
            # ⚠️ 真实外键（``ON DELETE RESTRICT``）在这里兜底。
            #    走到这个分支说明：条件 DELETE 的 NOT EXISTS 没看到某条引用 ——
            #    典型情形是引用方在我们查之后、删之前**提交**了（CC-3 真并发）。
            #
            #    ⚠️ 捕获必须包住**整个事务**而不是只包 execute：并发下 FK 违规是在
            #    引用方提交、锁释放的那一刻才抛给我们的，落在 savepoint 释放 /
            #    commit 上。开始时只 try 了 ``session.execute``，结果异常直接穿透
            #    到用户面前变成 500。
            logger.info("删除被外键拦截，翻译为 20003", extra={"resource": self.resource.key})
            raise BusinessError(
                ErrorCode.BASE_DATA_REFERENCED,
                f"{self.resource.doc_type} {code} 刚被引用，不能删除；请改为停用",
                details={"code": code},
            ) from exc

    async def _delete_physical(self, obj: Row, code: str) -> int:
        """物理删除的真正实现（**必须在事务内**，异常由 :meth:`delete` 翻译）。"""
        async with unit_of_work(self.session):
            cascaded = await self._delete_cascade_members(obj)
            condition = ref_count_expression(self.resource)
            stmt = delete(self.resource.model).where(self.resource.model.id == obj.id)
            if condition is not None:
                # 条件 DELETE：把"未被引用"写进 SQL 本身，消除"先查后删"的竞态（CC-3）
                stmt = stmt.where(~condition)
            result = await self.session.execute(stmt.execution_options(synchronize_session=False))
            if cast("CursorResult[Any]", result).rowcount == 0:
                # rowcount=0 有**两种**原因，必须区分清楚，否则文案会误导人：
                #   a) 查引用之后、删除之前有人插入了引用 → 20003「被引用」
                #   b) 另一个请求已经把行删了 → 20001「不存在」
                # 早先不区分，CC-3 的并发双删用例里第二个请求收到
                # "刚被引用，请改为停用"，而它要去的行已经不存在了 ——
                # 用户会去停用一个查不到的记录。
                if await self.repo.find_by_code(code) is None:
                    raise BusinessError(
                        ErrorCode.BASE_DATA_NOT_FOUND,
                        f"{self.resource.doc_type} {code} 已被他人删除，请刷新列表",
                        details={"code": code},
                    )
                references = await self.repo.references_of(obj)
                raise BusinessError(
                    ErrorCode.BASE_DATA_REFERENCED,
                    f"{self.resource.doc_type} {code} 刚被引用，不能删除；请改为停用",
                    details={
                        "code": code,
                        "ref_count": sum(item["count"] for item in references),
                        "references": references,
                    },
                )
            await self._write_log(ACTION_DELETE, obj, {"cascaded": cascaded}, "物理删除")
        return cascaded

    async def _delete_cascade_members(self, obj: Row) -> int:
        """码表删成员。**必须在事务内调用**。"""
        if self.resource.key != "size-groups":
            return 0
        from app.modules.base.models import SizeGroupItem

        result = await self.session.execute(
            delete(SizeGroupItem).where(SizeGroupItem.size_group_id == obj.id)
        )
        return int(cast("CursorResult[Any]", result).rowcount or 0)

    async def replace_size_group_items(self, group: Row, items: list[dict[str, Any]]) -> int:
        """全量替换码表成员（§4.4）。**必须在事务内调用。"""
        from app.modules.base.models import SizeGroupItem

        await self.session.execute(
            delete(SizeGroupItem).where(SizeGroupItem.size_group_id == group.id)
        )
        for item in items:
            self.session.add(
                SizeGroupItem(
                    size_group_id=group.id,
                    size_id=item["size_id"],
                    sort_order=item.get("sort_order", 0),
                )
            )
        await self.session.flush()
        return len(items)

    # ------------------------------------------------------------ 审计日志

    async def _write_log(
        self,
        action: str,
        obj: Row,
        changed_fields: dict[str, Any] | None,
        reason: str,
    ) -> None:
        """写 ``document_logs``（docs/04 §7.9 / docs/08 §1.1）。

        ⚠️ **必须在事务内调用**：日志与业务数据同生共死。日志单独提交的话，
        业务回滚了而日志留着，操作员会看到一条"删除成功"的记录去查不存在的单据。
        """
        operator_name = await self._operator_name()
        self.session.add(
            DocumentLog(
                doc_type=self.resource.doc_type,
                doc_id=obj.id,
                doc_no=self.resource.path_column_value(obj),
                action=action,
                operator_id=self.ctx.user_id,
                operator_name=operator_name,
                reason=reason,
                changed_fields=changed_fields,
            )
        )

    async def _operator_name(self) -> str:
        """冗余操作人姓名：用户改名后历史日志仍可读（docs/04 §7.9）。"""
        user = await self.session.get(User, self.ctx.user_id)
        return user.name if user else self.ctx.name
