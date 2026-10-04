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
#: ⚠️ 这是一张**必须手动维护**的白名单，且方向是「建了就删」：某张表真的建出来之后，
#: 它就该出现在模型里，于是这条断言会要求你从这里把它删掉（而不是留着）。
#: 留着不删的后果是「这张表建完之后，文档里写错的列名再也不会被抓」——
#: 因为白名单把它排除在列比对之外了。
#:
#: ⚠️ **每加一个名字之前先问「这张表真的还没建吗」**：往里塞一张已建的表，
#: 等于给自己开一个「文档可以写错」的洞，而那个洞不会有人发现。
P1_PENDING_TABLES = frozenset(
    {
        # §7.0 打菲件表（ADR-0016）
        "bundles",
        # §7.1 计件流水
        "piecework_logs",
        # §7.2 库存
        "material_stocks",
        "wip_stocks",
        "wip_ledger_lines",
        # §7.5 采购
        "purchase_orders",
        "purchase_order_lines",
        "purchase_arrivals",
        "purchase_arrival_lines",
        # §7.6 工资结算周期
        "payroll_periods",
        # 裁剪单（P1）
        "cutting_orders",
        "cutting_order_lines",
        "cutting_order_line_colors",
        "cutting_order_size_lines",
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
_COLUMN_NAME_PATTERN = re.compile(
    r"[a-z_][a-z0-9_]*\s+"
    r"(?:uuid|varchar|boolean|integer|numeric|text|timestamptz|time|date|inet|jsonb"
    r"|int|bigint|smallint|size_class|data_scope|doc_status)\b",
    re.IGNORECASE,
)


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
    body = re.sub(r"--[^\n]*", "", body)
    columns: set[str] = set()
    for fragment in body.split(","):
        # 每段取**第一个**「名字 + 类型」的匹配：`DEFAULT gen_random_uuid()` 这类
        # 表达式里不会出现第二个「名字 + 已知类型」的形状
        match = _COLUMN_NAME_PATTERN.search(fragment)
        if match is not None:
            columns.add(fragment.strip().split()[0])
    return columns


def _model_tables() -> dict[str, set[str]]:
    """已建表的「表名 → 列名集合」。

    ⚠️ 真相来源是 **ORM 模型**而不是迁移脚本：迁移与模型的漂移由闸门 4 的
    ``alembic check`` 负责（它连真库比对），而这里要做的是「文档对不对」，
    拿模型当基准才不会被迁移脚本的书写方式（``op.create_table`` vs
    ``op.execute``）影响。
    """
    import app.main  # noqa: F401 —— 副作用是注册全部模型
    from app.common.models import Base

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
