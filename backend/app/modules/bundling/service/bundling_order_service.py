"""打菲单业务逻辑：**草稿态**（T-BUND-003）+ **轻状态迁移**（T-BUND-005a）。

草稿态：建单 / 改表头 / 明细全量替换 / 详情 / 列表。
轻状态迁移：``submit`` / ``reject`` / ``withdraw`` / ``cancel``（在
:mod:`.state_mixin`，校验与预占在 :mod:`.state_guard`）。``approve`` / ``reverse``
在 T-BUND-005b。

参考 :mod:`app.modules.cutting.service` 的拆分模式：私有助手（校验 / 取单 /
乐观锁 / 明细落库 / 汇总重算 / 写日志）抽到 :mod:`.common` 的 ``CommonMixin``，
本类只留公开方法并组装（ADR-0031 单文件 ≤400 行；状态机那侧同样按 ADR-0030
拆成 ``state_mixin`` + ``state_guard``）。
"""

from collections.abc import Sequence  # noqa: F401 —— 仅供下方类型推导
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import DocumentStatus
from app.core.db import unit_of_work
from app.core.errors import BusinessError, ErrorCode
from app.core.numbering import BUSINESS_TZ
from app.core.permissions import AuthContext
from app.core.scope import assert_in_scope
from app.modules.bundling.models import BundlingOrder, BundlingOrderLine
from app.modules.bundling.repository import (
    BundlingOrderListQuery,
    count_orders,
    get_lines_for_update,
    get_order_with_lines,
    list_orders,
)
from app.modules.bundling.schemas import (
    BundlingOrderCreateIn,
    BundlingOrderPatchIn,
    PutLinesIn,
)
from app.modules.bundling.service.approve_mixin import ApproveMixin
from app.modules.bundling.service.common import CommonMixin
from app.modules.bundling.service.numbering import next_doc_no
from app.modules.bundling.service.preview import PreviewMixin
from app.modules.cutting.models import CuttingOrderSizeLine


class BundlingOrderService(CommonMixin, ApproveMixin, PreviewMixin):
    """打菲单草稿态服务 + 状态迁移 + 只读预演。**写路径的事务边界唯一入口**（docs/03 §1.4）。

    ⚠️ :class:`PreviewMixin` 里的方法**不开事务**（只读，见其模块 docstring），与其余
    Mixin 的写路径相反 —— 组装顺序不影响这一点，各方法各自管理边界。
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------ 建单

    async def create(
        self, payload: BundlingOrderCreateIn, operator_id: UUID, ctx: AuthContext | None = None
    ) -> BundlingOrder:
        """建一张**草稿态**打菲单（表头 + 明细一次提交）。

        校验：
        - 款号/工序存在且启用
        - 来源裁剪单 `APPROVED`
        - 每行 `cutting_size_line_id` 存在且 `size_code` 匹配
        - `hands > 0`
        - 重算 `hands_total` / `output_qty` / `balance_qty` / `planned_qty` 落库
        - 生成 `doc_no`（调用 T-BUND-002 的 `next_doc_no`）
        - 写 `document_logs`

        :param ctx: 传了就校验建单人落在数据范围内（车间必须在可见车间内）。
            **不传则跳过校验** —— 内部调用方（seed / 测试）没有 AuthContext。
        """
        async with unit_of_work(self.session):
            # 1) 校验款号、工序
            style = await self._assert_style_usable(payload.style_no)
            operation = await self._assert_operation_usable(payload.operation_no)

            # 2) 校验来源裁剪单（不存在/未审核时由断言内部抛错）
            await self._assert_cutting_approved(payload.source_cutting_order_id)

            # 3) 取号（必须在事务内，C1：生成即占用、永不复用）
            doc_no = await next_doc_no(self.session, doc_date=payload.doc_date)

            # 4) 落明细（逐行校验 cutting_size_line_id、尺码匹配、hands > 0）
            # 先在内存里建好明细对象，再统一计算汇总
            lines = []
            for idx, line_in in enumerate(payload.lines, start=1):
                cutting_size_line = await self.session.get(
                    CuttingOrderSizeLine, line_in.cutting_size_line_id
                )
                if cutting_size_line is None:
                    raise BusinessError(
                        ErrorCode.BASE_DATA_NOT_FOUND,
                        f"第 {idx} 行：裁剪尺码明细行 {line_in.cutting_size_line_id} 不存在",
                    )
                if cutting_size_line.size_code != line_in.size_code:
                    raise BusinessError(
                        ErrorCode.PARAM_INVALID,
                        f"第 {idx} 行：尺码 {line_in.size_code} 与裁剪明细行的 {cutting_size_line.size_code} 不匹配",
                    )
                if line_in.hands <= 0:
                    raise BusinessError(
                        ErrorCode.PARAM_INVALID, f"第 {idx} 行：手数必须 > 0，收到 {line_in.hands}"
                    )

                qty_per_hand = cutting_size_line.qty_per_hand
                if qty_per_hand <= 0:
                    raise BusinessError(
                        ErrorCode.PARAM_INVALID,
                        f"第 {idx} 行：裁剪尺码明细的 qty_per_hand 必须 > 0，当前 {qty_per_hand}",
                    )

                planned_qty = line_in.hands * qty_per_hand
                available_qty_before = Decimal("0")

                line = BundlingOrderLine(
                    # doc_id 稍后设置
                    line_no=line_in.line_no,
                    color_code=line_in.color_code,
                    size_code=line_in.size_code,
                    operation_no=line_in.operation_no,
                    cutting_size_line_id=line_in.cutting_size_line_id,
                    hands=line_in.hands,
                    planned_qty=planned_qty,
                    available_qty_before=available_qty_before,
                    group_no=line_in.group_no,
                    workstation_no=line_in.workstation_no,
                    remark=line_in.remark,
                    created_by=operator_id,
                    updated_by=operator_id,
                )
                lines.append(line)

            # 5) 计算表头汇总
            zero = Decimal("0")
            hands_total = sum(line.hands for line in lines)
            output_qty = sum((line.planned_qty for line in lines), start=zero)
            balance_qty = zero  # 草稿态余数为 0

            # 6) 建表头（带计算好的汇总）
            order = BundlingOrder(
                doc_no=doc_no,
                workshop_id=payload.workshop_id,
                style_no=style.style_no,
                operation_no=operation.operation_no,
                color_group=payload.color_group,
                color_code=payload.color_code,
                bundle_qty=payload.bundle_qty,
                doc_date=payload.doc_date,
                source_cutting_order_id=payload.source_cutting_order_id,
                status=DocumentStatus.DRAFT,
                hands_total=hands_total,
                output_qty=output_qty,
                balance_qty=balance_qty,
                label_print_qty=0,
                remark=payload.remark,
                created_by=operator_id,
                updated_by=operator_id,
            )
            self.session.add(order)
            await self.session.flush()

            # 7) 关联明细到表头并落库
            for line in lines:
                line.doc_id = order.id
                self.session.add(line)

            # 8) 落库汇总 + 写日志
            await self.session.flush()
            await self._write_log(
                order, operator_id, "CREATE", None, DocumentStatus.DRAFT.value, None
            )

            # 9) 重读返回（避免 Core UPDATE 导致 expire -> MissingGreenlet）
            fresh = await get_order_with_lines(self.session, order.id)
            if fresh is None:
                raise BusinessError(ErrorCode.CUTTING_STATUS_NOT_ALLOWED, "打菲单不存在或已删除")
        return fresh

    # ------------------------------------------------------------------ 写：表头 PATCH

    async def patch(
        self, order_id: UUID, payload: BundlingOrderPatchIn, operator_id: UUID, ctx: AuthContext
    ) -> BundlingOrder:
        """改表头（仅 ``DRAFT`` / ``REJECTED``，C14）。

        ⚠️ **只改表头**：明细要走 ``PUT /lines``（全量替换，各自带锁 + 各自重算）。
        ⚠️ ``version`` 不符 -> ``10003``，且**在动手之前**就拒。
        """
        async with unit_of_work(self.session):
            await self._editable_order(order_id, payload.version, ctx)
            extra: dict[str, Any] = {}
            if payload.doc_date is not None:
                extra["doc_date"] = payload.doc_date
            if payload.bundle_qty is not None:
                extra["bundle_qty"] = payload.bundle_qty
            if payload.remark is not None:
                extra["remark"] = payload.remark
            await self._bump_header(order_id, payload.version, operator_id, extra)
            order = await self._reloaded(order_id)
        return order

    # ------------------------------------------------------------------ 写：明细全量替换

    async def put_lines(
        self, order_id: UUID, payload: PutLinesIn, operator_id: UUID, ctx: AuthContext
    ) -> BundlingOrder:
        """明细**全量替换**（软删旧行 + 插新行，重算表头汇总，返回重读）。

        ⚠️ **全量替换语义**：不在 ``items`` 里的旧行被**软删**。
        ⚠️ 每一行都要重算，包括没被改动的那几行。
        """
        async with unit_of_work(self.session):
            order = await self._editable_order(order_id, payload.version, ctx)
            old_lines = await get_lines_for_update(self.session, order_id)
            # 软删旧行
            now = datetime.now(tz=BUSINESS_TZ)
            for line in old_lines:
                if line.deleted_at is None:
                    line.deleted_at = now
                    line.version = line.version + 1
                    line.updated_by = operator_id
            # ⚠️ 软删后必须先 flush，再插新行（避免唯一索引 WHERE deleted_at IS NULL 撞车）
            await self.session.flush()

            # 插新行
            lines = await self._insert_lines(order, payload.items, operator_id)

            # 重算表头汇总
            self._recalc_header(order, lines)

            # bump header 版本
            await self._bump_header(order_id, payload.version, operator_id)

            # ⚠️ 必须重读
            order = await self._reloaded(order_id)
        return order

    # ------------------------------------------------------------------ 读

    async def get(self, order_id: UUID, ctx: AuthContext) -> BundlingOrder:
        """单据详情（表头 + 明细）。

        ⚠️ 详情按 ID 直查是越权的经典入口（docs/07 §3.2 铁律 2）。
        顺序刻意是「先查存在性再判范围」：越权与不存在要报**不同的码**。
        """
        order = await get_order_with_lines(self.session, order_id)
        if order is None:
            raise BusinessError(ErrorCode.CUTTING_STATUS_NOT_ALLOWED, "打菲单不存在或已删除")
        assert_in_scope(order, ctx)
        return order

    async def list_orders(
        self, query: BundlingOrderListQuery, ctx: AuthContext
    ) -> tuple[list[BundlingOrder], int]:
        """单据列表（不含明细）。导出必须复用本方法（docs/07 §3.2 铁律 3）。"""
        query.validate()
        rows = await list_orders(self.session, ctx, query)
        total = await count_orders(self.session, ctx, query)
        return rows, total
