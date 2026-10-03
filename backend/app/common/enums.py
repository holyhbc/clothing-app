"""全部枚举集中于此（docs/03-代码规范.md §1.1 第 7 条：禁止散落 Literal 或魔法字符串）。

对应 PG enum，**只能追加值，不能改值**（docs/04-数据库规范.md §3）：
要改含义就新建枚举 + 双写迁移，并写 ADR 说明。

本文件只放 P0 需要的枚举；业务模块专属枚举在对应模块的 models.py 就近声明，
但状态/类型的公共取值一律回写到这里。
"""

from enum import StrEnum


class DocumentStatus(StrEnum):
    """单据状态（docs/08-单据状态机规范.md §1）。

    ⚠️ PG enum 只能追加值，不能改已有值的含义。
    """

    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    PAID = "PAID"


class DataScope(StrEnum):
    """数据范围（docs/07-认证与权限规范.md §2.1）。

    SELF 的具体含义由资源决定：计件/工资是"本人"，款号是"我负责的款号"。
    映射关系由 service 层用 ``ScopeSpec`` 显式声明，不靠列名推断。
    """

    SELF = "SELF"
    GROUP = "GROUP"
    WORKSHOP = "WORKSHOP"
    FACTORY = "FACTORY"


class SizeClass(StrEnum):
    """尺码类（docs/04-数据库规范.md §7.4 `CREATE TYPE size_class`）。"""

    MENS = "MENS"
    WOMENS = "WOMENS"
    KIDS = "KIDS"


class AuthChannel(StrEnum):
    """认证渠道（docs/07-认证与权限规范.md §1.1 / §1.2）。"""

    PC = "PC"
    H5_SMS = "H5_SMS"
    H5_WECOM = "H5_WECOM"
    H5_WECHAT_MP = "H5_WECHAT_MP"


class ExternalIdentityProvider(StrEnum):
    """外部身份提供方（ADR-0003）。新增微信/企业微信时只加枚举值，不改业务代码。"""

    WECOM = "wecom"
    WECHAT_MP = "wechat_mp"
    WECHAT_MINI = "wechat_mini"


class DocumentAction(StrEnum):
    """写 ``document_logs.action`` 的取值（docs/08-单据状态机规范.md §1.1）。

    基础资料的维护动作（import / export / disable / delete）与日志动作
    （CREATE / UPDATE / DELETE / RESTORE / IMPORT）都在此集中，避免散落字符串。
    """

    CREATE = "CREATE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    RESTORE = "RESTORE"
    IMPORT = "IMPORT"
    EXPORT = "EXPORT"
    SUBMIT = "SUBMIT"
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    WITHDRAW = "WITHDRAW"
    REVERSE = "REVERSE"
    CANCEL = "CANCEL"
