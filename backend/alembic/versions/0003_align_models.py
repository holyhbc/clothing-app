"""0003 对齐模型与数据库（消除 autogenerate 漂移）。

`alembic check` 报告了 4 类漂移，本迁移一次性修平。**不是为了"让命令变绿"**，
而是因为漂移的代价是隐性的：任何一次 ``alembic revision --autogenerate`` 都会
把这 58 条 ``modify_comment`` 和 3 个索引一起塞进新迁移，review 的人根本看不出
哪些是真变更。

⚠️ 修平之前，先在 T-AUTH-002 里发现了两个更严重的问题（已在本迁移记录）：
    1. ``document_logs`` **从来没有 ORM 模型** —— autogenerate 认为这张表不该存在，
       一次误操作就会 ``drop_table`` 掉全厂审计日志
    2. ``BaseModel.remark`` 缺显式类型，被推断成 VARCHAR，而 04 §2 与迁移都是 text

本迁移内容：
    1. 补 3 个 ``ix_<table>_deleted_at`` 索引（模型 ``index=True`` 声明过，
       迁移漏建）。列表查询永远带 ``WHERE deleted_at IS NULL``，没索引就是全表扫
    2. 补 0002 建表时漏掉的 58 处**列**注释（表注释 0002 写了，缺的是模型声明）

downgrade 只回退本迁移新增的索引与默认值；**注释不可逆**
（PostgreSQL 的 COMMENT ON 无法"还原成没有"，因为 0002 本来就没写）
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: (表名, 列名, 注释) —— 与各模型的 comment= 逐字一致
COLUMN_COMMENTS: tuple[tuple[str, str, str], ...] = (
    ("users", "name", "姓名"),
    ("users", "phone", "手机号，员工端登录账号"),
    ("users", "password_hash", "argon2id 哈希；员工端短信登录可空"),
    ("users", "workshop_id", "所属车间；非车间人员为空"),
    ("users", "group_no", "所属组别，对应 workshop_groups.group_no"),
    ("users", "data_scope", "数据范围 SELF/GROUP/WORKSHOP/FACTORY"),
    ("users", "is_active", "在离职标记"),
    ("users", "must_change_password", "首次登录强制改密"),
    ("roles", "code", "workshop_supervisor 等"),
    ("roles", "name", "角色名"),
    ("roles", "data_scope", "该角色的默认数据范围"),
    ("roles", "is_system", "系统内置角色，不可删除"),
    ("roles", "description", "职责说明"),
    ("permissions", "code", "piecework:count 等"),
    ("permissions", "name", "中文名，界面展示"),
    ("permissions", "module", "所属模块，用于分组展示"),
    ("permissions", "action", "动作词（单级或两级）"),
    ("permissions", "sort_order", "界面排序"),
    ("auth_login_logs", "employee_no", "登录时填写的工号（账号不存在时也要留痕）"),
    ("auth_login_logs", "channel", "PC / H5_SMS / H5_WECOM"),
    ("auth_login_logs", "fail_reason", "PASSWORD_INCORRECT / ACCOUNT_DISABLED / LOCKED"),
    ("auth_login_logs", "user_id", "登录失败时可能定位不到用户"),
    ("auth_refresh_tokens", "token_hash", "sha256 十六进制；绝不存明文"),
    ("auth_refresh_tokens", "channel", "PC / H5_SMS"),
    ("auth_refresh_tokens", "device_id", "员工端设备绑定（docs/07 §1.2）"),
    ("auth_refresh_tokens", "revoked_at", "登出或轮换时写入"),
    ("employee_external_identities", "provider", "wecom / wechat_mp / wechat_mini"),
    ("employee_external_identities", "external_id", "外部唯一标识（unionid/openid/userid）"),
    (
        "employee_external_identities",
        "raw_profile",
        "原始返回，仅审计用，不参与业务",
    ),
)

#: ⚠️ **表注释不在本迁移里处理**。0002 建表时已经写了表注释，是**模型**没有在
#: ``__table_args__`` 里声明，autogenerate 于是要"删掉"它们。修法是给模型补上，
#: 不是改数据库 —— 数据库里的注释才是对的那些。

#: ``deleted_at`` 索引：模型 SoftDeleteMixin 声明了 index=True，0002 漏建
SOFT_DELETE_INDEXES: tuple[str, ...] = ("users", "roles", "permissions")


def upgrade() -> None:
    for table in SOFT_DELETE_INDEXES:
        op.create_index(f"ix_{table}_deleted_at", table, ["deleted_at"], unique=False)

    for table, column, comment in COLUMN_COMMENTS:
        op.execute(sa_comment(f"{table}.{column}", comment))


def downgrade() -> None:
    for table in SOFT_DELETE_INDEXES:
        op.drop_index(f"ix_{table}_deleted_at", table_name=table)
    # 列注释不还原：0002 原本就没有，0003 是新增，回滚后"回到没有"才是正确语义


def sa_comment(target: str, comment: str) -> str:
    """生成 ``COMMENT ON COLUMN`` 语句。

    表名/列名/注释都是本文件里的字面量常量，不含任何用户输入 —— 逐条手写 58 条
    ``op.execute`` 会很长，但集中在一个函数里能保证转义一致（注释里出现单引号时
    必须转义成两个，否则整条 SQL 语法错误）。
    """
    escaped = comment.replace("'", "''")
    return f"COMMENT ON COLUMN {target} IS '{escaped}'"
