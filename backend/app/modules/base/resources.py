"""九个基础资料资源的**声明式注册表**（设计稿 §4.4）。

为什么用注册表而不是九个 router 函数：九个资源的 CRUD 形状**完全同构**，
差异只有 5 个点（写权限、路径参数列、真删与否、引用检查、额外字段）。
写成九份重复的 handler 意味着同一条业务规则要改九遍 —— 而"停用必填 reason"
这种规则漏掉一个资源，就是一个可以随便停用主数据的漏洞。

注册表把差异**集中声明**一次，router / service / repository 全部照它生成，
新增资源（T-BASE-002 的 customers / styles 等）只需再加一条声明。

⚠️ 本模块只做**声明**，不含任何业务判断 —— 判断全在 :mod:`service`。
"""

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import ColumnElement, literal, or_, select

from app.common.enums import DataScope
from app.modules.base.models import (
    Color,
    Operation,
    ProductCategory,
    Size,
    SizeGroup,
    SizeGroupItem,
    UomUnit,
    Warehouse,
    Workshop,
    WorkshopGroup,
)


@dataclass(frozen=True, slots=True)
class RefChecker:
    """一条引用关系。用于「删除前是否被引用」的判定。

    :param model: 被引用的表（引用方）
    :param column: 引用方里指向本资源的列
    :param label: 界面上展示的来源名称（写进 ``details.references[]``）
    :param active_only: 只统计"未软删"的引用方 —— 对方软删了就不算还引用着
    """

    model: type[Any]
    column: str
    label: str
    active_only: bool = True


@dataclass(frozen=True, slots=True)
class DictResource:
    """一个基础资料资源。"""

    #: URL 前缀（kebab-case）
    key: str
    #: ORM 模型
    model: type[Any]
    #: ``document_logs.doc_type``（docs/08 §1.1）
    doc_type: str
    #: 路径参数用的**业务编码列**（§4.4：``/colors/{color_code}``，不是 UUID）
    path_column: str
    #: 编码列（唯一键）
    code_column: str
    #: 名称列（候选搜索与展示）
    name_column: str
    #: 候选搜索的模糊匹配表达式（docs/04 §5.1）。**必须与 trgm 索引表达式逐字相同**，
    #: 否则 planner 匹配不上、索引白建
    trgm_expression: str | None = None
    #: ``sort_by`` 白名单：查询参数 → 列名。⚠️ 禁止把用户输入拼进 SQL（04 §5.1）
    sort_whitelist: dict[str, str] = field(default_factory=dict)
    #: 写权限点。``None`` 表示用默认的 ``base:create/update/disable/delete``
    write_permission: str | None = None
    #: 是否允许**物理删除**。False = 纯软删（04 §6.1）
    allow_physical_delete: bool = False
    #: 引用检查器。allow_physical_delete 为 True 时必填
    ref_checkers: tuple[RefChecker, ...] = ()
    #: 额外可过滤列（``is_builtin`` / ``size_class`` / ``workshop_id`` …）
    filter_columns: tuple[str, ...] = ()
    #: 是否带 ``is_builtin`` 列（字典表有，组织表没有）
    has_builtin_flag: bool = False
    #: ``is_active`` 列名（``size_group_items`` 没有这个概念）
    is_active_column: str | None = "is_active"
    #: 数据范围。§4.4 对九个资源一律标 **FACTORY**，所以默认就是它。
    #:
    #: ⚠️ 基础资料是**全厂共享主数据**：颜色、尺码、车间这些不存在"只属于某车间"
    #: 的语义。若跟着用户的数据范围走，一个 ``SELF`` 范围的用户打开颜色下拉会是
    #: **空的** —— 那不是权限问题，是把主数据当成单据数据了。
    #: 单据（裁剪单、计件流水…）才有车间/组别维度，那些资源各自登记自己的 ScopeSpec。
    data_scope: DataScope = DataScope.FACTORY

    def permission(self, action: str) -> str:
        """取某个动作的权限点。"""
        if self.write_permission is not None:
            return self.write_permission
        return f"base:{action}"

    def path_column_value(self, obj: object) -> str:
        """取实体的路径参数值（用于响应与日志）。"""
        return str(getattr(obj, self.path_column))


#: 九个资源。顺序即界面展示顺序。
RESOURCES: tuple[DictResource, ...] = (
    DictResource(
        key="workshops",
        model=Workshop,
        doc_type="Workshop",
        path_column="code",
        code_column="code",
        name_column="name",
        sort_whitelist={"code": "code", "name": "name", "created_at": "created_at"},
        filter_columns=(),
    ),
    DictResource(
        key="workshop-groups",
        model=WorkshopGroup,
        doc_type="WorkshopGroup",
        path_column="group_no",
        code_column="group_no",
        name_column="name",
        sort_whitelist={"group_no": "group_no", "name": "name"},
        filter_columns=("workshop_id",),
    ),
    DictResource(
        key="warehouses",
        model=Warehouse,
        doc_type="Warehouse",
        path_column="code",
        code_column="code",
        name_column="name",
        sort_whitelist={"code": "code", "name": "name"},
        filter_columns=("warehouse_type",),
    ),
    DictResource(
        key="uom-units",
        model=UomUnit,
        doc_type="UomUnit",
        path_column="code",
        code_column="code",
        name_column="name",
        sort_whitelist={"code": "code", "name": "name"},
    ),
    DictResource(
        key="product-categories",
        model=ProductCategory,
        doc_type="ProductCategory",
        path_column="code",
        code_column="code",
        name_column="name",
        sort_whitelist={"code": "code", "name": "name", "sort": "sort"},
        write_permission="base:category:manage",
        # §7.11：分类被 styles 引用后不可删除，只能停用。
        # styles 表 T-BASE-002 才建，所以现在还没有引用方 —— allow_physical_delete
        # 先给 False，等 styles 落地再评估（那时它有子表也删不掉）
        allow_physical_delete=False,
    ),
    DictResource(
        key="colors",
        model=Color,
        doc_type="Color",
        path_column="color_code",
        code_column="color_code",
        name_column="name",
        trgm_expression="(color_code || ' ' || name)",
        sort_whitelist={
            "color_code": "color_code",
            "name": "name",
            "created_at": "created_at",
        },
        allow_physical_delete=True,
        has_builtin_flag=True,
        # 引用判定（04 §7.4 规则表第三行）。styles / style_colors 等表 T-BASE-002
        # 与后续模块才建，届时在这里追加 RefChecker —— 追加后无需改 service。
        ref_checkers=(),
        filter_columns=("color_family",),
    ),
    DictResource(
        key="sizes",
        model=Size,
        doc_type="Size",
        path_column="size_code",
        code_column="size_code",
        name_column="name",
        trgm_expression="(size_code || ' ' || name)",
        sort_whitelist={
            "size_code": "size_code",
            "name": "name",
            "sort_order": "sort_order",
        },
        allow_physical_delete=True,
        has_builtin_flag=True,
        # ⚠️ 引用方是**关联表** ``size_group_items``，不是 ``size_groups`` ——
        #    ``size_groups`` 上根本没有 size_id 列（只有 name / size_class），
        #    指错会在运行时抛 AttributeError，而注册表是数据驱动的，容易漏测
        ref_checkers=(RefChecker(SizeGroupItem, "size_id", "尺码模板"),),
        filter_columns=("size_class",),
    ),
    DictResource(
        key="size-groups",
        model=SizeGroup,
        doc_type="SizeGroup",
        path_column="name",
        code_column="name",
        name_column="name",
        sort_whitelist={"name": "name", "size_class": "size_class"},
        allow_physical_delete=True,
        has_builtin_flag=True,
        ref_checkers=(),
    ),
    DictResource(
        key="operations",
        model=Operation,
        doc_type="Operation",
        path_column="operation_no",
        code_column="operation_no",
        name_column="name",
        trgm_expression="(operation_no || ' ' || name)",
        sort_whitelist={
            "operation_no": "operation_no",
            "name": "name",
            "sort_order": "sort_order",
        },
        write_permission="base:operation:manage",
        # R17：工序被 style_operations / piecework_logs / operation_rates 引用后
        # 只能停用。这三张表 P1 才建，届时追加 RefChecker
        allow_physical_delete=False,
        filter_columns=("workshop_id", "is_piecework"),
    ),
)

RESOURCES_BY_KEY: dict[str, DictResource] = {item.key: item for item in RESOURCES}


def get_resource(key: str) -> DictResource:
    """按 URL 前缀取资源。未知 key 直接抛 404 —— 那是路由配错，不是用户输入。"""
    try:
        return RESOURCES_BY_KEY[key]
    except KeyError as exc:
        raise LookupError(f"未注册的基础资料资源：{key}") from exc


def ref_count_expression(resource: DictResource) -> ColumnElement[bool] | None:
    """构造"是否被引用"的 SQL 条件（``EXISTS`` 子查询）。

    用于**删除时的条件 DELETE**：``DELETE ... WHERE NOT EXISTS (引用)``。
    放在一条 SQL 里而不是"先查后删"，是为了消除竞态 ——
    docs/03 §1.5 明确禁止"先查后插"这类两步写法，删除同理：
    先查引用数为 0、再删，中间被别的请求插一行引用，就留下悬空引用。
    """
    if not resource.ref_checkers:
        return None
    # ⚠️ 用 ``select(literal(1))`` 而不是 ``select(func.one())`` ——
    #    PG 没有 ``one()`` 这个函数，SQLAlchemy 也不会把它翻译成别的，
    #    结果是执行时才报 "function one() does not exist"。
    #    这个分支之前一直没被执行到（colors 无引用检查器、sizes 在检查阶段就抛错），
    #    是 CC-3 的"先删后引用"用例第一次真正跑到它。
    conditions = [
        select(literal(1))
        .select_from(checker.model)
        .where(getattr(checker.model, checker.column) == resource.model.id)
        .correlate(resource.model)
        .exists()
        for checker in resource.ref_checkers
    ]
    return conditions[0] if len(conditions) == 1 else or_(*conditions)


__all__ = ["RESOURCES", "RESOURCES_BY_KEY", "DictResource", "RefChecker", "get_resource"]
