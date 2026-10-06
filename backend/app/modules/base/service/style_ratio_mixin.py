"""款号比例全量替换 + 聚合版本（原 service.py 1416-1639）。"""

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import unit_of_work
from app.core.errors import BusinessError, ErrorCode
from app.core.numbering import normalize_style_no
from app.core.permissions import AuthContext
from app.core.scope import apply_data_scope
from app.modules.base.models import Style, StyleColorSizeRatio
from app.modules.base.schemas import RatioListOut, RatioOut, RatioReplaceIn

from .common import (
    ACTION_UPDATE,
    DOC_STYLE_RATIO,
    MAX_RATIO_ITEMS,
    SCALE_RATIO,
    _duplicated,
    numeric_str,
    write_document_log,
)


class StyleRatioMixin:
    session: AsyncSession
    ctx: AuthContext

    if TYPE_CHECKING:

        async def get_required(self, style_no: str, *, for_update: bool = False) -> Style: ...

        async def style_size_codes(self, style_no: str) -> set[str]: ...

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
