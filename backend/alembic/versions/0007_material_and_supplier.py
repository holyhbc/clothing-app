"""0007 物料类目、物料档案与供应商（T-BASE-005）。

权威来源：
    - ``docs/04 §7.15.1``        物料类目
    - ``docs/04 §7.15.2``        物料档案
    - ``docs/04 §7.15.3``        供应商
    - ``docs/09 §2.3``           物料编码规则与类目码取值表
    - **T-BASE-003 任务卡**       五项业务决策 D1~D5（本迁移**唯一**的字段集来源）

⚠️ **本迁移只建 3 张表，不建打菲两表。**
``bundling_orders.source_cutting_order_id`` 外键指向 ``cutting_orders``，
而那张表是裁剪单表（T-BASE-004 已把它的 DDL 写进 ``04 §7.1``）但**还不存在**。
现在建打菲表会报 ``relation "cutting_orders" does not exist`` ——
而报错完全看不出「真实原因是你把依赖顺序排反了」。打菲两表与裁剪单表一起建。

三处「实现按理由更强的一方」的地方（照抄 04 会出问题或做不到）：

1. **``material_type`` 用 PG 枚举而不是 varchar**（04 §7.15.2 已写明理由）：
   ``'FABRIC'`` 的批次要求 ``width_cm`` 非空（modules/06 §3.1），
   这是**代码里的分支判断**，加新类型要改代码，所以必须是枚举而不是字典表。
2. **``materials.code`` 的唯一索引是部分索引**（``WHERE deleted_at IS NULL``），
   与 ``styles.uq_styles_no`` 同口径（04 §5「单据号、款号、工号」那行）。
3. **``material_categories`` 不在 ADR-0025 的物理删除白名单里**：它是个别码表，
   停用足够；白名单每加一张表都要在闸门 4 兑现三处（迁移常量 / 04 §6.2.1 /
   ``test_migrations.py`` 的断言），而类目码没有「必须能删」的业务诉求（04 §7.15.1）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811 —— 别名照抄 docs/04 §2

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
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


#: ⚠️ ``create_type=False`` 是必需的：``postgresql.ENUM`` 默认 ``create_type=True``，
#: 在 create_table 时会**自己再发一条** ``CREATE TYPE``，与下面手写的那条撞车，
#: 报 ``type "material_type" already exists``。类型由我们自己建（这样 downgrade 才管得住），
#: 声明时就关掉自动建。0004 的 ``size_class`` 踩过一次，这里照抄那条注释的教训。
_MATERIAL_TYPE_ENUM = postgresql.ENUM(
    "FABRIC", "TRIMMING", "LABEL", "FINISHED_GOODS", name="material_type", create_type=False
)


def _ver(table: str) -> sa.CheckConstraint:
    return sa.CheckConstraint("version > 0", name=f"ck_{table}_version_positive")


def upgrade() -> None:
    _create_material_categories()
    _create_materials()
    _create_suppliers()
    _create_soft_delete_indexes()


def downgrade() -> None:
    # ⚠️ 先子后父：``materials.category_id`` → ``material_categories``，
    #    ``materials.uom_unit_id`` → ``uom_units``（0004 建的那张，本迁移不动它）
    for table in ("materials", "material_categories", "suppliers"):
        op.drop_table(table)
    # 枚举类型没有引用者后才能删。⚠️ 用 IF EXISTS 而不是无条件 DROP：
    # downgrade 到 0006 再 upgrade 回来时它已存在，无条件 DROP 会让往返失败
    op.execute("DROP TYPE IF EXISTS material_type")


def _create_soft_delete_indexes() -> None:
    """补 ``ix_<table>_deleted_at``（``SoftDeleteMixin`` 上声明了 ``index=True``）。

    列表查询恒带 ``WHERE deleted_at IS NULL``，没索引就是全表扫。
    """
    for table in ("material_categories", "materials", "suppliers"):
        op.create_index(f"ix_{table}_deleted_at", table, ["deleted_at"], unique=False)


# ------------------------------------------------------------------ 物料类目


def _create_material_categories() -> None:
    """物料类目（09 §2.3 物料编码 ``F-{类目码}-{6 位}`` 的第二段）。

    ⚠️ **不授 DELETE**：ADR-0025 的硬删白名单是 4 张字典表（colors / sizes /
    size_groups / size_group_items），类目码表不在其中，按现状只有软删。
    理由见 04 §7.15.1：类目码的数量是十几个级别，停用足够，而白名单每加一张表
    都要在闸门 4 兑现三处。
    """
    op.create_table(
        "material_categories",
        *_cols(),
        sa.Column("code", sa.String(16), nullable=False, comment="类目码，如 CT / ZL"),
        sa.Column("name", sa.String(64), nullable=False, comment="纯棉布 / 拉链"),
        sa.Column(
            "sort", sa.Integer(), server_default=sa.text("0"), nullable=False, comment="排序"
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
            comment="停用后不可新建物料，历史照常",
        ),
        sa.Column(
            "is_builtin",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment="内置类目（seed 写入）；墓碑机制见 ADR-0025 决策 3",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_material_categories"),
        _ver("material_categories"),
        comment="物料类目（09 §2.3 物料编码第二段）",
    )
    op.create_index("uq_material_categories_code", "material_categories", ["code"], unique=True)
    op.create_index("idx_material_categories_is_active", "material_categories", ["is_active"])


# ------------------------------------------------------------------ 物料档案


def _create_materials() -> None:
    """物料档案（``material_stocks.material_id`` 与 ``cutting_order_lines`` 的外键目标）。

    ⚠️ **档案层刻意不放** ``width_cm`` / ``weight_kg`` / 缸色 / ``unit_cost`` ——
    它们全在**批次**上（``material_stocks``，modules/06 §3.1）：同一缸不同批的门幅
    可以不同（缩水 / 织造偏差），放到档案层就丢掉了这个事实，而裁剪的门幅校验
    （02 C24）要用的正是**这一批**的门幅。
    """
    op.execute(
        "CREATE TYPE material_type AS ENUM ('FABRIC', 'TRIMMING', 'LABEL', 'FINISHED_GOODS')"
    )
    op.create_table(
        "materials",
        *_cols(),
        sa.Column(
            "code", sa.String(32), nullable=False, comment="F-CT-000128 / T-ZL-000456（09 §2.3）"
        ),
        sa.Column("name", sa.String(128), nullable=False, comment="32支全棉府绸"),
        sa.Column(
            "material_type",
            _MATERIAL_TYPE_ENUM,
            nullable=False,
            comment="面料 / 辅料 / 唛头 / 成衣；FABRIC 的批次要求 width_cm 非空",
        ),
        sa.Column(
            "category_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("material_categories.id", name="fk_materials_category_id"),
            nullable=False,
            comment="物料类目，编码第二段的来源",
        ),
        sa.Column(
            "uom_unit_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("uom_units.id", name="fk_materials_uom_unit_id"),
            nullable=False,
            comment="主计量单位；库存数量/单价/耗用单位都跟着它走",
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
            comment="停用后不可新建单据，历史照常",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_materials"),
        _ver("materials"),
        comment="物料档案（modules/06 §3；批次属性见 material_stocks）",
    )
    # ⚠️ 部分唯一索引：软删后可以复用同一个 code（与 styles.uq_styles_no 同口径）
    op.create_index(
        "uq_materials_code",
        "materials",
        ["code"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("idx_materials_category_id", "materials", ["category_id"])
    op.create_index("idx_materials_material_type", "materials", ["material_type"])
    op.create_index("idx_materials_uom_unit_id", "materials", ["uom_unit_id"])


# ------------------------------------------------------------------ 供应商


def _create_suppliers() -> None:
    """供应商（与 ``customers`` 字段高度重合但**不合表**，理由见 04 §7.15.3）。

    ⚠️ **账期字段是 ``settlement_period_days`` 而不是 ``payment_period_days`` 或
    ``credit_days``**（Q-F-07 已闭环）：跟随 ``customers`` 表上的既有实现 ——
    改代码的成本高于改文档（AGENTS §2.3「跟随既有模式」）。
    ⚠️ **银行四项可空**（T-BASE-003 D4）：原本文写「全必填」，但辅料商与个体工商户
    常无对公账户，而建档是低频动作 —— 卡住它会让人绕过系统。
    """
    op.create_table(
        "suppliers",
        *_cols(),
        sa.Column("code", sa.String(32), nullable=False, comment="供应商编码，转大写"),
        sa.Column("name", sa.String(128), nullable=False, comment="供应商全称"),
        sa.Column("short_name", sa.String(64), nullable=True, comment="简称，单据抬头用"),
        sa.Column("contact", sa.String(64), nullable=True, comment="联系人"),
        sa.Column("phone", sa.String(32), nullable=True, comment="联系电话"),
        sa.Column("address", sa.String(255), nullable=True, comment="地址"),
        sa.Column("tax_no", sa.String(32), nullable=True, comment="税号"),
        sa.Column(
            "settlement_period_days",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
            comment="账期天数，应付核销用（Q-F-07：与 customers 同名）",
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
            comment="停用后不可新建单据，历史照常",
        ),
        # ---- 往来扩展（modules/08 §3.1）
        sa.Column(
            "settlement_method",
            sa.String(16),
            server_default=sa.text("'NET'"),
            nullable=False,
            comment="PREPAY / COD / NET（modules/08 F-03）",
        ),
        sa.Column(
            "credit_limit",
            sa.Numeric(18, 4),
            server_default=sa.text("0"),
            nullable=False,
            comment="信用额度（docs/04 §4：金额一律 numeric(18,4)）",
        ),
        sa.Column(
            "default_warehouse_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("warehouses.id", name="fk_suppliers_default_warehouse_id"),
            nullable=True,
            comment="采购收货默认仓；可空（未指定则按行选）",
        ),
        # ---- 银行信息**可空**（T-BASE-003 D4）。付款单提交时若为空则提示补录
        sa.Column("bank_name", sa.String(128), nullable=True, comment="开户行名称"),
        sa.Column("bank_account_no", sa.String(64), nullable=True, comment="银行账号"),
        sa.Column("bank_branch", sa.String(128), nullable=True, comment="开户支行"),
        sa.Column("bank_account_name", sa.String(128), nullable=True, comment="账户名"),
        sa.PrimaryKeyConstraint("id", name="pk_suppliers"),
        _ver("suppliers"),
        sa.CheckConstraint("credit_limit >= 0", name="ck_suppliers_credit_limit"),
        comment="供应商（modules/01 §3.5 + modules/08 §3.1；DDL 见 04 §7.15.3）",
    )
    op.create_index("uq_suppliers_code", "suppliers", ["code"], unique=True)
    op.create_index("idx_suppliers_is_active", "suppliers", ["is_active"])
    op.create_index("idx_suppliers_default_warehouse_id", "suppliers", ["default_warehouse_id"])
