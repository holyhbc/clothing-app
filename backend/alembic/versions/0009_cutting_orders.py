"""0009 裁剪单四张表：`cutting_orders` / `cutting_order_lines` /
`cutting_order_line_colors` / `cutting_order_size_lines`（T-CUT-001a）。

权威来源：
    - ``docs/04 §7.7.2``       四张表的逐列 DDL（本迁移与它**逐字一致**）
    - ``docs/04 §7.3``         单据公共列（``cutting_orders`` 适用）
    - ``docs/04 §7.7.3``       耗料记在行 / 出数记在尺码明细的归属
    - ``docs/04 §7.7.4``       三种录入模式（``cutting_entry_mode``）
    - ``docs/modules/02 §3``   字段表（与 04 有 9 列差异，按 §3.0 的裁决以 04 为准，
                               差异登记在 ``docs/12`` §5 L-073）
    - **ADR-0013**             款号 × 颜色 × 尺码比例（``ratio`` 可小数）
    - **ADR-0017**             行 = 布批（缸号 + 匹号），行内多颜色
    - **ADR-0020**             手数是权威输入、件数 = 乘法精确值（**不再 floor**）
    - **ADR-0021**             改单原因（``remark_source``，红冲链）
    - **ADR-0022**             级联选料：供应商 → 面料 → 缸号 / 匹号
    - **ADR-0023**             生产追踪页按款号串链路

⚠️ **本迁移只建表，不做裁剪业务。** 数量口径（头汇总重算）、``floor`` 取整、
   乐观锁 UPDATE、锁批（``material_stocks.locked_qty``）、状态机流转全部在
   **T-CUT-001b**；接口与页面在 **T-CUT-001c**。理由见 ``docs/00 §7`` 的路线图拆分。

顺带建两个 PG 枚举：

1. ``document_status``（``04 §3`` / ``08 §1.1``）—— ⚠️ **在此之前它根本不存在**：
   库里有 ``data_scope`` / ``material_type`` / ``purchase_purpose`` / ``size_class``
   四个类型，却没有 ``document_status``，尽管 §3 从 P0 起就在定义它。
   原因是 P0 没有业务单据，第一个需要它的就是裁剪单。含 ``PAID`` 是因为
   ``08 §1.1`` REV-2026-10 把它放进了**通用**枚举（仅工资单使用），
   工资单那张卡可以直接用，不必再 ``ALTER TYPE ... ADD VALUE``。
2. ``cutting_entry_mode``（``04 §7.7.2`` 末尾原本就给了 ``CREATE TYPE``）

⚠️ **本迁移顺手修掉了 ``04 §7.7.2`` 的三处缺陷**（详见那节的 REV-2026-10 说明）：

1. ``status`` 原写 ``cutting_status`` —— 那个类型**全仓从未定义**（只有基线提交
   ``3cca53b`` 出现过那一次，连 ``CREATE TYPE`` 都没有），照抄会报
   ``type "cutting_status" does not exist``。改为 ``document_status``
   （``08 §1.1`` 的迁移图与 ``modules/02 §3.1`` 写的都是它）。
   **消除笔误，不是改决策。**
2. 补齐 ``04 §7.3`` 的 4 个审核列（``approved_by`` / ``approved_at`` /
   ``rejected_reason`` / ``cancelled_reason``）。不补的话 001b 做状态机时
   必然要回来 ``ALTER TABLE``，比一次建对贵。
3. ``workshop_id`` 补 ``REFERENCES workshops(id)``。本仓所有带车间列的已建表
   都有这根外键。不加**不会报错**，而是能传入一个不存在的车间 id，
   而 ``apply_data_scope`` 拿它 ``IN (...)`` 过滤时只返回空 ——
   症状与「车间主管看不到数据」完全一样。

另有三处「补齐既有规范的要求」，都在 ``04`` 里同步写了理由：

- ``idx_cutting_orders_status_workshop``：待审核列表（主管工作台）。
  ``04 §5`` 要求「高频列表筛选列」建索引，``modules/02 §8`` 点名要这个索引，
  而 §7.7.2 漏了。``status`` 放最前是因为**所有车间**的待审核单都要查，
  命中面比车间内查询大得多。
- ``idx_cutting_size_lines_line``：``line_id`` 是**冗余外键**（打菲 / 计件按布批
  反查），``04 §5`` 要求「所有被用作 JOIN 的外键建索引」，
  而 ``uq_cutting_size_lines`` 只覆盖 ``line_color_id``。
- ``04 §7.7.2`` 末尾那段 ``ALTER TABLE cutting_order_colors RENAME`` 迁移说明是
  **空文**：P0 从没建过业务单据表，那张旧表从来不存在。**不要照它写迁移。**

四处与直觉不同但**理由更强**的地方，实现按理由更强的一方：

1. **四张表都用 :class:`BaseModel` 的字段集**（软删 + 乐观锁 + 审计），
   没有一张是 append-only。裁剪单要反审核、要留痕、要软删历史单据 ——
   拿 append-only 那套（只有 ``id`` + ``created_at`` + ``created_by``）表达不了。
   唯一的 append-only 表是 ``wip_ledger_lines``（迁移 0008），本卡不涉及。
2. **``style_no`` / ``dye_lot_no`` / ``bolt_no`` / ``color_code`` 全部不加外键**：
   ``styles.uq_styles_no`` 与 ``material_stocks.uq_material_stocks_lot`` 都是
   ``WHERE deleted_at IS NULL`` 的**部分索引**，而 PG 的外键只能引用普通唯一约束 /
   主键（T-DOCS-003 实测：``there is no unique constraint matching given keys``）。
   外键在 ``style_id`` 上，业务键冗余 —— 与 0005 / 0008 同一口径。
3. **``ck_cutting_orders_hand`` 是 ``>= 0`` 而不是 ``> 0``**：
   ``hands_total`` 有 ``DEFAULT 0``，而**草稿态的单据还没有尺码明细**，
   ``hands_total`` 必然是 0。写成 ``> 0`` 会让**任何新建的草稿单都插不进去**
   （与 ``bundling_orders.hands_total`` 同一处坑，见 §7.16 的注释）。
   ``> 0`` 的实际校验在 service 的 ``submit``（modules/02 §2 C8），
   **不是**数据库 CHECK。
4. **不给这四张表授 DELETE**：裁剪单是业务数据，AGENTS §2.1 禁止物理删除。
   与 ADR-0025 的字典表白名单无关 —— 那 4 张是字典（删掉即不要了），单据不是。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811 —— 别名照抄 docs/04 §2

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: ``04 §3`` / ``08 §1.1`` 的通用单据状态。⚠️ ``PAID`` 在**这里**而不在裁剪单的值域里 ——
#: ``08 §1.1`` REV-2026-10 明确「加入通用枚举，仅 ``PayrollSheet`` 使用」，
#: 所以枚举是通用的、值的使用是分模块的。
DOCUMENT_STATUS_VALUES: tuple[str, ...] = (
    "DRAFT",
    "SUBMITTED",
    "APPROVED",
    "REJECTED",
    "CANCELLED",
    "PAID",
)

#: ``04 §7.7.2`` 末尾原本就给了这条 ``CREATE TYPE``（三种录入模式，ADR-0014）。
CUTTING_ENTRY_MODE_VALUES: tuple[str, ...] = ("MASTER", "UNIFORM", "MANUAL")

#: 本迁移建的枚举 → 取值。downgrade 按这个字典反查，只删**本迁移建过**的。
_CREATED_ENUMS: dict[str, tuple[str, ...]] = {
    "document_status": DOCUMENT_STATUS_VALUES,
    "cutting_entry_mode": CUTTING_ENTRY_MODE_VALUES,
}

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
#: （报 ``type already exists``）。类型由 :func:`_create_enums` 建 ——
#: 这样 downgrade 才管得住。0004 的 ``size_class`` 与 0007/0008 各踩过一次。
_DOCUMENT_STATUS = postgresql.ENUM(
    *DOCUMENT_STATUS_VALUES, name="document_status", create_type=False
)
_CUTTING_ENTRY_MODE = postgresql.ENUM(
    *CUTTING_ENTRY_MODE_VALUES, name="cutting_entry_mode", create_type=False
)


def _cols() -> list[sa.Column]:
    """``04 §2`` 的公共字段 + ``id``。四张表**全部**是软删表，所以四张都用它。"""
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
    """``04 §2`` 的 ``ck_<table>_version_positive``。"""
    return sa.CheckConstraint("version > 0", name=f"ck_{table}_version_positive")


def upgrade() -> None:
    created = _create_enums()
    _create_cutting_orders()
    _create_cutting_order_lines()
    _create_line_colors()
    _create_size_lines()
    _create_soft_delete_indexes()
    _revoke_delete()
    # 记在上下文里给 downgrade 用 —— 只有**本迁移建的**才该被删掉。
    # 之前建的由别的途径负责，我们不该替它做决定（删了会连带毁掉那张表的数据）
    op.get_context().config.attributes["created_enums"] = created


def downgrade() -> None:
    # ⚠️ 先子后父：size_lines → line_colors → lines → orders
    for table in (
        "cutting_order_size_lines",
        "cutting_order_line_colors",
        "cutting_order_lines",
        "cutting_orders",
    ):
        op.drop_table(table)
    for name in _CREATED_ENUMS:
        if op.get_context().config.attributes.get("created_enums", {}).get(name):
            # ⚠️ 只删本迁移建的。不加 IF EXISTS 也不加反向判断 —— 判据在上面，
            #    这里再判一次会出现「upgrade 没建、downgrade 也不删」的不对称
            op.execute(f"DROP TYPE {name}")


def _create_enums() -> dict[str, bool]:
    """建两个 PG 枚举，返回「哪个是真的建了」。

    ⚠️ **不能用 ``CREATE TYPE IF NOT EXISTS``** —— PostgreSQL **没有**这个语法
    （只有 ``CREATE TABLE IF NOT EXISTS``）。实测报 ``syntax error at or near "NOT"``。
    所以先查 ``pg_type`` 再决定建不建。

    ⚠️ 为什么要判存在性：``0007`` 的测试会在库里临时建枚举，而生产库里这些类型
    可能已经由别的途径建过 —— 无条件 CREATE 会让那些库升不上去
    （与 ``0008`` 的 ``_create_purchase_purpose`` 同一理由）。
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


def _revoke_delete() -> None:
    """显式 ``REVOKE DELETE``（``04 §6.2.1``：应用账号无硬删能力）。

    ⚠️ 正常部署下 ``ALTER DEFAULT PRIVILEGES`` 压根不授 DELETE，所以这是**双保险**：
    一旦有人图省事改成 ``GRANT ... ON ALL TABLES``，这里会把它按回去。
    与 ``0008`` 对 ``material_stocks`` 的处理同一口径（那里靠默认权限，
    这里显式写 —— 因为单据表的硬删诱惑比库存大得多）。
    """
    if not _app_role_exists():
        return
    for table in _CUTTING_TABLES:
        op.execute(f"REVOKE DELETE ON {table} FROM erp_app")


def _app_role_exists() -> bool:
    """``erp_app`` 是否存在。

    用绑定参数判断而不是拼 ``to_regrole('erp_app')`` —— 避免 ruff S608，
    也避免角色名不存在时整条语句报错（抄 ``0004`` 的同名函数）。
    """
    exists = (
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :name"), {"name": "erp_app"})
        .scalar()
    )
    return bool(exists)


def _create_cutting_orders() -> None:
    """裁剪单表头（``04 §7.7.2``）。

    ⚠️ ``color_codes`` 是 ``varchar(255)`` 的**逗号分隔**字符串，不是数组也不是子表：
    它只是列表页 / 选批的展示与筛选辅助值，**权威配色在 ``cutting_order_line_colors``
    逐行逐色**（ADR-0017 一床多色）。用字符串而不是数组是因为 04 §5 红线
    「不使用 PG 特有特性导致未来迁移困难」，而用子表则与行内颜色表职责重复。
    """
    op.create_table(
        "cutting_orders",
        *_cols(),
        sa.Column(
            "doc_no",
            sa.String(32),
            nullable=False,
            comment="CT-YYYYMMDD-6位序号（09 §2.1）",
        ),
        sa.Column(
            "workshop_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("workshops.id", name="fk_cutting_orders_workshop"),
            nullable=False,
            comment="数据范围过滤依据（INV-8）",
        ),
        # ⚠️ 外键指主键、业务键冗余：styles.uq_styles_no 是部分索引，PG 不允许外键引用
        #    （TD5-01 / T-DOCS-003 实测）。与 0005 / 0008 同一口径
        sa.Column(
            "style_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("styles.id", name="fk_cutting_orders_style"),
            nullable=False,
        ),
        sa.Column(
            "style_no",
            sa.String(32),
            nullable=False,
            comment="冗余：历史单据按它引用；⚠️ 不建外键（部分索引不可引用）",
        ),
        sa.Column(
            "color_codes",
            sa.String(255),
            nullable=False,
            comment="多色（本单要裁的颜色码，逗号分隔）；★ 权威配色在行内颜色表（ADR-0017）",
        ),
        sa.Column("doc_date", sa.Date(), nullable=False),
        sa.Column("delivery_date", sa.Date(), nullable=True),
        sa.Column(
            "ply_count",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
            comment="铺布层数",
        ),
        sa.Column(
            "entry_mode_default",
            _CUTTING_ENTRY_MODE,
            server_default=sa.text("'MASTER'"),
            nullable=False,
            comment="本单默认录入模式（逐颜色可覆盖，04 §7.7.4）",
        ),
        # ⚠️ 头汇总一律 service 重算并覆盖入参（modules/02 §2 C6「不信任前端」）；
        #    这里只提供列，不提供任何一致性保证
        sa.Column(
            "fabric_qty",
            sa.Numeric(14, 3),
            server_default=sa.text("0"),
            nullable=False,
            comment="= Σ行 fabric_qty（service 重算）",
        ),
        sa.Column(
            "output_qty",
            sa.Numeric(14, 3),
            server_default=sa.text("0"),
            nullable=False,
            comment="= Σ颜色 Σ尺码 output_qty（service 重算）",
        ),
        sa.Column(
            "cut_waste_qty",
            sa.Numeric(14, 3),
            server_default=sa.text("0"),
            nullable=False,
            comment="裁损合计，**含 balance_qty**；不变式 cut_waste_qty >= balance_qty",
        ),
        sa.Column(
            "balance_qty",
            sa.Numeric(14, 3),
            server_default=sa.text("0"),
            nullable=False,
            comment="尾数（不足件，不入库 / 不出码 / 不计件，09 §4.2）",
        ),
        sa.Column(
            "status",
            _DOCUMENT_STATUS,
            server_default=sa.text("'DRAFT'"),
            nullable=False,
            comment="⚠️ 04 §7.7.2 原写 cutting_status —— 那个类型全仓从未定义，照抄建表会失败",
        ),
        sa.Column(
            "hands_total",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
            comment="总手数 = Σ尺码 hands；草稿态为 0（还没明细），> 0 由 submit 校验",
        ),
        sa.Column(
            "remark_source", sa.Text(), nullable=True, comment="改单原因（红冲链，ADR-0021）"
        ),
        # ⚠️ 04 §7.3 的单据公共列。不在本节 DDL 里重复抄，抄一遍就多一个可能不一致的副本
        #    （§7.16 打菲单同款处理）
        sa.Column("approved_by", PgUUID(as_uuid=True), nullable=True, comment="审核人（08 §1.1）"),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True, comment="审核时间"),
        sa.Column("rejected_reason", sa.Text(), nullable=True, comment="驳回原因（08 §1.1 必填）"),
        sa.Column("cancelled_reason", sa.Text(), nullable=True, comment="作废原因（08 §1.1 必填）"),
        sa.PrimaryKeyConstraint("id", name="pk_cutting_orders"),
        sa.UniqueConstraint("doc_no", name="uq_cutting_orders_doc_no"),
        _ver("cutting_orders"),
        sa.CheckConstraint("ply_count >= 1", name="ck_cutting_orders_ply"),
        # ⚠️⚠️ `>= 0` 而非 `> 0`：fabric_qty / output_qty 有 DEFAULT 0，而草稿态
        #    还没有布批行，两者必然是 0 —— 写成 `> 0` 会让**任何草稿单都插不进去**
        #    （本卡实测撞出，报 `violates check constraint "ck_cutting_orders_qty"`）。
        #    与 hands_total 是同一个病。`> 0` 由 service 在 submit 时校验（C6/C8）
        sa.CheckConstraint(
            "fabric_qty >= 0 AND output_qty >= 0 AND cut_waste_qty >= 0 AND balance_qty >= 0",
            name="ck_cutting_orders_qty",
        ),
        # ⚠️ `>= 0` 而非 `> 0`：hands_total 有 DEFAULT 0，而草稿态必然是 0。
        #    写成 `> 0` 会让**任何新建的草稿单都插不进去**（同 bundling_orders）
        sa.CheckConstraint("hands_total >= 0", name="ck_cutting_orders_hand"),
        comment="裁剪单表头（04 §7.7.2；ADR-0017 三层结构第 1 层）",
    )
    # ⚠️ 三个索引都是**部分索引**（04 §5）：列表查询恒带 deleted_at IS NULL
    op.create_index(
        "idx_cutting_orders_scope",
        "cutting_orders",
        ["workshop_id", sa.text("doc_date DESC"), "style_no"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "idx_cutting_orders_trace",
        "cutting_orders",
        ["style_no", sa.text("doc_date DESC")],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    # 待审核列表（主管工作台）：status 在前，因为所有车间的待审核单都要查
    op.create_index(
        "idx_cutting_orders_status_workshop",
        "cutting_orders",
        ["status", "workshop_id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def _create_cutting_order_lines() -> None:
    """布批行（★ 耗料记在行，布的属性 —— ADR-0017 第 2 层的父）。

    ⚠️ ``stock_id`` **必填**（ADR-0022）：不允许自由输入缸号，
    必须从 ``material_stocks`` 里选一个已存在的布批行。
    ``dye_lot_no`` / ``bolt_no`` 是它的**冗余快照** —— 加它们不是冗余而是必需：
    审核与对账要「按缸号 + 匹号」查（``idx_cutting_order_lines_lot``），
    而那次查询不应该 JOIN 库存表。
    """
    op.create_table(
        "cutting_order_lines",
        *_cols(),
        sa.Column(
            "doc_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey(
                "cutting_orders.id", name="fk_cutting_order_lines_doc", ondelete="RESTRICT"
            ),
            nullable=False,
        ),
        sa.Column(
            "line_no", sa.Integer(), nullable=False, comment="1 起；UNIQUE (doc_id, line_no)"
        ),
        sa.Column(
            "stock_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("material_stocks.id", name="fk_cutting_order_lines_stock"),
            nullable=False,
            comment="★ 锁定的布批行（ADR-0022：必填，不允许自由输入缸号）",
        ),
        sa.Column(
            "supplier_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("suppliers.id", name="fk_cutting_order_lines_supplier"),
            nullable=True,
            comment="快照（只读，便于按供应商查裁剪，ADR-0022）",
        ),
        sa.Column(
            "material_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("materials.id", name="fk_cutting_order_lines_material"),
            nullable=False,
        ),
        sa.Column(
            "style_no",
            sa.String(32),
            nullable=False,
            comment="冗余：便于按款号汇总；⚠️ 不建外键（部分索引不可引用）",
        ),
        sa.Column(
            "dye_lot_no",
            sa.String(64),
            nullable=False,
            comment="缸号（09 §1.2）；行身份的一部分，耗料/扣料/台账/门幅校验都以此为准",
        ),
        sa.Column(
            "bolt_no",
            sa.String(32),
            nullable=False,
            comment="匹号（09 §1.2）；与 dye_lot_no 共同构成批次身份（ADR-0012）",
        ),
        sa.Column("color_plan", sa.String(64), nullable=True, comment="铺布排版说明（可选）"),
        sa.Column(
            "width_cm",
            sa.Numeric(8, 2),
            nullable=True,
            comment="铺布用门幅（默认取批次实测值）；有效门幅校验的输入（02 C24）",
        ),
        sa.Column(
            "fabric_qty",
            sa.Numeric(14, 3),
            nullable=False,
            comment="★ 耗料（米），行级 —— 布的属性；由铺布实耗正向录入，不由出数反推（C35）",
        ),
        sa.Column(
            "fabric_weight_kg",
            sa.Numeric(14, 3),
            nullable=True,
            comment="用掉的重量（kg）；布料主计量是重量（09 §1.2）",
        ),
        sa.Column(
            "waste_qty",
            sa.Numeric(14, 3),
            server_default=sa.text("0"),
            nullable=False,
            comment="布头（可再裁，入库）+ 布损；C33 耗料下限校验里必须加上这一项",
        ),
        sa.Column(
            "output_qty",
            sa.Numeric(14, 3),
            server_default=sa.text("0"),
            nullable=False,
            comment="行出数 = Σ(颜色 Σ(尺码 output_qty))；审核时按此重算",
        ),
        sa.Column(
            "balance_qty",
            sa.Numeric(14, 3),
            server_default=sa.text("0"),
            nullable=False,
            comment="行余量 = output_qty - Σ(颜色尺码 output_qty)；负数 → 30002（C34）",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_cutting_order_lines"),
        sa.UniqueConstraint("doc_id", "line_no", name="uq_cutting_order_lines"),
        _ver("cutting_order_lines"),
        sa.CheckConstraint("fabric_qty > 0", name="ck_cutting_lines_fabric"),
        comment="裁剪单布批行（★ 耗料记在行；04 §7.7.2 / ADR-0017）",
    )
    op.create_index(
        "idx_cutting_order_lines_lot",
        "cutting_order_lines",
        ["dye_lot_no", "bolt_no"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "idx_cutting_order_lines_sup",
        "cutting_order_lines",
        ["supplier_id", "doc_id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def _create_line_colors() -> None:
    """行内颜色（ADR-0017 第 2 层：这一匹布上裁哪些颜色）。

    ⚠️ ``UNIQUE (line_id, color_code)`` 而不是 ``(doc_id, color_code)`` ——
    「这个颜色用哪种录入模式、这套比例」是**这匹布上这个颜色**的属性，
    换一匹布就可能换模式（同一缸布被两个颜色用时，模式与比例各自独立）。
    这正是 ADR-0017 把颜色从单据级下沉到行级的全部理由。

    ⚠️ 三个汇总列都是 ``service`` 算完回写（不信任前端），本迁移不建任何跨表
    一致性约束 —— 那需要 CHECK，而 CHECK 不能跨表（§7.16 同款说明）。
    """
    op.create_table(
        "cutting_order_line_colors",
        *_cols(),
        sa.Column(
            "line_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey(
                "cutting_order_lines.id", name="fk_cutting_line_colors_line", ondelete="RESTRICT"
            ),
            nullable=False,
        ),
        sa.Column(
            "color_code",
            sa.String(32),
            nullable=False,
            comment="必须是该款已定义的颜色，否则 20007",
        ),
        sa.Column(
            "entry_mode",
            _CUTTING_ENTRY_MODE,
            server_default=sa.text("'MASTER'"),
            nullable=False,
            comment="录入模式：A 按比例带出 / B 统一件数 / C 自定义明细（ADR-0014）",
        ),
        sa.Column(
            "qty_per_hand",
            sa.Numeric(14, 4),
            nullable=True,
            comment="模式 A/B 的默认每手件数（尺码明细行的 qty_per_hand 才是权威值）",
        ),
        sa.Column(
            "uniform_qty",
            sa.Numeric(14, 3),
            nullable=True,
            comment="模式 B 的统一件数",
        ),
        sa.Column(
            "ratio_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="下单时的比例建议值快照 {size_code: ratio}；事后解释「为什么这么裁」（C29）",
        ),
        sa.Column(
            "hands_total",
            sa.Numeric(14, 4),
            server_default=sa.text("0"),
            nullable=False,
            comment="= Σ(尺码 hands)，service 重算（可小数，比例之和即手数合计）",
        ),
        sa.Column(
            "output_qty_total",
            sa.Numeric(14, 3),
            server_default=sa.text("0"),
            nullable=False,
            comment="= Σ(尺码 output_qty)，service 重算",
        ),
        sa.Column(
            "balance_qty_total",
            sa.Numeric(14, 3),
            server_default=sa.text("0"),
            nullable=False,
            comment="= Σ(尺码 balance_qty)；仅人工改出数时 > 0",
        ),
        sa.Column(
            "entry_mode_changed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="人工改出数自动转 MANUAL 的时刻（C28）",
        ),
        sa.Column(
            "entry_mode_changed_by",
            PgUUID(as_uuid=True),
            nullable=True,
            comment="模式切换留痕人（配合 C27 与 document_logs）",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_cutting_order_line_colors"),
        sa.UniqueConstraint("line_id", "color_code", name="uq_cutting_line_colors"),
        _ver("cutting_order_line_colors"),
        comment="裁剪单行内颜色（ADR-0017：一床可多个颜色；04 §7.7.2）",
    )


def _create_size_lines() -> None:
    """尺码明细（ADR-0017 第 3 层：出数的**权威**来源）。

    ⚠️ ``hands`` / ``qty_per_hand`` / ``output_qty`` 三个都是 **integer**
    （ADR-0020）：手数是权威输入，件数 = 乘法精确值，**不再 floor、不允许 1.5 手**。
    ⚠️ ``line_id`` 是**冗余外键**（打菲 / 计件按布批反查），所以要有
    ``idx_cutting_size_lines_line`` —— ``uq_cutting_size_lines`` 覆盖不到它。
    ⚠️ ``UNIQUE (line_color_id, size_line_no)``：``size_line_no`` 在**同颜色内**唯一即可，
    **允许** ``(line_color_id, size_code)`` 重复（同尺码多行，各行手数可不同，ADR-0014）。
    """
    op.create_table(
        "cutting_order_size_lines",
        *_cols(),
        sa.Column(
            "line_color_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey(
                "cutting_order_line_colors.id",
                name="fk_cutting_size_lines_color",
                ondelete="RESTRICT",
            ),
            nullable=False,
        ),
        sa.Column(
            "line_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey(
                "cutting_order_lines.id", name="fk_cutting_size_lines_line", ondelete="RESTRICT"
            ),
            nullable=False,
            comment="冗余：打菲 / 计件按布批反查（04 §5 要求这根外键建索引）",
        ),
        sa.Column(
            "size_line_no",
            sa.Integer(),
            nullable=False,
            comment="明细行号；UNIQUE (line_color_id, size_line_no)",
        ),
        sa.Column(
            "size_code",
            sa.String(32),
            nullable=False,
            comment="尺码码；⚠️ 同一 (line_color_id, size_code) **允许**多行（C30）",
        ),
        sa.Column(
            "hands",
            sa.Integer(),
            nullable=False,
            comment="★ 本行裁几手（整数，用户直接输入；ADR-0020）",
        ),
        sa.Column(
            "qty_per_hand",
            sa.Integer(),
            nullable=False,
            comment="★ 本行每手几件（整数）；同尺码多行可不同",
        ),
        sa.Column(
            "output_qty",
            sa.Integer(),
            nullable=False,
            comment="= hands × qty_per_hand（精确整数，**不取整**）",
        ),
        sa.Column(
            "output_qty_manual",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment="人工指定件数 → 该颜色转 MANUAL，差额进 balance_qty（C28）",
        ),
        sa.Column(
            "balance_qty",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
            comment="人工指定出数时的差额（不足件 → 损耗）；不入库 / 不出码 / 不计件",
        ),
        sa.Column(
            "hands_seq",
            sa.Integer(),
            nullable=True,
            comment="手序号（打菲按手拆分用，ADR-0016）；阶段一由 service 审核时回填",
        ),
        # ⚠️ **不声明 remark**：它由 _AUDIT 里的公共字段提供（04 §2）。
        #    04 §7.7.2 原先在本表显式写了一遍 `remark text`，紧接着又写 `...公共字段`
        #    —— 照抄报 DuplicateColumnError。两处是同一列，只留公共字段那份。
        sa.PrimaryKeyConstraint("id", name="pk_cutting_order_size_lines"),
        sa.UniqueConstraint("line_color_id", "size_line_no", name="uq_cutting_size_lines"),
        _ver("cutting_order_size_lines"),
        sa.CheckConstraint("hands > 0 AND qty_per_hand > 0", name="ck_cutting_size_hands"),
        sa.CheckConstraint("balance_qty >= 0", name="ck_cutting_size_balance"),
        comment="裁剪单尺码明细（出数权威来源；04 §7.7.2 / ADR-0017 / ADR-0020）",
    )
    op.create_index(
        "idx_cutting_size_lines_line",
        "cutting_order_size_lines",
        ["line_id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


#: 四张表（按建表顺序）。用于 ``REVOKE DELETE`` 与 downgrade 的顺序检查。
_CUTTING_TABLES: tuple[str, ...] = (
    "cutting_orders",
    "cutting_order_lines",
    "cutting_order_line_colors",
    "cutting_order_size_lines",
)


def _create_soft_delete_indexes() -> None:
    """``SoftDeleteMixin`` 上声明了 ``index=True``，每张软删表都要有。

    列表查询恒带 ``WHERE deleted_at IS NULL``，没索引就是全表扫
    （抄 ``0008`` 的 ``_create_soft_delete_indexes``）。
    """
    for table in _CUTTING_TABLES:
        op.create_index(f"ix_{table}_deleted_at", table, ["deleted_at"], unique=False)
