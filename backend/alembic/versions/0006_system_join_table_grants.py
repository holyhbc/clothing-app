"""0006 给两张关联表授予 ``DELETE``（ADR-0029）。

## 为什么需要这次迁移

T-AUTH-003 交付「用户角色分配」与「角色权限勾选」，两者的语义都是**整体替换**
（docs/07 §6.1 第6 条：授权界面天然是"勾选哪些"的全量语义）。整体替换必然要
"删掉不再勾选的那些行"，而 ``user_roles`` / ``role_permissions`` 是**关联表** ——
没有 ``deleted_at``，**软删这条路根本不存在**。

实测报错::

    ProgrammingError: permission denied for table user_roles
    [SQL: DELETE FROM user_roles WHERE user_roles.user_id = $1::UUID]

## 为什么这两张表可以有 DELETE（ADR-0029 §决策）

关联表的一行只表达一条**关系**（用户 X 有角色 Y / 角色 Z 有权限点 W）。
删掉 = 这条关系不再存在，**没有任何信息丢失**，也不参与任何界面回显 ——
与 ADR-0025 处理的字典表（"删掉就是不要了"，软删会带来历史包袱）性质不同。

审计不靠这张表：docs/07 §5 的「权限变更留痕」落在 ``document_logs``，
而它仍然是 ``REVOKE UPDATE, DELETE``（docs/04 §6.2.1 + §7.9）。
**审计不可篡改这条底线没有被削弱。**

## 范围

白名单**按表显式列举**（不是 ``GRANT DELETE ON ALL TABLES``），并对其余业务表
显式 ``REVOKE`` —— 让「应用账号没有任何其他硬删能力」这条断言保持为事实。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"

#: 本次新增到 ``DELETE`` 白名单的表（ADR-0029）。
JOIN_TABLE_DELETE_GRANTS: tuple[str, ...] = ("user_roles", "role_permissions")

#: ADR-0025 已有的四张字典表。一起重新授权，保证白名单只有一处真相。
DICT_TABLE_DELETE_GRANTS: tuple[str, ...] = (
    "colors",
    "sizes",
    "size_groups",
    "size_group_items",
)

#: 显式收回 DELETE 的业务表。列全是为了让"新增表时忘了收紧"不可能发生 ——
#: 没列到的新表本来就没有 DELETE（``ALTER DEFAULT PRIVILEGES`` 只授
#: SELECT/INSERT/UPDATE），这里是**防御性**声明，不是依赖。
BUSINESS_TABLES_WITHOUT_DELETE: tuple[str, ...] = (
    "users",
    "roles",
    "permissions",
    "role_workshops",
    "auth_refresh_tokens",
    "auth_login_logs",
    "document_logs",
    "workshops",
    "workshop_groups",
    "warehouses",
    "uom_units",
    "operations",
    "product_categories",
    "customers",
    "styles",
    "style_colors",
    "style_sizes",
    "style_color_size_ratios",
    "style_operations",
    "operation_rates",
    "style_no_sequences",
)


def upgrade() -> None:
    _apply_delete_grants()


def downgrade() -> None:
    """收回关联表的 ``DELETE``。

    ⚠️ 收回之后「角色换权限 / 用户换角色」这两个功能在生产库会直接 500
    （``permission denied for table user_roles``）。所以 downgrade 不是"回到旧状态"，
    而是真的**功能不可用** —— 这是 ADR-0029 决策的必然结果，回滚前要确认业务已停用该功能。
    """
    if not _app_role_exists():
        return
    for table in JOIN_TABLE_DELETE_GRANTS:
        op.execute(f"REVOKE DELETE ON {table} FROM erp_app")


def _apply_delete_grants() -> None:
    if not _app_role_exists():
        return
    for table in (*DICT_TABLE_DELETE_GRANTS, *JOIN_TABLE_DELETE_GRANTS):
        op.execute(f"GRANT DELETE ON {table} TO erp_app")
    for table in BUSINESS_TABLES_WITHOUT_DELETE:
        op.execute(f"REVOKE DELETE ON {table} FROM erp_app")


def _app_role_exists() -> bool:
    """``erp_app`` 是否存在。

    用绑定参数判断而不是 ``to_regrole('erp_app')``：避免 ruff S608，
    也避免角色名不存在时整条语句报错（本地手工起的库跳过了 init 脚本）。
    """
    exists = (
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :name"), {"name": "erp_app"})
        .scalar()
    )
    return bool(exists)


__all__: Sequence[str] = (
    "BUSINESS_TABLES_WITHOUT_DELETE",
    "DICT_TABLE_DELETE_GRANTS",
    "JOIN_TABLE_DELETE_GRANTS",
    "down_revision",
    "revision",
)
