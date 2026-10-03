"""编码与序号生成（docs/04 §9、modules/01 §2 R1）。

P0 只有**建议货号**一个生成器（``style_no_sequences``），其余编码全部由用户自定义
（工序号 R16、颜色 / 尺码码 R25、分类码）。所以本模块刻意**只有取号**一件事，
不做"编码格式校验" —— 那是业务规则，不能由 AI 发明（AGENTS.md §2.3）。

⚠️ **建议号不是强制的**：业务方 2026-10-03 决策（Q-P0-04）明确「款号 = 工厂自己
的货号，由用户自定义」。服务端给出的 ``HB-2026-0001`` 只是一个**建议**，用户输入
的号一律优先，冲突时由 ``uq_styles_no`` 兜底。这条设计决定本模块存在的唯一理由
是"给个不重复的建议"，而不是"决定款号"。

取号必须**并发安全**（验收：同客户同年 20 并发不重复）。做法是两步、都不抛异常：

    1. ``INSERT ... ON CONFLICT DO NOTHING``：保证计数器行存在
    2. ``UPDATE ... SET next_no = next_no + 1 RETURNING next_no - 1``：原子取号

第 2 步的 ``RETURNING next_no - 1`` 是关键：``next_no`` 的语义是**下一个可用序号**
（迁移 0005 的列注释），所以先把计数器加 1 再减 1 返回，拿到的就是本次分配到的号。
两个语句之间由行锁串行化，并发取号必然不重复。

⚠️ 为什么不用 ``INSERT ... ON CONFLICT DO UPDATE ... RETURNING``：那条语句在
``INSERT`` 分支返回 1、在 ``DO UPDATE`` 分支返回**加 1 之后**的值，两个分支语义
不一致，没法同时表达"从 1 开始"和"下一个可用"。用 DO NOTHING + UPDATE 就没这问题。
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Final
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessError, ErrorCode

#: 业务时区。docs/11 部署规范把 PG 配成 ``timezone=Asia/Shanghai``，而"复制当天 /
#: 建议号年份"这类口径**必须与工厂当地的日历一致** —— 用宿主机本地时区的话，
#: 容器时区一旦与工厂不同，"今天"就会差一天，于是建议号年份差一档。
BUSINESS_TZ: Final[ZoneInfo] = ZoneInfo("Asia/Shanghai")

#: 建议号里的**年份位数**（R1：``{客户前缀}-{年份}-{4 位}``）
YEAR_DIGITS = 4
#: 建议号里的**序号位数**（R1 固定 4 位：``0001``）
SEQUENCE_DIGITS = 4
#: 一个 ``(客户, 年份)`` 分组下建议号的最大个数 = 9999（4 位序号的自然上限）
MAX_SEQUENCE = 10**SEQUENCE_DIGITS - 1

#: **无客户**时建议号的前缀。
#:
#: ⚠️ 业务决策 2026-10-03（Q-P0-10）：``styles.customer_id`` 可空，款号不该被客户
#: 绑死，所以"无客户的款"是一等公民，它同样需要建议号。规范（R1）只写了
#: ``{客户前缀}-{年份}-{4 位}``，没有说全厂序列用什么前缀 —— 这一点**规范里没有**，
#: 因此取一个显式常量而不是散落的魔法字符串，并在此登记为待确认项。
FACTORY_STYLE_PREFIX = "ST"


def normalize_style_no(raw: str) -> str:
    """款号规范化：**去首尾空白 + 转大写**（R1「大小写不敏感唯一」）。

    TC-B06：传 ``hb-2026-0002`` 存库必须是 ``HB-2026-0002``，且唯一性按大写判。
    大小写归一必须在 **service 入口**做，只靠数据库的 ``upper()`` 唯一索引会出现
    "查重查得到、插不进去"的诡异报错。
    """
    return raw.strip().upper()


def business_today() -> date:
    """业务"今天"（工厂当地日历，见 :data:`BUSINESS_TZ`）。

    ⚠️ **不要用 ``date.today()``**：它取宿主机本地时区。容器时区与工厂不一致时
    （部署在海外 VPS、或 CI 跑在 UTC），"今天"会差一天，于是模板复制写入的
    ``effective_from`` 与用户看到的日期对不上，而调价区间也就错了一档。
    """
    return datetime.now(tz=BUSINESS_TZ).date()


def format_style_no(prefix: str, year: int, sequence: int) -> str:
    """拼建议号：``{前缀}-{年份}-{4 位序号}``（R1）。

    :raises BusinessError: 序号超出 4 位 —— 继续下去会拼出 ``HB-2026-10000`` 这种
        违反 R1 格式的号，而"建议号格式不符"比"没有建议号"更难排查。
    """
    if not 1 <= sequence <= MAX_SEQUENCE:
        raise BusinessError(
            ErrorCode.PARAM_INVALID,
            f"本年度建议号已用尽（上限 {MAX_SEQUENCE} 个），请手动输入款号",
            details={"prefix": prefix, "year": year, "max_sequence": MAX_SEQUENCE},
        )
    return f"{prefix}-{year:0{YEAR_DIGITS}d}-{sequence:0{SEQUENCE_DIGITS}d}"


async def take_style_no(session: AsyncSession, *, customer_id: UUID | None, year: int) -> int:
    """取一个建议序号（从 1 开始，按 ``(customer_id, year)`` 分组递增）。

    Q-P0-05 / 任务卡：序号按**客户**分组递增，客户为空落**全厂序列**
    （``style_no_sequences`` 用两条部分唯一索引分别管这两段，见迁移 0005）。

    ⚠️ **必须在 service 的事务内调用**：本方法靠行锁串行化，放到事务外两次并发
    调用会各自开一条隐式事务，`UPDATE` 照样串行但**返回后计数器就失效**了 ——
    更糟的是后面那条 ``INSERT`` 在另一个事务里，计数器行会被回滚掉。
    """
    await _ensure_counter_row(session, customer_id=customer_id, year=year)
    stmt = (
        update(_counter_model())
        .where(
            _counter_model().customer_id.is_(None)
            if customer_id is None
            else _counter_model().customer_id == customer_id,
            _counter_model().year == year,
        )
        .values(next_no=_counter_model().next_no + 1)
        .returning(_counter_model().next_no - 1)
    )
    allocated = (await session.execute(stmt)).scalar_one()
    return int(allocated)


async def suggest_style_no(
    session: AsyncSession, *, prefix: str, customer_id: UUID | None, year: int | None = None
) -> str:
    """生成一个建议货号。

    :param prefix: 客户编码（``customers.code``）；无客户传 :data:`FACTORY_STYLE_PREFIX`
    :param year: 年份，默认今天所在年（测试可显式传入，避免跨年失败 —— docs/10 §10）
    """
    resolved_year = year if year is not None else business_today().year
    sequence = await take_style_no(session, customer_id=customer_id, year=resolved_year)
    return format_style_no(prefix, resolved_year, sequence)


def quantize_price(value: Decimal, places: int = 6) -> Decimal:
    """金额量化到 ``places`` 位小数。

    ⚠️ 用 ``ROUND_HALF_UP`` 而不是默认的 ``ROUND_HALF_EVEN``：单价是给人看、
    给人算的钱，"0.5 分进位"按银行家舍入会让同一笔调价在不同机器上不一致，
    而 ``1.0800 × 0.350000 = 0.378000``（TC-B13）这类断言必须是确定的。
    """
    return value.quantize(Decimal(1).scaleb(-places), rounding="ROUND_HALF_UP")


def _counter_model() -> type[Any]:
    """取 ``StyleNoSequence`` 模型。

    函数内导入：``app.modules.base.models`` 依赖 ``app.common.models``，
    而 ``core`` 是更底层 —— 顶层导入会形成 ``core → modules → core`` 的环。
    """
    from app.modules.base.models import StyleNoSequence

    return StyleNoSequence


async def _ensure_counter_row(
    session: AsyncSession, *, customer_id: UUID | None, year: int
) -> None:
    """确保 ``(customer_id, year)`` 的计数器行存在。

    ⚠️ 用 ``ON CONFLICT DO NOTHING`` 而不是"先查后插"：并发首次取号时两个事务
    都可能查到"没有行"，然后都去插 —— 后者靠唯一索引报 ``IntegrityError``，
    而**异常会把整个事务置为 aborted**，调用方后续任何语句都失败。DO NOTHING
    不抛异常，因此后面那句 ``UPDATE`` 在任何并发形态下都能拿到行。
    """
    model = _counter_model()
    stmt = pg_insert(model).values(customer_id=customer_id, year=year, next_no=1)
    if customer_id is None:
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["year"], index_where=model.customer_id.is_(None)
        )
    else:
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["customer_id", "year"],
            index_where=model.customer_id.is_not(None),
        )
    await session.execute(stmt)


__all__ = [
    "BUSINESS_TZ",
    "FACTORY_STYLE_PREFIX",
    "MAX_SEQUENCE",
    "SEQUENCE_DIGITS",
    "YEAR_DIGITS",
    "business_today",
    "format_style_no",
    "normalize_style_no",
    "quantize_price",
    "suggest_style_no",
    "take_style_no",
]
