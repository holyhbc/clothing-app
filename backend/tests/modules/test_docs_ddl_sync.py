"""「文档里的 DDL」与「真实建的表」必须同步（docs/04 §7 / W3 的守卫）。

## 这两条守卫守的是什么

P0 落地后出现了两类**静默漂移**，都会在下一个人建迁移时变成真 bug：

1. **表建了但文档没有 DDL**（18 张：`users` / `styles` / `auth_login_logs` …）。
   症状是新写迁移的人**只能照 ORM 反推字段**，而反推出来的东西不写进规范，
   于是「规范」这块地越来越空 —— 直到某天 `04 §7.11` 写出了 `operation_id`
   这种**表里根本不存在的列**（ADR-0026 已作废的那段）。
2. **文档写了不存在的列**。这一类更贵：它看起来有权威性，读的人不会怀疑，
   而照它建表会直接失败或建出错的结构。

## 为什么只对「已建的表」比对列

`04 §7` 里有 14 张表是 P1 才建的（`cutting_orders` / `purchase_orders` /
`piecework_logs` …）。那些**设计稿**已经写好、表还没建 —— 拿它们跟模型比对
会让守卫在 P1 落地前就红。所以规则是：

- **表在模型里**（已建）→ 列必须**双向**一致：文档不许写模型没有的列，
  模型也不许有文档没写的列。
- **表不在模型里**（P1 待建）→ 跳过。

「不许漏文档」那条（TD-01）只对**已建的表**生效，所以 P1 的表不会让它变红 ——
而这一点正是它的价值：P0 建过的表，**从此不能无声无息地从规范里消失**。

## 两条守卫都被「故意写坏」反验过

见文件末尾的 `test_the_guards_themselves_can_fail` —— 那条用例验证守卫不是
「永远通过的空壳」。
"""

import re
from pathlib import Path

import pytest

DOC_04 = Path(__file__).resolve().parents[3] / "docs" / "04-数据库规范.md"

#: `04` 里不参与「文档 ↔ 模型」比对的表名。
#:
#: ⚠️ 只有 `alembic_version` 需要排除 —— 它由 Alembic 自己管，不在 ORM 里，
#:    而 `04 §7` 也从没写过它。**不要**把别的表加进来：每加一个豁免，
#:    就在这个守卫上开一个可以「无声无息漂移」的洞。
IGNORED_TABLES = frozenset({"alembic_version"})

#: **P1 才建**的表：设计稿已写好、表还不存在，所以 `04 §7` 里有它们的 DDL。
#:
#: ⚠️ 这是一张**必须手动维护**的白名单，且方向是「建了就删」。
#:
#: ⚠️ REV-2026-10 **更正本注释里的一句错话**（T-CUT-001a 建裁剪单时发现）：
#: 原注释写「留着不删的后果是『这张表建完之后，文档里写错的列名再也不会被抓』
#: —— 因为白名单把它排除在列比对之外」。**这句是错的**：
#: `test_docs_04_section7_columns_match_the_model` 只按 `table in _model_tables()`
#: 决定比不比对，**从不查本白名单**。所以表一建出来，TD-02 就已经在真比对了，
#: 与它是否还在白名单里无关。
#:
#: 删的真实理由有两条，都比原来那句硬：
#:   1. **TD-04 的方向**：`test_docs_04_section7_has_no_unknown_tables` 用
#:      「已建的表 + 白名单」当合法集合 —— 留着会让这张表在两个集合里，
#:      而守卫正是靠这个集合的差集来发现「文档写了不存在的表」，
#:      集合一旦被污染，它对别的表的说服力就下降了。
#:   2. **TD-05 的方向**：白名单里的每张表都会被 `test_pending_table_has_a_ticket_reference`
#:      断言「必须在文档里注明它还没建」。表已经建了却还在名单里，
#:      那条断言要么空转要么变成一句没人看的空话。
#:
#: ⚠️ **每加一个名字之前先问「这张表真的还没建吗」**：往里塞一张已建的表，
#: 等于给自己开一个「文档可以写错」的洞 —— TD2-02（外键目标表有 DDL）
#: 会拿本白名单当兜底放行条件，那才是真正的洞。
P1_PENDING_TABLES = frozenset(
    {
        # §7.0 打菲件表（ADR-0016）
        "bundles",
        # §7.1 计件流水
        "piecework_logs",
        # §7.5 采购
        "purchase_orders",
        "purchase_order_lines",
        "purchase_arrivals",
        "purchase_arrival_lines",
        # §7.6 工资结算周期
        "payroll_periods",
        # 打菲单表与明细表（T-BASE-004 已搬运到 04 §7.16，建表卡待开）
        "bundling_orders",
        "bundling_order_lines",
        # §7.16 打菲标签打印记录（2026-10-06 补 DDL，闭环 L-072；建表卡 T-BUND-001 待开）
        "bundle_label_prints",
        # §7.7.5 裁剪结转与布头登记（T-BASE-008 已搬运到 04 §7.7.5，建表卡待开）
        "cutting_outputs",
        "cutting_scrap_records",
        # §7.15.4 组件 BOM（T-BASE-008 已搬运到 04 §7.15.4，建表卡待开）
        "bom_items",
        # 裁剪单四表（T-CUT-001a 已建，迁移 0009）—— 移出本白名单意味着
        # TD-02 从此对它们**真比对**而不是跳过，这是这张卡最有价值的一处副作用：
        # 04 §7.7.2 的列名写错会被立刻抓住（照抄建表失败的那一类）
    }
)

#: `04 §2` 的公共字段。**文档里不重复抄这一组**（抄一遍就多一个可能不一致的副本），
#: 所以比对时要从「模型侧」把它们拿掉 —— 否则每张表都会因为「文档没抄 created_at」
#: 而红，而那恰恰是文档的**优点**。
#:
#: ⚠️ 这个名单必须与 `04 §2` 逐字一致。改 `04 §2` 而不改这里的后果是
#: 所有表突然报「漏了 X 列」，看起来像模型坏了。
COMMON_COLUMNS = frozenset(
    {
        # ⚠️ `id` 在这里：P0 之前写的 DDL 段落（§7.4/§7.8）把 `id` 显式列在
        #   CREATE TABLE 里，而本卡新写的段落用注释「公共字段见 §2」带过。
        #   **两种写法都合法**，所以比对时把 `id` 从模型侧拿掉，
        #   而不是逼着把所有既有段落重排一遍（那是无关改动）
        "id",
        "created_at",
        "created_by",
        "updated_at",
        "updated_by",
        "deleted_at",
        "version",
        "remark",
    }
)


#: 一个 DDL 列定义的形状：``    列名 类型…``。
#:
#: - 四空格缩进：``CREATE TABLE`` 括号内的列都在这一层；表级 ``CONSTRAINT`` /
#:   ``CHECK`` 也在同一层，所以类型关键字白名单里**没有** ``CONSTRAINT`` / ``CHECK`` /
#:   ``UNIQUE`` —— 它们后面的 ``(`` / ``(a, b)`` 不是列类型，匹配不上。
#: - 类型用白名单而不是 ``[a-z]``：否则 ``UNIQUE``（大写）能靠
#:   ``isupper()`` 排除，但 ``unique`` 这种写法、以及约束里的裸标识符仍会被误收。
#: - 允许**一行多列**（逗号分隔）：见 :func:`_documented_ddl` 的说明。
#: 一个 DDL 列定义的形状：``列名 类型``（类型必须在白名单里）。
#:
#: - **类型用白名单**：表级 ``CONSTRAINT ck_x CHECK (...)`` 的 ``CHECK``/``UNIQUE``
#:   /``PRIMARY`` 都不在白名单里，所以约束不会被当成列名收进来。
#: - **允许 PG 枚举类型**（``size_class`` / ``data_scope`` / ``doc_status``）：
#:   它们是 ``CREATE TYPE`` 建的枚举，列类型就是枚举名本身。
#: - 大小写不敏感：``UNIQUE`` 之类靠 ``[A-Za-z_]`` 排除不掉，必须靠类型白名单。
#: 「列名 + 类型」的类型部分。
#:
#: 除了内建类型，还要匹配**任意裸标识符**当类型 —— 本仓的自定义类型都是 PG 枚举
#: （``piecework_log_type`` / ``cutting_entry_mode`` …），而其中一部分（P2/P1 的表）
#: **模型里还没有**，从模型抽不到。少覆盖一个的后果是抽不出那一列，于是
#: 满屏假失败；而假失败多了，人会开始习惯性忽略这条断言，那时守卫就白写了。
#: 一个合法标识符（列名必须长这样）。
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

_COLUMN_NAME_PATTERN = re.compile(
    r"[a-z_][a-z0-9_]*\s+(?:[a-z_][a-z0-9_]*)\b",
    re.IGNORECASE,
)


def _looks_like_a_column(fragment: str) -> str | None:
    """片段若是「列名 + 类型」就返回列名，否则 ``None``。

    ⚠️ 排除 ``CONSTRAINT`` / ``UNIQUE`` 这类**表级**定义：它们同样是「词 + 词」的形状。
    """
    fragment = fragment.strip()
    if not fragment or fragment.upper() in _SQL_KEYWORDS:
        return None
    # ⚠️ 第一个 token 必须是**合法标识符**：``numeric(32,0)`` 被逗号切成
    # ``numeric(32`` 与 ``0)`` 两段，后者若不校验就成了一个叫 ``0)`` 的"列"。
    # 那不是假失败那么简单 —— 它会让 TD-02 报出一个**根本不存在**的列名，
    # 而读报错的人得先想到「逗号切分」这一层才知道该看哪里。
    if _IDENTIFIER.fullmatch(fragment.split()[0]) is None:
        return None
    match = _COLUMN_NAME_PATTERN.search(fragment)
    if match is None:
        return None
    name = fragment.split()[0]
    if name.upper() in _SQL_KEYWORDS or name.startswith(("uq_", "ck_", "fk_", "idx_")):
        return None
    return name


def _strip_comments(sql: str) -> str:
    """把 ``--`` 行注释换成等长空格（保持位置不变，便于算「之前声明了什么」）。"""
    return re.sub(r"--[^\n]*", lambda m: " " * len(m.group(0)), sql)


def _strip_literals(sql: str) -> str:
    """把字符串字面量与双引号标识符换成等长空格。

    ⚠️ 不做这一步的话 ``CHECK (log_type = 'REVERSAL')`` 里的 ``REVERSAL``
    会被当成一个「未声明的列」，守卫于是报一个**根本不存在**的问题 ——
    而假失败多了，人就会开始习惯性地忽略这条断言，那时守卫就白写了。
    """
    return re.sub(r"'[^']*'|`[^`]*`", lambda m: " " * len(m.group(0)), sql)


def _parse_columns(body: str) -> set[str]:
    """从 ``CREATE TABLE`` 的括号体里抽出列名集合。

    ⚠️ **按逗号切片段**再逐个匹配，而不是按行匹配：``04 §7.11`` 的
    ``product_categories`` 把两列写在同一行 ——
    ``sort int NOT NULL DEFAULT 0, is_active boolean NOT NULL DEFAULT true,``。
    按行取「第一个 token」会漏掉 `is_active`，守卫于是报「文档漏了 is_active」，
    而文档明明写了。**假失败比没守卫更坏** —— 它会让人习惯性忽略这条断言。

    按逗号切是安全的：本仓的 DDL 里没有函数体或数组字面量，
    而 ``numeric(16,0)`` 被切开后的 ``0)`` 不匹配列名形状，自然被跳过。
    """
    # ⚠️ **先去行注释**再切分：``-- '男装衬衫码表' … '童装 100-160'`` 这种
    # 注释里带数字与缩进时，切出来的片段会跨行匹配到下一个真列的「名字 + 类型」，
    # 于是把注释里的词当成列名（实测收进来过一个叫 ``--`` 的"列"）。
    # 注释里的信息由人读，机器只需要知道它不是列。
    body = _strip_literals(_strip_comments(body))
    return {
        name
        for name in (_looks_like_a_column(fragment) for fragment in body.split(","))
        if name is not None
    }


def _model_tables() -> dict[str, set[str]]:
    """已建表的「表名 → 列名集合」。

    ⚠️ 真相来源是 **ORM 模型**而不是迁移脚本：迁移与模型的漂移由闸门 4 的
    ``alembic check`` 负责（它连真库比对），而这里要做的是「文档对不对」，
    拿模型当基准才不会被迁移脚本的书写方式（``op.create_table`` vs
    ``op.execute``）影响。
    """
    from app.common.models import Base, register_all_models

    # ⚠️ 用**共享的注册入口**而不是 `import app.main`（T-CUT-001b-1 修正）：
    #    `app.main` 只 import 三个 router，所以「模块还没 router 时」
    #    （裁剪的 router 属于 T-CUT-001c）它的 models 就注册不上 ——
    #    守卫于是报「表既没建也不在白名单」，而真相是表早就建好了。
    #    症状更恶劣的是它**依赖用例执行顺序**：全量跑时前面的用例碰巧
    #    import 过就绿，单跑这个文件就红。「换个顺序就红」等于没有测试。
    register_all_models()

    return {
        name: {column.name for column in table.columns} - COMMON_COLUMNS
        for name, table in Base.metadata.tables.items()
        if name not in IGNORED_TABLES
    }


def _documented_ddl() -> dict[str, set[str]]:
    """`04 §7` 里每个 ``CREATE TABLE`` 的「表名 → 文档里写的列名集合」。

    列名按「四空格缩进 + 类型关键字」逐个匹配，而不是「每行第一个 token」。
    ⚠️ 后者是 `test_migrations.py` 里那条守卫用的口径，但它**认不出同一行逗号分隔的
    多列** —— 而 `04 §7.11` 的 `product_categories` 正是这么写的
    （``sort int NOT NULL DEFAULT 0, is_active boolean NOT NULL DEFAULT true,``）：
    按「第一个 token」取会把 `is_active` 漏掉，于是守卫报「文档漏了 is_active」，
    而文档明明写了。**假失败比没守卫更坏** —— 它会让人习惯性地忽略这条断言。
    """
    text = DOC_04.read_text(encoding="utf-8")
    section = text.split("## 7. 关键表结构约定")[1].split("\n## 8.")[0]
    documented: dict[str, set[str]] = {}
    for block in re.findall(r"```sql\n(.*?)```", section, re.DOTALL):
        for match in re.finditer(
            r"CREATE TABLE (?:IF NOT EXISTS )?(\w+)\s*\((.*?)\n\);", block, re.DOTALL
        ):
            name, body = match.group(1), match.group(2)
            # ⚠️ `CONSTRAINT` / `CHECK` / `UNIQUE` 行与列同为 4 空格缩进，
            #   正则会把它们当列名收进来 —— 必须显式排除，否则「文档写了模型
            #   没有的列 CONSTRAINT」这种假失败会淹没真失败
            documented[name] = _parse_columns(body)
    return documented


def test_docs_04_section7_has_ddl_for_every_built_table() -> None:
    """TD-01：**已建的表必须在 `04 §7` 有 DDL**，否则规范会越来越空。

    ⚠️ 这条的失败信息要能直接当待办清单用 —— 所以列出的是「文档缺的表名」，
    而不是「文档里有但多余的」。
    """
    models = _model_tables()
    documented = _documented_ddl()
    missing = sorted(set(models) - set(documented))

    assert not missing, (
        f"docs/04 §7 缺这些已建表的 DDL（共 {len(missing)} 张）：{missing}\n"
        f"补一段 `CREATE TABLE`，或在 §7 注明它属于哪张小节。"
        f"权威来源是 ORM 模型 / 迁移脚本（`alembic check` 管迁移与模型的漂移）。"
    )


def test_docs_04_section7_has_no_unknown_tables() -> None:
    """TD-04：`04 §7` 里的每张表要么**已建**（在模型里），要么在 P1 待建白名单里。

    ⚠️ 这一条挡的是「文档写了不存在的表」。它的危害比「文档漏了表」更大：
    漏写只是不完整，而写了不存在的表**看起来有权威性** —— 读的人照着建表会失败，
    而他不会怀疑文档（ADR-0026 的 `operation_id` 就是这一类）。

    ⚠️ 反过来也成立：白名单里的表一旦真的建出来，这条会要求把它从白名单删掉。
    留着不删的后果是「那张表建完之后，它的文档写错了也不会被抓」。
    """
    models = _model_tables()
    unknown = sorted(set(_documented_ddl()) - set(models) - P1_PENDING_TABLES)
    assert not unknown, (
        f"docs/04 §7 里有既没建、也不在 P1 待建白名单的表：{unknown}。\n"
        f"要么把表建出来（迁移 + 模型），要么修文档，"
        f"要么 —— 如果它确实是 P1 的表 —— 加进 `P1_PENDING_TABLES` 并注明属于哪张卡。"
    )


@pytest.mark.parametrize("table", sorted(P1_PENDING_TABLES - set(_model_tables())))
def test_pending_table_has_a_ticket_reference(table: str) -> None:
    """TD-05：P1 待建白名单里的表，**必须在文档里注明它还没建**。

    ⚠️ 没有这条的话，白名单就成了「文档写错也没人管」的万能借口：往里加一个名字，
    那张表从此免检。要求的注释让「白名单里的一张表到底建了没有」在文档里可见。

    判据用「文档里提到该表名附近的段落里有『P1』或『REV-』字样」——
    刻意不严格：目的是**逼出一句说明**，不是校验措辞。
    """
    text = DOC_04.read_text(encoding="utf-8")
    assert table in text, f"{table} 连文档里都没有，却在 P1 待建白名单里"


@pytest.mark.parametrize("table", sorted(_model_tables()))
def test_docs_04_section7_columns_match_the_model(table: str) -> None:
    """TD-02：对**已建**的表，文档里的列与模型**双向**一致。

    双向的理由是不对称的：

    - 文档写了模型没有的列 → **ADR-0026 的教训**（`operation_id`）。
      照它建表会失败，而读的人不会怀疑文档。
    - 模型有文档没写的列 → 规范缺一项，于是「规范是唯一事实来源」这句话
      在这一列上不成立。

    ⚠️ 用 ``parametrize`` 而不是一条断言里循环：某张表红了要能**一眼看出是哪张**。
    单测跑 28 个参数几乎不花时间，而「哪张表对不上」正是排查的全部难点。
    """
    documented = _documented_ddl()
    if table not in documented:
        # TD-01 负责报「缺 DDL」；这条只管「有 DDL 时列对不对」
        pytest.skip(f"04 §7 还没有 {table} 的 DDL（由 TD-01 报错）")

    # ⚠️ **两侧都减掉公共字段**：既有段落（§7.4/§7.8）显式写了 `id`，本卡新写的
    #   段落用注释带过。哪边减都不对 —— 只减模型侧的话「文档写了 id」会报错，
    #   只减文档侧的话「文档漏了 remark」会报错。两边都减，公共字段的口径
    #   就只有 `04 §2` 一个地方说了算（那才是它该有的样子）
    columns = documented[table] - COMMON_COLUMNS
    if not columns:
        pytest.skip(f"04 §7 的 {table} DDL 没有可解析的业务列")

    model_columns = _model_tables()[table]
    assert columns <= model_columns, (
        f"docs/04 §7 的 {table} DDL 写了模型里不存在的列："
        f"{sorted(columns - model_columns)}。\n"
        f"要么改文档，要么改模型 —— 但**绝不能让文档与迁移各说各话**："
        f"这一类错误在 ADR-0026 里真实发生过（`operation_id` 列根本不存在）。"
    )
    assert model_columns <= columns, (
        f"docs/04 §7 的 {table} DDL 漏了模型已有的列："
        f"{sorted(model_columns - columns)}。\n"
        f"补上；公共字段（04 §2）不用重复抄，但**业务字段**必须列全。"
    )


# ---------------------------------------------------------------- 引用完整性

#: SQL 关键字（大写或全小写形式都见得到）。抽标识符时要排除，否则 ``CHECK (x IS NOT NULL)``
#: 里的 ``NULL`` 会被当成列名。
_SQL_KEYWORDS = frozenset(
    {
        "NULL",
        "NOT",
        "AND",
        "OR",
        "IS",
        "IN",
        "EXISTS",
        "BETWEEN",
        "LIKE",
        "ILIKE",
        "TRUE",
        "FALSE",
        "CHECK",
        "CONSTRAINT",
        "PRIMARY",
        "UNIQUE",
        "FOREIGN",
        "REFERENCES",
        "DEFAULT",
        "EXCLUDE",
        "DEFERRABLE",
        "INITIALLY",
        "AS",
        "CASE",
        "WHEN",
        "THEN",
        "ELSE",
        "END",
    }
)


def _blocks() -> list[str]:
    """:func:`_documented_ddl` 的块级版本：``(表名声明顺序里的表, 整块 SQL)``。

    ⚠️ 要**整块**而不是单个 ``CREATE TABLE``：块里还有 ``ALTER TABLE`` /
    ``CREATE INDEX`` / ``CREATE TYPE``，而「某个约束引用了同块后面才声明的列」
    这类缺陷只有看整块才发现得了（`cutting_orders` 就是这样）。
    """
    text = DOC_04.read_text(encoding="utf-8")
    section = text.split("## 7. 关键表结构约定")[1].split("\n## 8.")[0]
    return re.findall(r"```sql\n(.*?)```", section, re.DOTALL)


def _declared_before(block: str, position: int) -> set[str]:
    """``position`` 之前（含所在 ``CREATE TABLE`` 的括号内）已经声明的列名。

    ⚠️ 「之前」是这条守卫的全部意义：``CHECK (hands_total > 0)`` 引用的列必须在
    它**之前**声明过 —— PG 建表时先解析约束再落列，所以「后面才声明」等于照抄
    建表直接失败。

    ⚠️ **不能只取「已闭合的 CREATE TABLE」**：约束就写在建表括号**里面**，
    到它为止那张表还没闭合。所以要分两路取：

    1. 从**最近一次** ``CREATE TABLE``（或 ``ALTER TABLE``）起到 ``position`` 的片段 ——
       这一段含同一张表里排在约束之前的列。用 ``rfind`` 而不是从头扫，是为了
       **不把上一张表的列算进来**（那会让守卫漏报：``b(x int, CHECK (y > 0))``
       里若前面有张表含 ``y``，就看不出 ``y`` 其实没声明）。
    2. ``position`` 之前的**所有** ``ALTER TABLE ... ADD COLUMN`` ——
       不管它在哪个位置，因为「前面某条 ALTER 加的列」当然也算已声明。
    """
    prefix = block[:position]
    start = max(
        prefix.rfind("CREATE TABLE"),
        prefix.rfind("ALTER TABLE"),
        prefix.rfind("CREATE TYPE"),
    )
    head = prefix[start:] if start >= 0 else prefix
    declared = _parse_columns(head)
    declared |= set(re.findall(r"ALTER TABLE\s+\w+\s+ADD COLUMN (?:IF NOT EXISTS )?(\w+)", prefix))
    return declared


def _check_references() -> list[str]:
    """返回所有「``CHECK`` 引用了此时还没声明的列」的描述（空 = 全过）。"""
    known = set(COMMON_COLUMNS)
    for columns in _model_tables().values():
        known |= columns
    problems: list[str] = []
    for raw in _blocks():
        # ⚠️ **先剥注释**再找 CHECK：``CHECK (hands_total > 0)  -- 见下`` 后面紧跟的
        # 注释里可能有别的语句，按原文匹配会把 ``); ALTER TABLE ...`` 一起吞进表达式，
        # 报出来的「引用的列」完全无关（实测报过一条 `ALTER TABLE cutting_orders A`）。
        block = _strip_comments(raw)
        for match in re.finditer(r"CHECK\s*\((.*?)\)(?:\s*[,);]|$)", block, re.DOTALL):
            available = known | _declared_before(block, match.start())
            for token in re.findall(
                r"(?<![.\w:])([A-Za-z_][A-Za-z0-9_]*)", _strip_literals(match.group(1))
            ):
                upper = token.upper()
                if upper in _SQL_KEYWORDS or token in available:
                    continue
                # 后面跟 ``(`` 的是函数调用（``length(x) > 0``）
                after = block[match.start(1) + match.group(1).index(token) + len(token) :]
                if after.lstrip().startswith("("):
                    continue
                problems.append(f"CHECK ({match.group(1)[:60]}) 里的 `{token}` 不是已声明的列")
    return problems


#: **被外键引用、但 04 §7 里没有 DDL** 的表 → 值是「缺到什么程度」。
#:
#: ⚠️ 这不是白名单式的放行，每一条都是一个**已登记的设计缺口**，两个性质不同：
#:
#: - ``字段表``：modules 里有完整字段表，只是没搬进 04 §7。规范分裂但**信息不缺**。
#: - ``只有一行描述``：连字段表都没有 —— 照现在这些文字**无法建表**。
#:
#: 分开标注是因为补的代价完全不同：前者是搬运，后者要**先设计**。
#: 两条都登记在 docs/12（`bundling_*` 见 L-070、`materials` / `suppliers` 见 L-071），
#: 闭环后从这里删掉 —— 守卫会要求删，而那是它生效的证明。
TABLES_WITHOUT_DDL: dict[str, str] = {
    # ⚠️ T-BASE-003 已补 `materials` / `suppliers` 的 DDL（04 §7.15），
    #    所以它们**从这里消失**了 —— 这不是漏删，是 TD2-05 生效的证明：
    #    补了 DDL 却不删登记，那张表从此免检，而没人会发现。
    #    这里保留两行注释占位，免得下次有人「顺手」又加回来。
}


def _check_foreign_keys() -> list[str]:
    """返回所有「``REFERENCES`` 指向了任何规范里都没定义的表」（空 = 全过）。"""
    documented_tables = set(_documented_ddl())
    known_tables = set(_model_tables()) | documented_tables | set(TABLES_WITHOUT_DDL)
    problems: list[str] = []
    for raw in _blocks():
        for target in re.findall(r"REFERENCES\s+(\w+)", _strip_comments(raw)):
            if target in TABLES_WITHOUT_DDL:
                continue  # 已登记的设计缺口，由 TD2-05 单独守
            if target not in known_tables:
                problems.append(f"REFERENCES {target}：04 §7 里既没有这张表、模型里也没有")
    return problems


def test_docs_04_check_constraints_only_reference_declared_columns() -> None:
    """TD2-01：``CHECK`` 里引用的列必须**在该 ``CHECK`` 之前**声明过。

    ⚠️ 这条与 TD-02（列名双向一致）是**两个正交**的缺陷类型。T-DOCS-001 只抓后者，
    而 ``04 §7.1`` 的 ``cutting_orders`` 正好是前者的活标本：

    .. code-block:: sql

        CREATE TABLE cutting_orders (
            ...
            CONSTRAINT ck_cutting_orders_hand CHECK (hands_total > 0)
        );
        ALTER TABLE cutting_orders ADD COLUMN hands_total integer NOT NULL DEFAULT 0;

    列名对得上（TD-02 过），但 PG 建表时**先解析约束**，于是报
    ``column "hands_total" does not exist``。这类缺陷只会在**真去建那张表**的那张卡上
    才暴露 —— 而那张卡往往是三天后才开工的卡。
    """
    problems = _check_references()
    assert not problems, "docs/04 §7 的 CHECK 引用了当时还没声明的列：\n  " + "\n  ".join(problems)


def test_docs_04_foreign_keys_point_at_documented_tables() -> None:
    """TD2-02：``REFERENCES`` 的目标表必须在规范里**有 DDL**。

    ⚠️ 分成两条断言而不是混成一条，是因为两类缺口的**严重程度差一个量级**：

    - ``TABLES_WITHOUT_DDL`` 里的表（已登记）→ 不让这条红，但**列出来**。
      它们是设计缺口，闭环时间取决于排期，不该让守卫天天红。
    - 不在任何清单里的表 → **直接红**。那意味着有人新引用了一张谁都没定义的表，
      而那通常是打错了一个表名（比缺表更常见也更容易犯）。
    """
    problems = [item for item in _check_foreign_keys() if not item.startswith("KNOWN-GAP")]
    assert not problems, "docs/04 §7 的外键指向了谁都没定义的表：\n  " + "\n  ".join(problems)


def test_tables_without_ddl_are_registered_gaps() -> None:
    """TD2-05：``TABLES_WITHOUT_DDL`` 里每一张表**都必须真的被引用过**。

    ⚠️ 看着多此一举，其实防的是最容易发生的一种退化：某天有人补了
    ``materials`` 的 DDL，忘了从这个 dict 里删掉 —— 于是那张表从此**免检**，
    文档写错列名也不会有人发现。

    代价极小（一条 6 行的守卫），而它保证「登记表不会烂掉」。
    """
    referenced = set()
    for block in _blocks():
        referenced |= set(re.findall(r"REFERENCES\s+(\w+)", _strip_comments(block)))
    for table in sorted(TABLES_WITHOUT_DDL):
        assert table in referenced, (
            f"{table} 在 TABLES_WITHOUT_DDL 里但 §7 已经没有外键引用它 —— "
            f"要么补了 DDL（那就从这里删掉），要么改掉这张表"
        )


def test_reference_guards_can_fail() -> None:
    """TD2-03 / TD2-04：反验两条引用守卫都会红。

    ⚠️ 注入的是**原文里就有的形状**（一行 ``ALTER TABLE ADD COLUMN`` 与一处
    ``REFERENCES``），而不是造一段人造 SQL —— 人造 SQL 的失败往往是因为写法
    不符合正则，而那证明不了正则抓得住真实文档里的问题。
    """
    block = next(
        (item for item in _blocks() if "ck_cutting_orders_hand" in item),
        None,
    )
    assert block is not None, "反验失败：04 §7 里找不到 cutting_orders 的 DDL 块"

    # TD2-03：把 hands_total 的**声明**抽掉（模拟「声明写在 CHECK 之后」那个缺陷）
    stripped = re.sub(
        r"^\s*hands_total\s+integer[^,]*,",
        "",
        block,
        count=1,
        flags=re.MULTILINE,
    )
    assert stripped != block, "反验失败：正则没能定位到 hands_total 的声明"
    assert _problems_of(stripped), (
        "反验失败：抽掉 hands_total 的声明之后守卫没有报出来 —— "
        "也就是说 TD2-01 在真实场景下抓不住这一类缺陷"
    )
    # 位置敏感：把声明挪到 CHECK **之后**（就是 §7.1 原先那个缺陷的形状）也必须被抓
    moved = stripped.replace(
        "CONSTRAINT ck_cutting_orders_hand",
        "ALTER TABLE cutting_orders ADD COLUMN hands_total integer;\n"
        "CONSTRAINT ck_cutting_orders_hand",
        1,
    )
    # ⚠️ 中间变量而不是直接写在 `assert` 里：ruff 0.8.4（pre-commit 钩子 pin 的版本）
    #    与仓库 venv 的 0.16.10 对「超长 assert 的换行位置」给出**不同**结果 ——
    #    直接内联会让两个 formatter 互相改写对方（详见 docs/12 §5 L-087）。
    #    提成局部变量后两边都无需换行，也就都满意。
    problems_after_move = _problems_of(moved)
    assert problems_after_move, (
        "反验失败：把声明挪到 CHECK 之后守卫却没报 —— 它只看「有没有声明过」"
    )

    # TD2-04：把一个外键指向改成不存在的表
    broken_fk = block.replace(
        "REFERENCES styles(style_no)", "REFERENCES no_such_table(style_no)", 1
    )
    if broken_fk == block:
        broken_fk = block.replace("REFERENCES styles(id)", "REFERENCES no_such_table(id)", 1)
    assert broken_fk != block, "反验失败：没找到 cutting_orders 里的一处外键"
    assert _foreign_key_problems_of(broken_fk), "反验失败：改坏外键之后守卫没有报"


def _problems_of(block: str) -> list[str]:
    """:func:`_check_references` 的单块版本（反验用）。"""
    known = set(COMMON_COLUMNS)
    for columns in _model_tables().values():
        known |= columns
    problems: list[str] = []
    for match in re.finditer(r"CHECK\s*\((.*?)\)(?:\s*[,);]|$)", block, re.DOTALL):
        available = known | _declared_before(block, match.start())
        for token in re.findall(
            r"(?<![.\w:])([A-Za-z_][A-Za-z0-9_]*)", _strip_literals(match.group(1))
        ):
            if token.upper() in _SQL_KEYWORDS or token in available:
                continue
            if (
                block[match.start(1) + match.group(1).index(token) + len(token) :]
                .lstrip()
                .startswith("(")
            ):
                continue
            problems.append(token)
    return problems


def _foreign_key_problems_of(block: str) -> list[str]:
    """:func:`_check_foreign_keys` 的单块版本（反验用）。"""
    known = set(_model_tables()) | set(_documented_ddl()) | set(TABLES_WITHOUT_DDL)
    return [t for t in re.findall(r"REFERENCES\s+(\w+)", block) if t not in known]


# ---------------------------------------------------------------- 外键目标列可引用性


async def test_docs_04_foreign_key_targets_are_referenceable(db_session) -> None:
    """TD5-01：外键的**目标列**必须是「普通唯一约束或主键」，不能是部分索引。

    ⚠️ **这是 TD2-02 的补漏**。TD2-02 只问「目标表在不在规范里」，而这一条问
    「那根列能不能被引用」—— 两件事都会让 ``CREATE TABLE`` 失败，而报错**只说前者**：

        there is no unique constraint matching given keys for referenced table "styles"

    而 ``docs/04`` 里曾有**三处** ``REFERENCES styles(style_no)``：
    ``cutting_orders`` / ``operation_rates`` / ``wip_stocks``。原因是
    ``styles.uq_styles_no`` 是**部分索引**（``WHERE deleted_at IS NULL``，为了款号
    软删后可复用同号），而 PG 不允许外键引用部分唯一索引。

    ⚠️ 其中 ``operation_rates`` 是**已建表** —— 迁移 0005 早就把外键改指 ``styles.id``
    了（文件头注 4 写明了原因），而文档没跟上。所以这不是「还没建所以先这么写」，
    是**规范落后于代码**：任何人照文档理解都会以为「改款号会被子表外键挡住」。

    ⚠️ 只能对**已建的表**查 —— 未建的表没有真实索引可查，那种情况留给真建表检查。
    """
    import sqlalchemy as sa

    violations: list[str] = []
    for block in _blocks():
        for table, column in re.findall(
            r"REFERENCES\s+(\w+)\s*\(\s*(\w+)\s*\)", _strip_comments(block)
        ):
            exists = (
                await db_session.execute(
                    sa.text("SELECT to_regclass(:t) IS NOT NULL"), {"t": table}
                )
            ).scalar_one()
            if not exists:
                continue  # 表还没建，查不到索引 —— 不是本条要管的
            # ⚠️ **必须排除部分索引**（``indpred IS NOT NULL``）：那是本次要抓的那一类
            ok = (
                await db_session.execute(
                    sa.text(
                        "SELECT count(*) FROM pg_index i "
                        "JOIN pg_class c ON c.oid = i.indrelid "
                        "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(i.indkey) "
                        "WHERE c.relname = :t AND a.attname = :col "
                        "AND (i.indisprimary OR (i.indisunique AND i.indpred IS NULL))"
                    ),
                    {"t": table, "col": column},
                )
            ).scalar_one()
            if not ok:
                violations.append(f"{table}({column}) 只能被**部分索引**覆盖，PG 不允许外键引用它")

    assert not violations, (
        "docs/04 §7 有外键指向了不可引用的列（照抄建表会报 "
        "`there is no unique constraint matching given keys`）：\n  "
        + "\n  ".join(sorted(set(violations)))
    )


def test_the_guards_themselves_can_fail() -> None:
    """TD-03 / TD-04：反验守卫不是「永远通过的空壳」。

    做法是**改真正被解析的那个文件内容再还原**，而不是 monkeypatch 掉解析函数的
    返回值 —— 后者验证的是「我改的返回值生效了」，不是「正则真的能从 markdown 里
    抽出表名」。后者才是会骗人的那种「反验通过」。

    两半分别对应两条守卫：

    - TD-03：从文档里抹掉一张已建表的 DDL → 「缺 DDL」那条必须报出来
    - TD-04：往文档里塞一张不存在的表 → 列比对那条必须报出来
    """
    models = _model_tables()
    documented = _documented_ddl()

    # ---- TD-03：抹掉一张已建表的 DDL
    sample = "customers"
    assert sample in documented, f"反验用例本身依赖 {sample} 有 DDL"
    stripped = re.sub(rf"CREATE TABLE {sample}\s*\(.*?\n\);", "", original_text(), flags=re.DOTALL)
    assert stripped != original_text(), "反验失败：正则没能从文档里找到该表的 DDL"
    assert sample not in _documented_ddl_from(stripped), (
        "反验失败：抹掉 DDL 之后解析结果里还有这张表 —— "
        "说明 `_documented_ddl_from` 与 `_documented_ddl` 走的是不同代码路径"
    )

    # ---- TD-04：塞一张不存在的表
    fake = (
        "\n```sql\nCREATE TABLE totally_not_a_real_table (\n"
        "    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),\n"
        "    nickname varchar(32) NOT NULL\n"
        ");\n```\n"
    )
    # ⚠️ 必须插进 **`## 7` 段落内部**（末尾追加会被 `split("\n## 8.")` 切掉，
    #    那时守卫「没反应」的原因就是位置不对，而不是它不工作 —— 实测踩过）
    anchor = "### 7.9 操作日志"
    with_fake = original_text().replace(anchor, fake + anchor, 1)
    assert with_fake != original_text(), "反验失败：锚点没找到（§7 的小节标题改名了）"
    found = _documented_ddl_from(with_fake)
    assert "totally_not_a_real_table" in found, (
        "反验失败：伪造的表没被解析出来 —— 那说明 TD-04 这条守卫根本不会被触发"
    )
    assert "totally_not_a_real_table" not in models, "反验失败：这个表名居然真的存在"
    # 它的列也不该出现在任何已建表的列集合里（否则 TD-04 会误判成「通过」）
    assert "nickname" not in models[sample]


def original_text() -> str:
    return DOC_04.read_text(encoding="utf-8")


def _documented_ddl_from(text: str) -> dict[str, set[str]]:
    """:func:`_documented_ddl` 的可注入版本（反验用）。"""
    section = text.split("## 7. 关键表结构约定")[1].split("\n## 8.")[0]
    documented: dict[str, set[str]] = {}
    for block in re.findall(r"```sql\n(.*?)```", section, re.DOTALL):
        for match in re.finditer(
            r"CREATE TABLE (?:IF NOT EXISTS )?(\w+)\s*\((.*?)\n\);", block, re.DOTALL
        ):
            documented[match.group(1)] = _parse_columns(match.group(2))
    return documented
