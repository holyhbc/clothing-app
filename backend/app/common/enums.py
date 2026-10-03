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


class RateSource(StrEnum):
    """取价档位（ADR-0026 §2 / ADR-0020）。

    ``resolve`` 与计件流水落库都要记这个值，"事后可追溯是哪一档价算的"。

    ==============  ===========================================  ===========
    值              匹配条件                                     优先级
    ==============  ===========================================  ===========
    ``STYLE``       ``style_no`` 有值 + 分类空                  1（最高）
    ``CATEGORY``    款号空 + ``product_category_id`` 有值       2
    ``OPERATION``   两者都空（全厂同工序统一价）                 3（最低）
    ==============  ===========================================  ===========

    ⚠️ **不是 PG enum**：库里没有这一列，它是由匹配结果**派生**的（响应字段，
    以及 P1 ``piecework_logs.rate_source``）。所以放 Python 侧即可，
    加值不需要迁移（docs/04 §3 只约束**入库列**）。
    """

    STYLE = "STYLE"
    CATEGORY = "CATEGORY"
    OPERATION = "OPERATION"


class TemplateCopyMode(StrEnum):
    """工序与单价模板复制的三个模式（modules/01 §5.1 / ADR-0009 §3）。

    ⚠️ **没有系统默认值**，缺省必须显式传 —— 默认值会让"用户到底选了哪个模式"
    变得不可追溯，而复制错误率必须为 0（ADR-0009 验证方式）。
    """

    COPY_PRICE_AS_IS = "COPY_PRICE_AS_IS"
    COPY_PRICE_WITH_RATIO = "COPY_PRICE_WITH_RATIO"
    COPY_STRUCTURE_ONLY = "COPY_STRUCTURE_ONLY"


class ConflictPolicy(StrEnum):
    """模板复制的冲突策略（modules/01 §5.1）。

    同样**不设系统默认**：默默覆盖别人配的工序与工价比报错更危险。
    """

    SKIP = "SKIP"
    OVERWRITE = "OVERWRITE"
    ABORT = "ABORT"
    MERGE = "MERGE"


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
