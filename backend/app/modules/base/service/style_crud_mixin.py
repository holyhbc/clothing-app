"""款号主表 CRUD + 建议号 / 查重 / 分类校验（原 service.py 1024-1197、1363-1414）。"""

from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import unit_of_work
from app.core.errors import BusinessError, ErrorCode
from app.core.numbering import FACTORY_STYLE_PREFIX, normalize_style_no, suggest_style_no
from app.core.permissions import AuthContext
from app.modules.base.models import Customer, ProductCategory, Style
from app.modules.base.schemas import StyleCreate, StyleOut, StylePatch

from .common import ACTION_CREATE, ACTION_UPDATE, DOC_STYLE, write_document_log


class StyleCrudMixin:
    session: AsyncSession
    ctx: AuthContext

    if TYPE_CHECKING:

        async def get_required(self, style_no: str, *, for_update: bool = False) -> Style: ...

        def _style_out(self, style: Style, suggested: str | None = None) -> StyleOut: ...

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

    async def disable(self, style_no: str, reason: str, version: int) -> StyleOut:
        """停用款号（R2：**不允许新建裁剪/打菲单，历史照常**）。

        ⚠️ 为什么是独立端点而不是靠 ``PATCH is_active``：停用有三重语义 ——
        原因必填（§4.4）、要写 ``document_logs``、且是**不可逆方向的单向动作**。
        让它走通用 PATCH 的话，这三条都可能被绕过（PATCH 里 ``is_active`` 只是
        一个普通字段，谁都能顺手改，且没原因）。同理也不复用字典的
        ``POST /{key}/{code}/disables``：那条路径的键是「业务编码列」，而款号的
        停用还要求 ``version`` 做乐观锁（有人在别的界面上刚改过款号时不该被覆盖）。

        ⚠️ **不做启用端点**：款号停用后的恢复走 PATCH（``is_active=true``），
        由通用修改的审计链路记录 —— 现场不会「误停用要立刻撤销」，但会
        「季度重开一个款号」，走同一条修改路径更省事，也不会多出第二种权限语义。
        """
        code = normalize_style_no(style_no)
        if not reason or not reason.strip():
            raise BusinessError(ErrorCode.MISSING_BUSINESS_PARAM, "停用必须填写原因（reason）")
        style = await self.get_required(code)
        if not style.is_active:
            raise BusinessError(
                ErrorCode.ILLEGAL_OPERATION, f"款号 {code} 已经是停用状态，无需重复停用"
            )

        async with unit_of_work(self.session):
            stmt = (
                update(Style)
                .where(
                    Style.id == style.id,
                    Style.version == version,
                    Style.deleted_at.is_(None),
                    Style.is_active.is_(True),
                )
                .values(
                    is_active=False,
                    remark=reason[:500],
                    version=Style.version + 1,
                    updated_by=self.ctx.user_id,
                )
                .execution_options(synchronize_session=False)
            )
            result = await self.session.execute(stmt)
            if cast("CursorResult[Any]", result).rowcount == 0:
                # ⚠️ 与 PATCH 同一判据，但文案不同：这里更可能是「刚被别人停用」，
                #    笼统说「已被他人修改」会让用户以为是款名/分类被改了
                raise BusinessError(
                    ErrorCode.OPTIMISTIC_LOCK_CONFLICT,
                    "款号已被他人修改或已被停用，请刷新后重试",
                    details={"expected_version": version},
                )
            await write_document_log(
                self.session,
                self.ctx,
                doc_type=DOC_STYLE,
                doc_id=style.id,
                doc_no=code,
                action=ACTION_UPDATE,
                reason=reason,
                changed_fields={"is_active": False},
            )
        await self.session.refresh(style)
        return self._style_out(style)

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
