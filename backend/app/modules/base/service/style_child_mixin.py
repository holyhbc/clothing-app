"""款号色码 / 尺码追加（原 service.py 1199-1361）。"""

from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import unit_of_work
from app.core.errors import BusinessError, ErrorCode
from app.core.numbering import normalize_style_no
from app.core.permissions import AuthContext
from app.modules.base.models import Size, SizeGroup, SizeGroupItem, Style, StyleColor, StyleSize
from app.modules.base.schemas import StyleColorCreate, StyleColorOut, StyleSizeCreate, StyleSizeOut

from .common import ACTION_CREATE, DOC_STYLE_COLOR, DOC_STYLE_SIZE, write_document_log


class StyleChildMixin:
    session: AsyncSession
    ctx: AuthContext

    if TYPE_CHECKING:

        async def get_required(self, style_no: str, *, for_update: bool = False) -> Style: ...

        async def _list_sizes(self, style_no: str) -> list[StyleSizeOut]: ...

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
