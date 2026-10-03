"""0005 款号、色码尺码、尺码比例、款号工序与工序单价（组 D）。

权威来源：
    - ``docs/04 §7.7.1``        尺码比例 DDL
    - ``docs/04 §7.8.2``        款号工序配置 DDL
    - **ADR-0026**              工序单价三档（本迁移的**唯一**权威，覆盖 04 §7.8.3）
    - ``docs/04 §7.11``        商品分类（``styles.category_id``）
    - ``docs/modules/01 §3.2``  styles / style_colors / style_sizes 字段表
    - ``docs/modules/01 §3.5``  customers 字段表

三处与旧文档不同的地方，实现按理由更强的一方并注明：

1. ``operation_rates`` 整表按 **ADR-0026** 重写 —— ``style_no`` 可空、新增
   ``product_category_id``、唯一索引改四列 + ``NULLS NOT DISTINCT``、
   新增 ``ck_operation_rates_target``。04 §7.11 里那条
   ``idx_operation_rates_lookup ON (operation_id, ...)`` 被 ADR-0026 §3 **作废**
   （``operation_id`` 列根本不存在）。
2. ``styles.customer_id`` **可空**（modules/01 §3.2 写的是必填）。依据是任务卡
   引用的 Q-P0-10 决策：客户只用于"建议号分组与筛选"，款号由用户自定义，
   不该被客户绑死。
3. 单价列用 ``numeric(12,6)``（docs/04 §4），不是 ``numeric(14,4)``。
4. **子表外键指向 ``styles.id`` 而不是 ``styles.style_no``**。§7.7.1 写的是
   ``REFERENCES styles(style_no)``，但 §7.11 又要求款号唯一索引是
   ``WHERE deleted_at IS NULL`` 的**部分索引** —— PostgreSQL 的外键只能引用普通
   唯一约束，引用不了部分索引（实测报 "there is no unique constraint matching
   given keys for referenced table \"styles\""）。
   两个要求只能取一个：保留部分唯一索引（款号软删后可复用）+ 外键改指主键。
   所以子表同时存 ``style_id``（uuid，FK→styles.id）与 ``style_no``
   （业务键，查询与唯一约束用），由 service 从同一次查询里取两个值一起写。
   已登记 docs/12 §5 L-029。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811 —— 别名照抄 docs/04 §2

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
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
    _create_customers()
    _create_styles()
    _create_style_colors_sizes()
    _create_ratios()
    _create_style_operations()
    _create_operation_rates()
    _create_sequences()
    _create_soft_delete_indexes()


def downgrade() -> None:
    for table in (
        "style_no_sequences",
        "operation_rates",
        "style_operations",
        "style_color_size_ratios",
        "style_sizes",
        "style_colors",
        "styles",
        "customers",
    ):
        op.drop_table(table)


def _create_soft_delete_indexes() -> None:
    """补 8 个 ``ix_<table>_deleted_at``。

    ``SoftDeleteMixin`` 在模型上声明了 ``index=True``，所以每张软删表都必须有。
    列表查询恒带 ``WHERE deleted_at IS NULL``，没索引就是全表扫 ——
    0002 漏建过、0003 补过，别再漏第三次。
    """
    for table in (
        "customers",
        "styles",
        "style_colors",
        "style_sizes",
        "style_color_size_ratios",
        "style_operations",
        "operation_rates",
    ):
        op.create_index(f"ix_{table}_deleted_at", table, ["deleted_at"], unique=False)


# ------------------------------------------------------------------ 客户


def _create_customers() -> None:
    op.create_table(
        "customers",
        *_cols(),
        sa.Column("code", sa.String(32), nullable=False, comment="客户编码"),
        sa.Column("name", sa.String(128), nullable=False, comment="客户全称"),
        sa.Column("short_name", sa.String(64), nullable=True, comment="简称"),
        sa.Column("contact", sa.String(64), nullable=True, comment="联系人"),
        sa.Column("phone", sa.String(32), nullable=True, comment="联系电话"),
        sa.Column("address", sa.String(255), nullable=True, comment="地址"),
        sa.Column("tax_no", sa.String(32), nullable=True, comment="税号"),
        sa.Column(
            "settlement_period_days",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
            comment="账期天数，应收核销用",
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
            comment="停用后不可新建单据，历史照常",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_customers"),
        _ver("customers"),
        comment="客户（modules/01 §3.5）",
    )
    op.create_index("uq_customers_code", "customers", ["code"], unique=True)
    op.create_index("idx_customers_is_active", "customers", ["is_active"])
    op.create_index(
        "idx_customers_settlement_period_days",
        "customers",
        ["settlement_period_days"],
    )
    # 04 §5.1：候选搜索必须命中 GIN 三元组索引
    op.execute(
        "CREATE INDEX idx_customers_trgm ON customers "
        "USING gin ((code || ' ' || name) gin_trgm_ops)"
    )


# ------------------------------------------------------------------ 款号


def _create_styles() -> None:
    op.create_table(
        "styles",
        *_cols(),
        sa.Column("style_no", sa.String(32), nullable=False, comment="款号，存库统一转大写（R1）"),
        # ⚠️ 可空：modules/01 §3.2 写必填，但 Q-P0-10 决策后款号由用户自定义，
        # 客户只用于建议号分组与筛选，不该把款号绑死在客户上
        sa.Column(
            "customer_id",
            PgUUID(as_uuid=True),
            nullable=True,
            comment="归属客户（仅用于建议号分组与筛选）",
        ),
        sa.Column(
            "customer_style_no", sa.String(64), nullable=True, comment="客户款号，印在唛头上"
        ),
        sa.Column("name", sa.String(128), nullable=False, comment="款名"),
        sa.Column("bulk_qty", sa.Integer(), nullable=True, comment="大货数量（09 §1.1）"),
        sa.Column(
            "category_id",
            PgUUID(as_uuid=True),
            nullable=False,
            comment="商品分类，04 §7.11：分类在款号档案上定死，单据不冗余",
        ),
        sa.Column(
            "merchandiser_id",
            PgUUID(as_uuid=True),
            nullable=True,
            comment="跟单员；Q-P0-05：跟单数据范围按此隔离（SELF → 本人款号）",
        ),
        sa.Column(
            "last_used_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="最近使用；05 §9.5.2 候选默认按此 DESC（常用优先）",
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
            comment="停用即 R2：不允许新建裁剪/打菲单，历史照常",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_styles"),
        _ver("styles"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], name="fk_styles_customers"),
        sa.ForeignKeyConstraint(
            ["category_id"], ["product_categories.id"], name="fk_styles_product_categories"
        ),
        sa.ForeignKeyConstraint(["merchandiser_id"], ["users.id"], name="fk_styles_merchandiser"),
        comment="款号（modules/01 §3.2；R2：已产生计件或库存的款号只能停用）",
    )
    # ⚠️ **部分唯一索引**（04 §7.11 / R2）：软删的款号不占用唯一键，
    # 否则删掉一个款号就再也建不了同号的
    op.execute("CREATE UNIQUE INDEX uq_styles_no ON styles (style_no) WHERE deleted_at IS NULL")
    op.create_index("idx_styles_customer_id", "styles", ["customer_id"])
    op.create_index("idx_styles_merchandiser_id", "styles", ["merchandiser_id"])
    op.create_index("idx_styles_last_used_at", "styles", ["last_used_at"])
    op.execute(
        "CREATE INDEX idx_styles_trgm ON styles USING gin ((style_no || ' ' || name) gin_trgm_ops)"
    )


def _create_style_colors_sizes() -> None:
    op.create_table(
        "style_colors",
        *_cols(),
        sa.Column(
            "style_id",
            PgUUID(as_uuid=True),
            nullable=False,
            comment="款号主键（外键指向 styles.id，见文件头注 4）",
        ),
        sa.Column(
            "style_no",
            sa.String(32),
            nullable=False,
            comment="款号（业务键，冗余便于查询与唯一约束）",
        ),
        sa.Column("color_group", sa.String(32), nullable=False, comment="色组（09 §1.1）"),
        sa.Column("color_code", sa.String(32), nullable=False, comment="色码 BLK / WHT"),
        sa.Column("color_name", sa.String(64), nullable=False, comment="中文色名"),
        sa.Column(
            "material_color_code",
            sa.String(32),
            nullable=True,
            comment="面料对应色/缸别标识，供排料对色",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_style_colors"),
        _ver("style_colors"),
        sa.ForeignKeyConstraint(["style_id"], ["styles.id"], name="fk_style_colors_styles"),
        comment="款号色组（modules/01 §3.2）",
    )
    # 一个款号下色码不重复；色组内色码也不重复
    op.create_index(
        "uq_style_colors_style_color", "style_colors", ["style_no", "color_code"], unique=True
    )
    op.create_index(
        "uq_style_colors_style_group", "style_colors", ["style_no", "color_group"], unique=True
    )
    op.create_index("idx_style_colors_color_code", "style_colors", ["color_code"])

    op.create_table(
        "style_sizes",
        *_cols(),
        sa.Column(
            "style_id",
            PgUUID(as_uuid=True),
            nullable=False,
            comment="款号主键（外键指向 styles.id，见文件头注 4）",
        ),
        sa.Column(
            "style_no",
            sa.String(32),
            nullable=False,
            comment="款号（业务键，冗余便于查询与唯一约束）",
        ),
        sa.Column("size_code", sa.String(32), nullable=False, comment="尺码码 S / M / L"),
        sa.Column("size_name", sa.String(64), nullable=False, comment="实际尺码 S(155/80A)"),
        sa.Column(
            "sort_no",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
            comment="排序，报表按序输出",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_style_sizes"),
        _ver("style_sizes"),
        sa.ForeignKeyConstraint(["style_id"], ["styles.id"], name="fk_style_sizes_styles"),
        comment="款号尺码集合（modules/01 §3.2）",
    )
    op.create_index(
        "uq_style_sizes_style_size", "style_sizes", ["style_no", "size_code"], unique=True
    )
    op.create_index("idx_style_sizes_size_code", "style_sizes", ["size_code"])


# ------------------------------------------------------------------ 比例


def _create_ratios() -> None:
    op.create_table(
        "style_color_size_ratios",
        *_cols(),
        sa.Column(
            "style_id",
            PgUUID(as_uuid=True),
            nullable=False,
            comment="款号主键（外键指向 styles.id，见文件头注 4）",
        ),
        sa.Column(
            "style_no",
            sa.String(32),
            nullable=False,
            comment="款号（业务键，冗余便于查询与唯一约束）",
        ),
        sa.Column("color_code", sa.String(32), nullable=False, comment="色码"),
        sa.Column("size_code", sa.String(32), nullable=False, comment="尺码码"),
        sa.Column(
            "ratio",
            sa.Numeric(14, 4),
            nullable=False,
            comment="手数（可小数，如 1.5 手）。⚠️ 比例只是**建议值**，"
            "裁剪单行的 hands × qty_per_hand 才是权威（ADR-0014）",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_style_color_size_ratios"),
        _ver("style_color_size_ratios"),
        sa.ForeignKeyConstraint(
            ["style_id"], ["styles.id"], name="fk_style_color_size_ratios_styles"
        ),
        sa.CheckConstraint("ratio > 0", name="ck_style_color_size_ratio"),
        comment="款号尺码比例（建议值，04 §7.7.1）",
    )
    op.create_index(
        "uq_style_color_size_ratios",
        "style_color_size_ratios",
        ["style_no", "color_code", "size_code"],
        unique=True,
    )
    op.create_index(
        "idx_style_color_size_ratios_lookup",
        "style_color_size_ratios",
        ["style_no", "color_code"],
    )


# ------------------------------------------------------------ 款号工序配置


def _create_style_operations() -> None:
    op.create_table(
        "style_operations",
        *_cols(),
        sa.Column(
            "style_id",
            PgUUID(as_uuid=True),
            nullable=False,
            comment="款号主键（外键指向 styles.id，见文件头注 4）",
        ),
        sa.Column(
            "style_no",
            sa.String(32),
            nullable=False,
            comment="款号（业务键，冗余便于查询与唯一约束）",
        ),
        sa.Column("operation_no", sa.String(16), nullable=False, comment="工序号"),
        sa.Column("sequence", sa.Integer(), nullable=False, comment="工序顺序 1,2,3…"),
        sa.Column(
            "bundle_qty",
            sa.Numeric(14, 3),
            server_default=sa.text("1"),
            nullable=False,
            comment="该款该工序一扎几件（R9：覆盖 operations.default_bundle_qty）",
        ),
        sa.Column(
            "is_piecework",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
            comment="该款该工序是否计件（可覆盖工序字典默认值）",
        ),
        # 04 §7.11 / ADR-0018：最后一道工序标记（默认整烫）
        sa.Column(
            "is_final_operation",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment="是否最后一道工序（默认整烫；识别不到必须人工指定）",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_style_operations"),
        _ver("style_operations"),
        sa.ForeignKeyConstraint(["style_id"], ["styles.id"], name="fk_style_operations_styles"),
        sa.ForeignKeyConstraint(
            ["operation_no"], ["operations.operation_no"], name="fk_style_operations_operations"
        ),
        sa.CheckConstraint("sequence > 0", name="ck_style_operations_sequence_positive"),
        sa.CheckConstraint("bundle_qty > 0", name="ck_style_operations_bundle_qty_positive"),
        comment="款号工序配置（04 §7.8.2；模板载体）",
    )
    op.create_index(
        "uq_style_operations", "style_operations", ["style_no", "operation_no"], unique=True
    )
    op.create_index("idx_style_operations_operation_no", "style_operations", ["operation_no"])


# ------------------------------------------------------------ 工序单价历史


def _create_operation_rates() -> None:
    """按 **ADR-0026** 建表（取代 04 §7.8.3 的单档版本）。"""
    op.create_table(
        "operation_rates",
        *_cols(),
        sa.Column("operation_no", sa.String(16), nullable=False, comment="工序号"),
        # ★ 可空：档位 2（分类价）与档位 3（工序通用价）都不限定具体款号
        sa.Column(
            "style_id",
            PgUUID(as_uuid=True),
            nullable=True,
            comment="★可空：款号主键，外键指向 styles.id（见文件头注 4）",
        ),
        sa.Column(
            "style_no",
            sa.String(32),
            nullable=True,
            comment="★可空：空 = 该档不限定款号（ADR-0026）。业务键，冗余便于取价与审计",
        ),
        sa.Column(
            "product_category_id",
            PgUUID(as_uuid=True),
            nullable=True,
            comment="★可空：空 = 不限分类（ADR-0026）",
        ),
        sa.Column("effective_from", sa.Date(), nullable=False, comment="生效日（含）"),
        sa.Column(
            "effective_to",
            sa.Date(),
            nullable=True,
            comment="失效日（不含）；NULL = 当前有效。调价只改这一列，绝不 UPDATE unit_price（R11）",
        ),
        sa.Column(
            "unit_price",
            sa.Numeric(12, 6),
            nullable=False,
            comment="单价；0 允许（免费工序），但调价仍需 reason（R20）",
        ),
        sa.Column("reason", sa.Text(), nullable=True, comment="调价原因（R20 调价必填）"),
        sa.PrimaryKeyConstraint("id", name="pk_operation_rates"),
        _ver("operation_rates"),
        sa.ForeignKeyConstraint(
            ["operation_no"], ["operations.operation_no"], name="fk_operation_rates_operations"
        ),
        sa.ForeignKeyConstraint(["style_id"], ["styles.id"], name="fk_operation_rates_styles"),
        sa.ForeignKeyConstraint(
            ["product_category_id"],
            ["product_categories.id"],
            name="fk_operation_rates_product_categories",
        ),
        # ★ 档位互斥：**两者不能同时有值**。
        #
        # ⚠️ ADR-0026 §1 的 DDL 写的是
        #    ``CHECK (style_no IS NOT NULL OR product_category_id IS NOT NULL)``
        #    （"至少指定其中之一"），但 §2 的**档位 3（工序通用价）恰恰要求两者都
        #    为空** —— 于是那条约束把第三档禁掉了，实测插入档位 3 直接报
        #    ``ck_operation_rates_target`` 违例。
        #
        #    三档的真实形态是"两者恰好一个有值，或者都没有"，所以约束应该是
        #    "不能同时有值"（AND 取反），而不是"至少一个有值"（OR）。
        #    已按此修正并登记 docs/12 §5 L-030，ADR §1 的 SQL 同步标注。
        sa.CheckConstraint(
            "NOT (style_no IS NOT NULL AND product_category_id IS NOT NULL)",
            name="ck_operation_rates_target",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_operation_rates_range",
        ),
        sa.CheckConstraint("unit_price >= 0", name="ck_operation_rates_price"),
        comment="工序单价历史（ADR-0026 三档；只追加，调价只关区间）",
    )
    # ★★ NULLS NOT DISTINCT 是 ADR-0026 的关键技术点（PG 15+，本项目 PG 16）。
    #    PG 默认认为 NULL 与 NULL 互不相等，没有它「款号通用 + 分类 NULL + 同工序
    #    + 同生效日」能插任意多行，取价变成不确定 —— 而计件金额错就是工资错。
    op.execute(
        "CREATE UNIQUE INDEX uq_operation_rates ON operation_rates "
        "(style_no, product_category_id, operation_no, effective_from) "
        "NULLS NOT DISTINCT"
    )
    # ADR-0026 §3 统一后的取价索引（作废 04 §7.11 的 operation_id 版本）
    op.execute(
        "CREATE INDEX idx_operation_rates_lookup ON operation_rates "
        "(operation_no, style_no, product_category_id, effective_from DESC)"
    )
    op.create_index(
        "idx_operation_rates_operation_current",
        "operation_rates",
        ["operation_no", "effective_from"],
        postgresql_where=sa.text("effective_to IS NULL"),
    )


# ---------------------------------------------------------------- 序号表


def _create_sequences() -> None:
    """建议货号的序号计数器。

    ⚠️ 这是**建议号**用的，款号本身由用户自定义（Q-P0-04）。表存在的意义是
    "给个不重复的建议" —— 取号走 ``SELECT ... FOR UPDATE``，同一 ``(客户, 年份)``
    分组递增，全厂（``customer_id`` 为空）单独一组。
    """
    op.create_table(
        "style_no_sequences",
        sa.Column(
            "id",
            PgUUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "customer_id",
            PgUUID(as_uuid=True),
            nullable=True,
            comment="客户；空 = 全厂序列",
        ),
        sa.Column("year", sa.Integer(), nullable=False, comment="年份，如 2026"),
        sa.Column(
            "next_no",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
            comment="下一个可用序号，从 1 开始",
        ),
        # ⚠️ **不能**用 ``PRIMARY KEY (customer_id, year)``：主键列隐式 NOT NULL，
        # 而"客户为空 = 全厂序列"要求 customer_id 真的能是 NULL —— 用主键的话
        # 全厂序列那一行永远插不进去。改成两条**部分唯一索引**，各管一段。
        sa.PrimaryKeyConstraint("id", name="pk_style_no_sequences"),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["customers.id"], name="fk_style_no_sequences_customers"
        ),
        sa.CheckConstraint("next_no >= 1", name="ck_style_no_sequences_next_no"),
        comment="建议货号序号计数器（Q-P0-05：序号按客户分组递增）",
    )
    # ① 有客户的：(customer_id, year) 唯一
    op.create_index(
        "uq_style_no_sequences_customer",
        "style_no_sequences",
        ["customer_id", "year"],
        unique=True,
        postgresql_where=sa.text("customer_id IS NOT NULL"),
    )
    # ② 全厂的：每年只允许一行（customer_id IS NULL）
    op.create_index(
        "uq_style_no_sequences_factory",
        "style_no_sequences",
        ["year"],
        unique=True,
        postgresql_where=sa.text("customer_id IS NULL"),
    )
