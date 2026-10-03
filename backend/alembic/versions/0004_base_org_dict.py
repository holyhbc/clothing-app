"""0004 组织、字典与工序主数据（组 C 十张表）。

权威来源：
    - ``docs/04 §7.4``    颜色 / 尺码 / 码表 DDL（逐字照抄）
    - ``docs/04 §7.8.1``  工序主数据 DDL
    - ``docs/04 §7.11``   商品分类 DDL
    - ``docs/04 §5.1``    trgm GIN 索引
    - ``docs/modules/01 §3.1`` 车间 / 组别 / 仓库 / 计量单位字段表

三条与规范原文不一致的地方，本迁移按**理由更强的一方**实现并在下面注明：

1. ``operations.workshop_id`` 在 §7.8.1 写的是 ``NOT NULL``，但**同一行的注释**写
   「可空 = 通用」，而 modules/01 §3.4 明确「04 §7.5.1 注明可空 = 通用工序（如
   查勘 全厂通用），故实体可空」。→ 实现为**可空**（两处文字都指向可空，
   ``NOT NULL`` 是笔误）。
2. ``sizes`` 唯一键用 ``(size_code, size_class)`` 而非 ``size_code``
   —— **ADR-0027**：内置码表里 L / XL 同时属于女款与男款。
3. §5.1 的 trgm 示例写 ``(color_code || ' ' || color_name)``，但 §7.4 的列名是
   ``name`` 而不是 ``color_name``。§7.4 自称权威 → 用 ``name``。

**权限**：ADR-0025 给了 4 张字典表 ``GRANT DELETE`` 的白名单例外；其余表保持
「应用账号无 DELETE」。迁移末尾显式 GRANT / REVOKE，不依赖默认权限。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811 —— 别名照抄 docs/04 §2

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: ADR-0025 §决策 2 的真删白名单：只有这 4 张字典表给应用账号 DELETE
DELETE_WHITELIST: tuple[str, ...] = ("colors", "sizes", "size_groups", "size_group_items")

#: 公共字段（docs/04 §2）—— 逐字照抄，不多不少
_AUDIT_COLUMNS: tuple[sa.Column, ...] = (
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
#: 在 create_table 时会**自己再发一条** ``CREATE TYPE``，与本迁移
#: ``_create_size_class_type()`` 手写的那条撞车，报
#: ``type "size_class" already exists``。类型由我们自己建，声明时就关掉自动建。
_SIZE_CLASS_ENUM = postgresql.ENUM("MENS", "WOMENS", "KIDS", name="size_class", create_type=False)


def _base_columns() -> list[sa.Column]:
    """每个新表的 ``id`` 主键（04 §1：uuid + gen_random_uuid()）。"""
    return [
        sa.Column(
            "id",
            PgUUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        *_AUDIT_COLUMNS,
    ]


def _version_check(table: str) -> sa.CheckConstraint:
    """04 §2 要求 ``ck_<table>_version_positive``。"""
    return sa.CheckConstraint("version > 0", name=f"ck_{table}_version_positive")


def upgrade() -> None:
    _create_size_class_type()
    _create_org_tables()
    _create_dict_tables()
    _create_operations()
    _create_product_categories()
    _create_soft_delete_indexes()
    _backfill_auth_foreign_keys()
    _grant_delete_whitelist()


def _create_soft_delete_indexes() -> None:
    """补 8 个 ``ix_<table>_deleted_at``。

    ``SoftDeleteMixin`` 在模型上声明了 ``index=True``，所以每张软删表都必须有
    这个索引。列表查询恒带 ``WHERE deleted_at IS NULL``，没索引就是全表扫。
    （迁移 0002 漏建了同类的三个，是 0003 补的 —— 别再漏第三次。）
    """
    for table in (
        "workshops",
        "workshop_groups",
        "warehouses",
        "uom_units",
        "colors",
        "sizes",
        "size_groups",
        "operations",
        "product_categories",
    ):
        op.create_index(f"ix_{table}_deleted_at", table, ["deleted_at"], unique=False)


def downgrade() -> None:
    # ⚠️ 顺序要紧：必须先撤掉本迁移补的那两条 auth 外键，否则 `workshops` 被
    # `users` / `role_workshops` 引用，`DROP TABLE workshops` 会报
    # "cannot drop table workshops because other objects depend on it"，
    # 而那时事务已部分执行，回滚会连带把已经 DROP 掉的表留在不一致状态。
    for name, table in (
        ("fk_users_workshops", "users"),
        ("fk_role_workshops_workshops", "role_workshops"),
    ):
        op.drop_constraint(name, table, type_="foreignkey")

    for table in (
        "product_categories",
        "operations",
        "size_group_items",
        "size_groups",
        "sizes",
        "colors",
        "uom_units",
        "warehouses",
        "workshop_groups",
        "workshops",
    ):
        op.drop_table(table)
    op.execute("DROP TYPE IF EXISTS size_class")


# ------------------------------------------------------------------ 枚举


def _create_size_class_type() -> None:
    """``CREATE TYPE size_class``（04 §7.4）。

    ⚠️ **只能追加值，不能改值**（04 §3）：要改含义就新建枚举 + 双写迁移 + ADR。
    枚举值用小写蛇形，与 PG 的惯例一致；Python 侧对应 :class:`app.common.enums.SizeClass`
    的 ``MENS`` / ``WOMENS`` / ``KIDS``（值本来就是大写，映射在模型里声明）。
    """
    op.execute("CREATE TYPE size_class AS ENUM ('MENS', 'WOMENS', 'KIDS')")


# -------------------------------------------------------------- 组织类四表


def _create_org_tables() -> None:
    op.create_table(
        "workshops",
        *_base_columns(),
        sa.Column("code", sa.String(32), nullable=False, comment="车间编码，如 CUT / SEW"),
        sa.Column("name", sa.String(64), nullable=False, comment="车间名"),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
            comment="停用后不允许新建本车间单据",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workshops"),
        _version_check("workshops"),
        comment="车间（modules/01 §3.1）",
    )
    op.create_index("uq_workshops_code", "workshops", ["code"], unique=True)
    op.create_index("idx_workshops_is_active", "workshops", ["is_active"])

    op.create_table(
        "workshop_groups",
        *_base_columns(),
        sa.Column("workshop_id", PgUUID(as_uuid=True), nullable=False, comment="所属车间"),
        sa.Column("group_no", sa.String(32), nullable=False, comment="组别号（09 §1.4 group_no）"),
        sa.Column("name", sa.String(64), nullable=False, comment="如 03 组 拼前"),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
            comment="停用不影响历史",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workshop_groups"),
        _version_check("workshop_groups"),
        # 组别号只在车间内唯一（modules/01 §3.1「唯一作用域是车间」）
        sa.ForeignKeyConstraint(
            ["workshop_id"], ["workshops.id"], name="fk_workshop_groups_workshops"
        ),
        comment="车间组别（表名不用 groups：GROUP 是 SQL 保留字）",
    )
    op.create_index(
        "uq_workshop_groups_workshop_no",
        "workshop_groups",
        ["workshop_id", "group_no"],
        unique=True,
    )
    op.create_index("idx_workshop_groups_workshop_id", "workshop_groups", ["workshop_id"])

    op.create_table(
        "warehouses",
        *_base_columns(),
        sa.Column("code", sa.String(32), nullable=False, comment="如 FABRIC / TRIM / FG"),
        sa.Column("name", sa.String(64), nullable=False, comment="仓库名"),
        sa.Column(
            "warehouse_type",
            sa.String(32),
            nullable=False,
            comment="FABRIC / TRIMMING / FINISHED_GOOD，与库存双栈对应",
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
            comment="停用后不得出入库",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_warehouses"),
        _version_check("warehouses"),
        comment="仓库（modules/01 §3.1）",
    )
    op.create_index("uq_warehouses_code", "warehouses", ["code"], unique=True)
    op.create_index("idx_warehouses_type_is_active", "warehouses", ["warehouse_type", "is_active"])

    op.create_table(
        "uom_units",
        *_base_columns(),
        sa.Column("code", sa.String(16), nullable=False, comment="M / YD / PCS / KG"),
        sa.Column("name", sa.String(32), nullable=False, comment="米 / 码 / 个 / 公斤"),
        sa.Column(
            "decimal_places",
            sa.SmallInteger(),
            server_default=sa.text("3"),
            nullable=False,
            comment="参与 numeric 精度对齐（docs/04：数量一律 numeric，不 float）",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_uom_units"),
        _version_check("uom_units"),
        sa.CheckConstraint(
            "decimal_places >= 0 AND decimal_places <= 6", name="ck_uom_units_decimal_places"
        ),
        comment="计量单位（modules/01 §3.4）",
    )
    op.create_index("uq_uom_units_code", "uom_units", ["code"], unique=True)


# -------------------------------------------------------------- 字典四表


def _create_dict_tables() -> None:
    op.create_table(
        "colors",
        *_base_columns(),
        sa.Column("color_code", sa.String(32), nullable=False, comment="WHT / BLK / KHK / GRN"),
        sa.Column("name", sa.String(64), nullable=False, comment="本白 / 黑 / 卡其 / 军绿"),
        sa.Column(
            "color_family",
            sa.String(32),
            nullable=True,
            comment="标准色卡族 Pantone TCX / 客户色卡 / 空",
        ),
        sa.Column("pantone_code", sa.String(32), nullable=True, comment="潘通号"),
        sa.Column(
            "is_builtin",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment="是否 seed 内置（仅界面角标，不影响删除权限，R27）",
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
            comment="停用后不参与新建单据，历史照常可查",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_colors"),
        _version_check("colors"),
        comment="颜色字典（04 §7.4；未被引用可真删，ADR-0025）",
    )
    op.create_index("uq_colors_code", "colors", ["color_code"], unique=True)
    # 04 §5.1：候选搜索必须命中 GIN 三元组索引，EXPLAIN 要出现 Bitmap Index Scan。
    # 列名用 name（不是 §5.1 示例里的 color_name）—— §7.4 自称权威。
    op.execute(
        "CREATE INDEX idx_colors_trgm ON colors "
        "USING gin ((color_code || ' ' || name) gin_trgm_ops)"
    )

    op.create_table(
        "sizes",
        *_base_columns(),
        sa.Column("size_code", sa.String(32), nullable=False, comment="S / M / L / XL / 3XL"),
        sa.Column("name", sa.String(64), nullable=False, comment="XL(170/92A) 这类围度标注"),
        sa.Column(
            "size_class",
            _SIZE_CLASS_ENUM,
            nullable=False,
            comment="MENS / WOMENS / KIDS",
        ),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "is_builtin",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment="是否 seed 内置",
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
            comment="停用后不参与新建单据，历史照常可查",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_sizes"),
        _version_check("sizes"),
        # ⚠️ 唯一键含 size_class（ADR-0027）：内置码表里 L / XL 同时属于女款与男款
        comment="尺码字典（04 §7.4 + ADR-0027）",
    )
    # ⚠️ ADR-0027：唯一键含 size_class（L / XL 同时属于女款与男款）
    op.create_index("uq_sizes_code_class", "sizes", ["size_code", "size_class"], unique=True)
    op.execute(
        "CREATE INDEX idx_sizes_trgm ON sizes USING gin ((size_code || ' ' || name) gin_trgm_ops)"
    )
    op.create_index("idx_sizes_active_sort_order", "sizes", ["is_active", "sort_order"])
    op.create_index("idx_sizes_size_class", "sizes", [sa.text("size_class")])

    op.create_table(
        "size_groups",
        *_base_columns(),
        sa.Column("name", sa.String(64), nullable=False, comment="男装衬衫码表"),
        sa.Column(
            "size_class",
            _SIZE_CLASS_ENUM,
            nullable=False,
            comment="一个码表 = 一个尺码类 + 一套有序尺码",
        ),
        sa.Column(
            "is_builtin",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment="是否 seed 内置（只内置 2 个）",
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
            comment="停用后不参与新建单据，历史照常可查",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_size_groups"),
        _version_check("size_groups"),
        comment="尺码模板 / 码表（04 §7.4）",
    )
    op.create_index("uq_size_groups_name", "size_groups", ["name"], unique=True)
    op.create_index("idx_size_groups_size_class", "size_groups", ["size_class"])

    # 明细表：**无公共字段**，复合主键（04 §7.4 逐字）。它是纯关联表，
    # created_at/updated_at 对它没有意义，加了反而要维护。
    op.create_table(
        "size_group_items",
        sa.Column("size_group_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("size_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint("size_group_id", "size_id", name="pk_size_group_items"),
        # ON DELETE RESTRICT：04 §7.4 明写。引用中的字典项只能停用不能删
        # 删码表时级联删成员：删掉码表却留下孤儿成员行，那才是"残渣"。
        # 而 size_id 上的 RESTRICT 要保留 —— 04 §7.4 明写，禁止删正在用着的尺码
        sa.ForeignKeyConstraint(
            ["size_group_id"],
            ["size_groups.id"],
            name="fk_size_group_items_groups",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["size_id"], ["sizes.id"], name="fk_size_group_items_sizes", ondelete="RESTRICT"
        ),
        comment="码表成员（有序）。纯关联表，豁免 04 §2 公共字段",
    )
    op.create_index(
        "idx_size_group_items_sort_order", "size_group_items", ["size_group_id", "sort_order"]
    )


# ---------------------------------------------------------------- 工序


def _create_operations() -> None:
    op.create_table(
        "operations",
        *_base_columns(),
        sa.Column("operation_no", sa.String(16), nullable=False, comment="01 / 02 …用户自定义"),
        sa.Column("name", sa.String(64), nullable=False, comment="裁 / 打边 / 拼前 / 查勘"),
        # ⚠️ 可空：§7.8.1 同一行注释写「归属车间（可空 = 通用）」，
        #    modules/01 §3.4 也明确「故实体可空」（如 查勘 全厂通用）。
        #    §7.8.1 里的 NOT NULL 是笔误，实现取可空。
        sa.Column(
            "workshop_id",
            PgUUID(as_uuid=True),
            nullable=True,
            comment="归属车间；空 = 通用工序（如 查勘 全厂通用）",
        ),
        sa.Column(
            "is_piecework",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
            comment="是否计件工序（查勘 / 包装可能不计件）",
        ),
        sa.Column(
            "default_bundle_qty",
            sa.Numeric(14, 3),
            server_default=sa.text("1"),
            nullable=False,
            comment="默认一扎几件；**仅默认值**，权威值在 style_operations.bundle_qty（R9）",
        ),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
            comment="停用后不参与新建单据，历史照常可查（R17）",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_operations"),
        _version_check("operations"),
        sa.ForeignKeyConstraint(["workshop_id"], ["workshops.id"], name="fk_operations_workshops"),
        sa.CheckConstraint("default_bundle_qty > 0", name="ck_operations_bundle_qty_positive"),
        comment="工序主数据（04 §7.8.1；用户自定义，不内置枚举，R16）",
    )
    op.create_index("uq_operations_no", "operations", ["operation_no"], unique=True)
    op.create_index(
        "idx_operations_workshop_id_is_active", "operations", ["workshop_id", "is_active"]
    )
    op.execute(
        "CREATE INDEX idx_operations_trgm ON operations "
        "USING gin ((operation_no || ' ' || name) gin_trgm_ops)"
    )


# ------------------------------------------------------------ 商品分类


def _create_product_categories() -> None:
    op.create_table(
        "product_categories",
        *_base_columns(),
        sa.Column(
            "code",
            sa.String(32),
            nullable=False,
            comment="SET / DRESS / TROUSERS / UNDERWEAR / VEST / THERMAL",
        ),
        sa.Column(
            "name",
            sa.String(32),
            nullable=False,
            comment="套装 / 单衣 / 单裤 / 棉毛 / 背心 / 打底裤",
        ),
        # ⚠️ 列名是 sort 而不是 sort_order —— 照 §7.11 字面实现。
        #    与其他表的 sort_order 不一致，已登记 docs/12 待修文档。
        sa.Column(
            "sort", sa.Integer(), server_default=sa.text("0"), nullable=False, comment="界面排序"
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
            comment="停用后不参与新建款号",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_product_categories"),
        _version_check("product_categories"),
        comment="商品分类（04 §7.11，ADR-0020；内置 6 类，被引用不可删只能停用）",
    )
    op.create_index("uq_product_categories_code", "product_categories", ["code"], unique=True)


# -------------------------------------------------- 补 T-AUTH-001 遗留的 FK


def _backfill_auth_foreign_keys() -> None:
    """补 ``users`` / ``role_workshops`` 的车间外键（T-AUTH-001 遗留项）。

    为什么现在才补：T-AUTH-001 建这两张表时 ``workshops`` 还不存在，
    **模型与迁移都刻意没声明 FK**（声明了会让任何涉及 ``users`` 的 ORM 查询
    抛 ``NoReferencedTableError``，登录功能直接不可用）。

    ⚠️ 必须补：``role_workshops.workshop_id`` 是数据范围过滤的输入，
    没有外键就可能存进不存在的车间，``WORKSHOP`` 范围过滤会静默查不到数据。
    """
    op.create_foreign_key("fk_users_workshops", "users", "workshops", ["workshop_id"], ["id"])
    op.create_foreign_key(
        "fk_role_workshops_workshops",
        "role_workshops",
        "workshops",
        ["workshop_id"],
        ["id"],
    )


# ------------------------------------------------------------- 权限口径


def _grant_delete_whitelist() -> None:
    """ADR-0025 §决策 2：只给 4 张字典表 ``GRANT DELETE``。

    两种情况都要显式处理，不能依赖 ``ALTER DEFAULT PRIVILEGES``：

    - 角色**存在**（正常部署）→ GRANT 白名单 + REVOKE 其余，确保「应用账号没有
      任何其他硬删能力」（04 §6.2.1）
    - 角色**不存在**（本地手工起的库跳过了 init 脚本）→ 直接跳过，
      否则迁移会在 ``erp_app`` 上报错而整个卡住
    """
    if not _app_role_exists():
        return

    for table in DELETE_WHITELIST:
        op.execute(f"GRANT DELETE ON {table} TO erp_app")

    for table in (
        "workshops",
        "workshop_groups",
        "warehouses",
        "uom_units",
        "operations",
        "product_categories",
        "document_logs",
    ):
        op.execute(f"REVOKE DELETE ON {table} FROM erp_app")


def _app_role_exists() -> bool:
    """``erp_app`` 角色是否存在。

    用绑定参数判断而不是拼 ``to_regrole('erp_app')`` —— 避免 ruff S608，
    也避免角色名不存在时整条语句报错。
    """
    exists = (
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :name"), {"name": "erp_app"})
        .scalar()
    )
    return bool(exists)
