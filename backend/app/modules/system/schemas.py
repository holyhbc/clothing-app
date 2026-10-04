"""用户与角色管理接口的请求 / 响应模型（docs/07 §2、§5）。

⚠️ **响应里绝不出现 `password_hash` 与 `phone`**（docs/05 §3）。这是**模型层**的
保证而不是"页面记得别渲染"：TC-W23 这类断言之所以能成立，是因为字段根本不在
schema 里 —— 就算某个页面不小心 `model_dump()` 整个对象也带不出去。
"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.common.enums import DataScope

#: 账号口令。约束与 `core.security.validate_password_strength` 一致（docs/09 §）。
Password = Annotated[str, Field(min_length=8, max_length=128)]

#: 停用 / 改权限的原因（docs/06 §5「危险确认必填原因」→ docs/04 §7.9 落 `document_logs`）。
Reason = Annotated[str, Field(min_length=5, max_length=500)]


class UserOut(BaseModel):
    """用户列表 / 详情行。**刻意不含** ``password_hash`` 与 ``phone``（docs/05 §3）。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    employee_no: str
    name: str
    workshop_id: UUID | None = Field(default=None, description="所属车间；空为总经办")
    group_no: str | None = None
    data_scope: DataScope
    is_active: bool = Field(description="false = 已停用，无法登录（11004）")
    must_change_password: bool
    role_codes: list[str] = Field(default_factory=list, description="已授予的角色 code")
    version: int = Field(description="乐观锁；PATCH 必传（docs/05 §2）")
    created_at: str
    updated_at: str


class UserCreate(BaseModel):
    """新建用户。"""

    model_config = ConfigDict(extra="forbid")

    employee_no: Annotated[str, Field(min_length=1, max_length=32, description="工号，跨车间唯一")]
    name: Annotated[str, Field(min_length=1, max_length=64)]
    password: Password
    data_scope: DataScope = Field(description="数据范围。这是**用户属性**，不从角色合并")
    workshop_id: UUID | None = None
    group_no: Annotated[str | None, Field(default=None, max_length=32)] = None
    role_codes: list[Annotated[str, Field(max_length=32)]] = Field(
        default_factory=list, description="授予的角色 code"
    )
    must_change_password: bool = Field(
        default=True, description="默认 true：新建账号强制首次登录改密"
    )
    remark: Annotated[str | None, Field(default=None, max_length=500)] = None


class UserPatch(BaseModel):
    """局部更新用户。⚠️ 未传的字段不动（docs/05 §2 的 PATCH 语义）。"""

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str | None, Field(default=None, min_length=1, max_length=64)] = None
    data_scope: DataScope | None = None
    workshop_id: UUID | None = None
    group_no: Annotated[str | None, Field(default=None, max_length=32)] = None
    must_change_password: bool | None = None
    remark: Annotated[str | None, Field(default=None, max_length=500)] = None
    version: int = Field(description="乐观锁；不匹配返回 10005（docs/05 §2）")


class UserRoleAssign(BaseModel):
    """整体替换用户的角色。"""

    model_config = ConfigDict(extra="forbid")

    role_codes: list[Annotated[str, Field(max_length=32)]] = Field(
        default_factory=list, description="目标角色 code 集合；整体替换而非增删"
    )
    version: int = Field(description="用户的 version")


class PasswordResetRequest(BaseModel):
    """管理员重置口令。

    ⚠️ **由管理员填写新口令，不是应用生成**（口径来源：``cli/seed_baseline.py`` 的
    ``ERP_INITIAL_ADMIN_PASSWORD`` —— 初始口令只从部署侧提供，应用**绝不生成**弱口令）。
    本项目也没有可用的送达通道（``auth/sms/send-code`` 是 P2 未启用），
    所以"应用生成一个随机口令再想办法告诉用户"这条路根本走不通。
    """

    model_config = ConfigDict(extra="forbid")

    new_password: Password
    reason: Reason = Field(description="重置原因，会记入 document_logs")


class RoleOut(BaseModel):
    """角色行。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: Annotated[str, Field(max_length=32)]
    name: Annotated[str, Field(max_length=64)]
    data_scope: DataScope = Field(description="建号时的默认数据范围")
    is_system: bool = Field(description="内置角色：code 只读且不可停用（docs/07 §2.3）")
    description: str | None = None
    permission_codes: list[str] = Field(default_factory=list)
    user_count: int = Field(default=0, description="已授予该角色的用户数")
    version: int
    created_at: str
    updated_at: str


class RoleCreate(BaseModel):
    """新建角色。"""

    model_config = ConfigDict(extra="forbid")

    code: Annotated[
        str,
        Field(min_length=1, max_length=32, pattern=r"^[a-z][a-z0-9_]*$", description="小写字母开头"),
    ]
    name: Annotated[str, Field(min_length=1, max_length=64)]
    data_scope: DataScope = Field(default=DataScope.SELF, description="建号时的默认数据范围")
    description: Annotated[str | None, Field(default=None, max_length=500)] = None
    permission_codes: list[Annotated[str, Field(max_length=64)]] = Field(default_factory=list)


class RolePatch(BaseModel):
    """局部更新角色。⚠️ ``code`` **不可改** —— 它被 ``user_roles`` 与审计日志引用。"""

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str | None, Field(default=None, min_length=1, max_length=64)] = None
    data_scope: DataScope | None = None
    description: Annotated[str | None, Field(default=None, max_length=500)] = None
    version: int


class RoleDisable(BaseModel):
    """停用角色。原因必填（docs/06 §5）。"""

    model_config = ConfigDict(extra="forbid")

    reason: Reason


class PermissionGroupOut(BaseModel):
    """按模块分组的权限点，供角色表单的勾选树使用（docs/06 §6.3「权限点按模块分组树」）。"""

    module: Annotated[str, Field(description="模块 code，如 `base` / `cutting`")]
    module_name: str = Field(description="模块中文名，如「基础资料」")
    permissions: list["PermissionOut"]


class PermissionOut(BaseModel):
    """单个权限点。code / 名称都来自后端 registry（单一来源）。"""

    code: str
    name: str


class ReplacePermissionsRequest(BaseModel):
    """整体替换角色的权限点集合。"""

    model_config = ConfigDict(extra="forbid")

    permission_codes: list[Annotated[str, Field(max_length=64)]]
    version: int


class UserOptionOut(BaseModel):
    """用户候选（Combo 用，docs/05 §9.5.2）。``label`` 已含工号。"""

    value: UUID
    label: Annotated[str, Field(description="`工号 姓名`")]
    sub: str | None = None
    disabled: bool = False


class RoleOptionOut(BaseModel):
    """角色候选。"""

    value: Annotated[str, Field(description="角色 code")]
    label: str
    sub: str | None = None
    disabled: bool = False


class DisableUserRequest(BaseModel):
    """停用用户。原因必填。"""

    model_config = ConfigDict(extra="forbid")

    reason: Reason


class EnableUserRequest(BaseModel):
    """启用用户（解除停用）。原因同样必填 —— 启用同样是敏感动作。"""

    model_config = ConfigDict(extra="forbid")

    reason: Reason


PermissionGroupOut.model_rebuild()

__all__ = [
    "DisableUserRequest",
    "EnableUserRequest",
    "Literal",
    "PasswordResetRequest",
    "PermissionGroupOut",
    "PermissionOut",
    "Reason",
    "ReplacePermissionsRequest",
    "RoleCreate",
    "RoleDisable",
    "RoleOptionOut",
    "RoleOut",
    "RolePatch",
    "UserCreate",
    "UserOptionOut",
    "UserOut",
    "UserPatch",
    "UserRoleAssign",
]
