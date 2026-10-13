"""计件写入 service —— 目前只有扫码链路 A（``docs/modules/04-计件.md`` §5.1）。

## 这个文件里唯一不能改的东西：①~⑪ 的顺序

§5.1 把顺序写成「不可调换」，不是随便说说的，每一对相邻步骤都有依赖：

- **① 必须最先**：``SELECT … FOR UPDATE`` 拿到行锁之后，②~⑨ 的判定与写入才
  是原子的。放到最后查就等于没锁（04-计件 §7.1：「只靠业务层检查 = 10 §5.4 判未通过」）。
- **③ 在 ④ 之前**：没绑定就不该去取价。反过来写会让「没绑定的员工」也触发一次
  ``operation_rates`` 查询，白扫一张表。
- **⑤ 在 ⑥ 之前**：金额是 ``qty × unit_price``，而 ``qty`` 在 ⑤ 才定下来（默认整手
  还是部分生产）。

## ① 锁码之后为什么先判 ``counted_at`` 而不是查流水表

ADR-0016 决策 3 把「一码一员工」钉在**码**上：``bundles.counted_at`` 非空 = 这手已被
计过。而「查 ``piecework_logs`` 里有没有别人的行」是**两个来源**（流水表 vs 码上的回写），
两者会漂移 —— 红冲流水后码上仍是已计、或者码上清了而流水还在。**判据只有一个**。

## 幂等有两层，别只做一层

1. **``Idempotency-Key``**（05 §5）—— 网络重试 / 扫码枪连扫，走 Redis 缓存，
   在 service 之外，由 router 负责。
2. **``uq_piecework_logs_idem``**（``(bundle_no, operation_no, employee_id,
   work_date, log_type)``）—— 数据库兜底，**离线重传**（C4：不带同一个幂等键的
   20 条缓存一次补传）靠它。命中唯一键 = 同员工同码同日同工序已记过 → 返回首次结果。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import unit_of_work
from app.core.errors import BusinessError, ErrorCode
from app.core.numbering import BUSINESS_TZ
from app.core.permissions import AuthContext
from app.modules.piecework.audit import employee_group_no, write_document_log
from app.modules.piecework.guard import (
    LockedBundle,
    already_counted_message,
    counted_by_self,
    guard_bundle,
    resolve_qty,
)
from app.modules.piecework.models import PieceworkLog, PieceworkLogType
from app.modules.piecework.pricing import resolve_price
from app.modules.piecework.repository import (
    build_bound_stmt,
    build_find_log_stmt,
    build_hands_total_stmt,
    build_insert_log_stmt,
    build_lock_bundle_stmt,
    build_mark_counted_stmt,
    build_style_operation_stmt,
    build_today_stmt,
)
from app.modules.piecework.schemas import CountPieceworkOut

#: 三个精度（照抄 ``piecework_logs`` 的列定义：``numeric(14,3)`` / ``numeric(12,6)`` /
#: ``numeric(18,4)``）。
#:
#: ⚠️ **响应里的数必须落在列精度上**，否则同一个字段的值会随数据来源而变：
#: 从库里 ``SUM`` 出来的 ``today_qty`` 是 ``"60.000"``（numeric(14,3) 的列标度），
#: 而 Python 侧算出来的 ``qty`` 是 ``"60"`` —— 同一个响应里两件件数写法不同，
#: 前端拿去做展示和求和都会莫名其妙。统一 quantize 到列标度即可对齐。
#:
#: ⚠️ **金额在乘法之后才 quantize**：先把单价 round 到 4 位再乘，会让
#: ``0.419999 × 60`` 算出 ``25.1999`` 而不是 ``25.2``。
_QTY_SCALE = Decimal("0.001")
_PRICE_SCALE = Decimal("0.000001")
_MONEY_SCALE = Decimal("0.0001")


def _at(value: Decimal, scale: Decimal) -> Decimal:
    """把数值落到列精度上。``quantize`` 只改小数位、不改数值，故不涉及舍入模式争议。"""
    return value.quantize(scale)


class PieceworkService:
    """计件 service。所有写方法的**事务入口是** :func:`unit_of_work`（03 §1.1 第 5 条）。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ================================================================== 链路 A

    async def count_piecework(
        self,
        *,
        bundle_no: str,
        employee_id: UUID,
        scanned_at: datetime,
        qty: Decimal | None,
        operator_id: UUID,
        ctx: AuthContext,
        station_id: str | None = None,
        device_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> CountPieceworkOut:
        """扫码计件（``04-计件 §5.1`` ①~⑪）。**校验顺序不可调换。**

        ``employee_id`` 与 ``operator_id`` **可以不是同一个人**：工位机上组长替
        员工扫码（``operator_id`` = 组长，``employee_id`` = 干活的员工），
        ``document_logs.operator_id`` 记前者、``piecework_logs.employee_id`` 记后者 ——
        「谁操作的」与「谁做的」在车间里不是一回事。
        """
        async with unit_of_work(self.session):
            row = (await self.session.execute(build_lock_bundle_stmt(bundle_no))).mappings().first()
            if row is None:
                raise BusinessError(
                    ErrorCode.BUNDLE_NO_NOT_FOUND, f"打菲号 {bundle_no} 不存在，请重新扫码"
                )
            bundle = guard_bundle(row, employee_id)

            # ② 款号/工序匹配（含 is_piecework）。⚠️ 用码上的 operation_no 而不是
            #   请求参数 —— 码与工序强绑定（ADR-0016），请求体里根本没有工序号。
            hit = await self.session.execute(
                build_style_operation_stmt(bundle["style_no"], bundle["operation_no"])
            )
            if hit.scalar_one_or_none() is None:
                raise BusinessError(
                    ErrorCode.BASE_DATA_NOT_FOUND,
                    f"款号 {bundle['style_no']} 未配置计件工序 {bundle['operation_no']}",
                    details={
                        "style_no": bundle["style_no"],
                        "operation_no": bundle["operation_no"],
                    },
                )

            # ③ 当日绑定（BR-PW-04：绑定当天有效，次日需重扫）
            work_date = scanned_at.astimezone(BUSINESS_TZ).date()
            bound = (
                await self.session.execute(
                    build_bound_stmt(
                        employee_id, bundle["style_no"], bundle["operation_no"], work_date
                    )
                )
            ).scalar_one()
            if not bound:
                raise BusinessError(
                    ErrorCode.PIECEWORK_NOT_BOUND,
                    f"员工未绑定 {bundle['style_no']} 的 {bundle['operation_no']} 工序，"
                    f"或绑定不是 {work_date - timedelta(days=1)} 的，请重新绑定",
                    details={
                        "style_no": bundle["style_no"],
                        "operation_no": bundle["operation_no"],
                        "work_date": work_date.isoformat(),
                    },
                )

            # ④ 按 work_date 取价（命中不到即 20004，**不回退旧价也不取 0**）。
            #    ⚠️ **刻意不调 RateService.resolve**：那是「取价预演」接口（ADR-0026 §2），
            #    里面带 ``_require_style`` 的**数据范围**判定，而 ``styles`` 的范围口径是
            #    「只看自己负责的款号」（``scope.py`` 的 user_column=merchandiser_id）。
            #    工位机上的组长不是款号负责人 —— 按那条判据，他扫任何一个码都取不到价，
            #    整个链路 A 对**真实用户**不可用（症状是每次扫码都 12002，
            #    而错误文案说的是「超出数据范围」，没人会想到是款号归属的问题）。
            #    这里的授权边界是**码**：① 已经按车间范围锁定了这手码、② 校验了款号配了
            #    这个计件工序，取价只是这两步之后的内部读取。
            #    取价的 SQL 仍走共享的 build_rate_resolve_stmt（ADR-0026 §2，勿改）。
            unit_price = await resolve_price(
                self.session, bundle["style_no"], bundle["operation_no"], work_date
            )

            # ⑤~⑥ 取件数并算金额
            resolved_qty = resolve_qty(qty, bundle["bundle_qty"])
            amount = (resolved_qty * unit_price).quantize(_MONEY_SCALE, rounding=ROUND_HALF_UP)

            # ⑦ 幂等回查：同员工同码同日同工序已记过 SCAN → 直接返回首次结果
            existing = await self._find_existing(
                bundle_no, bundle["operation_no"], employee_id, work_date
            )
            if existing is not None:
                return await self._respond(
                    bundle, existing, unit_price, amount, resolved_qty, work_date, idempotent=True
                )
            # ⚠️ 兜底：码上写着「已被**这人**计过」，流水却查不到。数据已经不自洽
            #   （正常路径到不了），再插一条会让 counted_qty 被覆盖两次 —— 报 32001
            #   而不是让它插，宁可让工位机停下来等人查。
            if counted_by_self(bundle, employee_id):
                raise BusinessError(
                    ErrorCode.PIECEWORK_COUNTED_BY_OTHER,
                    already_counted_message(bundle),
                    details={"inconsistent": True},
                )

            try:
                log = await self._insert_and_mark(
                    bundle=bundle,
                    employee_id=employee_id,
                    operator_id=operator_id,
                    work_date=work_date,
                    qty=resolved_qty,
                    unit_price=unit_price,
                    amount=amount,
                    scanned_at=scanned_at,
                    station_id=station_id,
                    device_id=device_id,
                    idempotency_key=idempotency_key,
                )
            except IntegrityError:
                # ⑩ 唯一索引兜底（savepoint 已回滚，事务仍可用）。回查确认归属：
                #   同员工 → 幂等返回；他人 → 32001。
                again = await self._find_existing(
                    bundle_no, bundle["operation_no"], employee_id, work_date
                )
                if again is not None:
                    return await self._respond(
                        bundle, again, unit_price, amount, resolved_qty, work_date, idempotent=True
                    )
                raise BusinessError(
                    ErrorCode.PIECEWORK_COUNTED_BY_OTHER,
                    already_counted_message(bundle),
                    details={
                        "bundle_qty": str(bundle["bundle_qty"]),
                        "hands": bundle["hands"],
                    },
                ) from None

            # ⑪ 留痕。⚠️ 放在 ⑧⑨ 之后：留痕记的是「已经记上的事实」，
            #   与流水、码上的回写在同一事务里，要么全成要么全不成。
            await write_document_log(
                self.session,
                log_id=log.id,
                bundle=bundle,
                operator_id=operator_id,
                employee_id=employee_id,
                qty=resolved_qty,
                amount=amount,
            )
            return await self._respond(
                bundle, log, unit_price, amount, resolved_qty, work_date, idempotent=False
            )

    # ================================================================== 内部

    async def _find_existing(
        self, bundle_no: str, operation_no: str, employee_id: UUID, work_date: date
    ) -> PieceworkLog | None:
        log: PieceworkLog | None = (
            await self.session.execute(
                build_find_log_stmt(bundle_no, operation_no, employee_id, work_date)
            )
        ).scalar_one_or_none()
        return log

    async def _insert_and_mark(
        self,
        *,
        bundle: LockedBundle,
        employee_id: UUID,
        operator_id: UUID,
        work_date: date,
        qty: Decimal,
        unit_price: Decimal,
        amount: Decimal,
        scanned_at: datetime,
        station_id: str | None,
        device_id: str | None,
        idempotency_key: str | None,
    ) -> PieceworkLog:
        """§5.1 ⑧⑨。返回刚落库的流水行（响应要它的 ``id``，不必再查一次）。"""
        group_no = await employee_group_no(self.session, employee_id)
        log = PieceworkLog(
            # ⚠️ 必须**在 Python 侧生成**：`BaseModel.id` 的默认值是服务端
            # `gen_random_uuid()`，构造完还是 None，而响应要立刻回 `log_id`、
            # `document_logs.doc_id` 也要拿它 —— 等 flush 后再读就得多一次 SELECT。
            id=uuid4(),
            bundle_no=bundle["bundle_no"],
            style_no=bundle["style_no"],
            operation_no=bundle["operation_no"],
            employee_id=employee_id,
            workshop_id=bundle["workshop_id"],
            group_no=group_no,
            work_date=work_date,
            bundle_id=bundle["id"],
            qty=qty,
            unit_price=unit_price,
            amount=amount,
            log_type=PieceworkLogType.SCAN,
            source_station=station_id,
            scanned_at=scanned_at,
            device_id=device_id,
            idempotency_key=idempotency_key,
            created_by=operator_id,
            updated_by=operator_id,
        )
        # ⚠️ savepoint 不是可有可无：唯一键冲突会**中止整个事务**，而 ⑩ 之后
        #   还要在同一事务里回查、返回幂等结果或抛 32001。不套 savepoint 就只能
        #   session.rollback()，那会把 ① 拿到的行锁一起放掉 —— 于是「回查确认是
        #   幂等」的窗口里第三个人又插了进来，把幂等变成重复计数。
        # ⚠️ `IntegrityError` 因此**不需要**在此捕获：它会穿过 savepoint 上抛，
        #   由 `count_piecework` 的 ⑩ 分支统一处理（回查 → 幂等 / 32001）。
        async with self.session.begin_nested():
            await self.session.execute(build_insert_log_stmt(log))
        # ⚠️ ``counted_by`` 写的是**员工**，不是操作员（组长）。ADR-0016 决策 3：
        #   责任单元是「手」，而一码一员工说的是「干活的那个人」。
        #   写成 operator_id 的话，32001 的提示会变成「你 09:44 已计 60 件」——
        #   自己扫自己被判成冲突，工位机直接卡死。``updated_by`` 才是操作员。
        await self.session.execute(
            build_mark_counted_stmt(
                bundle["id"], qty, employee_id, operator_id, datetime.now(tz=UTC)
            )
        )
        return log

    async def _respond(
        self,
        bundle: LockedBundle,
        log: PieceworkLog,
        unit_price: Decimal,
        amount: Decimal,
        qty: Decimal,
        work_date: date,
        *,
        idempotent: bool,
    ) -> CountPieceworkOut:
        hands_today, qty_today, amount_today = (
            await self.session.execute(build_today_stmt(log.employee_id, work_date))
        ).one()
        hands_total = (
            await self.session.execute(
                build_hands_total_stmt(bundle["doc_id"], bundle["color_code"], bundle["size_code"])
            )
        ).scalar_one()
        return CountPieceworkOut(
            log_id=log.id,
            bundle_no=bundle["bundle_no"],
            style_no=bundle["style_no"],
            operation_no=bundle["operation_no"],
            color_name=bundle["color_name"] or bundle["color_code"],
            size_name=bundle["size_name"] or bundle["size_code"],
            hands=bundle["hands"],
            hands_total_of_size=hands_total,
            qty=_at(qty, _QTY_SCALE),
            bundle_qty=_at(bundle["bundle_qty"], _QTY_SCALE),
            unit_price=_at(unit_price, _PRICE_SCALE),
            amount=_at(amount, _MONEY_SCALE),
            work_date=work_date,
            idempotent=idempotent,
            today_hands=hands_today,
            today_qty=_at(qty_today, _QTY_SCALE),
            today_amount=_at(amount_today, _MONEY_SCALE),
        )
