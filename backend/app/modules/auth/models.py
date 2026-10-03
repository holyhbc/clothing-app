"""认证与权限模型（docs/07-认证与权限规范.md §2.1 + 设计稿 §2.1 组 B）。

⚠️ 字段口径：
    - ``users`` / ``roles`` / ``permissions`` 属 **04 §6.1 硬删禁令表**，永不物理删除
    - ``user_roles`` / ``role_permissions`` / ``role_workshops`` 是关联表，
      **不带** 04 §2 公共字段（docs/07 §2.1 明示）
    - ``auth_login_logs`` / ``auth_refresh_tokens`` 是 append-only 表，
      **豁免** 公共字段（同 document_logs，docs/04 §7.9 先例）

⚠️ 与 auth 模块的分工（docs/modules/01 §1「与 auth 的分工」）：
    **业务属性**（姓名、工号、车间、组别、data_scope、在离职）由 base 模块维护；
    **密码、must_change_password、角色授权、登录日志**由 auth 模块写。
    两模块共表不同列，必须同事务约定写入同表不同列，**禁止两处同时 UPDATE 同一行**。

``from __future__ import annotations`` 是必需的：``AuthContext`` 只在 TYPE_CHECKING
下导入（避免循环依赖），若注解在运行期求值就会 NameError。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811 —— 别名照抄 docs/04 §2
from sqlalchemy.orm import Mapped, mapped_column

from app.common.enums import AuthChannel, DataScope, ExternalIdentityProvider
from app.common.models import Base, BaseModel, IdMixin

if TYPE_CHECKING:
    from app.core.permissions import AuthContext


class User(BaseModel):
    """员工 / 用户。

    一个自然人一个账号。车间为空表示非车间人员（财务、仓管）。
    """

    __tablename__ = "users"

    employee_no: Mapped[str] = mapped_column(String(32), nullable=False, comment="工号，跨车间唯一")
    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="姓名")
    phone: Mapped[str | None] = mapped_column(
        String(20), nullable=True, comment="手机号，员工端登录账号"
    )
    password_hash: Mapped[str | None] = mapped_column(
        Text(), nullable=True, comment="argon2id 哈希；员工端短信登录可空"
    )
    # ⚠️ 这里**故意不声明** ForeignKey("workshops.id")，与迁移 0002 保持一致
    #    （workshops 表在 T-BASE-001 才建，本迁移无法加 FK）。
    #    声明了反而会坏：SQLAlchemy 解析任意涉及 users 的查询都要先解析这张
    #    目标表，表不存在直接抛 NoReferencedTableError —— 登录功能会被打死。
    #    且模型有 FK 而迁移没有，autogenerate 会反复产生同一份 diff。
    #    T-BASE-001 必须补：ALTER TABLE users ADD CONSTRAINT fk_users_workshops
    workshop_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        nullable=True,
        comment="所属车间；非车间人员为空",
    )
    group_no: Mapped[str | None] = mapped_column(
        String(32), nullable=True, comment="所属组别，对应 workshop_groups.group_no"
    )
    data_scope: Mapped[DataScope] = mapped_column(
        String(16),
        nullable=False,
        default=DataScope.SELF,
        server_default=DataScope.SELF.value,
        comment="数据范围 SELF/GROUP/WORKSHOP/FACTORY",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true"), comment="在离职标记"
    )
    must_change_password: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment="首次登录强制改密",
    )

    __table_args__ = (
        # 04 §2 要求 ck_<table>_version_positive；Mixin 不拼表名（会生成错误约束名），
        # 故由各模型声明。**必须与迁移 0002 一致**，否则 autogenerate 反复产生 diff。
        CheckConstraint("version > 0", name="ck_users_version_positive"),
        Index("uq_users_employee_no", "employee_no", unique=True),
        Index("uq_users_phone", "phone", unique=True),
        Index("idx_users_workshop_id_is_active", "workshop_id", "is_active"),
        {"comment": "员工 / 用户（docs/04 §6.1 硬删禁令表）"},
    )

    def to_auth_context(
        self, permissions: frozenset[str], allowed_workshop_ids: frozenset[UUID]
    ) -> AuthContext:
        """转成请求上下文。

        权限集合**由调用方查库传入**而不是从 token 里读 —— docs/07 §1.1 明确
        「Token 载荷不放权限明细，权限查库，避免权限变更不生效」。
        """
        from app.core.permissions import AuthContext

        return AuthContext(
            user_id=self.id,
            name=self.name,
            employee_no=self.employee_no,
            workshop_id=self.workshop_id,
            group_no=self.group_no,
            permissions=permissions,
            data_scope=DataScope(self.data_scope),
            allowed_workshop_ids=allowed_workshop_ids,
        )


class Role(BaseModel):
    """角色。``is_system=True`` 的内置角色**禁止删除**（docs/07 §2.3）。"""

    __tablename__ = "roles"

    code: Mapped[str] = mapped_column(String(32), nullable=False, comment="workshop_supervisor 等")
    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="角色名")
    data_scope: Mapped[DataScope] = mapped_column(
        String(16), nullable=False, comment="该角色的默认数据范围"
    )
    is_system: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment="系统内置角色，不可删除",
    )
    description: Mapped[str | None] = mapped_column(Text(), nullable=True, comment="职责说明")

    __table_args__ = (
        CheckConstraint("version > 0", name="ck_roles_version_positive"),
        Index("uq_roles_code", "code", unique=True),
        {"comment": "角色（硬删禁令表）"},
    )


class Permission(BaseModel):
    """权限点。取值必须是 docs/07 §2.2 已登记的码，**禁止临时编造**。

    数据由 ``app/common/permissions_registry.py`` 提供（单一来源）。
    """

    __tablename__ = "permissions"

    code: Mapped[str] = mapped_column(String(64), nullable=False, comment="piecework:count 等")
    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="中文名，界面展示")
    module: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="所属模块，用于分组展示"
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False, comment="动作词（单级或两级）")
    sort_order: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default=text("0"), comment="界面排序"
    )

    __table_args__ = (
        CheckConstraint("version > 0", name="ck_permissions_version_positive"),
        Index("uq_permissions_code", "code", unique=True),
        Index("idx_permissions_module_sort", "module", "sort_order"),
        {"comment": "权限点，取值必须来自 docs/07 §2.2（硬删禁令表）"},
    )


class UserRole(IdMixin, Base):
    """用户↔角色关联表。**不带**公共字段（docs/07 §2.1）。"""

    __tablename__ = "user_roles"

    user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", name="fk_user_roles_users"), nullable=False
    )
    role_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("roles.id", name="fk_user_roles_roles"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )

    __table_args__ = (
        Index("pk_user_roles", "user_id", "role_id", unique=True),
        Index("idx_user_roles_role_id", "role_id"),
        {"comment": "用户↔角色"},
    )


class RolePermission(IdMixin, Base):
    """角色↔权限关联表。**不带**公共字段。"""

    __tablename__ = "role_permissions"

    role_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("roles.id", name="fk_role_permissions_roles"),
        nullable=False,
    )
    permission_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("permissions.id", name="fk_role_permissions_permissions"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )

    __table_args__ = (
        Index("pk_role_permissions", "role_id", "permission_id", unique=True),
        Index("idx_role_permissions_permission_id", "permission_id"),
        {"comment": "角色↔权限"},
    )


class RoleWorkshop(IdMixin, Base):
    """角色可用车间（超集场景，docs/07 §2.1）。

    一个角色可用于多个车间时，实际可见车间 = 本人所属车间 + 本角色授予的车间（并集）。
    """

    __tablename__ = "role_workshops"

    role_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("roles.id", name="fk_role_workshops_roles"),
        nullable=False,
    )
    # ⚠️ 同 users.workshop_id：不声明 FK（workshops 表 T-BASE-001 才建），
    #    原因与 T-BASE-001 待补项见该处注释
    workshop_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )

    __table_args__ = (
        Index("pk_role_workshops", "role_id", "workshop_id", unique=True),
        Index("idx_role_workshops_workshop_id", "workshop_id"),
        {"comment": "角色可用车间（超集场景）"},
    )


class EmployeeExternalIdentity(IdMixin, Base):
    """外部身份绑定（ADR-0003 预留：企业微信 / 公众号 / 小程序）。

    阶段一不写入任何数据；拿到企业主体认证后新增适配器即可，业务代码零改动。
    """

    __tablename__ = "employee_external_identities"

    user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", name="fk_employee_external_identities_users"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="wecom / wechat_mp / wechat_mini"
    )
    external_id: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="外部唯一标识（unionid/openid/userid）"
    )
    raw_profile: Mapped[dict[str, object] | None] = mapped_column(
        JSONB, nullable=True, comment="原始返回，仅审计用，不参与业务"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )

    __table_args__ = (
        Index(
            "uq_employee_external_identities",
            "provider",
            "external_id",
            unique=True,
        ),
        Index("idx_employee_external_identities_user_id", "user_id"),
        {"comment": "外部身份绑定（ADR-0003 预留，阶段一不写入）"},
    )


class AuthLoginLog(IdMixin, Base):
    """登录日志（append-only，docs/07 §5）。

    ⚠️ 本表 DDL 是本卡新增的：docs/07 §5 只要求"写 auth_login_logs"，未给 DDL。
    已登记待回写 docs/04 §7 与 docs/07 §2.1（设计稿 §10.1 W1）。
    """

    __tablename__ = "auth_login_logs"

    employee_no: Mapped[str | None] = mapped_column(
        String(32), nullable=True, comment="登录时填写的工号（账号不存在时也要留痕）"
    )
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    channel: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="PC / H5_SMS / H5_WECOM"
    )
    ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text(), nullable=True)
    is_success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    fail_reason: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="PASSWORD_INCORRECT / ACCOUNT_DISABLED / LOCKED"
    )
    user_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", name="fk_auth_login_logs_users"),
        nullable=True,
        comment="登录失败时可能定位不到用户",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )

    __table_args__ = (
        Index("idx_auth_login_logs_employee_no", "employee_no", text("created_at DESC")),
        Index("idx_auth_login_logs_created_at", text("created_at DESC")),
        {"comment": "登录日志 append-only（DDL 为本卡新增，待回写 docs/07 §2.1）"},
    )


class AuthRefreshToken(IdMixin, Base):
    """刷新令牌（append-only，docs/07 §1.1「存库可吊销」）。

    ⚠️ 只存 sha256 摘要，**不存明文**；轮换时旧行写 ``revoked_at``。
    DDL 同样是本卡新增，待回写 docs/04 §7 与 docs/07 §2.1（设计稿 §10.1 W1）。
    """

    __tablename__ = "auth_refresh_tokens"

    user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", name="fk_auth_refresh_tokens_users"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="sha256 十六进制；绝不存明文"
    )
    # server_default 与迁移 0002 保持一致：0002 建了默认值而模型只写了 Python 侧
    # ``default``，autogenerate 会把它报成"模型要删掉这个默认值"
    channel: Mapped[str] = mapped_column(
        String(16),
        server_default=text("'PC'"),
        nullable=False,
        default=AuthChannel.PC.value,
        comment="PC / H5_SMS",
    )
    device_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="员工端设备绑定（docs/07 §1.2）"
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="登出或轮换时写入"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )

    __table_args__ = (
        Index("uq_auth_refresh_tokens_hash", "token_hash", unique=True),
        Index(
            "idx_auth_refresh_tokens_user", "user_id", postgresql_where=text("revoked_at IS NULL")
        ),
        {"comment": "刷新令牌 append-only（DDL 为本卡新增，待回写 docs/07 §2.1）"},
    )


# ⚠️ docs/07 §2.1 里还列了 ``piecework_bindings``（员工扫码绑定），但它**不属于 auth 模块**：
#    DDL 权威在 docs/modules/04-计件.md §3.2，由计件模块建。§2.1 只是把"权限模型相关表"
#    集中罗列，便于一次看清，不要据此认为 auth 拥有该表。


__all__ = [
    "AuthChannel",
    "AuthLoginLog",
    "AuthRefreshToken",
    "EmployeeExternalIdentity",
    "ExternalIdentityProvider",
    "Permission",
    "Role",
    "RolePermission",
    "RoleWorkshop",
    "User",
    "UserRole",
]
