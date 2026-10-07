"""打菲单号与 `bundle_no` 纯函数（T-BUND-002 / docs/modules/03-打菲.md §5.2 / 09 §2.4）。

关键约束：
- **复用** `core/numbering.py::take_doc_no(prefix="BD")`，**不新增计数器表、不新增迁移**。
- `bundle_no` 格式：`{打菲单号}-{尺码码 1~3 位}{手序号 2 位}-{件序号 4 位=0001}`。
- Q-B18 未闭环前：尺码码允许 1~3 位，**不落 DB CHECK**，留 `TODO(业务待确认: Q-B18)`。
- 件序号一码一手时恒为 `0001`（Q-B10 未闭环，不生成 >0001）。
- 单文件 ≤400 行，纯函数无副作用，便于单测与复用。
"""

import re
from datetime import date
from typing import NamedTuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.numbering import take_doc_no

# ──────────────────────────────────────────────────────────────────────────────
# 常量
# ──────────────────────────────────────────────────────────────────────────────

# 手序号固定 2 位、件序号固定 4 位（09 §2.4、ADR-0016 §1）
HANDS_SEQ_DIGITS = 2
ITEM_SEQ_DIGITS = 4
DEFAULT_ITEM_SEQ = 1  # 一码一手时恒为 0001

# Q-B18 待确认：尺码码长度 1~3 位（S / XL / XXL / 3XL）
# TODO(业务待确认: Q-B18) 尺码码长度固定后，此处正则与 DB CHECK 需同步收敛
SIZE_CODE_PATTERN = r"[A-Z0-9]{1,3}"
# 使用字符串拼接而不是 f-string 避免嵌套大括号问题
BUNDLE_NO_PATTERN = (
    "^BD-[0-9]{8}-[0-9]{6}-"
    + SIZE_CODE_PATTERN
    + "[0-9]{"
    + str(HANDS_SEQ_DIGITS)
    + "}-"
    + "[0-9]{"
    + str(ITEM_SEQ_DIGITS)
    + "}$"
)

# 编译正则，供 is_valid_bundle_no 复用
_BUNDLE_NO_RE = re.compile(BUNDLE_NO_PATTERN)


# ──────────────────────────────────────────────────────────────────────────────
# 数据结构
# ──────────────────────────────────────────────────────────────────────────────


class ParsedBundleNo(NamedTuple):
    """`parse_bundle_no` 的返回值。"""

    doc_no: str
    size_code: str
    hands_seq: int
    item_seq: int


# ──────────────────────────────────────────────────────────────────────────────
# 单据号接线：next_doc_no
# ──────────────────────────────────────────────────────────────────────────────


async def next_doc_no(session: AsyncSession, doc_date: date | None = None) -> str:
    """取一个打菲单号 `BD-YYYYMMDD-6位`（复用 `take_doc_no`，不新增表）。

    :param session: 必须在 service 事务内传入（`take_doc_no` 靠行锁串行化）。
    :param doc_date: 业务日期，默认今天（工厂当地日历）。
                     **测试必须显式传入**，避免跨零点失败（docs/10 §10）。
    :return: 如 `BD-20261018-000031`
    """
    return await take_doc_no(session, prefix="BD", doc_date=doc_date)


# ──────────────────────────────────────────────────────────────────────────────
# bundle_no 构造 / 解析 / 校验
# ──────────────────────────────────────────────────────────────────────────────


def build_bundle_no(
    doc_no: str,
    size_code: str,
    hands_seq: int,
    item_seq: int = DEFAULT_ITEM_SEQ,
) -> str:
    """构造 `bundle_no`：`{doc_no}-{size_code}{hands_seq:02d}-{item_seq:04d}`。

    :param doc_no: 打菲单号，如 `BD-20261018-000031`（必须符合 09 §2.1）。
    :param size_code: 尺码码，**允许 1~3 位**（Q-B18 待确认），如 `L` / `XL` / `XXL` / `3XL`。
    :param hands_seq: 手序号（同单同色同尺码内从 1 递增），必须 ≥ 1。
    :param item_seq: 件序号，一码一手时恒为 1（默认），预留"一手拆多码"扩展（Q-B10）。
    :return: 如 `BD-20261018-000031-XL02-0001`
    :raises ValueError: 参数不合法（尺码码为空、手序号 < 1、件序号 < 1）。
    """
    if not size_code:
        raise ValueError("size_code 不能为空")
    if hands_seq < 1:
        raise ValueError("hands_seq 必须 ≥ 1")
    if item_seq < 1:
        raise ValueError("item_seq 必须 ≥ 1")

    # 尺码码统一大写（DB 里存大写，09 §2.2 款号规则同理）
    size_code_upper = size_code.strip().upper()

    hands_part = f"{size_code_upper}{hands_seq:0{HANDS_SEQ_DIGITS}d}"
    item_part = f"{item_seq:0{ITEM_SEQ_DIGITS}d}"
    return f"{doc_no}-{hands_part}-{item_part}"


def parse_bundle_no(bundle_no: str) -> ParsedBundleNo:
    """解析 `bundle_no` 为结构化字段。

    :param bundle_no: 完整打菲号，如 `BD-20261018-000031-XL02-0001`
    :return: `ParsedBundleNo(doc_no, size_code, hands_seq, item_seq)`
    :raises ValueError: 格式不符合预期。
    """
    if not is_valid_bundle_no(bundle_no):
        raise ValueError(f"非法 bundle_no 格式: {bundle_no}")

    # 格式：BD-YYYYMMDD-NNNNNN-SIZEHH-IIII
    # 从右向左切更稳健：最后 4 位是 item_seq，前 2 位是 hands_seq，再前是 size_code
    # 但 size_code 长度可变（1~3 位），所以用正则分组
    match = _BUNDLE_NO_RE.match(bundle_no)
    if not match:
        raise ValueError(f"无法解析 bundle_no: {bundle_no}")

    # 这里按最后两个 '-' 切分
    # doc_no = BD-YYYYMMDD-NNNNNN (固定 3 段)
    # 所以前 3 段用 '-' 连接
    parts = bundle_no.split("-")
    # parts = ["BD", "YYYYMMDD", "NNNNNN", "SIZEHH", "IIII"]
    if len(parts) != 5:
        raise ValueError(f"bundle_no 段数不为 5: {bundle_no}")

    doc_no = "-".join(parts[:3])
    hands_part = parts[3]  # 如 "XL02"
    item_seq = int(parts[4])

    # hands_part: size_code (1~3 位) + hands_seq (2 位)
    # size_code 长度 = len(hands_part) - 2
    size_code_len = len(hands_part) - HANDS_SEQ_DIGITS
    if size_code_len < 1:
        raise ValueError(f"hands_part 太短无法提取 size_code: {hands_part}")

    size_code = hands_part[:size_code_len]
    hands_seq = int(hands_part[size_code_len:])

    return ParsedBundleNo(
        doc_no=doc_no,
        size_code=size_code,
        hands_seq=hands_seq,
        item_seq=item_seq,
    )


def is_valid_bundle_no(bundle_no: str) -> bool:
    """校验 `bundle_no` 格式是否合法（Q-B18 允许尺码码 1~3 位）。

    :param bundle_no: 待校验字符串
    :return: True / False
    """
    if not bundle_no:
        return False
    return bool(_BUNDLE_NO_RE.match(bundle_no))
