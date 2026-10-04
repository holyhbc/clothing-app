"""基础资料业务逻辑（设计稿 §4.4 第一批 + §4.5 第二批）。

⚠️ **事务边界只在 service**（AGENTS.md §2.1）：所有写方法自带
``async with unit_of_work(session)``，router 不 commit。

本模块三个 service 类：

======================  ==========================================  ==================
类                     承载                                          任务卡
======================  ==========================================  ==================
:class:`DictService`   九个字典类资源的统一 CRUD                     T-BASE-001
:class:`StyleService`  款号 / 色码尺码 / 比例 / 款号工序 / 模板复制   T-BASE-002
:class:`RateService`   工序单价：设价 / 调价 / 取价 / 历史 / 导出      T-BASE-002
======================  ==========================================  ==================

第一批 9 个资源共享的 6 条业务规则，每条都对应一个测试：

1. **编码唯一靠唯一索引兜底**，不靠"先查后插"（docs/03 §1.5、modules/01 R15）。
   冲突统一报 ``10001``（参数冲突）而不是自定义码 —— docs/05 §4 没登记"编码重复"
   这个码，而 ``10001`` 的语义就是"参数不合法"。
2. **PATCH 必传 ``version``**，不匹配 → ``10003``。用**条件 UPDATE** 实现
   （``WHERE id=? AND version=?``），靠 ``rowcount=0`` 判定冲突 —— 这样并发下
   两个请求只有一个能成功，另一个拿到 10003，而不是"后写的悄悄覆盖先写的"。
3. **停用必填 ``reason``**（§4.4）。缺失由 Schema 拦（``min_length=1``），
   额外再查一次是防绕过 Schema 的直接调用。
4. **真删两分支**（ADR-0025）：未被引用 → 物理删除；被引用 → ``20003`` +
   ``details.ref_count`` + ``details.references[]``。判定用**一条 SQL 的条件
   DELETE**，不"先查后删" —— 那两步之间被并发插入引用就会留下悬空引用（CC-3）。
5. **每次变更写 ``document_logs``**（docs/04 §7.9），``doc_type`` 取资源注册表。
6. **码表删成员级联**：删 ``size_groups`` 时连带删 ``size_group_items`` 并回传条数
   （§4.4）。留孤儿成员行就是"残渣"。

第二批（款号与单价）另有 5 条**不可省**的规则：

7. **单价只 INSERT，永不 UPDATE** ``unit_price``（R11 / INV-P0-3）：调价 = 旧行
   只改 ``effective_to`` + 插入新行。TC-B14 断言历史区间 ``unit_price`` 未变。
8. **同一 ``(style_no, product_category_id, operation_no)`` 三元组内区间不重叠**，
   且**至多一条** ``effective_to IS NULL``（R18 / INV-P0-2）。区间行按
   ``effective_from`` 排序 ``FOR UPDATE``，重叠 → ``20005`` + ``details``。
9. **全量替换的两处子表操作走"聚合版本"**（``styles.version``）：比例与款号工序都
   是"旧行全删 + 新行全插"，子表自己的 ``version`` 每次从 1 重来，拿它当乐观锁
   等于没有锁（TC-B31 要求一成一败）。
10. **取价用一条 SQL**（ADR-0026 §2），档位优先 + 生效日两个维度一次性判定，
    避免"应用层按档位查三次"在并发调价时读到撕裂的组合。
11. **模板复制整批单事务**，且**档位 2（分类价）不复制**（ADR-0026 §4）——
    复制一款顺手改掉全厂工资口径是静默事故，比不复制危险得多。
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Select, and_, case, delete, func, literal_column, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import ConflictPolicy, RateSource, TemplateCopyMode
from app.common.models import DocumentLog
from app.core.db import unit_of_work
from app.core.errors import BusinessError, ErrorCode
from app.core.numbering import (
    FACTORY_STYLE_PREFIX,
    business_today,
    normalize_style_no,
    quantize_price,
    suggest_style_no,
)
from app.core.permissions import AuthContext
from app.core.scope import apply_data_scope, assert_in_scope
from app.modules.auth.models import User
from app.modules.base.models import (
    Customer,
    Operation,
    OperationRate,
    ProductCategory,
    Size,
    SizeGroup,
    SizeGroupItem,
    Style,
    StyleColor,
    StyleColorSizeRatio,
    StyleOperation,
    StyleSize,
)
from app.modules.base.repository import DictRepository, ListQuery, Row
from app.modules.base.resources import DictResource, ref_count_expression
from app.modules.base.schemas import (
    CopiedPriceOut,
    DictRow,
    OperationRateCreate,
    OperationRateOut,
    OperationRateSetOut,
    OptionOut,
    RateResolveOut,
    RatioListOut,
    RatioOut,
    RatioReplaceIn,
    StyleColorCreate,
    StyleColorOut,
    StyleCreate,
    StyleDetailOut,
    StyleListOut,
    StyleOperationOut,
    StyleOperationsListOut,
    StyleOperationsReplaceIn,
    StyleOut,
    StylePatch,
    StyleSizeCreate,
    StyleSizeOut,
    TemplateCopyIn,
    TemplateCopyOut,
)

logger = logging.getLogger("app.base")

#: ``document_logs.action`` 取值（docs/08 §1.1 的 DocumentAction）
ACTION_CREATE = "CREATE"
ACTION_UPDATE = "UPDATE"
ACTION_DELETE = "DELETE"
ACTION_RESTORE = "RESTORE"


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


def payload_dict_changes(payload: dict[str, Any], code_column: str) -> dict[str, Any]:
    """从 PATCH 体里取出真正要改的字段。

    两个要点：
    - 过滤 ``None``：``{"remark": None}`` 表示"不改备注"，不是"把备注清空"
    - 剔除 ``version``（它自己进 WHERE，不进 SET）与**编码列**：
      §4.4 明确"编码字段不可改"，而工序更严格 —— 引用方是字符串冗余，
      改号会让历史单据指向一个不存在的工序
    """
    skip = {code_column, "version", "items"}
    return {key: value for key, value in payload.items() if key not in skip and value is not None}


def _unique_violation(exc: IntegrityError, resource: DictResource) -> BusinessError:
    """把唯一索引冲突翻译成 ``10001``。

    ⚠️ 不暴露数据库错误文案 —— 里面带表名与约束名（docs/06 §5）。
    """
    logger.info("唯一约束冲突", extra={"resource": resource.key})
    return BusinessError(
        ErrorCode.PARAM_INVALID,
        f"{resource.code_column} 已存在，请换一个编码",
        details={"field": resource.code_column},
    )


# ==================================================================
# 组 D：款号、色码尺码、比例、款号工序、工序单价（T-BASE-002）
# ==================================================================

#: ``document_logs.doc_type`` 取值（docs/08 §1.1；日志必须能追溯"改了什么"）
DOC_STYLE = "Style"
DOC_STYLE_COLOR = "StyleColor"
DOC_STYLE_SIZE = "StyleSize"
DOC_STYLE_RATIO = "StyleColorSizeRatio"
DOC_STYLE_OPERATION = "StyleOperation"
DOC_STYLE_OPERATION_COPY = "StyleOperationCopy"
DOC_OPERATION_RATE = "OperationRate"

#: 款号模糊查询用的 trgm 表达式，**带表名限定**。
#:
#: ⚠️ 必须限定：款号列表要 LEFT JOIN ``customers`` / ``product_categories`` 取名字，
#: 三张表都有 ``name`` 列，不限定就会报 ``column reference "name" is ambiguous``。
#: 而限定**不影响索引命中** —— PostgreSQL 匹配表达式索引时会把查询表达式的
#: 限定符剥掉（实测限定与不限定生成完全相同的计划，04 §5.1 的 GIN 索引照常可用）。
STYLE_TRGM_QUALIFIED = "(styles.style_no || ' ' || styles.name)"

#: 款号 ``sort_by`` 白名单。⚠️ **禁止把用户输入拼进 SQL**（docs/03 §1.5、04 §5.1）
STYLE_SORT_WHITELIST: dict[str, str] = {
    "style_no": "style_no",
    "name": "name",
    "bulk_qty": "bulk_qty",
    "last_used_at": "last_used_at",
    "created_at": "created_at",
}
#: 单价 ``sort_by`` 白名单
RATE_SORT_WHITELIST: dict[str, str] = {
    "effective_from": "effective_from",
    "operation_no": "operation_no",
    "style_no": "style_no",
    "created_at": "created_at",
}
#: 候选 / 列表的硬上限（docs/05 §9.5.2、§2）
MAX_PAGE_SIZE = 200
MAX_OPTION_SIZE = 20
MAX_OFFSET = 10_000
#: 导出行数上限（docs/05 §4 的 ``11011``：超过 10 万行直接拒绝，不截断）
MAX_EXPORT_ROWS = 100_000

#: 比例全量替换的行数上限（modules/01 §6：≤100 行）
MAX_RATIO_ITEMS = 100
#: 款号工序全量替换的行数上限（modules/01 §6：≤500 行）
MAX_STYLE_OPERATION_ITEMS = 500

#: 模板复制成功后**必须**回给用户的一句话（modules/01 §5.1 前端表现）。
#:
#: ⚠️ 少了这句，用户会以为"工序和比例都配好了"，而比例没配要到出布头对不上账
#: 时才爆出来 —— 那时布已经织完。静默比不复制危险得多。
RATIO_NOT_COPIED_MESSAGE = "尺码比例未复制，请按目标款的销售构成为各颜色录入尺码比例"

#: 跳过原因码（写进 ``TemplateCopyOut.skipped[].reason``，前端据此提示）
SKIP_TARGET_HAS_OPERATION = "TARGET_HAS_OPERATION"
SKIP_TARGET_HAS_RATE = "TARGET_HAS_RATE"
SKIP_CATEGORY_RATE = "CATEGORY_RATE_NOT_COPIED"


@dataclass(frozen=True, slots=True)
class StyleQuery:
    """款号列表筛选。"""

    q: str | None = None
    is_active: bool | None = None
    customer_id: UUID | None = None
    category_id: UUID | None = None
    merchandiser_id: UUID | None = None
    sort_by: str | None = None
    sort_order: str = "asc"
    page: int = 1
    size: int = 20

    def validate(self) -> None:
        """分页与排序校验。**必须在拼 SQL 之前调用**（docs/05 §2）。"""
        _validate_page(self.page, self.size)
        _validate_sort(self.sort_by, STYLE_SORT_WHITELIST)
        _validate_sort_order(self.sort_order)


@dataclass(frozen=True, slots=True)
class RateQuery:
    """单价区间筛选（**含历史区间**，不只当前有效）。

    ⚠️ ``style_no`` **可选**（设计稿 §4.5 写的是"必填"）：档位 2（分类价）与
    档位 3（全厂同工序统一价）的行 ``style_no`` 本来就是 NULL，要求"必传
    style_no"会让这两档**建得出来、却查不到** —— 那是维护不了，不是更安全。

    ``effective_from`` / ``effective_to`` 过滤的是**生效起始日**落在该闭区间内的行
    （"这段时间开始生效的单价"），不是"这段时间还有效的单价"：后者在
    ``effective_to IS NULL`` 时无法表达成一条简单 SQL，而用户查历史要的正是前者。
    """

    style_no: str | None = None
    operation_no: str | None = None
    product_category_id: UUID | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    sort_by: str | None = "effective_from"
    sort_order: str = "desc"
    page: int = 1
    size: int = 20

    def validate(self) -> None:
        _validate_page(self.page, self.size)
        _validate_sort(self.sort_by, RATE_SORT_WHITELIST)
        _validate_sort_order(self.sort_order)
        if (
            self.effective_from is not None
            and self.effective_to is not None
            and self.effective_from > self.effective_to
        ):
            raise BusinessError(ErrorCode.PARAM_INVALID, "effective_from 不能晚于 effective_to")


def _validate_page(page: int, size: int) -> None:
    if page < 1:
        raise BusinessError(ErrorCode.PARAM_INVALID, "page 从 1 起")
    if not 1 <= size <= MAX_PAGE_SIZE:
        raise BusinessError(
            ErrorCode.PARAM_INVALID, f"page_size 必须在 1~{MAX_PAGE_SIZE} 之间，收到 {size}"
        )


def _validate_sort(sort_by: str | None, whitelist: dict[str, str]) -> None:
    if sort_by is not None and sort_by not in whitelist:
        raise BusinessError(
            ErrorCode.PARAM_INVALID,
            f"sort_by={sort_by} 不是可排序字段",
            details={"allowed": sorted(whitelist)},
        )


def _validate_sort_order(sort_order: str) -> None:
    if sort_order not in ("asc", "desc"):
        raise BusinessError(ErrorCode.PARAM_INVALID, "sort_order 只能是 asc 或 desc")


async def write_document_log(
    session: AsyncSession,
    ctx: AuthContext,
    *,
    doc_type: str,
    doc_id: UUID,
    doc_no: str,
    action: str,
    reason: str,
    changed_fields: dict[str, Any] | None = None,
) -> UUID:
    """写 ``document_logs`` 并返回日志 id（docs/04 §7.9 / docs/08 §1.1）。

    ⚠️ **必须在 service 的事务内调用**：日志与业务数据同生共死。日志单独提交的话，
    业务回滚了而日志留着，操作员会看到一条"删除成功"的记录去查不存在的单据。

    ``operator_name`` 冗余存一份：用户改名之后历史日志仍然可读。
    """
    user = await session.get(User, ctx.user_id)
    log = DocumentLog(
        doc_type=doc_type,
        doc_id=doc_id,
        doc_no=doc_no,
        action=action,
        operator_id=ctx.user_id,
        operator_name=user.name if user else ctx.name,
        reason=reason,
        changed_fields=changed_fields,
    )
    session.add(log)
    await session.flush()
    return log.id


def build_rate_resolve_stmt(
    *,
    operation_no: str,
    style_no: str,
    category_id: UUID | None,
    work_date: date,
) -> Select[OperationRate]:
    """**ADR-0026 §2 的唯一取价 SQL**，逐字照抄那份决策。

    抽成模块级函数有两个理由：

        1. 让集成测试能对**生产语句本身**做 ``EXPLAIN``（断言命中
           ``idx_operation_rates_lookup``），而不是对一份抄写副本断言 ——
           抄写副本会与生产代码悄悄分叉，而分叉之后测试依然全绿。
        2. 计件模块（P1）要复用同一口径，届时直接 import，不再抄第三遍。

    ⚠️ ``ORDER BY`` 的两段**不可交换**：先按档位（``CASE``），同档位内再取最新
    生效日。写成 ``effective_from DESC, CASE ...`` 会让"三个月前公布的款号价"
    盖掉"今天生效的分类价" —— 而款号价本来就该赢。
    """
    tier = case(
        (OperationRate.style_no == style_no, 0),
        (OperationRate.product_category_id == category_id, 1),
        else_=2,
    )
    return (
        select(OperationRate)
        .where(
            OperationRate.operation_no == operation_no,
            OperationRate.effective_from <= work_date,
            or_(OperationRate.effective_to.is_(None), OperationRate.effective_to > work_date),
            or_(
                and_(
                    OperationRate.style_no == style_no,
                    OperationRate.product_category_id.is_(None),
                ),
                and_(
                    OperationRate.style_no.is_(None),
                    OperationRate.product_category_id == category_id,
                ),
                and_(
                    OperationRate.style_no.is_(None),
                    OperationRate.product_category_id.is_(None),
                ),
            ),
            OperationRate.deleted_at.is_(None),
        )
        .order_by(tier, OperationRate.effective_from.desc())
        .limit(1)
    )


#: 各金额 / 数量列的**显示精度**（与迁移 0005 里的 ``numeric`` 精度逐项一致）。
#:
#: ⚠️ 必须显式量化，**不能靠数据库回显**：numeric 回显的标度取决于这一行的值是不是
#: 刚被 UPDATE 写进去的 —— 已加载的 ORM 属性不会被随后的 SELECT 覆盖，于是同一列
#: 会时而 ``"1.5"``、时而 ``"1.5000"``。前端按字符串比较就会漏判，而这种抖动极难查。
SCALE_RATIO = 4
SCALE_BUNDLE_QTY = 3
SCALE_UNIT_PRICE = 6


def numeric_str(value: Decimal, places: int) -> str:
    """Decimal → 定标度字符串（docs/05 §3：金额 / 数量 / 单价响应一律字符串）。"""
    return str(quantize_price(value, places))


def rate_source_of(rate: OperationRate) -> RateSource:
    """由命中行的形态派生 ``rate_source``（ADR-0026 §2 三档）。"""
    if rate.style_no is not None:
        return RateSource.STYLE
    if rate.product_category_id is not None:
        return RateSource.CATEGORY
    return RateSource.OPERATION


def rate_out(row: OperationRate) -> OperationRateOut:
    """``operation_rates`` 行 → 响应模型（``is_current`` 与 ``rate_source`` 都派生）。"""
    return OperationRateOut(
        id=row.id,
        version=row.version,
        remark=row.remark,
        created_at=row.created_at,
        updated_at=row.updated_at,
        operation_no=row.operation_no,
        style_no=row.style_no,
        product_category_id=row.product_category_id,
        effective_from=row.effective_from,
        effective_to=row.effective_to,
        unit_price=numeric_str(row.unit_price, SCALE_UNIT_PRICE),
        reason=row.reason,
        is_current=row.effective_to is None,
        rate_source=rate_source_of(row),
    )


def _style_list_stmt(ctx: AuthContext) -> Select[Any]:
    """款号列表 / 详情共用的 SELECT：款号 + 客户名 + 分类名。

    ⚠️ **必须显式 ``LEFT JOIN``**（``customer_id`` 可空）：写成
    ``select(Style, Customer, ProductCategory)`` 时 SQLAlchemy **不会**从外键推断
    连接，会生成 ``FROM styles, customers, product_categories`` 的笛卡尔积 ——
    表现是行数爆炸 + ``SAWarning``，而本项目 ``filterwarnings = error``，
    一条警告就变成 500。列表与详情共用这一条，避免两处 SQL 悄悄分叉。
    """
    return (
        apply_data_scope(select(Style), Style, ctx)
        .outerjoin(Customer, Customer.id == Style.customer_id)
        .outerjoin(ProductCategory, ProductCategory.id == Style.category_id)
        .add_columns(Customer.name, ProductCategory.name)
    )


def _duplicated(values: Sequence[str]) -> list[str]:
    """保序返回重复项（用于"同一次提交里重复了"的报错文案）。"""
    seen: set[str] = set()
    duplicated: list[str] = []
    for value in values:
        if value in seen and value not in duplicated:
            duplicated.append(value)
        seen.add(value)
    return duplicated


# ------------------------------------------------------------------ 款号


class StyleService:
    """款号与其子表（色码 / 尺码 / 比例 / 款号工序 / 单价）的全部业务规则。"""

    def __init__(self, session: AsyncSession, ctx: AuthContext) -> None:
        self.session = session
        self.ctx = ctx

    # ------------------------------------------------------------ 款号读

    async def list_styles(self, query: StyleQuery) -> tuple[list[StyleListOut], int]:
        """款号分页列表。

        第一行就是 ``apply_data_scope``（docs/07 §3.2 铁律 1）：跟单（``SELF``）
        只能看到 ``merchandiser_id`` 是自己的款号，其他人是 **0 条**（不是全部）。
        """
        query.validate()
        stmt = _style_list_stmt(self.ctx)
        if query.q:
            # ⚠️ 必须用与 ``idx_styles_trgm`` **同一个表达式**，否则 planner 匹配不上，
            #    04 §5.1 精心要求的模糊检索退化成全表扫且毫无征兆
            stmt = stmt.where(literal_column(STYLE_TRGM_QUALIFIED).ilike(f"%{query.q}%"))
        if query.is_active is not None:
            stmt = stmt.where(Style.is_active == query.is_active)
        if query.customer_id is not None:
            stmt = stmt.where(Style.customer_id == query.customer_id)
        if query.category_id is not None:
            stmt = stmt.where(Style.category_id == query.category_id)
        if query.merchandiser_id is not None:
            stmt = stmt.where(Style.merchandiser_id == query.merchandiser_id)

        column = getattr(Style, STYLE_SORT_WHITELIST[query.sort_by or "style_no"])
        direction = column.desc() if query.sort_order == "desc" else column.asc()
        stmt = stmt.order_by(direction, Style.style_no.asc())

        count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
        total = int((await self.session.execute(count_stmt)).scalar_one())
        offset = (query.page - 1) * query.size
        rows = (await self.session.execute(stmt.offset(offset).limit(query.size))).all()
        return [self._style_row(row) for row in rows], total

    async def list_options(
        self, keyword: str | None, size: int = MAX_OPTION_SIZE, offset: int = 0
    ) -> list[OptionOut]:
        """款号候选。**默认按 ``last_used_at DESC NULLS LAST``**（05 §9.5.2 常用优先）。"""
        if not 1 <= size <= MAX_OPTION_SIZE:
            raise BusinessError(
                ErrorCode.PARAM_INVALID, f"候选接口 size 必须在 1~{MAX_OPTION_SIZE} 之间"
            )
        if offset > MAX_OFFSET:
            raise BusinessError(ErrorCode.PARAM_INVALID, "offset 过大，请改用关键字搜索")
        stmt = apply_data_scope(select(Style), Style, self.ctx)
        if keyword:
            stmt = stmt.where(literal_column(STYLE_TRGM_QUALIFIED).ilike(f"%{keyword}%"))
        stmt = stmt.order_by(Style.last_used_at.desc().nullslast(), Style.style_no.asc())
        rows = (await self.session.execute(stmt.offset(offset).limit(size))).scalars().all()
        return [
            OptionOut(
                value=row.style_no,
                label=f"{row.style_no} {row.name}",
                sub=None if row.is_active else "已停用",
                disabled=not row.is_active,
            )
            for row in rows
        ]

    async def suggest(self, customer_id: UUID | None) -> str:
        """取一个建议款号（**不建档**）。

        Q-P0-04：款号由用户自定义，建议号只作参考。表单上的「生成建议号」按钮要的是
        "填进去让我改"，而 :meth:`create` 里那个建议号是**建档时**额外回一个 ——
        复用它等于每点一次按钮就多一个款号。

        ⚠️ 取号会消耗一个序号（`next_no + 1`）：这是设计上的取舍，见
        :class:`~app.modules.base.schemas.SuggestedStyleNoOut` 的注释。
        """
        return await self._suggest(customer_id)

    async def get_required(self, style_no: str, *, for_update: bool = False) -> Style:
        """取款号；不存在 → ``20001``，越权 → ``12002``。

        :param for_update: ``True`` 时加 ``FOR UPDATE``。**先加锁再校验 version**
            是乐观锁能生效的唯一顺序 —— 反过来两个并发请求会读到同一个 version
            并双双通过校验（TC-B31）。
        """
        code = normalize_style_no(style_no)
        stmt = select(Style).where(Style.style_no == code, Style.deleted_at.is_(None))
        if for_update:
            stmt = stmt.with_for_update()
        style = (await self.session.execute(stmt)).scalar_one_or_none()
        if style is None:
            raise BusinessError(
                ErrorCode.BASE_DATA_NOT_FOUND,
                f"款号 {code} 不存在或已删除",
                details={"style_no": code},
            )
        assert_in_scope(style, self.ctx)
        return style

    async def get_detail(self, style_no: str) -> StyleDetailOut:
        """款号详情 = 款号 + 色组 + 尺码 + 款号工序 + 现行价（设计稿 §4.5）。"""
        style = await self.get_required(style_no)
        row = (
            await self.session.execute(_style_list_stmt(self.ctx).where(Style.id == style.id))
        ).one()
        return StyleDetailOut(
            style=self._style_row(row),
            colors=await self._list_colors(style.style_no),
            sizes=await self._list_sizes(style.style_no),
            operations=await self.list_style_operations(style.style_no),
            current_rates=await self._current_rates(style.style_no),
        )

    async def _list_colors(self, style_no: str) -> list[StyleColorOut]:
        rows = (
            (
                await self.session.execute(
                    select(StyleColor)
                    .where(StyleColor.style_no == style_no, StyleColor.deleted_at.is_(None))
                    .order_by(StyleColor.color_group, StyleColor.color_code)
                )
            )
            .scalars()
            .all()
        )
        return [
            StyleColorOut(
                id=row.id,
                version=row.version,
                remark=row.remark,
                created_at=row.created_at,
                updated_at=row.updated_at,
                style_no=row.style_no,
                color_group=row.color_group,
                color_code=row.color_code,
                color_name=row.color_name,
                material_color_code=row.material_color_code,
            )
            for row in rows
        ]

    async def _list_sizes(self, style_no: str) -> list[StyleSizeOut]:
        rows = (
            (
                await self.session.execute(
                    select(StyleSize)
                    .where(StyleSize.style_no == style_no, StyleSize.deleted_at.is_(None))
                    .order_by(StyleSize.sort_no, StyleSize.size_code)
                )
            )
            .scalars()
            .all()
        )
        return [
            StyleSizeOut(
                id=row.id,
                version=row.version,
                remark=row.remark,
                created_at=row.created_at,
                updated_at=row.updated_at,
                style_no=row.style_no,
                size_code=row.size_code,
                size_name=row.size_name,
                sort_no=row.sort_no,
            )
            for row in rows
        ]

    async def style_size_codes(self, style_no: str) -> set[str]:
        """该款已定义的尺码集合（比例缺配提示与 20007 校验的共同依据）。"""
        rows = (
            await self.session.execute(
                select(StyleSize.size_code).where(
                    StyleSize.style_no == style_no, StyleSize.deleted_at.is_(None)
                )
            )
        ).scalars()
        return set(rows)

    @staticmethod
    def _style_row(row: tuple[Any, ...]) -> StyleListOut:
        # ⚠️ 这里取的是 ``add_columns`` 出来的**标量列**（客户名 / 分类名），
        # 不是 Customer / ProductCategory 实体 —— 写成实体解包会得到 str
        style, customer_name, category_name = row
        return StyleListOut(
            id=style.id,
            version=style.version,
            remark=style.remark,
            created_at=style.created_at,
            updated_at=style.updated_at,
            style_no=style.style_no,
            name=style.name,
            category_id=style.category_id,
            category_name=category_name,
            customer_id=style.customer_id,
            customer_name=customer_name,
            merchandiser_id=style.merchandiser_id,
            is_active=style.is_active,
            last_used_at=style.last_used_at,
        )

    def _style_out(self, style: Style, suggested: str | None = None) -> StyleOut:
        return StyleOut(
            id=style.id,
            version=style.version,
            remark=style.remark,
            created_at=style.created_at,
            updated_at=style.updated_at,
            style_no=style.style_no,
            name=style.name,
            category_id=style.category_id,
            customer_id=style.customer_id,
            customer_style_no=style.customer_style_no,
            bulk_qty=style.bulk_qty,
            merchandiser_id=style.merchandiser_id,
            last_used_at=style.last_used_at,
            is_active=style.is_active,
            suggested_style_no=suggested,
        )

    # ------------------------------------------------------------ 款号写

    async def create(self, payload: StyleCreate) -> StyleOut:
        """新建款号。款号**必填且用户自定义**（业务方 2026-10-03 决策 Q-P0-04）。

        三条口径：

        1. 款号**去空白 + 转大写**（R1 大小写不敏感唯一，TC-B06）。归一必须在
           service 入口做：只靠数据库唯一索引会出现"查重查得到、插不进去"。
        2. 重复 → ``10001`` + **回显已有款名**（TC-B07）。回显让用户知道撞的是哪
           一款，而不是自己去列表里找。
        3. 并发下两个请求同时通过上面的查重时，唯一索引兜底 → ``20002``
           （CC-1：20 并发建同款号，成功 1 个，其余 20002）。
        """
        style_no = normalize_style_no(payload.style_no)
        existing = await self._find_by_no(style_no)
        if existing is not None:
            raise BusinessError(
                ErrorCode.PARAM_INVALID,
                f"款号 {style_no} 已存在（款名：{existing.name}），请换一个款号",
                details={"style_no": style_no, "existing_name": existing.name},
            )
        await self._assert_category_usable(payload.category_id)

        async with unit_of_work(self.session):
            suggested = (
                await self._suggest(payload.customer_id) if payload.suggest_style_no else None
            )
            style = Style(
                style_no=style_no,
                customer_id=payload.customer_id,
                customer_style_no=payload.customer_style_no,
                name=payload.name,
                bulk_qty=payload.bulk_qty,
                category_id=payload.category_id,
                merchandiser_id=payload.merchandiser_id,
                remark=payload.remark,
                created_by=self.ctx.user_id,
                updated_by=self.ctx.user_id,
            )
            self.session.add(style)
            try:
                await self.session.flush()
            except IntegrityError as exc:
                # 唯一索引兜底（CC-1：并发下两个请求同时通过上面的查重）
                raise self._duplicate_style_error(style_no) from exc
            await write_document_log(
                self.session,
                self.ctx,
                doc_type=DOC_STYLE,
                doc_id=style.id,
                doc_no=style_no,
                action=ACTION_CREATE,
                reason="新建款号",
                changed_fields={
                    "style_no": style_no,
                    "name": payload.name,
                    "category_id": str(payload.category_id),
                    "customer_id": str(payload.customer_id or ""),
                    "suggested_style_no": suggested,
                },
            )
        return self._style_out(style, suggested)

    async def patch(self, style_no: str, payload: StylePatch) -> StyleOut:
        """改款号。``version`` 必填且必须匹配，否则 ``10003``。

        ``style_no`` **不可改**（Schema 里没有这个字段）：历史单据按字符串冗余存款号，
        改号等于让所有历史单据指向一个不存在的款号。
        """
        code = normalize_style_no(style_no)
        style = await self.get_required(code)
        changes = {
            key: value
            for key, value in payload.model_dump().items()
            if key != "version" and value is not None
        }
        if not changes:
            raise BusinessError(ErrorCode.PARAM_INVALID, "没有需要更新的字段")
        if "category_id" in changes:
            await self._assert_category_usable(changes["category_id"])

        async with unit_of_work(self.session):
            stmt = (
                update(Style)
                .where(
                    Style.id == style.id,
                    Style.version == payload.version,
                    Style.deleted_at.is_(None),
                )
                .values(**changes, version=Style.version + 1, updated_by=self.ctx.user_id)
                .execution_options(synchronize_session=False)
            )
            result = await self.session.execute(stmt)
            if cast("CursorResult[Any]", result).rowcount == 0:
                raise BusinessError(
                    ErrorCode.OPTIMISTIC_LOCK_CONFLICT,
                    "款号已被他人修改，请刷新后重试",
                    details={"expected_version": payload.version},
                )
            await write_document_log(
                self.session,
                self.ctx,
                doc_type=DOC_STYLE,
                doc_id=style.id,
                doc_no=code,
                action=ACTION_UPDATE,
                reason="修改款号",
                changed_fields=changes,
            )
        await self.session.refresh(style)
        return self._style_out(style)

    async def add_colors(self, style_no: str, payload: StyleColorCreate) -> list[StyleColorOut]:
        """新增色组行。款号上已存在的 ``color_code`` → ``10001``（唯一索引兜底）。"""
        code = normalize_style_no(style_no)
        style = await self.get_required(code)
        async with unit_of_work(self.session):
            exists = (
                await self.session.execute(
                    select(func.count())
                    .select_from(StyleColor)
                    .where(StyleColor.style_no == code, StyleColor.color_code == payload.color_code)
                )
            ).scalar_one()
            if exists:
                raise BusinessError(
                    ErrorCode.PARAM_INVALID,
                    f"款号 {code} 已有色码 {payload.color_code}，请勿重复添加",
                    details={"style_no": code, "color_code": payload.color_code},
                )
            row = StyleColor(
                style_id=style.id,
                style_no=code,
                color_group=payload.color_group,
                color_code=payload.color_code,
                color_name=payload.color_name,
                material_color_code=payload.material_color_code,
                created_by=self.ctx.user_id,
                updated_by=self.ctx.user_id,
            )
            self.session.add(row)
            try:
                await self.session.flush()
            except IntegrityError as exc:
                raise BusinessError(
                    ErrorCode.PARAM_INVALID,
                    f"色码 {payload.color_code} 或色组 {payload.color_group} 已被占用",
                    details={
                        "color_code": payload.color_code,
                        "color_group": payload.color_group,
                    },
                ) from exc
            await write_document_log(
                self.session,
                self.ctx,
                doc_type=DOC_STYLE_COLOR,
                doc_id=row.id,
                doc_no=code,
                action=ACTION_CREATE,
                reason="新增款号色组",
                changed_fields={
                    "color_code": payload.color_code,
                    "color_group": payload.color_group,
                },
            )
        return [
            StyleColorOut(
                id=row.id,
                version=row.version,
                remark=row.remark,
                created_at=row.created_at,
                updated_at=row.updated_at,
                style_no=row.style_no,
                color_group=row.color_group,
                color_code=row.color_code,
                color_name=row.color_name,
                material_color_code=row.material_color_code,
            )
        ]

    async def add_sizes(self, style_no: str, payload: StyleSizeCreate) -> list[StyleSizeOut]:
        """新增款号尺码：单码，或按**码表一键带出整套**（modules/01 §5.3、TC-B09）。

        ``sort_no`` 从 1 起连续编号、码表按 ``sort_order`` 升序展开 —— 报表按
        ``sort_no`` 输出，编号不连续会让导出的尺码顺序看起来"跳了"。单码模式允许
        显式指定 ``sort_no``（"在码表基础上加一个 4XL"），整套带出时忽略它。
        """
        code = normalize_style_no(style_no)
        style = await self.get_required(code)
        async with unit_of_work(self.session):
            items = await self._resolve_size_items(payload)
            max_sort_no = int(
                (
                    await self.session.execute(
                        select(func.coalesce(func.max(StyleSize.sort_no), 0)).where(
                            StyleSize.style_no == code, StyleSize.deleted_at.is_(None)
                        )
                    )
                ).scalar_one()
            )
            single = payload.size_group_name is None
            for offset, item in enumerate(items, start=1):
                self.session.add(
                    StyleSize(
                        style_id=style.id,
                        style_no=code,
                        size_code=item[0],
                        size_name=item[1],
                        sort_no=(
                            payload.sort_no
                            if single and payload.sort_no is not None
                            else max_sort_no + offset
                        ),
                        created_by=self.ctx.user_id,
                        updated_by=self.ctx.user_id,
                    )
                )
            try:
                await self.session.flush()
            except IntegrityError as exc:
                raise BusinessError(
                    ErrorCode.PARAM_INVALID,
                    f"款号 {code} 的尺码与现有尺码重复，请检查后再添加",
                    details={
                        "style_no": code,
                        "size_codes": [item[0] for item in items],
                    },
                ) from exc
            for size_code, _size_name in items:
                await write_document_log(
                    self.session,
                    self.ctx,
                    doc_type=DOC_STYLE_SIZE,
                    doc_id=style.id,
                    doc_no=code,
                    action=ACTION_CREATE,
                    reason="新增款号尺码",
                    changed_fields={"style_code": size_code},
                )
        return await self._list_sizes(code)

    async def _resolve_size_items(self, payload: StyleSizeCreate) -> list[tuple[str, str]]:
        """把两种建尺码模式统一成有序的 ``[(size_code, size_name), ...]``。"""
        if payload.size_group_name is None:
            return [(payload.size_code or "", payload.size_name or "")]
        group = (
            await self.session.execute(
                select(SizeGroup).where(
                    SizeGroup.name == payload.size_group_name,
                    SizeGroup.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if group is None:
            raise BusinessError(
                ErrorCode.BASE_DATA_NOT_FOUND,
                f"尺码模板 {payload.size_group_name} 不存在",
                details={"size_group_name": payload.size_group_name},
            )
        rows = (
            await self.session.execute(
                select(Size.size_code, Size.name)
                .select_from(SizeGroupItem)
                .join(Size, Size.id == SizeGroupItem.size_id)
                .where(SizeGroupItem.size_group_id == group.id, Size.deleted_at.is_(None))
                .order_by(SizeGroupItem.sort_order, Size.sort_order)
            )
        ).all()
        if not rows:
            raise BusinessError(
                ErrorCode.BASE_DATA_NOT_FOUND,
                f"尺码模板 {group.name} 里还没有尺码，请先给码表添加成员",
                details={"size_group_name": group.name},
            )
        return [(row[0], row[1]) for row in rows]

    async def _suggest(self, customer_id: UUID | None) -> str:
        """生成建议号：客户编码做前缀；无客户落全厂序列（Q-P0-05 / Q-P0-10）。"""
        if customer_id is None:
            return await suggest_style_no(
                self.session, prefix=FACTORY_STYLE_PREFIX, customer_id=None
            )
        code = (
            await self.session.execute(
                select(Customer.code).where(
                    Customer.id == customer_id, Customer.deleted_at.is_(None)
                )
            )
        ).scalar_one_or_none()
        if code is None:
            raise BusinessError(
                ErrorCode.BASE_DATA_NOT_FOUND,
                "归属客户不存在或已删除，无法生成建议款号",
                details={"customer_id": str(customer_id)},
            )
        return await suggest_style_no(self.session, prefix=str(code), customer_id=customer_id)

    async def _find_by_no(self, style_no: str) -> Style | None:
        return (
            await self.session.execute(
                select(Style).where(Style.style_no == style_no, Style.deleted_at.is_(None))
            )
        ).scalar_one_or_none()

    async def _assert_category_usable(self, category_id: UUID) -> None:
        """分类必须存在且启用（04 §7.11：停用分类不参与新建款号）。"""
        found = (
            await self.session.execute(
                select(ProductCategory.id).where(
                    ProductCategory.id == category_id,
                    ProductCategory.deleted_at.is_(None),
                    ProductCategory.is_active.is_(True),
                )
            )
        ).first()
        if found is None:
            raise BusinessError(
                ErrorCode.PARAM_INVALID,
                "商品分类不存在或已停用，请重新选择",
                details={"category_id": str(category_id)},
            )

    def _duplicate_style_error(self, style_no: str) -> BusinessError:
        return BusinessError(
            ErrorCode.STYLE_ALREADY_EXISTS,
            f"款号 {style_no} 刚被他人创建，请换一个款号",
            details={"style_no": style_no},
        )

    # ------------------------------------------------------------ 比例

    async def list_ratios(
        self, style_no: str, color_code: str | None = None, size_code: str | None = None
    ) -> RatioListOut:
        """比例列表 + ``hands_total`` + ``missing_size_codes``。

        ⚠️ **不抛 20006**：R24 / ADR-0014 明确 20006 只在「该颜色**完全**没配比例」
        且发生在**裁剪侧**（带出建议 / submit / approve）时触发。比例接口自己抛它，
        用户就没法"先查一下这个颜色配了没有"再决定去录 —— 查询必须永远能返回空集。
        """
        code = normalize_style_no(style_no)
        style = await self.get_required(code)
        rows = await self._load_ratios(style.style_no, color_code, size_code)
        return await self._ratio_payload(rows, code, color_code)

    async def _load_ratios(
        self, style_no: str, color_code: str | None = None, size_code: str | None = None
    ) -> list[StyleColorSizeRatio]:
        """读该款（可按颜色 / 尺码过滤的）比例活行，按 ``color_code, size_code`` 升序。

        ⚠️ UPDATE 之后**必须重查**再拼响应：``updated_at`` 带
        ``onupdate=func.now()``，数据库算出来的列会被 SQLAlchemy 标记为 expired，
        此时读属性就是一次隐式懒加载 —— ``AsyncSession`` 下不在 greenlet 里会抛
        ``MissingGreenlet``，而那是个极难定位的报错。
        """
        stmt = apply_data_scope(select(StyleColorSizeRatio), StyleColorSizeRatio, self.ctx)
        stmt = stmt.where(
            StyleColorSizeRatio.style_no == style_no,
            StyleColorSizeRatio.deleted_at.is_(None),
        )
        if color_code is not None:
            stmt = stmt.where(StyleColorSizeRatio.color_code == color_code)
        if size_code is not None:
            stmt = stmt.where(StyleColorSizeRatio.size_code == size_code)
        rows = await self.session.execute(
            stmt.order_by(StyleColorSizeRatio.color_code, StyleColorSizeRatio.size_code)
        )
        return list(rows.scalars().all())

    async def _ratio_payload(
        self,
        rows: Sequence[StyleColorSizeRatio],
        style_no: str,
        color_code: str | None,
    ) -> RatioListOut:
        configured = {row.size_code for row in rows}
        declared = await self.style_size_codes(style_no)
        total = sum((row.ratio for row in rows), Decimal("0"))
        return RatioListOut(
            items=[
                RatioOut(
                    id=row.id,
                    version=row.version,
                    remark=row.remark,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                    style_no=row.style_no,
                    color_code=row.color_code,
                    size_code=row.size_code,
                    ratio=numeric_str(row.ratio, SCALE_RATIO),
                )
                for row in rows
            ],
            hands_total=numeric_str(total, SCALE_RATIO),
            missing_size_codes=[] if color_code is None else sorted(declared - configured),
        )

    async def replace_ratios(self, payload: RatioReplaceIn) -> RatioListOut:
        """按 ``(style_no, color_code)`` **全量替换**比例（≤100 行）。

        并发口径（modules/01 §7、TC-B31）：事务内先锁**聚合行**
        （``styles ... FOR UPDATE``）再锁该维度的比例行（按 ``size_code`` 排序），
        然后才校验 ``version`` —— "先校验 version 再加锁"会让两个并发请求读到
        同一个 version 并双双通过校验，那就等于没有锁。

        ⚠️ **不物理删行**（04 §6.2.1 / ADR-0025）：运行账号 ``erp_app`` 被 REVOKE 了
        全部 DELETE，而"全量替换"靠 DELETE 实现的话，这个接口在生产上根本调不通。
        改用「**按唯一键 upsert + 多余行软删 + 用到软删键时复活**」实现：

        ==========================  ==========================================
        提交的行                     处理
        ==========================  ==========================================
        已有活行                    ``UPDATE`` 比例
        只有软删行（键还在）          **复活**（``deleted_at = NULL``）再 UPDATE
            —— 必须复活，因为 ``uq_style_color_size_ratios`` 不是部分索引，
            软删行仍占着 ``(style_no, color_code, size_code)``
        从没有过                   ``INSERT``
        现有活行但本次没提交         **软删**（``deleted_at``），行保留供审计
        ==========================  ==========================================

        可观察结果与"全删全插"一致（旧行不再出现在任何查询里），但遵守了
        "禁止物理删除业务数据"这条铁律。
        """
        code = normalize_style_no(payload.style_no)
        style = await self.get_required(code)
        if len(payload.items) > MAX_RATIO_ITEMS:
            raise BusinessError(
                ErrorCode.PARAM_INVALID,
                f"比例一次最多 {MAX_RATIO_ITEMS} 行，收到 {len(payload.items)} 行，请分批",
                details={"max_items": MAX_RATIO_ITEMS, "received": len(payload.items)},
            )
        declared = await self.style_size_codes(code)
        unknown = sorted({item.size_code for item in payload.items} - declared)
        if unknown:
            # R24 / 20007：比例出现款号没有的尺码 = 主数据脏数据，**硬拦**。
            # 不拦的话裁剪侧会带出一个不存在的尺码，整缸布的耗用与结转全错。
            raise BusinessError(
                ErrorCode.SIZE_RATIO_SIZE_MISMATCH,
                f"尺码 {', '.join(unknown)} 不在款号 {code} 的尺码集合内，请先加进款号尺码",
                details={"style_no": code, "unknown_size_codes": unknown},
            )
        duplicates = _duplicated([item.size_code for item in payload.items])
        if duplicates:
            raise BusinessError(
                ErrorCode.PARAM_INVALID,
                f"同一次提交里尺码 {', '.join(duplicates)} 重复了",
                details={"duplicated_size_codes": duplicates},
            )

        async with unit_of_work(self.session):
            await self.session.refresh(style, with_for_update=True)
            self._assert_aggregate_version(style, payload.version)
            existing = {
                row.size_code: row
                for row in (
                    await self.session.execute(
                        select(StyleColorSizeRatio)
                        .where(
                            StyleColorSizeRatio.style_no == code,
                            StyleColorSizeRatio.color_code == payload.color_code,
                        )
                        .order_by(StyleColorSizeRatio.size_code)
                        .with_for_update()
                    )
                )
                .scalars()
                .all()
            }
            # ⚠️ 先拍 before 快照再改：改了之后再读 row.ratio 拿到的是新值，
            # 那样的"变更记录"毫无审计价值（docs/04 §7.9）
            before = {
                row.size_code: str(row.ratio) for row in existing.values() if row.deleted_at is None
            }
            submitted = {item.size_code: item.ratio for item in payload.items}
            for size_code in sorted(submitted):
                ratio = submitted[size_code]
                row = existing.get(size_code)
                if row is None:
                    row = StyleColorSizeRatio(
                        style_id=style.id,
                        style_no=code,
                        color_code=payload.color_code,
                        size_code=size_code,
                        ratio=ratio,
                        created_by=self.ctx.user_id,
                        updated_by=self.ctx.user_id,
                    )
                    self.session.add(row)
                else:
                    row.ratio = ratio
                    row.deleted_at = None
                    row.updated_by = self.ctx.user_id
                    row.version = row.version + 1
            for size_code, row in existing.items():
                if size_code in submitted or row.deleted_at is not None:
                    continue
                row.deleted_at = datetime.now(tz=UTC)
                row.updated_by = self.ctx.user_id
                row.version = row.version + 1
            try:
                await self.session.flush()
            except IntegrityError as exc:
                raise BusinessError(
                    ErrorCode.PARAM_INVALID,
                    f"比例写入冲突（款号 {code} 颜色 {payload.color_code}），请刷新后重试",
                    details={"style_no": code, "color_code": payload.color_code},
                ) from exc
            await self._bump_aggregate(style)
            log_id = await write_document_log(
                self.session,
                self.ctx,
                doc_type=DOC_STYLE_RATIO,
                doc_id=style.id,
                doc_no=f"{code}/{payload.color_code}",
                action=ACTION_UPDATE,
                reason="全量替换尺码比例",
                changed_fields={
                    "style_no": code,
                    "color_code": payload.color_code,
                    "before": before,
                    "after": {size: str(value) for size, value in submitted.items()},
                    "removed_size_codes": sorted(before.keys() - submitted.keys()),
                },
            )
        result = await self._ratio_payload(
            await self._load_ratios(code, payload.color_code), code, payload.color_code
        )
        result.document_log_id = log_id
        return result

    def _assert_aggregate_version(self, style: Style, expected: int) -> None:
        if style.version != expected:
            raise BusinessError(
                ErrorCode.OPTIMISTIC_LOCK_CONFLICT,
                "该款号的比例或工序已被他人修改，请刷新后重试",
                details={"expected_version": expected, "current_version": style.version},
            )

    async def _bump_aggregate(self, style: Style) -> None:
        """子表全量替换后把聚合行 ``styles.version`` +1（见 :class:`RatioReplaceIn`）。

        ⚠️ 必须**同步刷新内存态**：session 配了 ``expire_on_commit=False``
        （docs/03 §1.4，接口层少一次查询），所以条件 UPDATE 之后 identity map 里
        那份对象仍是旧 version。同一个请求里接着改第二张子表就会误判成 ``10003``，
        而用户那边看到的是"刚保存成功就报冲突"。
        """
        await self.session.execute(
            update(Style)
            .where(Style.id == style.id, Style.version == style.version)
            .values(version=Style.version + 1, updated_by=self.ctx.user_id)
            .execution_options(synchronize_session=False)
        )
        style.version = style.version + 1

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

    # -------------------------------------------------------- 模板复制

    async def copy_template(
        self, target_style_no: str, source_style_no: str, payload: TemplateCopyIn
    ) -> TemplateCopyOut:
        """工序与单价模板复制（modules/01 §5.1 / ADR-0009 §3 / ADR-0026 §4）。

        整批**单事务**：先校验源与目标，再写工序结构，最后写单价；任一环节失败整单
        回滚 —— "复制了一半"比完全没复制更坑（用户会以为已经好了）。

        ⚠️ **档位 2（分类价）不复制**（ADR-0026 §4）：分类价是全厂口径，复制一款
        顺手把它改掉属于静默改写全厂工资口径。跳过的行进 ``skipped[]``，并固定附上
        :data:`RATIO_NOT_COPIED_MESSAGE`。
        """
        target_code = normalize_style_no(target_style_no)
        source_code = normalize_style_no(source_style_no)
        if target_code == source_code:
            raise BusinessError(
                ErrorCode.PARAM_INVALID,
                "源款号与目标款号不能是同一个",
                details={"style_no": target_code},
            )
        target = await self.get_required(target_code, for_update=True)
        source = await self.get_required(source_code)
        if not source.is_active:
            raise BusinessError(
                ErrorCode.BASE_DATA_NOT_FOUND,
                f"源款号 {source_code} 已停用，不能作为复制来源",
                details={"source_style_no": source_code},
            )

        async with unit_of_work(self.session):
            source_operations = list(
                (
                    await self.session.execute(
                        select(StyleOperation)
                        .where(
                            StyleOperation.style_no == source_code,
                            StyleOperation.deleted_at.is_(None),
                        )
                        .order_by(StyleOperation.sequence, StyleOperation.operation_no)
                    )
                )
                .scalars()
                .all()
            )
            source_rates = list(
                (
                    await self.session.execute(
                        select(OperationRate).where(
                            # 档位 1（源款号专用价）+ 档位 2 / 3（``style_no`` 为空）。
                            # 档位 2 也要读进来 —— 只有读进来才能在 ``skipped[]``
                            # 里告诉用户"分类价没复制"（ADR-0026 §4 强制要求可解释）
                            or_(
                                OperationRate.style_no == source_code,
                                OperationRate.style_no.is_(None),
                            ),
                            OperationRate.effective_to.is_(None),
                            OperationRate.deleted_at.is_(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
            result = await self._copy_structure(target, source_operations, payload)
            await self._copy_prices(target, source_rates, payload, result)
            await self._bump_aggregate(target)
            log_id = await write_document_log(
                self.session,
                self.ctx,
                doc_type=DOC_STYLE_OPERATION_COPY,
                doc_id=target.id,
                doc_no=target_code,
                action=ACTION_CREATE,
                reason="工序与单价模板复制",
                changed_fields={
                    "source_style_no": source_code,
                    "target_style_no": target_code,
                    "copy_mode": payload.copy_mode.value,
                    "conflict_policy": payload.conflict_policy.value,
                    "price_ratio": str(payload.price_ratio or ""),
                    "structure_written": result.structure_written,
                    "structure_overwritten": result.structure_overwritten,
                    "price_written": result.price_written,
                    "price_overwritten": result.price_overwritten,
                    "skipped": result.skipped,
                },
            )
        messages = [RATIO_NOT_COPIED_MESSAGE]
        if result.skipped:
            messages.append(f"已跳过 {len(result.skipped)} 项，原因见 skipped 明细")
        return TemplateCopyOut(
            target_style_no=target_code,
            source_style_no=source_code,
            copy_mode=payload.copy_mode,
            conflict_policy=payload.conflict_policy,
            structure_written=result.structure_written,
            structure_overwritten=result.structure_overwritten,
            price_written=result.price_written,
            price_overwritten=result.price_overwritten,
            skipped=result.skipped,
            prices=result.prices,
            messages=messages,
            document_log_id=log_id,
            operations=await self.list_style_operations(target_code),
            rates=await self._current_rates(target_code),
        )

    async def _copy_structure(
        self,
        target: Style,
        source_operations: Sequence[StyleOperation],
        payload: TemplateCopyIn,
    ) -> TemplateCopyOut:
        existing = {
            row.operation_no: row
            for row in (
                await self.session.execute(
                    select(StyleOperation)
                    .where(StyleOperation.style_no == target.style_no)
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        }
        conflicts = [
            {"target": item.operation_no, "reason": "目标款号已配置该工序"}
            for item in source_operations
            if item.operation_no in existing
        ]
        if conflicts and payload.conflict_policy == ConflictPolicy.ABORT:
            raise BusinessError(
                ErrorCode.PARAM_INVALID,
                "目标款号已配置部分工序，已按 ABORT 整体回滚；请改用 SKIP 或 OVERWRITE",
                details={"conflicts": conflicts},
            )
        out = TemplateCopyOut(
            target_style_no=target.style_no,
            source_style_no="",
            copy_mode=payload.copy_mode,
            conflict_policy=payload.conflict_policy,
            structure_written=0,
        )
        for item in source_operations:
            conflict = existing.get(item.operation_no)
            # MERGE 与 SKIP 对工序结构同义：保留目标已有的，只补缺的。
            # 两者只在**单价**上不同（MERGE 一律不覆盖已有价）
            if conflict is not None and payload.conflict_policy in (
                ConflictPolicy.SKIP,
                ConflictPolicy.MERGE,
            ):
                out.skipped.append(
                    {"target": item.operation_no, "reason": SKIP_TARGET_HAS_OPERATION}
                )
                continue
            if conflict is None:
                self.session.add(
                    StyleOperation(
                        style_id=target.id,
                        style_no=target.style_no,
                        operation_no=item.operation_no,
                        sequence=item.sequence,
                        bundle_qty=item.bundle_qty,
                        is_piecework=item.is_piecework,
                        is_final_operation=item.is_final_operation,
                        remark=item.remark,
                        created_by=self.ctx.user_id,
                        updated_by=self.ctx.user_id,
                    )
                )
                out.structure_written += 1
                continue
            await self.session.execute(
                update(StyleOperation)
                .where(StyleOperation.id == conflict.id)
                .values(
                    sequence=item.sequence,
                    bundle_qty=item.bundle_qty,
                    is_piecework=item.is_piecework,
                    is_final_operation=item.is_final_operation,
                    remark=item.remark,
                    version=StyleOperation.version + 1,
                    updated_by=self.ctx.user_id,
                )
                .execution_options(synchronize_session=False)
            )
            out.structure_overwritten += 1
        await self.session.flush()
        return out

    async def _copy_prices(
        self,
        target: Style,
        source_rates: Sequence[OperationRate],
        payload: TemplateCopyIn,
        out: TemplateCopyOut,
    ) -> None:
        """复制单价。**必须在事务内调用**（写 ``operation_rates``）。"""
        if payload.copy_mode == TemplateCopyMode.COPY_STRUCTURE_ONLY:
            return
        copy_date = business_today()
        ratio = payload.price_ratio
        target_open = {
            row.operation_no: row
            for row in (
                await self.session.execute(
                    select(OperationRate)
                    .where(
                        OperationRate.style_no == target.style_no,
                        OperationRate.effective_to.is_(None),
                        OperationRate.deleted_at.is_(None),
                    )
                    .order_by(OperationRate.effective_from)
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        }
        target_dates = set(
            (
                await self.session.execute(
                    select(OperationRate.effective_from).where(
                        OperationRate.style_no == target.style_no,
                        OperationRate.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in source_rates:
            source_rate = rate_source_of(row)
            if source_rate == RateSource.CATEGORY:
                # ADR-0026 §4：分类价属全厂口径，**不复制**
                out.skipped.append({"target": row.operation_no, "reason": SKIP_CATEGORY_RATE})
                continue
            conflict = target_open.get(row.operation_no)
            if conflict is not None:
                if payload.conflict_policy in (ConflictPolicy.SKIP, ConflictPolicy.MERGE):
                    out.skipped.append({"target": row.operation_no, "reason": SKIP_TARGET_HAS_RATE})
                    continue
                if copy_date in target_dates:
                    # modules/01 §7：复制与调价并发，同生效日必撞唯一索引 →
                    # 后到者拿 20002，**不允许静默覆盖**
                    raise BusinessError(
                        ErrorCode.STYLE_ALREADY_EXISTS,
                        f"款号 {target.style_no} 的工序 {row.operation_no} "
                        f"在 {copy_date.isoformat()} 已有单价记录，无法覆盖",
                        details={
                            "style_no": target.style_no,
                            "operation_no": row.operation_no,
                            "effective_from": copy_date.isoformat(),
                        },
                    )
                await self.session.execute(
                    update(OperationRate)
                    .where(OperationRate.id == conflict.id, OperationRate.effective_to.is_(None))
                    .values(
                        effective_to=copy_date,
                        version=OperationRate.version + 1,
                        updated_by=self.ctx.user_id,
                    )
                    .execution_options(synchronize_session=False)
                )
                out.price_overwritten += 1
            new_price = row.unit_price if ratio is None else quantize_price(row.unit_price * ratio)
            self.session.add(
                OperationRate(
                    operation_no=row.operation_no,
                    style_id=target.id,
                    style_no=target.style_no,
                    product_category_id=None,
                    effective_from=copy_date,
                    effective_to=None,
                    unit_price=new_price,
                    reason=f"模板复制自 {row.style_no or '全厂通用价'}（{copy_date.isoformat()}）",
                    created_by=self.ctx.user_id,
                    updated_by=self.ctx.user_id,
                )
            )
            out.price_written += 1
            out.prices.append(
                CopiedPriceOut(
                    operation_no=row.operation_no,
                    source_unit_price=numeric_str(row.unit_price, SCALE_UNIT_PRICE),
                    target_unit_price=numeric_str(new_price, SCALE_UNIT_PRICE),
                    rate_source=source_rate,
                )
            )


# ------------------------------------------------------------------ 单价


class RateService:
    """工序单价：设价 / 调价 / 取价 / 历史（ADR-0026）。"""

    def __init__(self, session: AsyncSession, ctx: AuthContext) -> None:
        self.session = session
        self.ctx = ctx

    # -------------------------------------------------------------- 读

    async def list_rates(self, query: RateQuery) -> tuple[list[OperationRateOut], int]:
        """历史区间列表 + 派生 ``is_current``。"""
        query.validate()
        stmt = await self._filtered(query)
        column = getattr(OperationRate, RATE_SORT_WHITELIST[query.sort_by or "effective_from"])
        direction = column.desc() if query.sort_order == "desc" else column.asc()
        stmt = stmt.order_by(direction, OperationRate.operation_no.asc())

        count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
        total = int((await self.session.execute(count_stmt)).scalar_one())
        offset = (query.page - 1) * query.size
        rows = list(
            (await self.session.execute(stmt.offset(offset).limit(query.size))).scalars().all()
        )
        return [rate_out(row) for row in rows], total

    async def _filtered(self, query: RateQuery) -> Select[OperationRate]:
        """单价列表 / 导出**共用**的筛选条件（docs/07 §3.2 铁律 3）。

        ⚠️ 必须共用：另写一条导出路径的话，"列表看到的"与"导出的"会不一致 ——
        那类问题只有在对账时才发现，而那时已经导出并入账了。

        数据范围：``style_no`` 给了就按款号校验（越权 → ``12002``）；没给时，
        非全厂范围的用户只能看到「自己款号的档位 1」+「全厂口径的档位 2/3」。
        """
        stmt = apply_data_scope(select(OperationRate), OperationRate, self.ctx)
        if query.style_no is not None:
            style = await StyleService(self.session, self.ctx).get_required(query.style_no)
            stmt = stmt.where(OperationRate.style_no == style.style_no)
        elif not self.ctx.is_factory_scoped:
            visible = await self._visible_style_nos()
            stmt = stmt.where(
                or_(
                    OperationRate.style_no.is_(None),
                    OperationRate.style_no.in_(visible),
                )
            )
        if query.operation_no is not None:
            stmt = stmt.where(OperationRate.operation_no == query.operation_no)
        if query.product_category_id is not None:
            stmt = stmt.where(OperationRate.product_category_id == query.product_category_id)
        if query.effective_from is not None:
            stmt = stmt.where(OperationRate.effective_from >= query.effective_from)
        if query.effective_to is not None:
            stmt = stmt.where(OperationRate.effective_from <= query.effective_to)
        return stmt

    async def export_rates(self, query: RateQuery) -> list[OperationRateOut]:
        """导出：**与列表同一套筛选**、不分页（docs/07 §3.2 铁律 3、docs/05 §9.1）。

        行数上限 ``11011``：超了直接拒绝而不是截断 —— 截断出来的导出会让用户
        以为导全了，那比报错危险得多。
        """
        query.validate()
        stmt = await self._filtered(query)
        column = getattr(OperationRate, RATE_SORT_WHITELIST[query.sort_by or "effective_from"])
        direction = column.desc() if query.sort_order == "desc" else column.asc()
        stmt = stmt.order_by(direction, OperationRate.operation_no.asc())
        total = int(
            (
                await self.session.execute(
                    select(func.count()).select_from(stmt.order_by(None).subquery())
                )
            ).scalar_one()
        )
        if total > MAX_EXPORT_ROWS:
            raise BusinessError(
                ErrorCode.EXPORT_RANGE_TOO_LARGE,
                f"导出结果 {total} 行超过上限 {MAX_EXPORT_ROWS}，请缩小筛选范围",
                details={"row_count": total, "max_rows": MAX_EXPORT_ROWS},
            )
        rows = list((await self.session.execute(stmt)).scalars().all())
        return [rate_out(row) for row in rows]

    async def _visible_style_nos(self) -> list[str]:
        """当前用户在数据范围内可见的款号（只查款号列，不加载整行）。"""
        stmt = apply_data_scope(select(Style.style_no), Style, self.ctx)
        return [normalize_style_no(item) for item in (await self.session.execute(stmt)).scalars()]

    async def resolve(self, style_no: str, operation_no: str, work_date: date) -> RateResolveOut:
        """取价预演。**只读不写库**（modules/01 §6 末条）。

        SQL 见 :func:`build_rate_resolve_stmt`（ADR-0026 §2，勿改）。
        """
        code = normalize_style_no(style_no)
        style = await self._require_style(code)
        stmt = build_rate_resolve_stmt(
            operation_no=operation_no,
            style_no=code,
            category_id=style.category_id,
            work_date=work_date,
        )
        hit = (await self.session.execute(stmt)).scalar_one_or_none()
        if hit is None:
            raise BusinessError(
                ErrorCode.OPERATION_RATE_NOT_SET,
                f"款号 {code} 的工序 {operation_no} 在 {work_date.isoformat()} 没有生效单价，请先设价",
                details={
                    "style_no": code,
                    "operation_no": operation_no,
                    "work_date": work_date.isoformat(),
                },
            )
        return RateResolveOut(
            style_no=code,
            operation_no=operation_no,
            work_date=work_date,
            unit_price=numeric_str(hit.unit_price, SCALE_UNIT_PRICE),
            effective_from=hit.effective_from,
            effective_to=hit.effective_to,
            rate_source=rate_source_of(hit),
            product_category_id=hit.product_category_id,
        )

    async def _require_style(self, style_no: str) -> Style:
        """款号必须存在**且在数据范围内**。

        走 :meth:`StyleService.get_required`（先查、再 ``assert_in_scope``）而不是
        「带数据范围过滤的查询」：后者会把"款号存在但不属于我"与"款号根本不存在"
        一起变成 ``20001``，前端于是提示"款号不存在"，用户去列表里找不到就更加
        困惑。口径必须与款号详情一致：越权 ``12002``、不存在 ``20001``。
        """
        return await StyleService(self.session, self.ctx).get_required(style_no)

    # -------------------------------------------------------------- 写

    async def set_rate(self, payload: OperationRateCreate) -> OperationRateSetOut:
        """设价（首次）与调价（关旧区间 + 插新区间）。

        完整口径：

        1. **档位互斥**：``style_no`` 与 ``product_category_id`` 不能同时给
           （Schema 已拦，这里再兜一层防绕过直调）。两者都空 = 档位 3 全厂统一价。
        2. **同一生效日已有行 → ``20002``**：追加式模型下"改今天的价"必须换一个
           生效日，禁 ``UPDATE unit_price``（R11 / INV-P0-3）。
        3. **区间重叠 → ``20005`` + ``details``**（R18）。唯一能重叠的形态是
           "旧的当前档 + 新区间从它中间开始"，那就是正常调价 → 关掉旧档。
        4. **调价必填 ``reason`` → ``10006``**（R20）。首次设价可空。
        5. 区间行按 ``effective_from`` 排序 ``FOR UPDATE``，防并发插出两条
           ``effective_to IS NULL``（CC-4）。
        """
        operation_no = payload.operation_no
        style_no = normalize_style_no(payload.style_no) if payload.style_no else None
        if style_no is not None and payload.product_category_id is not None:
            raise BusinessError(
                ErrorCode.PARAM_INVALID,
                "style_no 与 product_category_id 不能同时给：一个价要么限款号、要么限分类",
                details={"style_no": style_no},
            )
        style = await self._resolve_target_style(style_no, payload.product_category_id)

        async with unit_of_work(self.session):
            existing = list(
                (
                    await self.session.execute(
                        select(OperationRate)
                        .where(
                            OperationRate.operation_no == operation_no,
                            OperationRate.style_no.is_(None)
                            if style_no is None
                            else OperationRate.style_no == style_no,
                            OperationRate.product_category_id.is_(None)
                            if payload.product_category_id is None
                            else OperationRate.product_category_id == payload.product_category_id,
                            OperationRate.deleted_at.is_(None),
                        )
                        .order_by(OperationRate.effective_from)
                        .with_for_update()
                    )
                )
                .scalars()
                .all()
            )
            closed = await self._reconcile_intervals(existing, payload)
            if closed and not (payload.reason or "").strip():
                raise BusinessError(
                    ErrorCode.REASON_REQUIRED,
                    "调价必须填写原因（reason），说明为什么调",
                    details={"operation_no": operation_no, "style_no": style_no},
                )
            row = OperationRate(
                operation_no=operation_no,
                style_id=style.id if style else None,
                style_no=style_no,
                product_category_id=payload.product_category_id,
                effective_from=payload.effective_from,
                effective_to=payload.effective_to,
                unit_price=payload.unit_price,
                reason=payload.reason,
                created_by=self.ctx.user_id,
                updated_by=self.ctx.user_id,
            )
            self.session.add(row)
            try:
                await self.session.flush()
            except IntegrityError as exc:
                # 并发下两个请求同生效日插入（CC-4）：``uq_operation_rates`` 兜底
                raise BusinessError(
                    ErrorCode.STYLE_ALREADY_EXISTS,
                    "该生效日已有一条单价记录，可能刚被他人调整，请刷新后重试",
                    details={
                        "style_no": style_no,
                        "operation_no": operation_no,
                        "effective_from": payload.effective_from.isoformat(),
                    },
                ) from exc
            log_id = await write_document_log(
                self.session,
                self.ctx,
                doc_type=DOC_OPERATION_RATE,
                doc_id=row.id,
                doc_no=f"{style_no or 'ALL'}/{operation_no}",
                action=ACTION_UPDATE,
                reason=payload.reason or "首次设价",
                changed_fields={
                    "rate_source": rate_source_of(row).value,
                    "style_no": style_no,
                    "product_category_id": str(payload.product_category_id or ""),
                    "operation_no": operation_no,
                    "effective_from": payload.effective_from.isoformat(),
                    "effective_to": payload.effective_to.isoformat()
                    if payload.effective_to
                    else None,
                    "unit_price": str(payload.unit_price),
                    "closed": [
                        {
                            "effective_from": item.effective_from.isoformat(),
                            "effective_to": payload.effective_from.isoformat(),
                            "unit_price": str(item.unit_price),
                        }
                        for item in closed
                    ],
                },
            )
        # ⚠️ ``closed`` 里的行刚被 UPDATE 过：``updated_at`` 由数据库算出、
        #    被标记为 expired，此时读属性就是隐式懒加载（AsyncSession 下抛
        #    MissingGreenlet）。所以重查一次再拼响应 —— 也顺带让回显的
        #    ``effective_to`` 是真正落库的值
        closed_out: list[OperationRateOut] = []
        if closed:
            rows = (
                (
                    await self.session.execute(
                        select(OperationRate)
                        .where(OperationRate.id.in_([item.id for item in closed]))
                        .order_by(OperationRate.effective_from)
                    )
                )
                .scalars()
                .all()
            )
            closed_out = [rate_out(item) for item in rows]
        return OperationRateSetOut(
            rate=rate_out(row),
            closed_rates=closed_out,
            document_log_id=log_id,
        )

    async def _reconcile_intervals(
        self, existing: Sequence[OperationRate], payload: OperationRateCreate
    ) -> list[OperationRate]:
        """区间重叠判定 + 关旧区间；返回本次被关闭的行。**必须在事务内调用**。

        "先查区间再插入"**不足以防重**（docs/03 §1.5）：加锁由调用方的
        ``SELECT ... FOR UPDATE`` 提供，唯一性最后由 ``uq_operation_rates`` 兜底。
        """
        new_from = payload.effective_from
        new_to = payload.effective_to
        same_day = [row for row in existing if row.effective_from == new_from]
        if same_day:
            raise BusinessError(
                ErrorCode.STYLE_ALREADY_EXISTS,
                f"工序 {payload.operation_no} 在 {new_from.isoformat()} 已有单价记录；"
                "单价只追加不修改，请换一个生效日",
                details={
                    "operation_no": payload.operation_no,
                    "effective_from": new_from.isoformat(),
                    "existing_unit_price": str(same_day[0].unit_price),
                },
            )
        overlapping: list[OperationRate] = []
        for row in existing:
            # 半开区间 ``[f, t)`` 相交判定：新区间完全在既有区间之前，或完全在其后
            before = new_to is not None and row.effective_from >= new_to
            after = row.effective_to is not None and row.effective_to <= new_from
            if before or after:
                continue
            overlapping.append(row)

        # 唯一可关的形态：既有的"当前档"（effective_to IS NULL）且起点早于新区间
        closable = [
            row for row in overlapping if row.effective_to is None and row.effective_from < new_from
        ]
        closable_ids = {row.id for row in closable}
        conflicting = [row for row in overlapping if row.id not in closable_ids]
        if conflicting:
            raise BusinessError(
                ErrorCode.RATE_RANGE_OVERLAP,
                f"工序 {payload.operation_no} 的生效区间与既有区间重叠，请调整生效日",
                details={
                    "operation_no": payload.operation_no,
                    "new_interval": {
                        "effective_from": new_from.isoformat(),
                        "effective_to": new_to.isoformat() if new_to else None,
                    },
                    "conflicts": [
                        {
                            "effective_from": row.effective_from.isoformat(),
                            "effective_to": row.effective_to.isoformat()
                            if row.effective_to
                            else None,
                            "unit_price": str(row.unit_price),
                        }
                        for row in conflicting
                    ],
                },
            )
        for row in closable:
            await self.session.execute(
                update(OperationRate)
                .where(OperationRate.id == row.id, OperationRate.effective_to.is_(None))
                .values(
                    effective_to=new_from,
                    version=OperationRate.version + 1,
                    updated_by=self.ctx.user_id,
                )
                .execution_options(synchronize_session=False)
            )
            # 同步内存态：``synchronize_session=False`` 不会刷新 ORM 对象，
            # 而响应里的 ``closed_rates`` 直接读它 —— 不同步就会回显旧区间
            row.effective_to = new_from
        await self.session.flush()
        return closable

    async def _resolve_target_style(
        self, style_no: str | None, category_id: UUID | None
    ) -> Style | None:
        """校验档位目标存在（款号 / 分类），并校验款号数据范围。"""
        if style_no is not None:
            return await self._require_style(style_no)
        if category_id is not None:
            found = (
                await self.session.execute(
                    select(ProductCategory.id).where(
                        ProductCategory.id == category_id,
                        ProductCategory.deleted_at.is_(None),
                    )
                )
            ).first()
            if found is None:
                raise BusinessError(
                    ErrorCode.BASE_DATA_NOT_FOUND,
                    "商品分类不存在或已删除",
                    details={"product_category_id": str(category_id)},
                )
        return None


__all__ = [
    "ACTION_CREATE",
    "ACTION_DELETE",
    "ACTION_UPDATE",
    "DictService",
    "RateQuery",
    "RateService",
    "StyleQuery",
    "StyleService",
    "build_rate_resolve_stmt",
]
