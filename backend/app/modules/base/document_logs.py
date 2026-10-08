"""变更历史（审计日志）查询（docs/06 §2.3「变更历史 Drawer」）。

⚠️ **这张表写了半年却没人读过**：`document_logs` 由迁移 0001 建出、每张单据与每条主数据
变更都在写它（`write_document_log`），连索引 `idx_document_logs_doc (doc_type, doc_id,
created_at DESC)` 都是**照着详情页的查询形态**建的 —— 但没有任何一个端点读它。结果是
审计数据只进不出：出了纠纷想查"谁在什么时候把单价从 0.35 改成 0.30"，只能直连数据库。
本模块补上那个读者。

## 为什么必须显式登记数据范围，而不是"照抄列表接口的过滤"

审计表**没有** `workshop_id` / `merchandiser_id` 这类归属列，所以 `apply_data_scope`
对它无能为力（`core/scope.py` 的守卫会直接抛"表未登记"）。而"跟单只能看自己的款号"
是硬要求（Q-P0-05 / docs/07 §3.2）。这里的做法是**反查归属**：

    doc_type ∈ 款号系 → 用 doc_no 反查款号 → 走 StyleService 的可见性判定
    否则             → 非全厂范围一律 12002

⚠️ 判定放在**读日志之前**而不是"读了再过滤"：后者在页面上表现为"跟单打开详情看到一片
空白"，而用户会以为是系统坏了。先判权限、直接报"无数据权限"才是真的原因。
"""

from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.models import DocumentLog
from app.core.errors import BusinessError, ErrorCode
from app.core.permissions import AuthContext
from app.core.responses import PageData
from app.modules.base.schemas import DocumentLogOut

#: 分页上限。审计行数随操作量增长，但一个单据的历史不会到几百行 —— 上限给 100 足够，
#: 且防止"查全部日志"把整张 append-only 表拉进内存。
MAX_PAGE_SIZE = 100

#: 与款号相关的 ``doc_type``。这些的 ``doc_no`` 就是款号（``OperationRate`` 除外，
#: 它的 ``doc_no`` 形如 ``HB-2026-0001/01``，取斜杠前一段）。
STYLE_DOC_TYPES: frozenset[str] = frozenset(
    {
        "Style",
        "StyleColor",
        "StyleSize",
        "StyleColorSizeRatio",
        "StyleOperation",
        "StyleOperationCopy",
    }
)

#: 单价日志：``doc_type='OperationRate'``，``doc_no='{款号或ALL}/{工序号}'``
RATE_DOC_TYPE = "OperationRate"


def _style_no_of(doc_type: str, doc_no: str) -> str | None:
    """从日志的 ``doc_type`` / ``doc_no`` 反出款号；不是款号系则返回 ``None``。"""
    if doc_type in STYLE_DOC_TYPES:
        return doc_no
    if doc_type == RATE_DOC_TYPE:
        # `ALL/01` = 全厂通用价的工序 01，**不属于任何款号** → 跟单不该看到
        return doc_no.split("/", 1)[0] if "/" in doc_no else None
    return None


async def list_document_logs(
    session: AsyncSession,
    ctx: AuthContext,
    *,
    doc_type: str,
    doc_no: str,
    page: int = 1,
    size: int = 20,
) -> PageData[DocumentLogOut]:
    """按 ``(doc_type, doc_no)`` 查某一张单据 / 一条主数据的变更历史。

    :raises BusinessError: 参数不合法 → ``10001``；无权看这份数据 → ``12002``
    """
    if not doc_type.strip() or not doc_no.strip():
        raise BusinessError(ErrorCode.PARAM_INVALID, "doc_type 与 doc_no 均为必填")
    if page < 1 or size < 1 or size > MAX_PAGE_SIZE:
        raise BusinessError(
            ErrorCode.PARAM_INVALID,
            f"page_size 必须在 1~{MAX_PAGE_SIZE} 之间，收到 {size}",
        )

    # ⚠️ 权限判定在**查询之前**：日志本身没有归属列，判不出来；而"跟单打开别人的款号
    #    看到一片空白"会被误解成系统坏了（真实原因是无权看，不是没数据）。
    if not ctx.is_factory_scoped:
        style_no = _style_no_of(doc_type, doc_no)
        if style_no is None:
            raise BusinessError(
                ErrorCode.DATA_SCOPE_DENIED,
                "无数据权限查看该单据的变更历史",
                details={"doc_type": doc_type},
            )
        # 复用款号自身的可见性判定（不存在 → 20001，越权 → 12002），不重复实现一遍
        from app.modules.base.service import StyleService

        await StyleService(session, ctx).get_required(style_no)

    stmt = select(DocumentLog).where(
        DocumentLog.doc_type == doc_type,
        DocumentLog.doc_no == doc_no,
    )
    total = int(
        (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    )
    rows: list[Any] = list(
        (
            await session.execute(
                stmt.order_by(DocumentLog.created_at.desc(), DocumentLog.id.desc())
                .offset((page - 1) * size)
                .limit(size)
            )
        )
        .scalars()
        .all()
    )
    return PageData(
        items=[DocumentLogOut.model_validate(row).model_dump(mode="json") for row in rows],
        total=total,
        page=page,
        page_size=size,
    )


async def list_document_logs_by_doc(
    session: AsyncSession,
    *,
    doc_type: str,
    doc_id: UUID,
    page: int = 1,
    size: int = 20,
) -> PageData[DocumentLogOut]:
    """按 ``(doc_type, doc_id)`` 查某一张单据的变更历史（详情页抽屉的分页版）。

    ⚠️ **刻意不判数据范围**，与上面那个按 ``doc_no`` 查的变体相反 —— 那是刻意的分工：

    - ``doc_no`` 版是**通用入口**（``base:read`` + 只有款号可反查），范围只能在这里判；
    - 本函数是**从单据详情页调进来的**，归属单据本身已被 service 的
      ``assert_in_scope`` 验过（07 §3.2 铁律 2），再判一次只会逼着调用方拼一个假的
      ``is_factory_scoped`` —— 那才是把权限判定漏到 router 的老毛病。

    :raises BusinessError: 分页参数不合法 → ``10001``（与列表端点同一口径）
    """
    if page < 1 or size < 1 or size > MAX_PAGE_SIZE:
        raise BusinessError(
            ErrorCode.PARAM_INVALID,
            f"page_size 必须在 1~{MAX_PAGE_SIZE} 之间，收到 {size}",
        )
    stmt = select(DocumentLog).where(DocumentLog.doc_type == doc_type, DocumentLog.doc_id == doc_id)
    total = int(
        (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    )
    rows: list[Any] = list(
        (
            await session.execute(
                stmt.order_by(DocumentLog.created_at.desc(), DocumentLog.id.desc())
                .offset((page - 1) * size)
                .limit(size)
            )
        )
        .scalars()
        .all()
    )
    return PageData(
        items=[DocumentLogOut.model_validate(row).model_dump(mode="json") for row in rows],
        total=total,
        page=page,
        page_size=size,
    )
