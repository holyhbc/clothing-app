"""认证接口的请求/响应模型（docs/05-接口设计规范.md §2、§3）。

⚠️ 三条约定：
    1. **请求模型不接收 ``workshop_id`` / ``role_ids`` / ``permissions``**
       —— 这些一律从 token + 库里取。让客户端能传就等于让它能选（docs/07 §3.2）
    2. 金额与数量在响应里是字符串；本模块无金额，不涉及
    3. 每个模型都带 ``json_schema_extra`` 示例，方便前端对接与 OpenAPI 自检
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.common.enums import AuthChannel, DataScope


class LoginRequest(BaseModel):
    """``POST /auth/login`` 请求体。"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"employee_no": "A001", "password": "Abcd1234", "channel": "PC"},
        }
    )

    employee_no: str = Field(min_length=1, max_length=32, description="工号（跨车间唯一）")
    password: str = Field(min_length=1, max_length=128, description="口令明文，仅用于本次校验")
    channel: AuthChannel = Field(default=AuthChannel.PC, description="认证渠道")
    device_id: str | None = Field(
        default=None, max_length=64, description="设备标识；员工端首次登录后存 localStorage"
    )


class UserBrief(BaseModel):
    """用户概要。**刻意不含** ``password_hash`` 与 ``phone``（docs/05 §3）。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(description="用户 ID")
    employee_no: str = Field(description="工号")
    name: str = Field(description="姓名")
    workshop_id: UUID | None = Field(default=None, description="所属车间；空为总经办/未分配")
    group_no: str | None = Field(default=None, description="所属组别")
    data_scope: DataScope = Field(description="数据范围")
    must_change_password: bool = Field(description="是否需要强制改密")


class RoleBrief(BaseModel):
    """角色概要。"""

    model_config = ConfigDict(from_attributes=True)

    code: str = Field(description="角色 code")
    name: str = Field(description="角色名")
    data_scope: DataScope = Field(description="该角色的数据范围")


class LoginResponse(BaseModel):
    """``POST /auth/login`` 响应体。

    ⚠️ 只返回 access token；refresh token 走 **HttpOnly Cookie**（docs/07 §1.1），
    不放响应体 —— 放响应体的话前端 JS 能读到，XSS 就能偷走长期凭证。
    """

    access_token: str = Field(description="访问令牌，放 Authorization 头")
    token_type: str = Field(default="bearer", description="固定 bearer")
    expires_in: int = Field(description="access token 剩余有效期（秒）")
    refresh_expires_at: datetime = Field(description="refresh token 过期时刻")
    user: UserBrief = Field(description="登录用户概要")


class RefreshResponse(BaseModel):
    """``POST /auth/refresh`` 响应体。刷新成功即轮换 refresh token。"""

    access_token: str = Field(description="新的访问令牌")
    token_type: str = Field(default="bearer", description="固定 bearer")
    expires_in: int = Field(description="access token 剩余有效期（秒）")
    refresh_expires_at: datetime = Field(description="新的 refresh token 过期时刻")


class MeResponse(BaseModel):
    """``GET /auth/me`` 响应体：登录态自描述。

    权限明细**只在这里**返回一次，前端据此控制按钮显隐；但服务端每个接口仍会
    独立校验（docs/07 §1.1 —— 前端隐藏按钮不是安全边界）。
    """

    user: UserBrief = Field(description="用户概要")
    roles: list[RoleBrief] = Field(default_factory=list, description="已授予角色")
    permissions: list[str] = Field(default_factory=list, description="权限点 code 列表")
    data_scope: DataScope = Field(description="数据范围")
    allowed_workshop_ids: list[UUID] = Field(
        default_factory=list, description="WORKSHOP 范围下可见的车间；FACTORY 为空表示全厂"
    )


class ChangePasswordRequest(BaseModel):
    """``PUT /auth/password`` 请求体。"""

    model_config = ConfigDict(
        json_schema_extra={"example": {"old_password": "Abcd1234", "new_password": "Wxyz5678"}}
    )

    old_password: str = Field(min_length=1, max_length=128, description="原口令")
    new_password: str = Field(min_length=1, max_length=128, description="新口令，需满足强度策略")


class LogoutResponse(BaseModel):
    """``POST /auth/logout`` 响应体。"""

    revoked_tokens: int = Field(ge=0, description="本次吊销的 refresh token 数量")


class SmsSendCodeRequest(BaseModel):
    """``POST /auth/sms/send-code`` 请求体（员工端短信登录占位）。

    仅 P2 使用；阶段一不真正发短信（ADR-0004 的测试通道）。
    """

    model_config = ConfigDict(json_schema_extra={"example": {"phone": "13800138000"}})

    phone: str = Field(min_length=11, max_length=20, pattern=r"^1[3-9]\d{9}$", description="手机号")


class SmsLoginRequest(BaseModel):
    """``POST /auth/sms/login`` 请求体（员工端短信登录占位）。"""

    model_config = ConfigDict(
        json_schema_extra={"example": {"phone": "13800138000", "code": "123456", "device_id": "d1"}}
    )

    phone: str = Field(min_length=11, max_length=20, pattern=r"^1[3-9]\d{9}$", description="手机号")
    code: str = Field(min_length=4, max_length=8, description="短信验证码")
    device_id: str | None = Field(default=None, max_length=64, description="设备标识")
