"""0002 认证与权限表 + 基线数据

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-03

内容：
    1. 组 B 九张表（docs/07-认证与权限规范.md §2.1）
    2. ``permissions`` 全量 seed —— 122 个，来源 ``app/common/permissions_registry.py``
    3. 10 个内置角色 + 角色权限绑定（docs/07 §2.3）

⚠️ 为什么迁移 ``import`` 了 ``permissions_registry``：
    Alembic 惯例是迁移脚本自包含（不 import 应用代码），但那只针对**表结构** ——
    结构会随版本演进，历史迁移必须冻结。而权限点是**静态配置**，与 docs/07 §2.2
    一一对应，若在迁移里再抄一份就成了第二个真相来源（两份必然漂移）。
    因此：表结构写在本文件里（自包含），seed 数据 import registry（单一来源），
    并由 ``tests/modules/test_permission_registry.py`` 保证
    「迁移 seed == registry == docs/07 §2.2」三者一致。

⚠️ ``document_logs`` / ``users`` / ``roles`` / ``permissions`` 属 docs/04 §6.1
硬删禁令表；``auth_login_logs`` / ``auth_refresh_tokens`` 为 append-only，
豁免公共字段。

回滚代价：drop 掉 9 张表会丢失全部账号与授权数据。
生产执行 downgrade 前必须先备份（docs/11 §5 第 3 步）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. users / roles / permissions
    # ------------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.Column("employee_no", sa.String(32), nullable=False, comment="工号，跨车间唯一"),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("phone", sa.String(20), nullable=True, comment="员工端登录账号"),
        sa.Column("password_hash", sa.Text(), nullable=True, comment="argon2id"),
        sa.Column(
            "workshop_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="FK 待 T-BASE-001 补",
        ),
        sa.Column("group_no", sa.String(32), nullable=True),
        sa.Column("data_scope", sa.String(16), server_default=sa.text("'SELF'"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "must_change_password", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.CheckConstraint("version > 0", name="ck_users_version_positive"),
        comment="员工 / 用户（docs/04 §6.1 硬删禁令表）",
    )
    # ⚠️ users.workshop_id 的 FK 依赖 workshops 表（T-BASE-001 才建），故本迁移不加；
    #    T-BASE-001 必须补 ALTER TABLE users ADD CONSTRAINT fk_users_workshops
    op.create_index("uq_users_employee_no", "users", ["employee_no"], unique=True)
    op.create_index("uq_users_phone", "users", ["phone"], unique=True)
    op.create_index("idx_users_workshop_id_is_active", "users", ["workshop_id", "is_active"])

    op.create_table(
        "roles",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("data_scope", sa.String(16), nullable=False),
        sa.Column("is_system", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_roles"),
        sa.CheckConstraint("version > 0", name="ck_roles_version_positive"),
        comment="角色（硬删禁令表）",
    )
    op.create_index("uq_roles_code", "roles", ["code"], unique=True)

    op.create_table(
        "permissions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("module", sa.String(32), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("sort_order", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_permissions"),
        sa.CheckConstraint("version > 0", name="ck_permissions_version_positive"),
        comment="权限点，取值必须来自 docs/07 §2.2（硬删禁令表）",
    )
    op.create_index("uq_permissions_code", "permissions", ["code"], unique=True)
    op.create_index("idx_permissions_module_sort", "permissions", ["module", "sort_order"])

    # ------------------------------------------------------------------
    # 2. 关联表（不带公共字段，docs/07 §2.1）
    # ------------------------------------------------------------------
    op.create_table(
        "user_roles",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user_roles_users"),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], name="fk_user_roles_roles"),
        sa.PrimaryKeyConstraint("id", name="pk_user_roles_id"),
        comment="用户↔角色",
    )
    op.create_index("pk_user_roles", "user_roles", ["user_id", "role_id"], unique=True)
    op.create_index("idx_user_roles_role_id", "user_roles", ["role_id"])

    op.create_table(
        "role_permissions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("permission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], name="fk_role_permissions_roles"),
        sa.ForeignKeyConstraint(
            ["permission_id"], ["permissions.id"], name="fk_role_permissions_permissions"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_role_permissions_id"),
        comment="角色↔权限",
    )
    op.create_index(
        "pk_role_permissions", "role_permissions", ["role_id", "permission_id"], unique=True
    )
    op.create_index("idx_role_permissions_permission_id", "role_permissions", ["permission_id"])

    op.create_table(
        "role_workshops",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workshop_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], name="fk_role_workshops_roles"),
        # workshops 表在 T-BASE-001 才建，故此处先不加该 FK，T-BASE-001 补
        sa.PrimaryKeyConstraint("id", name="pk_role_workshops_id"),
        comment="角色可用车间（超集场景）",
    )
    op.create_index("pk_role_workshops", "role_workshops", ["role_id", "workshop_id"], unique=True)
    op.create_index("idx_role_workshops_workshop_id", "role_workshops", ["workshop_id"])

    # ------------------------------------------------------------------
    # 3. 外部身份（ADR-0003 预留）
    # ------------------------------------------------------------------
    op.create_table(
        "employee_external_identities",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("external_id", sa.String(128), nullable=False),
        sa.Column("raw_profile", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_employee_external_identities_users"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_employee_external_identities"),
        comment="外部身份绑定（ADR-0003 预留，阶段一不写入）",
    )
    op.create_index(
        "uq_employee_external_identities",
        "employee_external_identities",
        ["provider", "external_id"],
        unique=True,
    )
    op.create_index(
        "idx_employee_external_identities_user_id", "employee_external_identities", ["user_id"]
    )

    # ------------------------------------------------------------------
    # 4. append-only 表：登录日志 + 刷新令牌（豁免公共字段）
    # ------------------------------------------------------------------
    op.create_table(
        "auth_login_logs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("employee_no", sa.String(32), nullable=True),
        sa.Column("phone", sa.String(20), nullable=True),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("is_success", sa.Boolean(), nullable=False),
        sa.Column("fail_reason", sa.String(64), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_auth_login_logs_users"),
        sa.PrimaryKeyConstraint("id", name="pk_auth_login_logs"),
        comment="登录日志 append-only（DDL 为本卡新增，待回写 docs/07 §2.1）",
    )
    op.create_index(
        "idx_auth_login_logs_employee_no",
        "auth_login_logs",
        ["employee_no", sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_auth_login_logs_created_at", "auth_login_logs", [sa.text("created_at DESC")]
    )

    op.create_table(
        "auth_refresh_tokens",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, comment="sha256，不存明文"),
        sa.Column("channel", sa.String(16), server_default=sa.text("'PC'"), nullable=False),
        sa.Column("device_id", sa.String(64), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_auth_refresh_tokens_users"),
        sa.PrimaryKeyConstraint("id", name="pk_auth_refresh_tokens"),
        comment="刷新令牌 append-only（DDL 为本卡新增，待回写 docs/07 §2.1）",
    )
    op.create_index(
        "uq_auth_refresh_tokens_hash", "auth_refresh_tokens", ["token_hash"], unique=True
    )
    op.create_index(
        "idx_auth_refresh_tokens_user",
        "auth_refresh_tokens",
        ["user_id"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    # ------------------------------------------------------------------
    # 5. 基线数据：权限点 + 内置角色（幂等）
    # ------------------------------------------------------------------
    _seed_baseline()


def _seed_baseline() -> None:
    """写入权限点与内置角色。

    幂等口径（docs/07 §6）：
        - 权限点：``ON CONFLICT (code) DO NOTHING``，**不覆盖**用户改过的名称
        - 角色：``ON CONFLICT (code) DO NOTHING``，内置角色的 code 不可改
        - 角色权限绑定：``ON CONFLICT DO NOTHING``
    """
    from app.common.permissions_registry import PERMISSIONS, ROLES, resolve_role_permissions

    bind = op.get_bind()

    # 权限点
    for item in PERMISSIONS:
        bind.execute(
            sa.text(
                "INSERT INTO permissions (id, code, name, module, action, sort_order, "
                "created_by, updated_by, version) "
                "VALUES (gen_random_uuid(), :code, :name, :module, :action, :sort_order, "
                "gen_random_uuid(), gen_random_uuid(), 1) "
                "ON CONFLICT (code) DO NOTHING"
            ),
            {
                "code": item.code,
                "name": item.name,
                "module": item.module,
                "action": item.action,
                "sort_order": item.sort_order,
            },
        )

    # 内置角色
    for role in ROLES:
        bind.execute(
            sa.text(
                "INSERT INTO roles (id, code, name, data_scope, is_system, description, "
                "created_by, updated_by, version) "
                "VALUES (gen_random_uuid(), :code, :name, :data_scope, true, :description, "
                "gen_random_uuid(), gen_random_uuid(), 1) "
                "ON CONFLICT (code) DO NOTHING"
            ),
            {
                "code": role.code,
                "name": role.name,
                "data_scope": role.data_scope.value,
                "description": role.description,
            },
        )

    # 角色→权限绑定
    for role in ROLES:
        bind.execute(
            sa.text(
                "INSERT INTO role_permissions (id, role_id, permission_id, created_at) "
                "SELECT gen_random_uuid(), r.id, p.id, now() "
                "FROM roles r, permissions p "
                "WHERE r.code = :role_code AND p.code = ANY(:codes) "
                "ON CONFLICT DO NOTHING"
            ),
            {"role_code": role.code, "codes": list(resolve_role_permissions(role))},
        )


def downgrade() -> None:
    # 破坏性 DDL：会丢失全部账号与授权数据，生产执行前必须已备份（docs/11 §5 第 3 步）
    op.drop_index("idx_auth_refresh_tokens_user", table_name="auth_refresh_tokens")
    op.drop_index("uq_auth_refresh_tokens_hash", table_name="auth_refresh_tokens")
    op.drop_table("auth_refresh_tokens")

    op.drop_index("idx_auth_login_logs_created_at", table_name="auth_login_logs")
    op.drop_index("idx_auth_login_logs_employee_no", table_name="auth_login_logs")
    op.drop_table("auth_login_logs")

    op.drop_index(
        "idx_employee_external_identities_user_id", table_name="employee_external_identities"
    )
    op.drop_index("uq_employee_external_identities", table_name="employee_external_identities")
    op.drop_table("employee_external_identities")

    op.drop_index("idx_role_workshops_workshop_id", table_name="role_workshops")
    op.drop_index("pk_role_workshops", table_name="role_workshops")
    op.drop_table("role_workshops")

    op.drop_index("idx_role_permissions_permission_id", table_name="role_permissions")
    op.drop_index("pk_role_permissions", table_name="role_permissions")
    op.drop_table("role_permissions")

    op.drop_index("idx_user_roles_role_id", table_name="user_roles")
    op.drop_index("pk_user_roles", table_name="user_roles")
    op.drop_table("user_roles")

    op.drop_index("idx_permissions_module_sort", table_name="permissions")
    op.drop_index("uq_permissions_code", table_name="permissions")
    op.drop_table("permissions")

    op.drop_index("uq_roles_code", table_name="roles")
    op.drop_table("roles")

    op.drop_index("idx_users_workshop_id_is_active", table_name="users")
    op.drop_index("uq_users_phone", table_name="users")
    op.drop_index("uq_users_employee_no", table_name="users")
    op.drop_table("users")
