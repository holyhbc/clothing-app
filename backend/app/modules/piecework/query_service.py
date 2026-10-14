"""计件只读查询的编排（``docs/modules/04-计件.md`` §6.1 的 5 个读端点）。

## 唯一不能省的第一行

每个列表 / 详情方法的第一件事都是 :func:`app.core.scope.apply_data_scope`
（07 §3.2 铁律 1）。**导出也不例外** —— 导出是绕过界面直接拿数据的地方，
比界面更容易泄露，而「导出没过滤」是那种审查看不出来的缺陷
（页面上你看不到自己车间以外的东西，导出的 CSV 里却有）。

## 列表与导出共用同一条筛选路径

:func:`list_logs` 与 :func:`export_logs` 都走 :func:`build_count_stmt` /
:func:`build_list_stmt`，二者只差「要不要 limit/offset」。另写一条导出查询的话，
「列表看到的」与「导出的」会在对账时不一致 —— 而对账恰恰是导出存在的理由。

## 「手数」在每一层都要留着

:func:`list_logs` 之外，:func:`summary` 与 :func:`station_today` 都同时返回
``total_hands`` / ``today_hands``。少了它主管看到的是「做完了 3600 件」而不是
「做了 60 手」，而车间真正在意的单位是手（ADR-0016 决策 3：责任单元是手）。
"""

from __future__ import annotations

import csv
import io
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessError, ErrorCode
from app.core.numbering import business_today
from app.core.permissions import AuthContext
from app.core.scope import apply_data_scope
from app.modules.piecework.models import PieceworkLog, PieceworkLogType
from app.modules.piecework.query import (
    build_bundle_stmt,
    build_count_stmt,
    build_detail_stmt,
    build_list_stmt,
    build_related_logs_stmt,
    build_station_last_stmt,
    build_station_today_stmt,
    build_summary_stmt,
    fetch_size_color_names,
    fetch_workshop_names,
)
from app.modules.piecework.schemas import (
    BundleInfo,
    LogDetailOut,
    LogQuery,
    LogRowOut,
    RelatedLog,
    StationTodayOut,
    SummaryRowOut,
)

#: CSV 列顺序（``04-计件 §6.1`` 逐字照抄）。
#:
#: ⚠️ **金额与数量在 CSV 里也保持字符串原样**：Excel 会把 ``25.2000`` 显示成
#: ``25.2``（无伤大雅），但如果这里转成 float 再 ``str()``，``0.42 × 60``
#: 就会变成 ``25.199999999999996`` —— 对账的人会以为系统算错了。
CSV_COLUMNS: tuple[str, ...] = (
    "work_date",
    "workshop_name",
    "group_no",
    "style_no",
    "color_name",
    "operation_name",
    "employee_no",
    "employee_name",
    "bundle_no",
    "hands",
    "bundle_qty",
    "qty",
    "unit_price",
    "amount",
    "log_type",
    "is_settled",
)

#: 导出上限（05 §2：批量上限 500 行/请求）。⚠️ **必须限**：导出没有分页，
#: 一个 FACTORY 范围的用户选「全部历史」就是全表扫描 + 全量 CSV，
#: 在 2C VPS 上会把接口拖到分钟级，而且没有任何办法中途取消。
EXPORT_LIMIT = 500


class PieceworkQueryService:
    """计件**只读**查询。写路径在 :class:`app.modules.piecework.service.PieceworkService`。"""

    def __init__(self, session: AsyncSession, ctx: AuthContext) -> None:
        self.session = session
        self.ctx = ctx

    # ------------------------------------------------------------------ 列表 / 导出

    async def list_logs(self, query: LogQuery) -> tuple[list[LogRowOut], int]:
        """分页列表。返回 ``(行, 总数)``。"""
        total = (
            await self.session.execute(
                apply_data_scope(build_count_stmt(query), PieceworkLog, self.ctx)
            )
        ).scalar_one()
        stmt = build_list_stmt(query, limit=query.size, offset=(query.page - 1) * query.size)
        rows = (
            (await self.session.execute(apply_data_scope(stmt, PieceworkLog, self.ctx)))
            .mappings()
            .all()
        )
        return await self._to_rows(rows), total

    async def export_logs(self, query: LogQuery) -> list[LogRowOut]:
        """导出。⚠️ 与 :meth:`list_logs` **共用同一对 stmt**，只是不加分页。

        超过 :data:`EXPORT_LIMIT` 行时**截断并报错**，而不是悄悄只导前 500 行 ——
        静默截断会让人拿一份不完整的数据去对账，而对账「平了」的假象比报错更贵。
        """
        total = (
            await self.session.execute(
                apply_data_scope(build_count_stmt(query), PieceworkLog, self.ctx)
            )
        ).scalar_one()
        if total > EXPORT_LIMIT:
            raise BusinessError(
                ErrorCode.PARAM_INVALID,
                f"符合条件的有 {total} 条，超过单次导出上限 {EXPORT_LIMIT} 条，请缩小日期范围",
                details={"total": total, "limit": EXPORT_LIMIT},
            )
        stmt = build_list_stmt(query, limit=EXPORT_LIMIT, offset=0)
        rows = (
            (await self.session.execute(apply_data_scope(stmt, PieceworkLog, self.ctx)))
            .mappings()
            .all()
        )
        return await self._to_rows(rows)

    @staticmethod
    def to_csv(rows: Sequence[LogRowOut]) -> str:
        """CSV 文本（**不含 BOM**，BOM 由 router 加）。

        ⚠️ 用 ``csv`` 模块而不是手拼 ``",".join``：款号 / 颜色名里有逗号是迟早的事，
        手拼出来的每一行都会错位，而错位在 Excel 里看起来仍然是「正常的表」。
        """
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(CSV_COLUMNS)
        for row in rows:
            writer.writerow([_csv_cell(row, column) for column in CSV_COLUMNS])
        return buffer.getvalue()

    async def _to_rows(self, rows: Sequence[Any]) -> list[LogRowOut]:
        """映射行 → :class:`LogRowOut`，并**一次性**补上车间名。

        ⚠️ 车间名走 :func:`query.fetch_workshop_names` 的单次 ``IN`` 查询：
        逐行查的话导出 500 行就是 500 次往返，在 2C VPS 上要好几秒。
        """
        names = await fetch_workshop_names(
            self.session, {row["workshop_id"] for row in rows if row["workshop_id"] is not None}
        )
        sizes, colors = await fetch_size_color_names(
            self.session,
            size_codes={r["size_code"] for r in rows if r["size_code"]},
            color_codes={r["color_code"] for r in rows if r["color_code"]},
        )
        return [
            LogRowOut(
                log_id=row["id"],
                bundle_no=row["bundle_no"],
                style_no=row["style_no"],
                operation_no=row["operation_no"],
                operation_name=None,
                color_name=colors.get(row["color_code"]),
                size_code=row["size_code"],
                size_name=sizes.get(row["size_code"]),
                employee_id=row["employee_id"],
                employee_no=row["employee_no"],
                employee_name=row["employee_name"],
                group_no=row["group_no"],
                workshop_name=names.get(row["workshop_id"]),
                work_date=row["work_date"],
                hands=row["hands"],
                hands_total_of_size=None,  # 列表不带（要再一次窗口查询，见 §8 的取舍）
                bundle_qty=row["bundle_qty"],
                qty=row["qty"],
                unit_price=row["unit_price"],
                amount=row["amount"],
                log_type=PieceworkLogType(row["log_type"]),
                payroll_id=row["payroll_id"],
                is_settled=bool(row["is_settled"]),
            )
            for row in rows
        ]

    # ------------------------------------------------------------------ 详情

    async def get_log(self, log_id: UUID) -> LogDetailOut:
        """详情 + 码 + 关联流水。

        ## 查不到时一律 ``12002``（``04-计件 §6.1`` 给这个端点登记的**只有** 12002）

        ⚠️ 「不存在」与「超出数据范围」**故意不区分**：分开就要么回 404（泄漏了
        「这条记录确实存在」）、要么回 12002（把不存在的也说成无权，调用方没法判断
        是自己传错了 id 还是真没权限）。合在一起的话，越权探测拿不到任何额外信息，
        而「id 传错了」这个场景前端可以用一句提示覆盖掉。

        ⚠️ **数据范围直接进 SQL**（``apply_data_scope``），不是查出来再判：
        查出来再判要多一次往返，而这里每次点开详情都会调。
        """
        stmt = apply_data_scope(build_detail_stmt(log_id), PieceworkLog, self.ctx)
        row = (await self.session.execute(stmt)).mappings().one_or_none()
        if row is None:
            raise BusinessError(ErrorCode.DATA_SCOPE_DENIED, "计件流水不存在或超出你的数据范围")
        bundle_info: BundleInfo | None = None
        bundle_stmt = build_bundle_stmt(row["bundle_id"])
        if bundle_stmt is not None:
            bundle_row = (await self.session.execute(bundle_stmt)).mappings().one_or_none()
            if bundle_row is not None:
                bundle_info = BundleInfo.model_validate(dict(bundle_row))
        related = (await self.session.execute(build_related_logs_stmt(log_id))).mappings().all()
        return LogDetailOut(
            log_id=row["id"],
            bundle_no=row["bundle_no"],
            style_no=row["style_no"],
            operation_no=row["operation_no"],
            employee_id=row["employee_id"],
            employee_name=row["employee_name"],
            group_no=row["group_no"],
            work_date=row["work_date"],
            qty=row["qty"],
            unit_price=row["unit_price"],
            amount=row["amount"],
            log_type=PieceworkLogType(row["log_type"]),
            payroll_id=row["payroll_id"],
            reversal_of_id=row["reversal_of_id"],
            remark=row["remark"],
            scanned_at=row["scanned_at"],
            created_at=row["created_at"],
            bundle=bundle_info,
            reversal_of=next(
                (_related(r) for r in related if r["id"] == row["reversal_of_id"]), None
            ),
            reversed_by=[
                _related(r)
                for r in related
                if r["id"] != row["id"] and r["reversal_of_id"] == log_id
            ],
        )

    # ------------------------------------------------------------------ 汇总 / 工位

    async def summary(self, query: LogQuery, group_by: str) -> list[SummaryRowOut]:
        """按 ``employee`` / ``style`` / ``operation`` 汇总。

        ⚠️ ``group_by`` 走**固定三分支**而不是白名单查字典：这三个值直接决定
        SELECT 与 GROUP BY 的**列数与形状**（按 style 分还要带颜色名），
        不是换个 ``ORDER BY`` 就能覆盖的。
        """
        if group_by not in ("employee", "style", "operation"):
            raise BusinessError(
                ErrorCode.PARAM_INVALID,
                f"group_by 只能是 employee / style / operation，收到 {group_by!r}",
                details={"allowed": ["employee", "style", "operation"]},
            )
        rows = (
            (
                await self.session.execute(
                    apply_data_scope(build_summary_stmt(query, group_by), PieceworkLog, self.ctx)
                )
            )
            .mappings()
            .all()
        )
        _, colors = await fetch_size_color_names(
            self.session,
            size_codes=set(),
            color_codes={r["color_code"] for r in rows if r.get("color_code")},
        )
        return [
            SummaryRowOut(
                employee_id=row.get("employee_id"),
                employee_no=row.get("employee_no"),
                employee_name=row.get("employee_name"),
                style_no=row.get("style_no"),
                color_name=colors.get(row.get("color_code")),
                operation_no=row.get("operation_no"),
                group_no=row.get("group_no"),
                total_hands=row["hands"],
                total_qty=row["qty_sum"],
                total_amount=row["amount_sum"],
            )
            for row in rows
        ]

    async def station_today(
        self, station_no: str, work_date: date | None = None
    ) -> StationTodayOut:
        """工位机大字卡。⚠️ ``work_date`` 缺省**今天**（工厂当地日历）。

        ⚠️ 不套 ``apply_data_scope``：这张卡查的是「**本工位**今日」，
        而工位机的 token 往往就是工位机本身的数据范围（SELF）——
        套上车间过滤会让组别主管在工位机上看到 0，这不是「更安全」，是「坏了」。
        """
        day = work_date or business_today()
        hands, qty, amount = (
            await self.session.execute(build_station_today_stmt(station_no, day))
        ).one()
        last = (
            (await self.session.execute(build_station_last_stmt(station_no)))
            .mappings()
            .one_or_none()
        )
        return StationTodayOut(
            station_no=station_no,
            work_date=day,
            today_hands=hands,
            today_qty=qty,
            today_amount=amount,
            last_bundle_no=last["bundle_no"] if last else None,
            last_hands=last["hands"] if last else None,
            last_scanned_at=last["scanned_at"] if last else None,
        )


def _csv_cell(row: LogRowOut, column: str) -> str:
    value = getattr(row, column)
    return "" if value is None else str(value)


def _related(row: Mapping[str, Any]) -> RelatedLog:
    """关联流水行 → :class:`RelatedLog`（红冲行取负值，不做绝对值化）。"""
    return RelatedLog(
        log_id=row["id"],
        log_type=PieceworkLogType(row["log_type"]),
        qty=row["qty"],
        amount=row["amount"],
        work_date=row["work_date"],
    )


__all__ = ["CSV_COLUMNS", "EXPORT_LIMIT", "PieceworkQueryService"]
