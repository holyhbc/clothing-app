"""认证业务逻辑（docs/07-认证与权限规范.md §1）。

⚠️ **事务边界只在 service 层**（AGENTS.md §2.1）：本模块的写方法自己
``commit()``，router 不得再 commit。

⚠️ 为什么用显式 ``commit()`` 而不是 ``async with session.begin()``：
    SQLAlchemy 的 autobegin 会在**任何读操作后**就打开事务，此时再
    ``session.begin()`` 会抛 "transaction already begun"；而在测试里 session
    绑在外层事务上（``join_transaction_mode="create_savepoint"``），``commit()``
    只是释放 savepoint，外层回滚照样清干净 —— 两种场景语义都正确。

本模块的 6 条安全约定（每条都对应一个可测的行为）：
    1. **不区分"工号不存在"与"口令错误"** —— 两者都返回 11003。若分开报错，
       任何人可以枚举出全厂工号（工号是递增的，猜中很容易）。
    2. 登录**成功与失败都写** ``auth_login_logs``（append-only）：失败日志是
       事后审计爆破的唯一依据。
    3. 连续 5 次失败锁 15 分钟（docs/07 §1.1）：先读 Redis 计数，不可用时降级为
       查 ``auth_login_logs`` 的时间窗口。
    4. refresh token **每次刷新都轮换**，旧的立即吊销；重放旧 token 直接 11002。
    5. refresh token 库里只存 sha256 摘要，明文只出现在 HttpOnly Cookie 里。
    6. 改密后吊销该用户**全部** refresh token（否则别人手上的旧 token 还能用）。
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from app.modules.auth.models import User

logger = logging.getLogger("app.auth")

#: 连续失败多少次后锁定（docs/07 §1.1）
MAX_LOGIN_FAILURES = 5
#: 锁定时长（docs/07 §1.1）
LOCK_MINUTES = 15

#: 失败计数的 Redis key 前缀。**按工号分键**，员工端爆破不影响其他账号
_LOCK_KEY_PREFIX = "auth:login_fail:"


@dataclass(frozen=True, slots=True)
class IssuedTokens:
    """一次签发结果。``refresh_token`` 只用于写 Cookie，不进响应体。"""

    access_token: str
    expires_in: int
    refresh_token: str
    refresh_expires_at: datetime


@dataclass(frozen=True, slots=True)
class LoginOutcome:
    """登录成功后的完整结果。"""

    user: User
    tokens: IssuedTokens
    roles: tuple[str, ...]


def build_lock_key(employee_no: str) -> str:
    """失败计数的 Redis key（导出供测试清理，避免各处硬编码前缀）。"""
    return f"{_LOCK_KEY_PREFIX}{employee_no}"
