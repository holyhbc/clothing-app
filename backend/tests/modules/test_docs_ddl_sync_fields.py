"""「modules 的字段表」↔「`04 §7` 的 DDL」必须**双向**一致（TD4-01，搬运保真度）。

## 这条守卫守的是什么

`04 §7` 号称「关键表结构约定」，而一批表的字段表原本只在 `modules/XX` 里 ——
于是「照 04 建表」这条路对它们**走不通**，而下一个人只能**照字段表**建，
或者照 ORM 反推。规范分裂的直接后果不是报错，是**两份都可能对、且没人比过**。

搬错的概率不低，而**搬错不一定报错**：

- 列少一个 → 代码里的 INSERT 会报（还算好）
- 列多一个 / 类型差一位（`numeric(14,3)` vs `numeric(14,4)`）→ **要到运行时数据溢出才炸**
- 两边都"对"但口径已经分叉 → 永远没人发现

而搬运是**一次性动作**：搬完之后没人会再对照两边。所以「搬错了但没人发现」能一直留着 ——
T-CUT-001a 就是在搬运裁剪单四表时当场撞出 `hands_total` 声明位置错
（`04 §2.1` TD2-01 抓的）与 `CHECK (> 0)` 和 `DEFAULT 0` 自相矛盾两处。

## 覆盖的表（每加一张都要确认字段表能被解析器读出来）

| 表 | 字段表出处 | `04 §7` 出处 | 搬运卡 |
| --- | --- | --- | --- |
| `bundling_orders` / `bundling_order_lines` | `modules/03` §3.1/§3.2 | §7.16 | T-BASE-004 |
| `stock_ledgers` / `stock_ledger_lines` / `stock_reservations` | `modules/06` §3.3/§3.4/§3.5 | §7.2.5 | T-BASE-008 |
| `cutting_outputs` / `cutting_scrap_records` | `modules/02` §3.6/§3.7 | §7.7.5 | T-BASE-008 |
| `bom_items` | `modules/01` §3.3 | §7.15.4 | T-BASE-008 |

## 为什么单独一个文件

`test_docs_ddl_sync.py` 早已 824 行，越过 `AGENTS §7.1` 的**单文件 400 行硬线**
（存量违规登记在 `docs/12` L-081，本轮只加规则不回改存量）。T-BASE-008 要给它
再加 6 张表的保真度断言，塞进去只会更深。

于是把**同一条守卫整体搬出来** —— 不是新开一个「例外文件」，而是让文件边界
对上职责边界：那份文件守「`04 §7` ↔ 真实建的表」，这份守「`04 §7` ↔ modules 字段表」。
搬完之后旧文件反而回到 700 出头。守卫语义没变。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ⚠️ 从**兄弟测试模块**导入解析器，而不是在这里重抄一份正则：
#    这两份文档守卫的价值全在「解析器认得真实文档的写法」上，
#    重抄一份就多一个会各自漂移的副本 —— 而漂移的方向恰好是「守卫变假绿」。
from test_docs_ddl_sync import (
    COMMON_COLUMNS,
    DOC_PUBLIC_COLUMNS,
    DOC_PUBLIC_CONVENTION_TABLES,
    _documented_ddl,
    _parse_columns,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DOC_04 = REPO_ROOT / "docs" / "04-数据库规范.md"

#: 表名 → （字段表所在的 docs 文件、起始标记、结束标记）。
#:
#: ⚠️ 结束标记用**下一个 `###` 标题**，这样字段表里多一行说明不会把守卫读歪。
FIELD_TABLE_SOURCES: dict[str, tuple[str, str, str]] = {
    # 打菲（T-BASE-004）
    "bundling_orders": ("modules/03-打菲.md", "### 3.1 `bundling_orders`", "### 3.2 "),
    "bundling_order_lines": ("modules/03-打菲.md", "### 3.2 `bundling_order_lines`", "### 3.3 "),
    # 打菲标签打印记录（T-BUND-001；append-only，DDL 见 04 §7.16）
    "bundle_label_prints": (
        "modules/03-打菲.md",
        "### 3.4 `bundle_label_prints`",
        "### 3.5 ",
    ),
    # 库存台账（T-BASE-008）
    "stock_ledgers": ("modules/06-库存.md", "### 3.3 `stock_ledgers`", "### 3.4 "),
    "stock_ledger_lines": ("modules/06-库存.md", "### 3.4 `stock_ledger_lines`", "### 3.5 "),
    "stock_reservations": ("modules/06-库存.md", "### 3.5 `stock_reservations`", "### 3.6 "),
    # 裁剪结转与布头（T-BASE-008）
    "cutting_outputs": ("modules/02-裁剪.md", "### 3.6 `cutting_outputs`", "### 3.7 "),
    "cutting_scrap_records": ("modules/02-裁剪.md", "### 3.7 `cutting_scrap_records`", "### 3.8 "),
    # 组件 BOM（T-BASE-008）
    "bom_items": ("modules/01-基础资料.md", "**`bom_items`（组件 BOM", "### 3.4 "),
}

#: **字段表里有、`04 §7` 刻意不建列** → 为什么不建。
#:
#: ⚠️ 与 `DDL_ONLY_COLUMNS` 方向相反，别弄混：这里是「字段表多写了」，
#: 那里是「DDL 多建了」。两边的差集都是要红的，所以每一项都必须写清理由。
FIELD_TABLE_ONLY_COLUMNS: dict[str, set[str]] = {
    # modules/02 §3.6 自己写明「PG 生成列不支持跨表，**由视图**计算」——
    # 建成普通列就得靠 service 维护，于是「可打菲余量」多出第四份可被忘记的口径
    "cutting_outputs": {"available_qty"},
}

#: **`04 §7` 里建了、字段表里刻意没有** → 为什么不建。
DDL_ONLY_COLUMNS: dict[str, set[str]] = {
    # ADR-0016 之后码内不再有全局件序号，无「件号区间」概念。
    # ⚠️ `modules/03 §3.1` 字段表里这两列标的是「❌ 作废列」，
    #    **不要**照抄成真列（守卫报「DDL 有列、字段表没有」就是这条在生效）
    "bundling_orders": {"source_bundle_seq_from", "source_bundle_seq_to"},
}


def _strip_strikethrough(cell: str) -> str:
    """把 ``~~作废列~~`` 整段去掉。

    ⚠️ 这一步不是可有可无的洁癖：`modules/06 §3.3` 用删除线标了
    ``~~unit_cost`` / ``amount``~~ 两列（REV-2026-10 从台账移除成本列），
    而「取反引号里的标识符」会把它们照收 —— 于是守卫报「字段表里有列、DDL 没建」，
    而 DDL **恰恰是对的**。假失败比没守卫更坏：它会让人习惯性忽略这条断言。
    """
    return re.sub(r"~~.*?~~", "", cell, flags=re.DOTALL)


def _field_table_columns_from(text: str, source: tuple[str, str, str]) -> set[str]:
    """从一张表的**字段表**（不是整个小节）里抽出列名集合。不含任何排除项。

    ⚠️ 只读**起始标记之后的第一张 markdown 表**，读到第一个非 `|` 行就停 ——
    这不是省事，是必需的：`modules/06 §3.3` 的字段表下面还有一张
    「哪些动作不写台账」的表，它第一格里有 `` `approve` `` 与 `` `balance_qty` ``，
    按「读到下一个标题为止」去扫整节就会把这两个词当成列收进来，
    守卫于是报「字段表里有列、DDL 没建」—— 而 DDL **是对的**。
    假失败比没守卫更坏：它会让人习惯性忽略这条断言，那时守卫就白写了。

    ⚠️ 字段表里**有一行是 `` `stock_type` / `stock_id` `` 这种「多个字段写在一行」**
    的形态（`modules/06 §3.3` 至少三处），所以不能只取反引号里的第一个 ——
    要把行内**所有**反引号标识符都算上。漏掉后半截会让「文档比字段表多一列」
    永远不成立，而那正是要抓的方向之一。

    ⚠️ 只读**第一格**：字段表的「类型」格里也有反引号（`` `uuid` FK→`styles` ``），
    全行扫会把类型名与外键目标表名一起收进来。

    ⚠️ **起始标记找不到时必须 assert 而不是静默返回空集**：返回空集会让
    「字段表里有列、DDL 没建」那条差集恒为空，守卫从此对这张表完全失效，
    而且**全绿** —— 小节改个名就报废一条守卫，而症状是「什么都没发生」。
    """
    doc_rel, start_marker, end_marker = source
    assert start_marker in text, (
        f"{doc_rel} 里找不到字段表起始标记 {start_marker!r} —— "
        "守卫读了个空段落会**全绿**，那比没有守卫更坏（小节改名了）"
    )
    section = text.split(start_marker)[1].split(end_marker)[0]
    columns: set[str] = set()
    in_table = False
    for line in section.split("\n"):
        if not line.strip().startswith("|"):
            if in_table:
                break  # 字段表结束（表下面开始是说明文字 / 第二张表）
            continue
        in_table = True
        first_cell = _strip_strikethrough(line.strip().strip("|").split("|")[0])
        columns |= set(re.findall(r"`([a-z_][a-z0-9_]*)`", first_cell))
    return columns


def _field_table_columns(source: tuple[str, str, str]) -> set[str]:
    doc_rel, _, _ = source
    return _field_table_columns_from(
        (REPO_ROOT / "docs" / doc_rel).read_text(encoding="utf-8"), source
    )


def _ddl_columns(table: str) -> set[str]:
    return _documented_ddl().get(table, set())


@pytest.mark.parametrize("table", sorted(FIELD_TABLE_SOURCES))
def test_field_tables_match_04_ddl(table: str) -> None:
    """TD4-01：字段表与 `04 §7` 的 DDL **双向**一致（搬运保真度）。

    ⚠️ 这条守卫同时把「搬运」的**代价显式化**：以后任何人改其中一边，
    另一边会被逼着同步 —— 那正是「搬运」应该有的后续维护。搬完就没人再看两遍，
    所以差异只会单向漂移。
    """
    in_ddl = (_ddl_columns(table) - COMMON_COLUMNS) - DDL_ONLY_COLUMNS.get(table, set())
    in_field = (
        _field_table_columns(FIELD_TABLE_SOURCES[table])
        - COMMON_COLUMNS
        - FIELD_TABLE_ONLY_COLUMNS.get(table, set())
    )
    if table in DOC_PUBLIC_CONVENTION_TABLES:
        in_field -= DOC_PUBLIC_COLUMNS

    assert not (in_field - in_ddl), (
        f"字段表里有列，而 04 §7 的 DDL 没建：{sorted(in_field - in_ddl)}。"
        f"要么补 DDL，要么这列不该建 —— 后者要写进 {table} 的排除项并说明理由"
    )
    assert not (in_ddl - in_field), (
        f"04 §7 的 {table} DDL 有列，而字段表里没有：{sorted(in_ddl - in_field)}。"
        f"要么补字段表，要么这列不该建（字段表里有几处明确标了「❌ 作废列」，"
        f"**不要**照抄成真列）"
    )


def test_the_fidelity_guard_itself_can_fail() -> None:
    """TD4-02：**反验这条守卫**，别让它变成「永远通过的空壳」。

    ⚠️ 做法是**改真正被解析的那两份文档内容再还原**，而不是 monkeypatch 掉
    `_ddl_columns` 的返回值 —— 后者验证的是「我改的返回值生效了」，
    不是「解析器真能从 markdown 里抽出列」。后者才是会骗人的那种「反验通过」。

    ⚠️ 注入的形状取自**原文里真实存在的形态**：字段表里的删除线列
    （``modules/06 §3.3`` 的 ``~~unit_cost`` / ``amount````）与
    DDL 块里的注释行。造一段人造表格的话，失败往往是因为写法不符合解析器，
    而那证明不了解析器在真实文档上抓得住问题。
    """
    # ---- 方向一：字段表多一列 → 必须报「字段表里有列、DDL 没建」
    source = FIELD_TABLE_SOURCES["stock_ledger_lines"]
    doc_rel = source[0]
    text = (REPO_ROOT / "docs" / doc_rel).read_text(encoding="utf-8")
    injected = text.replace(
        "| `qty` | `numeric(14,3)` | 是 | — |",
        "| `probe_only_in_field_table` | `numeric(14,3)` | 是 | — |\n"
        "| `qty` | `numeric(14,3)` | 是 | — |",
        1,
    )
    assert injected != text, "反验失败：没找到 stock_ledger_lines 字段表里的 `qty` 行"
    # ⚠️ 长表达式提成局部变量而不是内联进 `assert`：ruff 0.8.4（pre-commit 钩子 pin 的
    #    版本）与仓库 venv 的 0.16.10 对「超长 assert 的换行位置」给出**不同**结果，
    #    内联会让两个 formatter 互相改写对方（详见 docs/12 §5 L-087）
    parsed = _field_table_columns_from(injected, source)
    ddl_columns = _ddl_columns("stock_ledger_lines")
    assert "probe_only_in_field_table" in parsed, (
        "反验失败：注入的列没被解析出来 —— 说明字段表解析器这条路根本没被执行，而 TD4-01 会一直假绿"
    )
    assert "probe_only_in_field_table" not in ddl_columns, "反验失败：这个列名居然真的在 04 §7 里"

    # ---- 方向二：DDL 里多一列 → 必须报「DDL 有列、字段表没有」
    ddl_text = DOC_04.read_text(encoding="utf-8")
    injected_ddl = ddl_text.replace(
        "    amount            numeric(18,4) NOT NULL,",
        "    amount            numeric(18,4) NOT NULL,\n"
        "    probe_only_in_ddl  numeric(9,9) NOT NULL,",
        1,
    )
    assert injected_ddl != ddl_text, "反验失败：没找到 04 §7.2.5 里 stock_ledger_lines 的 amount 列"
    ddl_parsed = _documented_ddl_from(injected_ddl)["stock_ledger_lines"]
    assert "probe_only_in_ddl" in ddl_parsed, "反验失败：注入的列没被 DDL 解析器抽出来"

    # ---- 方向三：删除线列**不得**进集合（否则 `modules/06 §3.3` 的
    #      `~~unit_cost` / `amount`~~ 会让守卫天天报假失败）
    struck = _field_table_columns(FIELD_TABLE_SOURCES["stock_ledgers"])
    assert not ({"unit_cost", "amount"} & struck), (
        "反验失败：删除线标掉的作废列被当成真列收进来了 —— "
        "守卫会对一个**正确**的 DDL 报「字段表里有列、DDL 没建」"
    )
    assert "source_doc_type" in struck, "反验失败：字段表解析器读不出正常的列（回归了）"


def _documented_ddl_from(text: str) -> dict[str, set[str]]:
    """:func:`test_docs_ddl_sync._documented_ddl` 的可注入版本（反验用）。"""
    section = text.split("## 7. 关键表结构约定")[1].split("\n## 8.")[0]
    documented: dict[str, set[str]] = {}
    for block in re.findall(r"```sql\n(.*?)```", section, re.DOTALL):
        for match in re.finditer(
            r"CREATE TABLE (?:IF NOT EXISTS )?(\w+)\s*\((.*?)\n\);", block, re.DOTALL
        ):
            documented[match.group(1)] = _parse_columns(match.group(2))
    return documented
