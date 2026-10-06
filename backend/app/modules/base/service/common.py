"""base service 拆分后的共享常量、查询参数、校验与审计助手。

本模块**不 import 任何 mixin / service**，只依赖 common / core / models 层，
以切断循环导入（设计稿 §2.1）。所有常量与函数逐字来自原 ``base/service.py``。
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.models import DocumentLog
from app.core.errors import BusinessError, ErrorCode
from app.core.numbering import quantize_price
from app.core.permissions import AuthContext
from app.modules.auth.models import User

logger = logging.getLogger("app.base")


#: ``document_logs.action`` 取值（docs/08 §1.1 的 DocumentAction）
ACTION_CREATE = "CREATE"
ACTION_UPDATE = "UPDATE"
ACTION_DELETE = "DELETE"
ACTION_RESTORE = "RESTORE"


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


def _duplicated(values: Sequence[str]) -> list[str]:
    """保序返回重复项（用于"同一次提交里重复了"的报错文案）。"""
    seen: set[str] = set()
    duplicated: list[str] = []
    for value in values:
        if value in seen and value not in duplicated:
            duplicated.append(value)
        seen.add(value)
    return duplicated
