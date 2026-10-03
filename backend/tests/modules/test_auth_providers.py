"""外部认证通道测试（docs/07 §1.2、ADR-0003）。

阶段一短信通道是占位实现，测试要锁住两件事：
    1. **未配置服务商时明确拒绝**，而不是"假装发送成功"
    2. **验证码明文不进日志**（docs/11 §8.1）
"""

import logging

import pytest

from app.common.enums import AuthChannel
from app.core.errors import BusinessError, ErrorCode
from app.modules.auth.providers import AuthProvider, SmsAuthProvider
from app.modules.auth.providers.sms import SmsProviderConfig


class _Enabled(SmsProviderConfig):
    @property
    def enabled(self) -> bool:
        return True


def test_sms_provider_satisfies_protocol() -> None:
    """结构化子类型检查：新增通道必须实现同一组方法（ADR-0003 决策 3）。"""
    assert isinstance(SmsAuthProvider(), AuthProvider)


def test_channel_and_provider_are_declared() -> None:
    provider = SmsAuthProvider()
    assert provider.channel is AuthChannel.H5_SMS


async def test_send_code_raises_when_not_configured() -> None:
    provider = SmsAuthProvider()
    assert provider.enabled is False
    with pytest.raises(BusinessError) as excinfo:
        await provider.send_code("13800138000", "123456")
    assert excinfo.value.code is ErrorCode.ILLEGAL_OPERATION
    # 文案要告诉用户下一步怎么做，而不是只说"未启用"
    assert "PC" in excinfo.value.message


async def test_send_code_does_not_log_the_code(caplog: pytest.LogCaptureFixture) -> None:
    """验证码明文绝不进日志（docs/11 §8.1）。

    用真实可识别的验证码串，扫全部日志输出确认它不出现。
    """
    provider = SmsAuthProvider(_Enabled())
    with caplog.at_level(logging.DEBUG):
        await provider.send_code("13800138000", "SENTINEL-9Z8Y7X")
    assert "SENTINEL-9Z8Y7X" not in caplog.text


async def test_verify_code_is_false_in_phase_one() -> None:
    """阶段一没有真的发过验证码，所以任何验证码都不能通过。"""
    assert await SmsAuthProvider().verify_code("13800138000", "123456") is False


async def test_fetch_identity_is_rejected() -> None:
    provider = SmsAuthProvider(_Enabled())
    with pytest.raises(BusinessError):
        await provider.fetch_identity("whatever")
