"""0012 库存台账与批次耗用明细：`stock_ledgers` / `stock_ledger_lines`
（T-BASE-009 第 1 张卡，modules/06 库存）。

权威来源：**``docs/04 §7.2.5``**（逐列 DDL，本迁移与它逐字一致）+
``docs/04 §7.2.4``（成本对账视图）+ ``docs/modules/06 §3.3`` / ``§3.4``（字段表）+
ADR-0011（逐批实际成本）/ ADR-0018（三段式出入库）/ ADR-0022（库存供应商维度）。

⚠️ **本迁移只建表，不做库存业务。** 写入台账的是裁剪单审核第 ①② 步与
   到货登记第 ③ 步，都在 **T-CUT-001b-3**。

## 这里只留「迁移特有」的信息，其余一律指 04

⚠️ **刻意不在本文件里复述 04 §7.2.5 的建表理由**（append-only 口径、唯一键为什么
   不含 ``stock_id``、``material_id`` 为什么 NOT NULL 且 P3 要放宽）。抄一遍就多一个
   可能不一致的副本 —— 而这几条**已经**在 04 与 modules 里各写了一遍，
   本文件是**第三遍**。真要改口径时三处都要改，而只有一处会被 grep 到。

迁移特有的三件事写在这里：

1. **顺带建三个 PG 枚举**（AGENTS §5 红线「枚举一律 PG enum + 迁移里新增值」）：
   ``stock_type`` / ``stock_direction`` / ``stock_doc_type``。
   ⚠️ ``stock_direction`` **刻意不叫 ``direction``**：那是本表的**列名**，
   类型名与列名同名会让 ``CREATE TYPE`` 与列声明里各写一遍、读起来分不清。
   ⚠️ ``stock_doc_type`` 的 7 个值**只能追加**：PG enum 不能删值也不能改值，
   要加新单据类型只能 ``ALTER TYPE ... ADD VALUE``，而那条**不能进事务块**，
   迁移里必须包 ``autocommit_block()``。
   ⚠️ ``values_callable`` 在模型侧必须显式给（成员名与值不一致，见 ``app/modules/stock/models.py``）。
2. **``stock_reservations`` 单独在迁移 0013**：台账与明细之间有 FK + 生命周期依赖
   （明细必须挂在一条台账上，``v_stock_cost_check`` 同时依赖两者），合在一个迁移里
   才有一个可回滚单元；锁定表**对两者零外键零依赖**，拆开让两个文件都落在
   ``AGENTS §7.1`` 的**单文件 400 行硬线**之内。
3. **``created_at`` / ``created_by`` / ``remark``**（append-only 必备列，见 ``_APPEND_ONLY_COLS``）。
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811

from alembic import op

revision: str = "0012"
down_revision: str = "0011"
branch_labels = None
depends_on = None

STOCK_TYPE_VALUES: tuple[str, ...] = ("MATERIAL", "FINISHED_GOODS")
STOCK_DIRECTION_VALUES: tuple[str, ...] = ("IN", "OUT", "ADJUST")
#: 04 §7.2.5 的枚举值域。⚠️ **只能追加、不能删改**（AGENTS §5 / 04 §3）。
STOCK_DOC_TYPE_VALUES: tuple[str, ...] = (
    "PurchaseOrder",
    "CuttingOrder",
    "MaterialIssue",
    "FinishedGoodsReceipt",
    "SalesDelivery",
    "StockTransfer",
    "Stocktaking",
)

#: 本迁移建的枚举 → 取值。downgrade 只删**本迁移建过**的（抄 0009 同款判据）。
_CREATED_ENUMS: dict[str, tuple[str, ...]] = {
    "stock_type": STOCK_TYPE_VALUES,
    "stock_direction": STOCK_DIRECTION_VALUES,
    "stock_doc_type": STOCK_DOC_TYPE_VALUES,
}

#: ⚠️ ``create_type=False`` 是必需的：``postgresql.ENUM`` 默认 ``create_type=True``，
#: 在 create_table 时会**自己再发一条** ``CREATE TYPE``，与本迁移手写的那条撞车
#: （报 ``type already exists``）。类型由 :func:`_create_enums` 建 ——
#: 这样 downgrade 才管得住。0004 / 0007 / 0008 / 0009 各踩过一次。
_STOCK_TYPE = postgresql.ENUM(*STOCK_TYPE_VALUES, name="stock_type", create_type=False)
_STOCK_DIRECTION = postgresql.ENUM(
    *STOCK_DIRECTION_VALUES, name="stock_direction", create_type=False
)
_STOCK_DOC_TYPE = postgresql.ENUM(*STOCK_DOC_TYPE_VALUES, name="stock_doc_type", create_type=False)

#: append-only 表的列（04 §7.2.5）：**没有** version / deleted_at / updated_*。
#: 抄 ``0008`` 的 ``wip_ledger_lines`` 那一组，只列真正有的。
_APPEND_ONLY_COLS: tuple[sa.Column, ...] = (
    sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.Column(
        "created_by",
        PgUUID(as_uuid=True),
        nullable=False,
        comment="写入人（append-only：不设 updated_*）",
    ),
    sa.Column("remark", sa.Text(), nullable=True),
)


def _append_only_id() -> sa.Column:
    """append-only 表的 ``id``（``04 §2``：uuid + gen_random_uuid()）。

    ⚠️ 这里**不**写 ``primary_key=True``，而是在 ``op.create_table`` 的参数里
    单独给一条 ``sa.PrimaryKeyConstraint("id", name="pk_<table>")`` ——
    与 ``0004`` / ``0008`` / ``0009`` 同一写法。

    ⚠️ **漏了它不会报「建表失败」，而是报一句与真实原因无关的错**（本卡实测撞到）：
    ``stock_ledgers`` 有自引用外键 ``fk_stock_ledgers_reversal_of →
    stock_ledgers(id)``，而 PG 要求被引用的列上有唯一约束 —— 没有 PK 就没有，
    于是报 ``there is no unique constraint matching given keys for referenced
    table "stock_ledgers"``。这句话看起来在说「自引用外键不行」，而实际上
    是「这张表忘了建主键」；**排查会往外键方向白费很多时间**。
    """
    return sa.Column(
        "id",
        PgUUID(as_uuid=True),
        server_default=sa.text("gen_random_uuid()"),
        nullable=False,
    )


def upgrade() -> None:
    created = _create_enums()
    _create_stock_ledgers()
    _create_stock_ledger_lines()
    _create_cost_check_view()
    _revoke_mutations()
    # 记在上下文里给 downgrade 用 —— 只有**本迁移建的**才该被删掉
    op.get_context().config.attributes["created_enums"] = created


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_stock_cost_check")
    # ⚠️ 先子后父：lines → ledgers（lines 有 FK 指向 ledgers）
    for table in ("stock_ledger_lines", "stock_ledgers"):
        op.drop_table(table)
    for name in _CREATED_ENUMS:
        if op.get_context().config.attributes.get("created_enums", {}).get(name):
            # ⚠️ 只删本迁移建的。不加 IF EXISTS 也不加反向判断 —— 判据在上面，
            #    这里再判一次会出现「upgrade 没建、downgrade 也不删」的不对称
            op.execute(f"DROP TYPE {name}")


def _create_enums() -> dict[str, bool]:
    """建三个 PG 枚举，返回「哪个是真的建了」。

    ⚠️ **不能用 ``CREATE TYPE IF NOT EXISTS``** —— PostgreSQL **没有**这个语法
    （只有 ``CREATE TABLE IF NOT EXISTS``）。实测报 ``syntax error at or near "NOT"``。
    所以先查 ``pg_type`` 再决定建不建。

    ⚠️ 为什么要判存在性：测试会临时建这些枚举，而生产库里它们可能已经由别的
    途径建过 —— 无条件 CREATE 会让那些库升不上去（与 ``0008`` 的
    ``_create_purchase_purpose`` 同一理由）。
    """
    created: dict[str, bool] = {}
    for name, values in _CREATED_ENUMS.items():
        exists = (
            op.get_bind()
            .execute(sa.text("SELECT 1 FROM pg_type WHERE typname = :name"), {"name": name})
            .scalar()
        )
        if exists:
            created[name] = False
            continue
        # ⚠️ 值是本文件里的常量元组，不是外部输入 —— 拼进 DDL 安全
        literals = ", ".join(f"'{value}'" for value in values)
        op.execute(f"CREATE TYPE {name} AS ENUM ({literals})")
        created[name] = True
    return created


def _create_stock_ledgers() -> None:
    """台账：一次动作一条（``04 §7.2.5``）。**append-only。**

    ⚠️ ``stock_id`` **不建外键**：``modules/06 §3.6`` 明确「按 ``stock_type``
    多态无法建 FK，由 service 保证 + ``40003`` 对账兜底」。建了 FK 只能指一张
    结存表，另一栈就写不进去。
    ⚠️ ``source_doc_id`` 同样**不建 FK**：单据可整体软删或反审核，建 FK 会
    阻止这两件合法操作。
    """
    op.create_table(
        "stock_ledgers",
        _append_only_id(),
        sa.Column("stock_type", _STOCK_TYPE, nullable=False, comment="哪一栈（多态，不建 FK）"),
        sa.Column("stock_id", PgUUID(as_uuid=True), nullable=False, comment="结存行 id"),
        sa.Column(
            "warehouse_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("warehouses.id", name="fk_stock_ledgers_warehouse"),
            nullable=False,
            comment="冗余，报表免 join",
        ),
        sa.Column("direction", _STOCK_DIRECTION, nullable=False),
        sa.Column(
            "qty",
            sa.Numeric(14, 3),
            nullable=False,
            comment="有符号：IN 正 / OUT 负 / ADJUST 按差额",
        ),
        sa.Column("source_doc_type", _STOCK_DOC_TYPE, nullable=False),
        sa.Column(
            "source_doc_id",
            PgUUID(as_uuid=True),
            nullable=False,
            comment="不建 FK（单据可软删/反审核）",
        ),
        sa.Column(
            "source_line_no",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
            comment="0 = 单据级动作",
        ),
        sa.Column(
            "reversal_of_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("stock_ledgers.id", name="fk_stock_ledgers_reversal_of"),
            nullable=True,
            comment="INV-4 红冲链：反向台账指向原台账行",
        ),
        *_APPEND_ONLY_COLS,
        sa.PrimaryKeyConstraint("id", name="pk_stock_ledgers"),
        sa.CheckConstraint("qty <> 0", name="ck_stock_ledgers_qty"),
        # ⚠️ 唯一键**只有**这一条：stock_id 进唯一约束会在第二张裁剪单审核时撞号
        sa.UniqueConstraint(
            "source_doc_type",
            "source_doc_id",
            "source_line_no",
            "direction",
            name="uq_stock_ledgers_source",
        ),
        comment="出入库流水（append-only；04 §7.2.5）",
    )
    # ⚠️ 三个索引都**不带** `WHERE deleted_at IS NULL` —— 那一列不存在（append-only）
    op.create_index(
        "idx_stock_ledgers_stock",
        "stock_ledgers",
        ["stock_type", "stock_id", sa.text("created_at DESC")],
        unique=False,
    )
    op.create_index(
        "idx_stock_ledgers_source",
        "stock_ledgers",
        ["source_doc_type", "source_doc_id"],
        unique=False,
    )
    op.create_index("idx_stock_ledgers_reversal", "stock_ledgers", ["reversal_of_id"], unique=False)


def _create_stock_ledger_lines() -> None:
    """批次耗用明细：一条台账 → N 行（``04 §7.2.5``）。**append-only。**

    ⚠️ ``material_id`` 建 ``NOT NULL``：字段表的「必填」与同段那句
    「成衣栈台账此列为 NULL」自相矛盾，本轮按「必填」执行 ——
    成衣栈（``finished_goods_stocks``）属 P3，届时必须
    ``ALTER COLUMN material_id DROP NOT NULL``。已登记 ``docs/12`` §5。
    ⚠️ ``stock_id`` **不建 FK**：批次行软删不影响明细追溯（明细存的是快照）。
    """
    op.create_table(
        "stock_ledger_lines",
        _append_only_id(),
        sa.Column(
            "ledger_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("stock_ledgers.id", name="fk_stock_ledger_lines_ledger"),
            nullable=False,
            comment="必建 FK（04 §7.2.4「每条出库都有批次耗用」）",
        ),
        sa.Column(
            "material_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("materials.id", name="fk_stock_ledger_lines_material"),
            nullable=False,
            comment="⚠️ P3 建成衣栈时必须放宽为可空",
        ),
        sa.Column(
            "stock_id", PgUUID(as_uuid=True), nullable=True, comment="被扣减的批次行 id（不建 FK）"
        ),
        sa.Column(
            "dye_lot_no",
            sa.String(64),
            server_default=sa.text("'-'"),
            nullable=False,
            comment="批次快照：无缸号填 -",
        ),
        sa.Column(
            "bolt_no",
            sa.String(32),
            server_default=sa.text("'-'"),
            nullable=False,
            comment="批次快照：不分匹填 -",
        ),
        sa.Column(
            "color",
            sa.String(32),
            server_default=sa.text("'-'"),
            nullable=False,
            comment="⚠️ 缸色 ≠ 款色",
        ),
        sa.Column("qty", sa.Numeric(14, 3), nullable=False, comment="有符号，与父台账同向"),
        sa.Column(
            "unit_cost",
            sa.Numeric(12, 6),
            nullable=False,
            comment="批次成本快照（ADR-0011），永不被改价影响",
        ),
        sa.Column(
            "amount", sa.Numeric(18, 4), nullable=False, comment="= ROUND(qty × unit_cost, 4)"
        ),
        sa.Column(
            "reversal_of_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("stock_ledger_lines.id", name="fk_stock_ledger_lines_reversal_of"),
            nullable=True,
            comment="红冲明细逐条反向指回原明细（modules/06 §7 K6）",
        ),
        *_APPEND_ONLY_COLS,
        sa.PrimaryKeyConstraint("id", name="pk_stock_ledger_lines"),
        sa.CheckConstraint("qty <> 0", name="ck_stock_ledger_lines_qty"),
        # ⚠️ 金额一致性放**行级**：台账已无 amount 列，台账↔明细的「有无 + 数量」
        #    由 v_stock_cost_check 兜，而金额是行内静态的，CHECK 恰好够用
        sa.CheckConstraint(
            "ROUND(qty * unit_cost, 4) = amount", name="ck_stock_ledger_lines_amount"
        ),
        # 同一台账内同批次只允许一行，避免重复扣减。⚠️ **不需要**部分索引：
        # append-only 从不软删，deleted_at 恒为 NULL
        sa.UniqueConstraint(
            "ledger_id", "dye_lot_no", "bolt_no", name="uq_stock_ledger_lines_batch"
        ),
        comment="台账批次耗用明细（append-only；04 §7.2.5）",
    )
    op.create_index(
        "idx_stock_ledger_lines_ledger", "stock_ledger_lines", ["ledger_id"], unique=False
    )
    op.create_index(
        "idx_stock_ledger_lines_material",
        "stock_ledger_lines",
        ["material_id", "dye_lot_no", sa.text("created_at DESC")],
        unique=False,
    )
    op.create_index(
        "idx_stock_ledger_lines_reversal", "stock_ledger_lines", ["reversal_of_id"], unique=False
    )


def _create_cost_check_view() -> None:
    """成本对账视图（``04 §7.2.4``），**必须恒为 0 行**。

    ⚠️ **不需要** ``deleted_at`` 过滤：台账与明细是 append-only、没有那一列。
    ⚠️ 数量对账视图 ``v_stock_reconciliation`` 在**迁移 0013**（它还依赖
    ``material_stocks``，与锁定表同属「结存侧」的事）。
    ⚠️ **不过滤 stock_type**：这条查的是「台账 ↔ 明细」一致性，与哪一栈无关。
    加个 ``WHERE stock_type = 'MATERIAL'`` 会让成衣栈的台账**永远不被检查** ——
    「检查不到」比「检查报错」危险得多，因为它给的是「全绿」的假安全感。
    ⚠️ 这里**不能**再加一条 ``SUM(d.amount)`` 的 HAVING：红冲是**新增**一条反向明细，
    于是「台账 qty（不含红冲）」与「明细 Σ（含红冲）」在红冲后必然不等 ——
    那是正常状态不是异常，加进去的报红会是**每次反审核都误报一次**。
    """
    op.execute(
        """
        CREATE OR REPLACE VIEW v_stock_cost_check AS
        SELECT l.id AS ledger_id,
               l.source_doc_type, l.source_doc_id, l.direction,
               l.qty AS ledger_qty,
               COALESCE(SUM(d.qty), 0)    AS line_qty,
               COALESCE(SUM(d.amount), 0) AS line_amount
        FROM stock_ledgers l
        LEFT JOIN stock_ledger_lines d ON d.ledger_id = l.id
        GROUP BY l.id, l.source_doc_type, l.source_doc_id, l.direction, l.qty
        HAVING COUNT(d.id) = 0
            OR l.qty <> COALESCE(SUM(d.qty), 0)
        """
    )


#: **append-only** 表：UPDATE 与 DELETE 都不授。与迁移 0008 的 ``wip_ledger_lines``
#: 同一口径（``REVOKE UPDATE, DELETE``）—— 因为这两张表的 UPDATE 与 DELETE
#: **都没有合法的业务语义**：纠错只能走红冲 + 重入（INV-4）。
_APPEND_ONLY_TABLES: tuple[str, ...] = ("stock_ledgers", "stock_ledger_lines")


def _revoke_mutations() -> None:
    """收回 append-only 表的 UPDATE + DELETE（``04 §6.2.1``）。

    ⚠️ 正常部署下 ``ALTER DEFAULT PRIVILEGES`` 压根不授 DELETE，所以这是**双保险**：
    一旦有人图省事改成 ``GRANT ... ON ALL TABLES``，这里会把它按回去。
    台账尤其需要 —— 一条被物理删掉或被 UPDATE 过的流水会让
    ``v_stock_reconciliation`` 永远对不上且**无从追溯**：append-only 的全部价值
    就在于「原行永远保留」，这两条权限就是它的最后一道物理保证。
    """
    if not _app_role_exists():
        return
    for table in _APPEND_ONLY_TABLES:
        op.execute(f"REVOKE UPDATE, DELETE ON {table} FROM erp_app")


def _app_role_exists() -> bool:
    """``erp_app`` 是否存在（抄 ``0009`` 的同名函数）。

    用绑定参数判断而不是拼 ``to_regrole('erp_app')`` —— 避免 ruff S608，
    也避免角色名不存在时整条语句报错。
    """
    query = sa.text("SELECT 1 FROM pg_roles WHERE rolname = :name")
    return bool(op.get_bind().execute(query, {"name": "erp_app"}).scalar())
