"""认证与权限模型结构测试（T-AUTH-001）。

覆盖 docs/07 §2.1 的表结构口径：
    - 带公共字段的表（users / roles / permissions）
    - **不带**公共字段的关联表（user_roles / role_permissions / role_workshops）
    - append-only 表豁免公共字段（auth_login_logs / auth_refresh_tokens）
    - 04 §6.1 硬删禁令表的软删口径
"""

import pytest
from sqlalchemy import CheckConstraint

from app.common.models import BaseModel
from app.modules.auth.models import (
    AuthLoginLog,
    AuthRefreshToken,
    EmployeeExternalIdentity,
    Permission,
    Role,
    RolePermission,
    RoleWorkshop,
    User,
    UserRole,
)

COMMON_COLUMNS = {
    "id",
    "created_at",
    "created_by",
    "updated_at",
    "updated_by",
    "deleted_at",
    "version",
    "remark",
}

#: docs/07 §2.1：这三张是「模型表」，要带 04 §2 公共字段
MODELS_WITH_COMMON_FIELDS = [User, Role, Permission]

#: docs/07 §2.1：这三张是「关联表」，**无**公共字段
ASSOCIATION_TABLES = [UserRole, RolePermission, RoleWorkshop]

#: append-only 表：豁免公共字段（先例 docs/04 §7.9 document_logs）
APPEND_ONLY_TABLES = [AuthLoginLog, AuthRefreshToken, EmployeeExternalIdentity]


def _column_names(model: type[BaseModel]) -> set[str]:
    return {col.name for col in model.__table__.columns}


@pytest.mark.parametrize("model", MODELS_WITH_COMMON_FIELDS, ids=lambda m: m.__tablename__)
def test_models_have_common_fields(model: type[BaseModel]) -> None:
    """models 表必须带齐 04 §2 的 8 个公共字段。"""
    assert _column_names(model) >= COMMON_COLUMNS, f"{model.__tablename__} 缺公共字段"


#: 关联表**禁止**出现的公共字段（docs/07 §2.1：关联表无公共字段）
#: created_at 是例外 —— 授权变更需要留时间用于排查，因此保留。
FORBIDDEN_IN_ASSOCIATION = {
    "created_by",
    "updated_at",
    "updated_by",
    "deleted_at",
    "version",
    "remark",
}


@pytest.mark.parametrize("model", ASSOCIATION_TABLES, ids=lambda m: m.__tablename__)
def test_association_tables_have_no_audit_fields(model: type[BaseModel]) -> None:
    """关联表不带 updated_*/version/deleted_at/remark（docs/07 §2.1 明示）。"""
    names = _column_names(model)
    leaked = FORBIDDEN_IN_ASSOCIATION & names
    assert not leaked, f"{model.__tablename__} 不该有 {sorted(leaked)}"
    assert {"id", "created_at"} <= names, "关联表至少要有 id 与创建时间"


@pytest.mark.parametrize("model", APPEND_ONLY_TABLES, ids=lambda m: m.__tablename__)
def test_append_only_tables_exempt_from_common_fields(model: type[BaseModel]) -> None:
    """append-only 表豁免公共字段：永不 UPDATE，故不需要 version / updated_*。

    这是 docs/04 §7.9（document_logs）已确立的先例，本卡把它扩展到三张新表，
    已登记待回写 docs/04 §2（设计稿 §10.1 W2）。
    """
    names = _column_names(model)
    for forbidden in ("updated_at", "updated_by", "version", "deleted_at", "remark"):
        assert forbidden not in names, f"{model.__tablename__} 不该有 {forbidden}"


@pytest.mark.parametrize("model", [User, Role, Permission], ids=lambda m: m.__tablename__)
def test_hard_delete_forbidden_tables_use_soft_delete(model: type[BaseModel]) -> None:
    """04 §6.1 硬删禁令表：必须有 ``deleted_at``（只软删）。"""
    assert "deleted_at" in _column_names(model)


def test_user_soft_delete_index_exists() -> None:
    """软删过滤是高频查询，必须有索引（docs/04 §5.1 全部走部分/普通索引）。"""
    index_names = {idx.name for idx in User.__table__.indexes}
    assert any(name and name.startswith("idx_users") for name in index_names)


def test_user_unique_constraints() -> None:
    """工号与手机号唯一（docs/07 §2.1）。"""
    index_names = {idx.name for idx in User.__table__.indexes}
    assert "uq_users_employee_no" in index_names
    assert "uq_users_phone" in index_names
    for index in User.__table__.indexes:
        if index.name in {"uq_users_employee_no", "uq_users_phone"}:
            assert index.unique, f"{index.name} 必须是唯一索引"


def test_refresh_token_hash_is_unique_and_not_plaintext() -> None:
    """refresh token 摘必须唯一；列名体现"不存明文"。"""
    names = _column_names(AuthRefreshToken)
    assert "token_hash" in names
    assert "token" not in names, "不得有明文 token 列"
    index_names = {idx.name for idx in AuthRefreshToken.__table__.indexes}
    assert "uq_auth_refresh_tokens_hash" in index_names


def test_login_log_keeps_failed_attempts_without_user() -> None:
    """登录失败时可能定位不到用户，``user_id`` 必须可空（否则失败日志写不进去）。"""
    assert AuthLoginLog.__table__.columns["user_id"].nullable is True
    assert AuthLoginLog.__table__.columns["employee_no"].nullable is True


def test_external_identity_is_unique_per_provider() -> None:
    """外部身份按 (provider, external_id) 唯一（ADR-0003）。"""
    index_names = {idx.name for idx in EmployeeExternalIdentity.__table__.indexes}
    assert "uq_employee_external_identities" in index_names


def test_check_constraint_on_version_is_declared() -> None:
    """04 §2 要求 ``ck_<table>_version_positive CHECK (version > 0)``。

    Mixin 里不拼表名（会生成错误的约束名），因此由各模型自行声明 —— 这里验证
    User 上确实声明了，避免"约束名对不上"导致 autogenerate 反复产生 diff。
    """
    constraints = {
        constraint.name: constraint
        for constraint in User.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert "ck_users_version_positive" in constraints, f"实际约束：{sorted(constraints)}"


def test_user_to_auth_context_maps_business_fields() -> None:
    """``to_auth_context`` 只带业务身份，权限由调用方传入（docs/07 §1.1 权限查库）。"""
    import uuid

    from app.common.enums import DataScope

    user = User(
        employee_no="E001",
        name="张三",
        data_scope=DataScope.WORKSHOP,
        workshop_id=uuid.uuid4(),
        group_no="01",
        password_hash="$argon2id$xxx",
    )
    allowed = frozenset({uuid.uuid4()})
    ctx = user.to_auth_context(
        permissions=frozenset({"cutting:approve"}), allowed_workshop_ids=allowed
    )

    assert ctx.employee_no == "E001"
    assert ctx.name == "张三"
    assert ctx.data_scope == DataScope.WORKSHOP
    assert ctx.allowed_workshop_ids == allowed
    assert ctx.has("cutting:approve")
    assert not ctx.has("payroll:approve")
    from dataclasses import fields

    field_names = {field.name for field in fields(ctx)}
    assert "password_hash" not in field_names, "上下文绝不能带口令哈希"
    assert "phone" not in field_names, "上下文不承载手机号（避免误落日志）"


def test_auth_context_wildcard_and_any() -> None:
    """``*`` 通配与"任一权限"判定（T-AUTH-002 的 require_permission 依赖它）。"""
    import uuid

    from app.common.enums import DataScope
    from app.core.permissions import AuthContext

    base = {
        "user_id": uuid.uuid4(),
        "name": "n",
        "employee_no": "E",
        "workshop_id": None,
        "group_no": None,
        "data_scope": DataScope.FACTORY,
    }
    limited = AuthContext(
        permissions=frozenset({"a:read"}), **{**base, "data_scope": DataScope.WORKSHOP}
    )
    root = AuthContext(permissions=frozenset({"*"}), **base)

    assert limited.has("a:read") and not limited.has("b:read")
    assert root.has("anything:goes")
    assert limited.has_any({"a:read", "b:read"})
    assert not limited.has_any({"b:read", "c:read"})
    assert root.has_any({"b:read"})
    assert limited.is_factory_scoped is False, "车间范围不该被当成全厂（否则会跳过数据范围过滤）"
    assert root.is_factory_scoped is True
