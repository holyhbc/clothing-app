"""0013 库存锁定与数量对账视图：`stock_reservations` + `v_stock_reconciliation`
（T-BASE-009 第 1 张卡之二，modules/06 库存）。

权威来源：**``docs/04 §7.2.5``**（逐列 DDL，本迁移与它逐字一致）+
``docs/04 §7.2.4``（数量对账视图）+ ``docs/modules/06 §3.5``（字段表）/ ``§3.7``（视图）。

⚠️ **为什么与迁移 0012（台账 + 明细）分开**：台账与明细之间有 FK + 生命周期依赖，
   合在一个迁移里才有一个可回滚单元；而锁定表**对两者零外键零依赖**，
   数量对账视图虽然读 ``stock_ledgers``，但**只读不写**、没有生命周期耦合。
   拆开让两个文件都落在 ``AGENTS §7.1`` 的**单文件 400 行硬线**之内。

⚠️ **本迁移里的两张东西都不是 append-only**（与 0012 相反，别照抄那边的写法）：

1. ``stock_reservations`` 是**软删表**（``04 §2`` 完整公共字段 + ``version > 0``）。
   锁定的**释放**是「置 ``released_at``」（INV-7「释放用置值不用删行」），
   行本身还要留着回答「这批布曾经被哪张单占过、什么时候放的」；
   而单据被删除时它的锁要软删 —— 所以 ``deleted_at`` / ``version`` 都有用。
   权限上因此**只 REVOKE DELETE、保留 UPDATE**（``released_at`` 要被正常改）。
2. ``v_stock_reconciliation`` 是**视图**：它要被 ``CREATE OR REPLACE`` 反复更新，
   权限与表无关。

⚠️ **视图里两个容易漏的条件**（``modules/06 §3.7`` 末尾「补充要求」②③ 要求、
   而正文 SQL 原本两样都没写）：

- ``l.stock_type = 'MATERIAL'``：两张结存表的 uuid 不同源，漏掉它会在极端情况下
  把成衣台账算到面料结存头上；而且它是 ``idx_stock_ledgers_stock`` 的**最左列**，
  不带它这个索引就用不上。
- ``WHERE s.deleted_at IS NULL``：**漏掉它会让任何一次软删立刻把视图变成非空** ——
  而视图非空的含义是「账实不一致」，那是发布要拦的事，于是软删一批布就再也发不了版。
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811

from alembic import op

revision: str = "0013"
down_revision: str = "0012"
branch_labels = None
depends_on = None

#: ``04 §2`` 的公共字段 + ``id``。**只有** ``stock_reservations`` 用它。
_SOFT_DELETE_COLS: tuple[sa.Column, ...] = (
    sa.Column(
        "id",
        PgUUID(as_uuid=True),
        server_default=sa.text("gen_random_uuid()"),
        nullable=False,
    ),
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

#: PG enum 类型的引用。⚠️ ``create_type=False``：类型由**迁移 0012** 建，
#: 这里只是拿来给列声明用；不加这个 SQLAlchemy 会**自己再发一条 CREATE TYPE**，
#: 撞车报 ``type already exists``（0004 / 0007 / 0008 / 0009 / 0012 各踩过一次）。
_STOCK_TYPE = postgresql.ENUM("MATERIAL", "FINISHED_GOODS", name="stock_type", create_type=False)
_STOCK_DOC_TYPE = postgresql.ENUM(
    "PurchaseOrder",
    "CuttingOrder",
    "MaterialIssue",
    "FinishedGoodsReceipt",
    "SalesDelivery",
    "StockTransfer",
    "Stocktaking",
    name="stock_doc_type",
    create_type=False,
)


def _ver(table: str) -> sa.CheckConstraint:
    """``04 §2`` 的 ``ck_<table>_version_positive``。"""
    return sa.CheckConstraint("version > 0", name=f"ck_{table}_version_positive")


def upgrade() -> None:
    _create_stock_reservations()
    _create_reconciliation_view()
    _revoke_delete()


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_stock_reconciliation")
    op.drop_table("stock_reservations")
    # ⚠️ 三个枚举由迁移 0012 建，**不在这里删** —— 0012 的 downgrade 管它们，
    #    两处都删会出现「upgrade 建一次、downgrade 删两次」的不对称


def _create_stock_reservations() -> None:
    """库存锁定（``04 §7.2.5``）。

    ⚠️ ``stock_id`` / ``stock_type`` **不建外键**（``stock_type`` 多态，
    见 ``modules/06 §3.6``）；``doc_id`` / ``doc_type`` 同样不建 ——
    单据可整体软删，建 FK 会阻止合法操作。
    """
    op.create_table(
        "stock_reservations",
        *_SOFT_DELETE_COLS,
        sa.Column("stock_type", _STOCK_TYPE, nullable=False, comment="哪一栈（多态，不建 FK）"),
        sa.Column("stock_id", PgUUID(as_uuid=True), nullable=False, comment="被占用的结存行 id"),
        sa.Column("doc_type", _STOCK_DOC_TYPE, nullable=False, comment="占用方单据类型"),
        sa.Column("doc_id", PgUUID(as_uuid=True), nullable=False, comment="占用方单据 id"),
        sa.Column("qty", sa.Numeric(14, 3), nullable=False, comment="占用量（米）"),
        sa.Column(
            "released_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="NULL = 仍锁定",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_stock_reservations"),
        _ver("stock_reservations"),
        sa.CheckConstraint("qty > 0", name="ck_stock_reservations_qty"),
        sa.UniqueConstraint(
            "stock_type", "stock_id", "doc_type", "doc_id", name="uq_stock_reservations_doc"
        ),
        comment="库存锁定（释放用 released_at，INV-7；04 §7.2.5）",
    )
    # ⚠️ ``SoftDeleteMixin`` 上声明了 ``index=True``，每张软删表都要有这一条
    op.create_index(
        "ix_stock_reservations_deleted_at", "stock_reservations", ["deleted_at"], unique=False
    )
    op.create_index(
        "idx_stock_reservations_doc",
        "stock_reservations",
        ["doc_type", "doc_id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL AND released_at IS NULL"),
    )


def _create_reconciliation_view() -> None:
    """数量对账视图（``04 §7.2.4``），**必须恒为 0 行**。

    ⚠️ 台账**没有** ``deleted_at``（append-only），所以 JOIN 侧不需要软删过滤；
    结存表 ``material_stocks`` 的软删过滤在 ``WHERE`` 侧带。
    ⚠️ 台账 ``qty`` **有符号**（IN 正 / OUT 负），所以 ``SUM`` 出来的是净变动，
    ``= stock_qty`` 成立 —— 不需要按 ``direction`` 分组求和再相减。
    """
    op.execute(
        """
        CREATE OR REPLACE VIEW v_stock_reconciliation AS
        SELECT s.id, s.stock_qty, COALESCE(SUM(l.qty), 0) AS ledger_qty
        FROM material_stocks s
        LEFT JOIN stock_ledgers l ON l.stock_id = s.id AND l.stock_type = 'MATERIAL'
        WHERE s.deleted_at IS NULL
        GROUP BY s.id, s.stock_qty
        HAVING s.stock_qty <> COALESCE(SUM(l.qty), 0)
        """
    )


def _revoke_delete() -> None:
    """显式 ``REVOKE DELETE``（``04 §6.2.1``）。

    ⚠️ **只收 DELETE、不收 UPDATE**：锁定行的 ``released_at`` 是被正常 UPDATE 的
    （INV-7「释放用置值不用删行」），把 UPDATE 也收掉会让锁永远释放不掉。
    与迁移 0012 的两张 append-only 表口径相反 —— 那两张表的 UPDATE / DELETE
    **都没有合法的业务语义**（纠错只能走红冲），所以两个都收。

    ⚠️ 正常部署下 ``ALTER DEFAULT PRIVILEGES`` 压根不授 DELETE，所以这是**双保险**：
    一旦有人图省事改成 ``GRANT ... ON ALL TABLES``，这里会把它按回去。
    """
    if not _app_role_exists():
        return
    op.execute("REVOKE DELETE ON stock_reservations FROM erp_app")


def _app_role_exists() -> bool:
    """``erp_app`` 是否存在（抄 ``0009`` 的同名函数）。

    用绑定参数判断而不是拼 ``to_regrole('erp_app')`` —— 避免 ruff S608，
    也避免角色名不存在时整条语句报错。
    """
    query = sa.text("SELECT 1 FROM pg_roles WHERE rolname = :name")
    return bool(op.get_bind().execute(query, {"name": "erp_app"}).scalar())
