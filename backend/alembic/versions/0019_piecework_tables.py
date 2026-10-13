"""0019 计件两表 + `piecework_log_type` 枚举（T-PIECE-001）。

建表权威来源：``docs/04 §7.1``（计件流水）、``docs/04 §7.1.1``（员工工序绑定）、
``docs/04 §2``（公共字段）。索引照 ``docs/modules/04-计件 §8``。

只留迁移特有的四件事（其余一律指 04，不复述）：

1. 顺带建 PG 枚举 ``piecework_log_type``（四个值一次建全；后续新增走
   ``ALTER TYPE ... ADD VALUE``，AGENTS §5 禁止改 enum 值本身）。
2. **两处外键、一处刻意不建**（表见 ``docs/04 §7.1`` 的「两处外键的取舍」）：
   ``bundle_id`` 建 FK → ``bundles.id``；``reversal_of_id`` 建自引用 FK；
   ``payroll_id`` **只建列不建 FK** —— ``piecework_settlements`` 是工资阶段的表
   （``05-工资 §3.2``），本迁移执行时它还不存在，建 FK 会直接失败。
3. 公共字段 ``created_by`` / ``updated_by`` **不带外键** —— 与 ``bundling_*`` 四表
   同款：人事离职、车间停用重组都是合法的档案维护动作，建 FK 会阻止它们。
4. 授权边界：两张都是软删表 → ``REVOKE DELETE``（``04 §6.2.1`` 双保险）。

本迁移**零依赖** ``piecework_settlements``，可与工资阶段的建表卡并行落地，
谁先谁取当时的 ``head+1``。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811 —— 照抄 docs/04 §2

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: ``docs/04 §7.1`` 的 ``piecework_log_type``（``04-计件 §2.1`` 语义）。
PIECEWORK_LOG_TYPE_VALUES: tuple[str, ...] = ("SCAN", "MANUAL", "REVERSAL", "SUPPLEMENT")
_CREATED_ENUMS: dict[str, tuple[str, ...]] = {"piecework_log_type": PIECEWORK_LOG_TYPE_VALUES}

#: ⚠️ ``create_type=False`` 必需：否则 create_table 会**自己再发一条** ``CREATE TYPE``，
#: 与本迁移手写的那条撞车（``type already exists``）。抄 0014 同款。
_PIECEWORK_LOG_TYPE = postgresql.ENUM(
    *PIECEWORK_LOG_TYPE_VALUES, name="piecework_log_type", create_type=False
)

#: ``04 §2`` 公共字段。⚠️ 两张表**都**是软删 + 乐观锁（``piecework_bindings``
#: 解绑是置 ``is_active=false`` + ``deleted_at``，不是物理删，INV-7）。
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

#: 先父后子（``reversal_of_id`` 是自引用，同表内）。downgrade 反向。
_PIECEWORK_TABLES: tuple[str, ...] = ("piecework_logs", "piecework_bindings")


def _pk() -> sa.Column:
    return sa.Column(
        "id", PgUUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False
    )


def _audit_cols() -> list[sa.Column]:
    """``04 §2`` 公共字段 + ``id``。"""
    return [_pk(), *_AUDIT]


def _ver(table: str) -> sa.CheckConstraint:
    return sa.CheckConstraint("version > 0", name=f"ck_{table}_version_positive")


def upgrade() -> None:
    _create_enums()
    _create_piecework_logs()
    _create_piecework_bindings()
    _create_indexes()
    _revoke_mutations()


def downgrade() -> None:
    for table in reversed(_PIECEWORK_TABLES):
        op.drop_table(table)
    # ⚠️ 用 ``IF EXISTS`` 无条件删，而不是靠 ``config.attributes`` 里 upgrade 时
    # 记下的「我建过」标记：CLI 的 ``upgrade`` / ``downgrade`` 是两个进程，
    # 那份标记在 downgrade 时是空的（0014 的 TC-B07 实测撞到 —— 枚举没被删）。
    for name in _CREATED_ENUMS:
        op.execute(f"DROP TYPE IF EXISTS {name}")


def _create_enums() -> None:
    """建 ``piecework_log_type``（PG 无 ``CREATE TYPE IF NOT EXISTS``，先查 ``pg_type``）。"""
    for name, values in _CREATED_ENUMS.items():
        exists = (
            op.get_bind()
            .execute(sa.text("SELECT 1 FROM pg_type WHERE typname = :name"), {"name": name})
            .scalar()
        )
        if exists:
            continue
        literals = ", ".join(f"'{value}'" for value in values)  # 常量元组，非外部输入
        op.execute(f"CREATE TYPE {name} AS ENUM ({literals})")


def _create_indexes() -> None:
    """``SoftDeleteMixin`` 的 ``deleted_at`` 声明了 ``index=True``，每张软删表都要有。

    列表查询恒带 ``WHERE deleted_at IS NULL``，没索引就是全表扫（抄 0014 同款）。
    """
    for table in _PIECEWORK_TABLES:
        op.create_index(f"ix_{table}_deleted_at", table, ["deleted_at"], unique=False)


def _revoke_mutations() -> None:
    """软删表 ``REVOKE DELETE``（``04 §6.2.1``；纠错手段只有红冲 + 重入，INV-4）。"""
    if not _app_role_exists():
        return
    for table in _PIECEWORK_TABLES:
        op.execute(f"REVOKE DELETE ON {table} FROM erp_app")


def _app_role_exists() -> bool:
    query = sa.text("SELECT 1 FROM pg_roles WHERE rolname = :name")
    return bool(op.get_bind().execute(query, {"name": "erp_app"}).scalar())


def _create_piecework_logs() -> None:
    """计件流水（``04 §7.1``，逐字照抄）。

    ⚠️ ``qty`` 的 ``DEFAULT 1`` **是兜底不是便利**：服务层漏写
    ``bundles.bundle_qty`` 就会落成 1 件，正好退回「扫码 +1 件」的旧口径。
    迁移照抄、但**服务层必须显式赋值**（``04-计件`` TC-39 钉死）。
    """
    op.create_table(
        "piecework_logs",
        *_audit_cols(),
        sa.Column("bundle_no", sa.String(64), nullable=False),
        sa.Column("style_no", sa.String(32), nullable=False),
        sa.Column("operation_no", sa.String(16), nullable=False),
        sa.Column("employee_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("workshop_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("group_no", sa.String(32), nullable=True),
        sa.Column("work_date", sa.Date(), nullable=False),
        sa.Column(
            "bundle_id",
            PgUUID(as_uuid=True),
            # ⚠️ ON DELETE RESTRICT（BR-PW-30）：码被物理删会让这条流水的追溯断链，
            #   而码的合法终结方式是作废（status='VOIDED'），不是物理删
            sa.ForeignKey("bundles.id", name="fk_piecework_logs_bundle", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("qty", sa.Numeric(14, 3), server_default=sa.text("1"), nullable=False),
        sa.Column("unit_price", sa.Numeric(12, 6), nullable=False),
        sa.Column("amount", sa.Numeric(18, 4), nullable=False),
        sa.Column(
            "log_type", _PIECEWORK_LOG_TYPE, server_default=sa.text("'SCAN'"), nullable=False
        ),
        # ⚠️ **刻意不建 FK**：piecework_settlements 是工资阶段的表（05-工资 §3.2），
        #   本迁移执行时它还不存在。由工资建表卡补 REFERENCES，届时 04 §7.1 同步。
        sa.Column("payroll_id", PgUUID(as_uuid=True), nullable=True),
        sa.Column(
            "reversal_of_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey(
                "piecework_logs.id", name="fk_piecework_logs_reversal_of", ondelete="RESTRICT"
            ),
            nullable=True,
        ),
        sa.Column("source_station", sa.String(32), nullable=True),
        # ⚠️ NOT NULL 无默认值：客户端必须给扫码时刻。work_date 由它推导（BR-PW-11），
        #   给 DEFAULT now() 会让「补录」与「实时扫码」混成同一条口径
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("device_id", sa.String(64), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_piecework_logs"),
        # ⚠️ 幂等键是**数据库兜底**（04 §7.1 要点）；它不含「码是否已被他人计过」，
        #   一码一员工靠 bundles.counted_at + FOR UPDATE 保证（BR-PW-29）
        sa.UniqueConstraint(
            "bundle_no",
            "operation_no",
            "employee_id",
            "work_date",
            "log_type",
            name="uq_piecework_logs_idem",
        ),
        _ver("piecework_logs"),
        # 符号规则：只有红冲行为负。⚠️ 旧的 `qty > 0` CHECK 会拒掉红冲行，不得再用
        sa.CheckConstraint(
            "((log_type = 'REVERSAL' AND qty < 0 AND amount < 0)"
            " OR (log_type <> 'REVERSAL' AND qty > 0 AND amount > 0))",
            name="ck_piecework_logs_sign",
        ),
        # 整件口径：尾数不计件不入库（09 §4.2）。部分生产只允许整数件
        sa.CheckConstraint("qty = trunc(qty)", name="ck_piecework_logs_whole"),
        comment="计件流水（04 §7.1；append-only，纠错靠红冲 + 重入）",
    )
    # §5 的「工厂典型查询」逐字照抄：流水列表恒按 (work_date, workshop_id, style_no)
    op.create_index(
        "idx_piecework_logs_work_date_workshop_id_style_no",
        "piecework_logs",
        # ⚠️ DESC 必须与列名写在**同一个** text 片段里：写成 ["work_date", text("DESC")]
        #   会渲染成 `(work_date, DESC, ...)` —— 语法错误（0014 的 idx_bundling_orders
        #   已踩过同款，那里是 ["workshop_id", text("doc_date DESC")]）
        [sa.text("work_date DESC"), "workshop_id", "style_no"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "idx_piecework_logs_bundle_id",
        "piecework_logs",
        ["bundle_id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "idx_piecework_logs_employee_work_date",
        "piecework_logs",
        ["employee_id", "work_date"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "idx_piecework_logs_bundle_no",
        "piecework_logs",
        ["bundle_no"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "idx_piecework_logs_reversal_of_id",
        "piecework_logs",
        ["reversal_of_id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    # 未结算流水清单（payroll_id IS NULL 是权威判据，04 §7.1 要点；不用派生列）
    op.create_index(
        "idx_piecework_logs_unsettled",
        "piecework_logs",
        ["work_date"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL AND payroll_id IS NULL"),
    )
    op.create_index(
        "idx_piecework_logs_station",
        "piecework_logs",
        ["source_station"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def _create_piecework_bindings() -> None:
    """员工工序绑定（``04 §7.1.1``，搬运自 ``04-计件 §3.2``）。

    ⚠️ ``bundle_no`` **不建 FK**：码会被作废、打菲单会被反审核（``03 §4.1``），
    建 FK 会阻止这些合法动作；绑定只需要「当时扫的是哪张码」这一条追溯信息。
    """
    op.create_table(
        "piecework_bindings",
        *_audit_cols(),
        sa.Column("user_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("style_no", sa.String(32), nullable=False),
        sa.Column("operation_no", sa.String(16), nullable=False),
        sa.Column("bundle_no", sa.String(64), nullable=False),
        sa.Column("workshop_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("work_date", sa.Date(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_piecework_bindings"),
        # ⚠️ 命中它不是报错而是**幂等返回既有绑定**（04 §7.1.1 要点）
        sa.UniqueConstraint(
            "user_id", "style_no", "operation_no", "work_date", name="uq_piecework_bindings"
        ),
        _ver("piecework_bindings"),
        comment="员工工序绑定（04 §7.1.1；绑定当天有效，次日需重扫）",
    )
    op.create_index(
        "idx_piecework_bindings_user_date",
        "piecework_bindings",
        ["user_id", "work_date"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "idx_piecework_bindings_bundle_no",
        "piecework_bindings",
        ["bundle_no"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
