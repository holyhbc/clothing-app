"""GRANT SELECT on v_cutting_output_available to the application role.

Why: `v_cutting_output_available` 由 0015 建表时创建，但它**从来没拿到过应用账号
的授权**，于是凡是读它的地方对上真实请求都直接 500
（`permission denied for view v_cutting_output_available`）。

⚠️ 为什么单测与 E2E 之外的路径一直没暴露：

- 单测用 owner 连接（`DATABASE_URL_MIGRATION` = `erp_ddl`），**owner 绕过授权**，
  所以 992 条用例全绿；
- 只在「以 `erp_app` 身份发真实 HTTP 请求」时才会撞上 ——
  也就是 E2E 这条路径。补打菲 E2E 时第一次打真实请求就撞到了。

⚠️ 为什么 `docker/postgres/init/01-create-roles.sh` 的
`ALTER DEFAULT PRIVILEGES ... ON TABLES` 兜不住：

默认权限里 `ON TABLES` **不覆盖视图**。实测（在本迁移加入前于 CI 库验证）：
以 `erp_ddl` 新建一个视图，`information_schema.role_table_grants` 里
**只有 owner 一行**，`erp_app` 什么都没有。表能自动授权、视图不能 ——
这是 `ON TABLES` 与视图的语义差异，不是配置漏了。

所以视图必须在创建时（或此刻）**显式** GRANT。

⚠️ 为什么 GRANT 只给 SELECT、且不给 DELETE：
本视图是 `cutting_outputs` 的只读投影，`available_qty` 是计算列；
余量的真正写入路径是改 `cutting_outputs` 基础表，
对视图授权写权限只会让「改余量」多一条绕过 `reserved_qty` 语义的暗道。

⚠️ 角色不存在时跳过（抄 0009/0012 的同名函数）：本地手工起的库跳过了 init 脚本，
没有 `erp_app`，此时 GRANT 会报错并让整条迁移卡住。

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-12
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0018"
down_revision = "0017"

VIEW_NAME = "v_cutting_output_available"
APP_ROLE = "erp_app"


def upgrade() -> None:
    if not _app_role_exists():
        return
    # ⚠️ 只 SELECT。理由见模块 docstring：视图是只读投影，
    # 授写权限等于给「绕过 reserved_qty 语义改余量」开暗道。
    op.execute(f"GRANT SELECT ON {VIEW_NAME} TO {APP_ROLE}")


def downgrade() -> None:
    if not _app_role_exists():
        return

    # 不接受绑定参数（标识符无法参数化），f-string 是唯一写法。
    # 抄 0004/0006/0012 的同款处理。
    op.execute(f"REVOKE SELECT ON {VIEW_NAME} FROM {APP_ROLE}")  # noqa: S608


def _app_role_exists() -> bool:
    """``erp_app`` 是否存在（抄 ``0012`` 的同名函数）。

    用绑定参数判断而不是拼 ``to_regrole('erp_app')`` —— 避免 ruff S608，
    也避免角色名不存在时整条语句报错。
    """
    query = sa.text("SELECT 1 FROM pg_roles WHERE rolname = :name")
    return bool(op.get_bind().execute(query, {"name": APP_ROLE}).scalar())
