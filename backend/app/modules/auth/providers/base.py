"""外部认证通道抽象（docs/07 §1.2、ADR-0003）。

员工端短信 / 企业微信 / 微信公众号三条通道都走同一个协议，新增通道只加实现，
不改业务代码（ADR-0003 决策 3）。

为什么用 ``Protocol`` 而不是 ABC：实现方可能是普通类也可能是模块级函数集合，
结构化子类型更贴合，且不强制单继承。
"""

from typing import Protocol, runtime_checkable

from app.common.enums import AuthChannel, ExternalIdentityProvider


@runtime_checkable
class AuthProvider(Protocol):
    """一个外部认证通道。

    ``@runtime_checkable`` 让 ``isinstance()`` 可用，于是测试能断言"新增通道确实
    实现了同一组方法"（ADR-0003 决策 3）。它只校验**方法名存在**，不校验签名 ——
    真正的契约靠类型检查（mypy）与 review 保证。
    """

    #: 通道标识
    channel: AuthChannel
    #: 外部身份提供方
    provider: ExternalIdentityProvider

    async def send_code(self, target: str, code: str) -> None:
        """发送验证码。

        :param target: 手机号或外部 openid
        :param code: 验证码明文（**禁止**写日志）
        :raises NotImplementedError: 通道未启用时
        """
        ...

    async def verify_code(self, target: str, code: str) -> bool:
        """校验验证码。返回 False 表示不匹配（不抛异常，便于统一计数与锁定）。"""
        ...

    async def fetch_identity(self, credential: str) -> tuple[str, dict[str, object]]:
        """用外部凭证换取 (external_id, 原始资料)。

        :param credential: 外部授权码 / code
        :returns: (外部唯一 id, 原始资料快照，落 ``employee_external_identities.raw_profile``)
        """
        ...
