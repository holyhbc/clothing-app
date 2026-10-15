"""计件扫码链路的 SQL（``docs/modules/04-计件.md`` §5.1 ①~⑨）。

## 为什么单独一个 repository，而不像别处那样用 ORM

§5.1 ① 是 ``SELECT … FOR UPDATE``。**加锁必须在 SQL 里写死**，而 ORM 的
``select(Bundle)`` 加 ``.with_for_update()`` 容易在重构时被漏掉 —— 而漏掉的后果是
C3（两人同扫）静默双写（``04-计件 §7.1`` 原文：「只靠业务层检查 = 10 §5.4 判**未通过**」）。
把锁、锁定后的判定字段、回写三件事放在一起，让「哪条语句加了锁」一眼可查。

## 车间从 ``bundling_orders`` 取，不从 ``bundles`` 取

⚠️ ``bundles`` **没有 ``workshop_id`` 列**（03 §3.3 明确：车间在单据上）。
``piecework_logs.workshop_id`` 是行级冗余，取值来自 JOIN 后的单据车间。
本模块的 JOIN 是**读数据**用的，与 :mod:`app.core.scope` 里 ``bundles`` 的
``ScopeVia`` 各司其职 —— 不要把两处合成一处（scope 那层的注释已说明理由）。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Insert, Select, Update, func, insert, select, update

from app.modules.auth.models import User
from app.modules.base.models import Color, Size, StyleOperation
from app.modules.bundling.models import Bundle, BundlingOrder
from app.modules.piecework.models import PieceworkBinding, PieceworkLog, PieceworkLogType

#: 单行结果的别名。SQLAlchemy 2.1 的 ``Select`` 按**返回元组的形状**泛型化且不变，
#: 而列数会随字段增减变化 —— 在**函数签名**里写死形状只会得到一串每次加列都要改的
#: ``Select[UUID, UUID, str, int, ...]``。故形状标在常量上（见 :data:`_LOCK_COLUMNS`），
#: 函数签名统一用 ``Select[Any]``。
type Row = Any

#: 锁码时一并带出的字段。⚠️ ``hands_total_of_size`` 是**同尺码全部手数**，
#: 不是 ``bundle.hands``（后者是手序号）。两者在响应里都要出现，含义不同。
_LOCK_COLUMNS: tuple[Any, ...] = (
    Bundle.id,
    Bundle.doc_id,
    Bundle.bundle_no,
    Bundle.hands,
    Bundle.style_no,
    Bundle.color_code,
    Bundle.size_code,
    Bundle.operation_no,
    Bundle.bundle_qty,
    Bundle.counted_qty,
    Bundle.counted_by,
    Bundle.counted_at,
    Bundle.status,
    BundlingOrder.workshop_id,
    Color.name.label("color_name"),
    Size.name.label("size_name"),
    # ⚠️ 32001 的文案要「谁计的」（04-计件 §6.1）。LEFT JOIN 取名字而**不在 SQL 里
    #   过滤** —— 用 INNER JOIN 会把「尚未计件的码」整行过滤掉，扫码就变成 31001。
    User.name.label("counted_by_name"),
)


def build_lock_bundle_stmt(bundle_no: str) -> Select[Any]:
    """§5.1 ① **锁码**：``SELECT … FROM bundles WHERE bundle_no = :bn FOR UPDATE``。

    ⚠️ ``with_for_update(of=Bundle)`` 里的 ``of=Bundle`` **不是可选优化，去掉就报错**：
    本查询 LEFT JOIN 了 ``colors`` / ``sizes`` / ``users``，而 PostgreSQL 对外连接的
    可空侧直接报 ``FOR UPDATE cannot be applied to the nullable side of an outer join``。
    ``of=Bundle`` 同时也是**语义**要求：只锁码，不锁字典表 —— 工位机上多人并行时，
    顺带把 ``users`` 行锁进去会让同一个人名被反复串行。

    ⚠️ 三处刻意的写法：

    1. 上面这条：锁只作用在码上，但**判定字段（``counted_at`` / ``counted_qty`` /
       ``counted_by_name``）都在同一行里读出来**，不用再查一次 —— 锁内的读取必须
       与加锁同源，否则「查了、锁了、之后又读了一遍」之间还有窗口。
    2. ``hands_total_of_size`` **刻意不在这里算**。原本用窗口函数
       ``count(*) OVER (PARTITION BY …)`` 顺带带出来（省一次查询），但 PostgreSQL
       直接拒绝：「FOR UPDATE is not allowed with window functions」——
       **加锁与窗口函数互斥**，不是写法问题。改走 :func:`build_hands_total_stmt`，
       那条不带锁、且在本事务内跑，读到的是同一快照。
    3. ``deleted_at IS NULL`` 显式带上：软删是唯一的删除方式（AGENTS §2.1），
       但 ``Bundle`` 走的是全局软删过滤器，不显式写会依赖全局配置。
    """
    return (
        select(*_LOCK_COLUMNS)
        .select_from(Bundle)
        .join(BundlingOrder, BundlingOrder.id == Bundle.doc_id)
        .join(Color, Color.color_code == Bundle.color_code, isouter=True)
        .join(Size, Size.size_code == Bundle.size_code, isouter=True)
        .outerjoin(User, User.id == Bundle.counted_by)
        .where(Bundle.bundle_no == bundle_no, Bundle.deleted_at.is_(None))
        .with_for_update(of=Bundle)
    )


def build_hands_total_stmt(doc_id: UUID, color_code: str, size_code: str) -> Select[int]:
    """该尺码共有几手（响应里的 ``hands_total_of_size``，即「第 N 手 / 共 M 手」）。

    ⚠️ **单独一条查询**而不是锁查询里的窗口函数：PostgreSQL 不允许
    ``FOR UPDATE`` 与窗口函数共存（见 :func:`build_lock_bundle_stmt` 的第 2 条）。
    它不带锁也不需要锁 —— 计件只读这个数，不改它。
    """
    return (
        select(func.count(Bundle.id))
        .where(
            Bundle.doc_id == doc_id,
            Bundle.color_code == color_code,
            Bundle.size_code == size_code,
            Bundle.deleted_at.is_(None),
        )
        .select_from(Bundle)
    )


def build_bound_stmt(
    user_id: UUID, style_no: str, operation_no: str, work_date: date
) -> Select[Any]:
    """§5.1 ③ 当日绑定校验：命中即 1，未命中即 ``32002``。

    ⚠️ **判定同时要求 ``is_active = true``**：解绑走的是 ``is_active=false``（软删），
    只判「有这条绑定」会让解绑后当天仍然能计件 —— 而解绑的语义正是「现在不做了」。
    """
    return select(func.count(PieceworkBinding.id)).where(
        PieceworkBinding.user_id == user_id,
        PieceworkBinding.style_no == style_no,
        PieceworkBinding.operation_no == operation_no,
        PieceworkBinding.work_date == work_date,
        PieceworkBinding.is_active.is_(True),
        PieceworkBinding.deleted_at.is_(None),
    )


def build_style_operation_stmt(style_no: str, operation_no: str) -> Select[Any]:
    """§5.1 ② 款号/工序匹配 → ``20001``。

    ⚠️ 同时校验 ``is_piecework``：非计件工序（如「查衫」「包装」）**不许计件**，
    而工序表是用户自定义的（09 §4.3），一个款号可以配出任意工序名。
    """
    return select(StyleOperation.style_no).where(
        StyleOperation.style_no == style_no,
        StyleOperation.operation_no == operation_no,
        StyleOperation.is_piecework.is_(True),
        StyleOperation.deleted_at.is_(None),
    )


def build_insert_log_stmt(log: PieceworkLog) -> Insert:
    """§5.1 ⑧ 插入流水。

    用 Core ``insert()`` 而非 ``session.add()``：并发下唯一键
    ``uq_piecework_logs_idem`` 冲突会抛 ``IntegrityError``，而 ORM 实体一旦
    ``add()`` 进 session，冲突之后 session 状态就脏了 —— 要回查再返回幂等结果，
    就得先 ``expunge`` / ``rollback`` 到 savepoint，比 Core 插入麻烦得多。
    冲突处理见 :mod:`.service` ⑩。
    """
    return insert(PieceworkLog).values(
        # ⚠️ **必须显式给 id**：``BaseModel.id`` 的默认值是**服务端** ``gen_random_uuid()``，
        # 只在 Python 侧构造对象的话它还是 None —— 而响应要立刻回 ``log_id``、
        # ``document_logs.doc_id`` 也要拿它。不给就得 flush 后再 SELECT 一次，
        # 而 Core insert 没有「flush 后对象自动刷新」这回事。
        id=log.id,
        bundle_no=log.bundle_no,
        style_no=log.style_no,
        operation_no=log.operation_no,
        employee_id=log.employee_id,
        workshop_id=log.workshop_id,
        group_no=log.group_no,
        work_date=log.work_date,
        bundle_id=log.bundle_id,
        qty=log.qty,
        unit_price=log.unit_price,
        amount=log.amount,
        log_type=log.log_type,
        source_station=log.source_station,
        scanned_at=log.scanned_at,
        device_id=log.device_id,
        idempotency_key=log.idempotency_key,
        reversal_of_id=log.reversal_of_id,
        remark=log.remark,
        created_by=log.created_by,
        updated_by=log.updated_by,
    )


def build_find_log_stmt(
    bundle_no: str, operation_no: str, employee_id: UUID, work_date: date
) -> Select[Any]:
    """§5.1 ⑦ 幂等回查：同员工同码同日同工序的 ``SCAN`` 流水。"""
    return select(PieceworkLog).where(
        PieceworkLog.bundle_no == bundle_no,
        PieceworkLog.operation_no == operation_no,
        PieceworkLog.employee_id == employee_id,
        PieceworkLog.work_date == work_date,
        PieceworkLog.log_type == PieceworkLogType.SCAN,
        PieceworkLog.deleted_at.is_(None),
    )


def build_mark_counted_stmt(
    bundle_id: UUID,
    counted_qty: Decimal,
    employee_id: UUID,
    operator_id: UUID,
    counted_at: datetime,
) -> Update:
    """§5.1 ⑨ 回写 ``bundles.counted_*``。

    ⚠️ **只写这三列**，绝不碰 ``bundle_qty`` / ``hands``（BR-PW-32）——
    「这一手多少件」是裁剪侧给的，计件侧改它就是抢了打菲的活，
    而 DB 上 ``ck_bundles_cnt (counted_qty ≤ bundle_qty)`` 只能拦住「计多了」，
    拦不住「把 60 改成 12」这类把件数改小、于是永远计不满的。
    ⚠️ **``counted_by`` 是员工、``updated_by`` 是操作员**（ADR-0016 决策 3）：
    工位机是组长替员工扫码，两个 id 指向不同的人 —— 混用会让 ``32001`` 的提示
    变成「你已计」，而扫码的人正是他本人。

    ⚠️ ``version`` 一并 +1：码的并发改动（打菲改手数）走乐观锁，两边都看得到。
    """
    return (
        update(Bundle)
        .where(Bundle.id == bundle_id, Bundle.deleted_at.is_(None))
        .values(
            counted_qty=counted_qty,
            counted_by=employee_id,
            counted_at=counted_at,
            updated_by=operator_id,
            updated_at=datetime.now(tz=UTC),
            version=Bundle.version + 1,
        )
    )


def build_today_stmt(employee_id: UUID, work_date: date) -> Select[int, Decimal, Decimal]:
    """今日汇总：``today_hands`` / ``today_qty`` / ``today_amount``。

    ⚠️ ``hands`` 口径是 ``COUNT(*)``（**手数**），``SUM(qty)`` 是**件数** ——
    ADR-0016 §6 规定手数是主计数，所以一个 ``COUNT`` 不能省。
    ⚠️ 按 ``work_date`` 而非 ``scanned_at`` 的日期区间：``work_date`` 是
    业务日（Asia/Shanghai 推导，BR-PW-11），跨零点扫码要算前一天。
    """
    return select(
        func.count(PieceworkLog.id),
        func.coalesce(func.sum(PieceworkLog.qty), 0),
        func.coalesce(func.sum(PieceworkLog.amount), 0),
    ).where(
        PieceworkLog.employee_id == employee_id,
        PieceworkLog.work_date == work_date,
        PieceworkLog.deleted_at.is_(None),
    )


def build_is_counted_stmt(bundle_id: UUID) -> Select[UUID | None, datetime | None, Decimal]:
    """⑩ 唯一索引冲突后的回查判定（不锁行 —— 此时已在本事务内）。"""
    return select(Bundle.counted_by, Bundle.counted_at, Bundle.counted_qty).where(
        Bundle.id == bundle_id, Bundle.deleted_at.is_(None)
    )


__all__ = [
    "PieceworkLogType",
    "build_bound_stmt",
    "build_find_log_stmt",
    "build_hands_total_stmt",
    "build_insert_log_stmt",
    "build_is_counted_stmt",
    "build_lock_bundle_by_id_stmt",
    "build_lock_bundle_stmt",
    "build_log_for_update_stmt",
    "build_mark_counted_stmt",
    "build_reset_counted_stmt",
    "build_style_operation_stmt",
    "build_today_stmt",
]


def build_lock_bundle_by_id_stmt(bundle_id: UUID) -> Select[Any]:
    """红冲 / 补录时**锁码**（§7.3：红冲也要 ``FOR UPDATE``，否则与"他人扫同码"
    并发会两头各写各的 —— 红冲清空 `counted_*` 的那一刻，别人扫进来就撞空）。

    ⚠️ 与 :func:`build_lock_bundle_stmt` **同列同锁**（`FOR UPDATE OF Bundle`），
    只是 WHERE 换成 ``id`` —— 红冲已经拿着 `bundle_id`，不必再反查一次 `bundle_no`。
    """
    return (
        select(*_LOCK_COLUMNS)
        .select_from(Bundle)
        .join(BundlingOrder, BundlingOrder.id == Bundle.doc_id)
        .join(Color, Color.color_code == Bundle.color_code, isouter=True)
        .join(Size, Size.size_code == Bundle.size_code, isouter=True)
        .outerjoin(User, User.id == Bundle.counted_by)
        .where(Bundle.id == bundle_id, Bundle.deleted_at.is_(None))
        .with_for_update(of=Bundle)
    )


def build_reset_counted_stmt(bundle_id: UUID, operator_id: UUID) -> Update:
    """红冲的**同事务清空** `bundles.counted_*`（该手回到未计件，§5.3 步骤 3）。

    ⚠️ 只清这三列，**不碰** `bundle_qty` / `hands`（BR-PW-32）。回写三列是计件侧的
    特权，但 `bundle_qty` 是裁剪侧的权威 —— 红冲是「这手没人做了」，不是「这手变小了」。

    ⚠️ `counted_by` / `counted_at` 置 ``NULL`` 而非 0 / now：判定「未计件」看
    ``counted_at IS NULL``（`idx_bundles_counted_pending` 的谓词也是它），
    置 0 或置 now 会让「该手有没有人做」的判定与这里的清空**分叉**。
    """
    return (
        update(Bundle)
        .where(Bundle.id == bundle_id, Bundle.deleted_at.is_(None))
        .values(
            counted_qty=Decimal(0),
            counted_by=None,
            counted_at=None,
            updated_by=operator_id,
            updated_at=datetime.now(tz=UTC),
            version=Bundle.version + 1,
        )
    )


def build_log_for_update_stmt(log_id: UUID) -> Select[Any]:
    """红冲先按 id 取原流水（**要能判定是否已结算 / 已红冲 / 越权**）。

    ⚠️ 返回类型按 `Select[Any]`：mypy 对 `Select` 的泛型是**不变的**，
    写死 16 列的精确形状是纯噪音，且加列就要改签名。
    """
    cols: tuple[Any, ...] = (
        PieceworkLog.id,
        PieceworkLog.bundle_id,
        PieceworkLog.bundle_no,
        PieceworkLog.style_no,
        PieceworkLog.operation_no,
        PieceworkLog.employee_id,
        PieceworkLog.workshop_id,
        PieceworkLog.group_no,
        PieceworkLog.work_date,
        PieceworkLog.qty,
        PieceworkLog.unit_price,
        PieceworkLog.amount,
        PieceworkLog.log_type,
        PieceworkLog.payroll_id,
        PieceworkLog.reversal_of_id,
        PieceworkLog.created_by,
    )
    return (
        select(*cols)
        .where(PieceworkLog.id == log_id, PieceworkLog.deleted_at.is_(None))
        .with_for_update(of=PieceworkLog)
    )
