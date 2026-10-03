"""外部认证通道实现包（docs/07 §1.2）。

阶段一只落地短信占位通道；企业微信 / 微信公众号在 P2 按 ADR-0003 补齐。
"""

from app.modules.auth.providers.base import AuthProvider
from app.modules.auth.providers.sms import SmsAuthProvider

__all__ = ["AuthProvider", "SmsAuthProvider"]
