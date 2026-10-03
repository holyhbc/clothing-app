"""短信通道（docs/07 §1.2）。

**阶段一为占位实现**（ADR-0004）：``SMS_PROVIDER`` 未配置时
``send_code`` 直接抛 ``10008`` 并给出配置指引，**绝不**打印验证码到 stdout ——
真接第三方后误打一行日志就是线上安全事故。

验证码存储形态已按生产设计预留：``send_code`` 只负责投递，校验与频控由
service 层用 Redis 完成（docs/07 §1.1 的登录失败锁定同一套机制）。
"""

import logging
from typing import Protocol

from app.common.enums import AuthChannel, ExternalIdentityProvider
from app.core.errors import BusinessError, ErrorCode

logger = logging.getLogger("app.auth.sms")


class SmsProviderConfig(Protocol):
    """短信服务商配置。阶段一没有真实实现，用协议描述契约避免引入 ``Any``。"""

    @property
    def enabled(self) -> bool: ...


class SmsAuthProvider:
    """短信验证码通道占位实现。"""

    channel = AuthChannel.H5_SMS
    provider = ExternalIdentityProvider.WECHAT_MINI

    def __init__(self, config: SmsProviderConfig | None = None) -> None:
        self._config = config

    @property
    def enabled(self) -> bool:
        """是否配置了短信服务商。"""
        return self._config is not None and self._config.enabled

    async def send_code(self, target: str, code: str) -> None:
        """投递验证码。未配置服务商时抛 10008。

        :param target: 手机号
        :param code: 验证码明文。**本函数禁止把它写进任何日志字段**
            （docs/11 §8.1「严禁记录口令/令牌/验证码明文」）
        """
        del target, code
        if not self.enabled:
            logger.info("短信通道未启用，拒绝发送验证码")
            raise BusinessError(
                ErrorCode.ILLEGAL_OPERATION,
                "短信登录通道尚未启用；阶段一请用 PC 端工号口令登录",
            )

    async def verify_code(self, target: str, code: str) -> bool:
        """校验验证码。阶段一恒返回 False（没有任何验证码被发出过）。"""
        del target, code
        return False

    async def fetch_identity(self, credential: str) -> tuple[str, dict[str, object]]:
        """阶段一不支持。"""
        del credential
        raise BusinessError(ErrorCode.ILLEGAL_OPERATION, "短信通道不支持换取外部身份")
