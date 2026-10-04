"""0008 库存基础表：`material_stocks` 与 `wip_stocks`（T-BASE-006）。

权威来源：
    - ``docs/04 §7.2.0``       ``material_stocks`` 逐列定义
    - ``docs/04 §7.10``        ``wip_stocks`` / ``wip_ledger_lines``
    - ``docs/modules/06 §3``   字段表（本迁移与它**双向一致**，守卫 TD4-01 守着）
    - **ADR-0011**              批次实际成本（``unit_cost`` 批次内恒定）
    - **ADR-0012**              采购用途 / 门幅 / 重量（``purpose`` / ``width_cm`` / ``weight_kg``）
    - **ADR-0018**              WIP 出入库链路

⚠️ **本迁移只建这两张表 + 一张流水表，不做库存业务。**
``00 §7`` 路线图 2026-10-04 调整为「方案 A」：库存**基础表**提前到 P1，
库存**业务**（出入库单 / 调拨 / 盘点 / 成本结转）仍在 P3。P1 只用这三张表的
**「列批次 + 锁一行 + 记流水」** 三个能力，其余一概不做。
理由：``cutting_order_lines.stock_id`` 外键指向 ``material_stocks`` ——
裁剪单的核心动作「锁一个布批」需要这张表先存在，否则 P1 的裁剪单表根本建不出来。

四处与旧文档不同，实现按理由更强的一方并注明：

1. **唯一键不含 ``batch_no``**：业务确认 2026-10-04「**现在不存在同缸不同价**」。
   原 ``modules/06 §3.1`` 写「同缸不同价必须有不同 ``batch_no``」，而原 ``§7.2.0``
   的 DDL 里**根本没有 ``batch_no`` 列** —— 两处互相矛盾，且谁也表达不了「同缸不同价」。
   现在的口径：同缸同匹唯一一行，价格由 ADR-0011「批次内恒定」保证；要改价走红冲重入。
2. **``dye_lot_no`` / ``bolt_no`` 由可空改 ``NOT NULL DEFAULT '-'``**：它们在唯一键里，
   而 **PG 的 NULL 不参与唯一判定** —— 可空会让「无缸号」的批次（辅料最常见）
   重复插入任意多次，而采购单重复提交恰恰最可能发生在辅料上。
3. **``color_code`` 不加外键到 ``colors``**：业务确认「面料缸色与成衣色卡**不是同一概念**，
   但取值共用颜色表」。加外键会**在数据库层面暗示两者等价**，而选批时要按缸色换算
   成衣用量仍需采购到货时的映射 —— 那个映射 P1 不做。存字符串 + 候选接口从
   ``colors`` 取值，是「复用取值但不强加等价」的做法。
4. **``wip_ledger_lines.unit_cost`` 恒为 NULL**（ADR-0018）：WIP 不结转成本，
   成衣成本在最后一道工序入库时一次性结转。所以那一列建成可空而**不加 CHECK** ——
   加了 ``CHECK (unit_cost IS NULL)`` 就把「将来若要改口径」变成一次迁移。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811 —— 别名照抄 docs/04 §2

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_AUDIT: tuple[sa.Column, ...] = (
    sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.Column("created_by", PgUUID(as_uuid=True), nullable=False),
    sa.Column(
        "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.Column("updated_by", PgUUID(as_uuid=True), nullable=False),
    sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
    sa.Column("remark", sa.Text(), nullable=True),
)

#: ⚠️ ``create_type=False`` 是必需的：``postgresql.ENUM`` 默认 ``create_type=True``，
#: 在 create_table 时会**自己再发一条** ``CREATE TYPE``，与本迁移手写的那条撞车
#: （报 ``type already exists``）。类型由我们自己建 —— 这样 downgrade 才管得住。
#: 0004 的 ``size_class`` 与 0007 的 ``material_type`` 都踩过一次。
_PURCHASE_PURPOSE_ENUM = postgresql.ENUM(
    "NORMAL", "REWORK_RECEIPT", "RETURN", "SAMPLE", name="purchase_purpose", create_type=False
)


def _cols() -> list[sa.Column]:
    return [
        sa.Column(
            "id",
            PgUUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        *_AUDIT,
    ]


def _ver(table: str) -> sa.CheckConstraint:
    return sa.CheckConstraint("version > 0", name=f"ck_{table}_version_positive")


def upgrade() -> None:
    created_purpose = _create_purchase_purpose()
    _create_material_stocks()
    _create_wip_stocks()
    _create_soft_delete_indexes()
    # 记在上下文里给 downgrade 用 —— 只有**本迁移建的**才该被删掉。
    # 之前建的由别的途径负责，我们不该替它做决定（删了会连带毁掉那张表的数据）
    op.get_context().config.attributes["created_purchase_purpose"] = created_purpose


def downgrade() -> None:
    # ⚠️ 先子后父：wip_ledger_lines → wip_stocks → material_stocks
    for table in ("wip_ledger_lines", "wip_stocks", "material_stocks"):
        op.drop_table(table)
    if op.get_context().config.attributes.get("created_purchase_purpose"):
        # ⚠️ 只删本迁移建的。不加 IF EXISTS 也不加反向判断 —— 判据在上面，
        #    这里再判一次会出现「upgrade 没建、downgrade 也不删」的不对称
        op.execute("DROP TYPE purchase_purpose")


def _create_purchase_purpose() -> bool:
    """采购用途枚举（ADR-0012）。返回「是否真的建了」。

    ⚠️ **不能用 ``CREATE TYPE IF NOT EXISTS``** —— PostgreSQL **没有**这个语法
    （只有 ``CREATE TABLE IF NOT EXISTS``）。实测报
    ``syntax error at or near "NOT"``。所以先查 ``pg_type`` 再决定建不建。

    ⚠️ 为什么要判存在性：0007 的测试会在库里临时建枚举，而生产库里这个类型
    可能已经由别的途径建过 —— 无条件 CREATE 会让那些库升不上去。
    """
    exists = (
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM pg_type WHERE typname = 'purchase_purpose'"))
        .scalar()
    )
    if exists:
        return False
    op.execute(
        "CREATE TYPE purchase_purpose AS ENUM ('NORMAL', 'REWORK_RECEIPT', 'RETURN', 'SAMPLE')"
    )
    return True


def _create_soft_delete_indexes() -> None:
    """``SoftDeleteMixin`` 上声明了 ``index=True``，每张软删表都要有。

    列表查询恒带 ``WHERE deleted_at IS NULL``，没索引就是全表扫。
    """
    for table in ("material_stocks", "wip_stocks"):
        op.create_index(f"ix_{table}_deleted_at", table, ["deleted_at"], unique=False)


def _create_material_stocks() -> None:
    """面料/辅料结存（**批次粒度**）。

    ⚠️ **不给 ``erp_app`` 授 DELETE**：库存是业务数据，AGENTS §2.1 禁止物理删除。
    与 ADR-0025 的字典表白名单无关 —— 那 4 张是字典（删掉即不要了），库存不是。
    """
    op.create_table(
        "material_stocks",
        *_cols(),
        sa.Column(
            "warehouse_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("warehouses.id", name="fk_material_stocks_warehouse"),
            nullable=False,
        ),
        sa.Column(
            "material_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("materials.id", name="fk_material_stocks_material"),
            nullable=False,
        ),
        sa.Column(
            "supplier_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("suppliers.id", name="fk_material_stocks_supplier"),
            nullable=True,
            comment="来源供应商（ADR-0022：由到货登记写入，不可手改）",
        ),
        sa.Column(
            "batch_no",
            sa.String(64),
            nullable=True,
            comment="对外批次号（09 §1.6，展示用，不进唯一键）",
        ),
        sa.Column(
            "color_code",
            sa.String(32),
            server_default=sa.text("'-'"),
            nullable=False,
            comment="缸号对应色；⚠️ 与款号色不是同一概念，取值共用 colors 字典",
        ),
        sa.Column(
            "dye_lot_no",
            sa.String(64),
            server_default=sa.text("'-'"),
            nullable=False,
            comment="缸号；无缸号时填 `-`（Q-ST05）。NOT NULL 的原因见类注释",
        ),
        sa.Column(
            "bolt_no",
            sa.String(32),
            server_default=sa.text("'-'"),
            nullable=False,
            comment="匹号；同缸多匹 → 多行；不分匹时填 `-`",
        ),
        sa.Column(
            "width_cm",
            sa.Numeric(8, 2),
            nullable=False,
            comment="门幅（cm）",
        ),
        sa.Column(
            "effective_width_cm",
            sa.Numeric(8, 2),
            nullable=True,
            comment="有效门幅（02 C24 门幅校验用）",
        ),
        sa.Column(
            "weight_kg",
            sa.Numeric(14, 3),
            server_default=sa.text("0"),
            nullable=False,
            comment="重量（kg），布料主计量",
        ),
        sa.Column(
            "stock_qty",
            sa.Numeric(14, 3),
            server_default=sa.text("0"),
            nullable=False,
            comment="账面结存（布料按米）",
        ),
        sa.Column(
            "total_length_m",
            sa.Numeric(14, 3),
            server_default=sa.text("0"),
            nullable=False,
            comment="总米数",
        ),
        sa.Column(
            "locked_qty",
            sa.Numeric(14, 3),
            server_default=sa.text("0"),
            nullable=False,
            comment="冗余锁定量缓存，与 stock_reservations 聚合一致",
        ),
        sa.Column(
            "safety_stock", sa.Numeric(14, 3), nullable=True, comment="安全库存，低于触发 stock_low"
        ),
        sa.Column(
            "purpose",
            _PURCHASE_PURPOSE_ENUM,
            server_default=sa.text("'NORMAL'"),
            nullable=False,
            comment="采购用途（ADR-0012）：返修布成本天然分开的唯一依据（BR-ST-25）",
        ),
        sa.Column(
            "unit_cost",
            sa.Numeric(12, 6),
            nullable=False,
            comment="批次实际成本（元/米），批次内恒定（ADR-0011）",
        ),
        sa.Column(
            "total_amount",
            sa.Numeric(18, 4),
            nullable=True,
            comment="= ROUND(stock_qty × unit_cost, 4)，冗余供报表",
        ),
        sa.Column("in_date", sa.Date(), nullable=False, comment="入库日（FIFO 排序键）"),
        sa.Column(
            "expiry_date",
            sa.Date(),
            nullable=True,
            comment="有效期（FEFO 排序键；空即视为 FEFO 不适用）",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_material_stocks"),
        _ver("material_stocks"),
        sa.CheckConstraint("stock_qty >= 0", name="ck_material_stocks_qty"),
        sa.CheckConstraint(
            "locked_qty >= 0 AND locked_qty <= stock_qty", name="ck_material_stocks_lock"
        ),
        sa.CheckConstraint("width_cm > 0", name="ck_material_stocks_width"),
        comment="面料/辅料结存（批次粒度；04 §7.2.0）",
    )
    # ⚠️ **不含 batch_no**：业务确认「不存在同缸不同价」（见迁移头注 1）
    op.create_index(
        "uq_material_stocks_lot",
        "material_stocks",
        ["warehouse_id", "material_id", "dye_lot_no", "bolt_no"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    # ⚠️ 部分索引 + ``stock_qty > locked_qty``：选批候选只查「还有可用量」的批次，
    #    把不可用的排除在索引外（BR-ST-17）
    op.create_index(
        "idx_material_stocks_pick",
        "material_stocks",
        ["material_id", "warehouse_id", "in_date"],
        postgresql_where=sa.text("deleted_at IS NULL AND stock_qty > locked_qty"),
    )


def _create_wip_stocks() -> None:
    """在制品 WIP（裁片，ADR-0018）。

    ⚠️ ``style_no`` **无外键**：``styles.uq_styles_no`` 是部分索引，PG 不允许外键引用
    （T-DOCS-003 修过同一类）。外键在 ``style_id`` 上。
    """
    op.create_table(
        "wip_stocks",
        *_cols(),
        sa.Column(
            "workshop_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("workshops.id", name="fk_wip_stocks_workshop"),
            nullable=False,
        ),
        sa.Column(
            "style_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("styles.id", name="fk_wip_stocks_style"),
            nullable=False,
        ),
        sa.Column("style_no", sa.String(32), nullable=False, comment="冗余：历史单据按它引用"),
        sa.Column(
            "product_category_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("product_categories.id", name="fk_wip_stocks_category"),
            nullable=True,
            comment="建单时快照，便于按分类统计",
        ),
        sa.Column("color_code", sa.String(32), nullable=False, comment="色码"),
        sa.Column("size_code", sa.String(32), nullable=False, comment="尺码码"),
        sa.Column(
            "qty",
            sa.Numeric(14, 3),
            server_default=sa.text("0"),
            nullable=False,
            comment="裁片件数",
        ),
        sa.Column(
            "in_qty",
            sa.Numeric(14, 3),
            server_default=sa.text("0"),
            nullable=False,
            comment="累计由裁剪转入",
        ),
        sa.Column(
            "out_qty",
            sa.Numeric(14, 3),
            server_default=sa.text("0"),
            nullable=False,
            comment="累计转成衣/报损",
        ),
        sa.Column("source_doc_type", sa.String(32), nullable=False, comment="'CUTTING_ORDER'"),
        sa.Column("source_doc_id", PgUUID(as_uuid=True), nullable=False, comment="来源单据 id"),
        sa.PrimaryKeyConstraint("id", name="pk_wip_stocks"),
        _ver("wip_stocks"),
        sa.CheckConstraint("qty >= 0 AND out_qty <= in_qty", name="ck_wip_qty"),
        comment="在制品 WIP 裁片（ADR-0018；04 §7.10）",
    )
    op.create_index(
        "uq_wip_stocks_src",
        "wip_stocks",
        ["style_no", "color_code", "size_code", "source_doc_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("idx_wip_stocks_style", "wip_stocks", ["style_id", "color_code", "size_code"])
    op.create_index("idx_wip_stocks_source", "wip_stocks", ["source_doc_type", "source_doc_id"])

    # ⚠️ append-only 流水（04 §2 的例外）：不给应用账号 UPDATE/DELETE，
    #    与 document_logs 同款做法。见 downgrade 的 REVOKE。
    op.create_table(
        "wip_ledger_lines",
        sa.Column(
            "id", PgUUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column(
            "wip_stock_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("wip_stocks.id", name="fk_wip_ledger_lines_stock"),
            nullable=False,
        ),
        sa.Column("direction", sa.String(8), nullable=False, comment="IN / OUT"),
        sa.Column("qty", sa.Numeric(14, 3), nullable=False, comment="数量"),
        # ⚠️ **不加 CHECK (unit_cost IS NULL)**：ADR-0018 说 WIP 不结转成本，但把口径
        #    写死成约束就意味着「将来要改」必须走一次迁移。留可空 + 注释说明现状。
        sa.Column(
            "unit_cost",
            sa.Numeric(12, 6),
            nullable=True,
            comment="WIP 不结转成本，恒为 NULL；⚠️ 刻意**不加 CHECK (unit_cost IS NULL)**",
        ),
        sa.Column("doc_type", sa.String(32), nullable=False),
        sa.Column("doc_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("operator_id", PgUUID(as_uuid=True), nullable=True),
        sa.Column("remark", sa.Text(), nullable=True),
        # ⚠️ **不建 updated_at / updated_by**：append-only 表没有「更新」这个动作，
        #    那两列会永远是空的 —— 空着比没有更糟（读的人会以为「没人改过」）。
        #    口径与 document_logs 一致，见 models.py::WipLedgerLine 的类注释
        sa.Column(
            "created_by",
            PgUUID(as_uuid=True),
            nullable=False,
            comment="写入人（append-only：不设 updated_*）",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_wip_ledger_lines"),
        comment="WIP 出入库流水（append-only；04 §7.10）",
    )
    op.create_index("idx_wip_ledger_lines_stock", "wip_ledger_lines", ["wip_stock_id"])
    op.create_index("idx_wip_ledger_lines_doc", "wip_ledger_lines", ["doc_type", "doc_id"])
    op.execute("REVOKE UPDATE, DELETE ON wip_ledger_lines FROM erp_app")
