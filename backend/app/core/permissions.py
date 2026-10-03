"""请求上下文与权限依赖（docs/07-认证与权限规范.md §3.1）。

⚠️ 本文件分两步落地：
    - **T-AUTH-001**（当前）：只落 :class:`AuthContext` 数据结构 —— ``User.to_auth_context()``
      需要它，且 mypy 必须能解析
    - **T-AUTH-002**：补 :func:`get_auth_context` / :func:`require_permission` / bearer 依赖

拆两步的原因：数据容器与鉴权链路是两件事，先把数据结构定死，模型层才能编译通过。
"""

from dataclasses import dataclass, field
from uuid import UUID

from app.common.enums import DataScope


@dataclass(frozen=True, slots=True)
class AuthContext:
    """一次请求的权限上下文。

    :param permissions: 该用户**当前**拥有的权限点集合。

        ⚠️ 每次请求从库里查，**不从 token 里读**（docs/07 §1.1：Token 载荷不放权限
        明细，避免权限变更不生效）。token 只带身份（sub / role_ids / data_scope）。
    :param data_scope: 数据范围。``SELF`` 的具体含义由资源决定：计件/工资是"本人"，
        款号是"我负责的款号"，由 service 层用显式列映射声明，不靠列名推断。
    :param allowed_workshop_ids: 可见车间集合 = 本人所属车间 + 各角色授予的车间。
        仅 ``data_scope=WORKSHOP`` 时有意义；为空且非 FACTORY 时查不到任何数据
        （见 ``app/core/scope.py``，T-AUTH-002 落地）。
    """

    user_id: UUID
    name: str
    employee_no: str
    workshop_id: UUID | None
    group_no: str | None
    permissions: frozenset[str]
    data_scope: DataScope
    allowed_workshop_ids: frozenset[UUID] = field(default_factory=frozenset)

    def has(self, permission: str) -> bool:
        """是否拥有某权限点。``*`` 为通配（系统超管）。"""
        return "*" in self.permissions or permission in self.permissions

    def has_any(self, permissions: frozenset[str] | set[str]) -> bool:
        """是否拥有其中任一权限点（前端 ``v-can="['a','b']"`` 的后端对应）。"""
        if "*" in self.permissions:
            return True
        return bool(self.permissions & permissions)

    @property
    def is_factory_scoped(self) -> bool:
        """是否全厂范围。用于跳过数据范围过滤（少一次查询）。"""
        return self.data_scope == DataScope.FACTORY
