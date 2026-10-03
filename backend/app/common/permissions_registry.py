"""权限点与内置角色的**单一来源**（docs/07-认证与权限规范.md §2.2 / §2.3）。

为什么需要这个文件：权限点要在**三处**保持一致 ——
    1. ``permissions`` 表的 seed 数据（迁移 0002 与 ``seed_baseline``）
    2. 前端常量 ``packages/shared/enums/permissions.ts``
    3. docs/07 §2.2 的表格

docs/07 §2.2 明确「三处不一致视为闸门 1 失败」，因此把 (1) 的内容收敛到这个
模块，让「代码侧的真相」只有一份；测试
``tests/modules/test_permission_registry.py`` 负责把它与 docs/07 §2.2 双向比对。

⚠️ 数据来源与口径：
    - 权限点：**逐条抄自 docs/07 §2.2**，共 122 个（含 4 个 ``self:*``）
    - 两个**已作废**的码不得入库（它们只出现在 docs/07 的"作废说明"里，不是权限点）：
        * ``self:piecework:count`` —— "员工端不开放计件写入"（REV-2026-10）
        * ``bundling:void_code`` —— 领域动作必须写成两级，现行是 ``bundling:code:void``
    - 内置角色：docs/07 §2.3 的 10 个（含数据范围）
    - 非 super_admin 角色的权限集合：docs/07 §2.3 只有文字描述，**完整矩阵分散在各
      模块文档的 §10**，此处按描述给保守集合，已登记 L-024 待业务方确认

新增权限点的流程（docs/07 §6）：
    1. 先改 docs/07 §2.2 表格
    2. 在本文件加一条（迁移与 seed 自动带上）
    3. 更新前端 ``packages/shared/enums/permissions.ts``
    4. 跑测试确认三处一致
"""

from dataclasses import dataclass

from app.common.enums import DataScope


@dataclass(frozen=True, slots=True)
class PermissionSeed:
    """一条权限点。"""

    code: str
    name: str
    module: str
    action: str
    sort_order: int


@dataclass(frozen=True, slots=True)
class RoleSeed:
    """一个内置角色。"""

    code: str
    name: str
    data_scope: DataScope
    description: str
    #: 该角色拥有的权限点 code；``None`` 表示"全部"（仅 super_admin）
    permissions: tuple[str, ...] | None = None


#: 全部权限点，顺序 = 界面展示顺序（按模块分组，模块内按 code 排序）
PERMISSIONS: tuple[PermissionSeed, ...] = (
    PermissionSeed(
        code="base:category:manage",
        name="基础资料·商品分类维护",
        module="base",
        action="category:manage",
        sort_order=1,
    ),
    PermissionSeed(
        code="base:create", name="基础资料·新建", module="base", action="create", sort_order=2
    ),
    PermissionSeed(
        code="base:delete", name="基础资料·删除", module="base", action="delete", sort_order=3
    ),
    PermissionSeed(
        code="base:disable", name="基础资料·停用", module="base", action="disable", sort_order=4
    ),
    PermissionSeed(
        code="base:export", name="基础资料·导出", module="base", action="export", sort_order=5
    ),
    PermissionSeed(
        code="base:import", name="基础资料·导入", module="base", action="import", sort_order=6
    ),
    PermissionSeed(
        code="base:operation:manage",
        name="基础资料·工序主数据维护",
        module="base",
        action="operation:manage",
        sort_order=7,
    ),
    PermissionSeed(
        code="base:rate_template:manage",
        name="基础资料·款号工序模板复制",
        module="base",
        action="rate_template:manage",
        sort_order=8,
    ),
    PermissionSeed(
        code="base:ratio:manage",
        name="基础资料·尺码比例维护",
        module="base",
        action="ratio:manage",
        sort_order=9,
    ),
    PermissionSeed(
        code="base:read", name="基础资料·查看", module="base", action="read", sort_order=10
    ),
    PermissionSeed(
        code="base:update", name="基础资料·修改", module="base", action="update", sort_order=11
    ),
    PermissionSeed(
        code="bundling:approve",
        name="打菲·审核",
        module="bundling",
        action="approve",
        sort_order=12,
    ),
    PermissionSeed(
        code="bundling:cancel", name="打菲·作废", module="bundling", action="cancel", sort_order=13
    ),
    PermissionSeed(
        code="bundling:code:void",
        name="打菲·作废打菲码",
        module="bundling",
        action="code:void",
        sort_order=14,
    ),
    PermissionSeed(
        code="bundling:create", name="打菲·新建", module="bundling", action="create", sort_order=15
    ),
    PermissionSeed(
        code="bundling:export", name="打菲·导出", module="bundling", action="export", sort_order=16
    ),
    PermissionSeed(
        code="bundling:print", name="打菲·打印", module="bundling", action="print", sort_order=17
    ),
    PermissionSeed(
        code="bundling:read", name="打菲·查看", module="bundling", action="read", sort_order=18
    ),
    PermissionSeed(
        code="bundling:reject", name="打菲·驳回", module="bundling", action="reject", sort_order=19
    ),
    PermissionSeed(
        code="bundling:reverse",
        name="打菲·反审核",
        module="bundling",
        action="reverse",
        sort_order=20,
    ),
    PermissionSeed(
        code="bundling:submit", name="打菲·提交", module="bundling", action="submit", sort_order=21
    ),
    PermissionSeed(
        code="bundling:update", name="打菲·修改", module="bundling", action="update", sort_order=22
    ),
    PermissionSeed(
        code="bundling:withdraw",
        name="打菲·撤回",
        module="bundling",
        action="withdraw",
        sort_order=23,
    ),
    PermissionSeed(
        code="cutting:approve", name="裁剪·审核", module="cutting", action="approve", sort_order=24
    ),
    PermissionSeed(
        code="cutting:cancel", name="裁剪·作废", module="cutting", action="cancel", sort_order=25
    ),
    PermissionSeed(
        code="cutting:create", name="裁剪·新建", module="cutting", action="create", sort_order=26
    ),
    PermissionSeed(
        code="cutting:export", name="裁剪·导出", module="cutting", action="export", sort_order=27
    ),
    PermissionSeed(
        code="cutting:import", name="裁剪·导入", module="cutting", action="import", sort_order=28
    ),
    PermissionSeed(
        code="cutting:read", name="裁剪·查看", module="cutting", action="read", sort_order=29
    ),
    PermissionSeed(
        code="cutting:reject", name="裁剪·驳回", module="cutting", action="reject", sort_order=30
    ),
    PermissionSeed(
        code="cutting:reverse",
        name="裁剪·反审核",
        module="cutting",
        action="reverse",
        sort_order=31,
    ),
    PermissionSeed(
        code="cutting:submit", name="裁剪·提交", module="cutting", action="submit", sort_order=32
    ),
    PermissionSeed(
        code="cutting:trace:read",
        name="裁剪·生产溯源查看",
        module="cutting",
        action="trace:read",
        sort_order=33,
    ),
    PermissionSeed(
        code="cutting:update", name="裁剪·修改", module="cutting", action="update", sort_order=34
    ),
    PermissionSeed(
        code="cutting:withdraw",
        name="裁剪·撤回",
        module="cutting",
        action="withdraw",
        sort_order=35,
    ),
    PermissionSeed(
        code="finance:ap:read",
        name="往来/财务·应付查看",
        module="finance",
        action="ap:read",
        sort_order=36,
    ),
    PermissionSeed(
        code="finance:ar:read",
        name="往来/财务·应收查看",
        module="finance",
        action="ar:read",
        sort_order=37,
    ),
    PermissionSeed(
        code="finance:export",
        name="往来/财务·导出",
        module="finance",
        action="export",
        sort_order=38,
    ),
    PermissionSeed(
        code="finance:import",
        name="往来/财务·导入",
        module="finance",
        action="import",
        sort_order=39,
    ),
    PermissionSeed(
        code="finance:payment",
        name="往来/财务·付款",
        module="finance",
        action="payment",
        sort_order=40,
    ),
    PermissionSeed(
        code="finance:payment:reverse",
        name="往来/财务·付款红冲",
        module="finance",
        action="payment:reverse",
        sort_order=41,
    ),
    PermissionSeed(
        code="finance:period:close",
        name="往来/财务·会计期间关账",
        module="finance",
        action="period:close",
        sort_order=42,
    ),
    PermissionSeed(
        code="finance:period:reopen",
        name="往来/财务·会计期间反关账",
        module="finance",
        action="period:reopen",
        sort_order=43,
    ),
    PermissionSeed(
        code="finance:receipt",
        name="往来/财务·收款",
        module="finance",
        action="receipt",
        sort_order=44,
    ),
    PermissionSeed(
        code="finance:receipt:reverse",
        name="往来/财务·收款红冲",
        module="finance",
        action="receipt:reverse",
        sort_order=45,
    ),
    PermissionSeed(
        code="finance:settle",
        name="往来/财务·结算",
        module="finance",
        action="settle",
        sort_order=46,
    ),
    PermissionSeed(
        code="finance:statement",
        name="往来/财务·对账单",
        module="finance",
        action="statement",
        sort_order=47,
    ),
    PermissionSeed(
        code="finance:voucher:create",
        name="往来/财务·新建凭证",
        module="finance",
        action="voucher:create",
        sort_order=48,
    ),
    PermissionSeed(
        code="finance:voucher:post",
        name="往来/财务·凭证过账",
        module="finance",
        action="voucher:post",
        sort_order=49,
    ),
    PermissionSeed(
        code="finance:voucher:read",
        name="往来/财务·凭证查看",
        module="finance",
        action="voucher:read",
        sort_order=50,
    ),
    PermissionSeed(
        code="finance:voucher:reverse",
        name="往来/财务·凭证红冲",
        module="finance",
        action="voucher:reverse",
        sort_order=51,
    ),
    PermissionSeed(
        code="finance:voucher:update",
        name="往来/财务·修改凭证",
        module="finance",
        action="voucher:update",
        sort_order=52,
    ),
    PermissionSeed(
        code="payroll:approve", name="工资·审核", module="payroll", action="approve", sort_order=53
    ),
    PermissionSeed(
        code="payroll:cancel", name="工资·作废", module="payroll", action="cancel", sort_order=54
    ),
    PermissionSeed(
        code="payroll:create", name="工资·新建", module="payroll", action="create", sort_order=55
    ),
    PermissionSeed(
        code="payroll:export", name="工资·导出", module="payroll", action="export", sort_order=56
    ),
    PermissionSeed(
        code="payroll:pay", name="工资·发放", module="payroll", action="pay", sort_order=57
    ),
    PermissionSeed(
        code="payroll:period:manage",
        name="工资·结算周期维护",
        module="payroll",
        action="period:manage",
        sort_order=58,
    ),
    PermissionSeed(
        code="payroll:read", name="工资·查看", module="payroll", action="read", sort_order=59
    ),
    PermissionSeed(
        code="payroll:reject", name="工资·驳回", module="payroll", action="reject", sort_order=60
    ),
    PermissionSeed(
        code="payroll:reverse",
        name="工资·反审核",
        module="payroll",
        action="reverse",
        sort_order=61,
    ),
    PermissionSeed(
        code="payroll:settle", name="工资·结算", module="payroll", action="settle", sort_order=62
    ),
    PermissionSeed(
        code="payroll:submit", name="工资·提交", module="payroll", action="submit", sort_order=63
    ),
    PermissionSeed(
        code="payroll:update", name="工资·修改", module="payroll", action="update", sort_order=64
    ),
    PermissionSeed(
        code="payroll:withdraw",
        name="工资·撤回",
        module="payroll",
        action="withdraw",
        sort_order=65,
    ),
    PermissionSeed(
        code="piecework:count",
        name="计件·扫码计件",
        module="piecework",
        action="count",
        sort_order=66,
    ),
    PermissionSeed(
        code="piecework:export",
        name="计件·导出",
        module="piecework",
        action="export",
        sort_order=67,
    ),
    PermissionSeed(
        code="piecework:manual",
        name="计件·手工补录",
        module="piecework",
        action="manual",
        sort_order=68,
    ),
    PermissionSeed(
        code="piecework:rate:manage",
        name="计件·单价维护",
        module="piecework",
        action="rate:manage",
        sort_order=69,
    ),
    PermissionSeed(
        code="piecework:read", name="计件·查看", module="piecework", action="read", sort_order=70
    ),
    PermissionSeed(
        code="piecework:reverse",
        name="计件·反审核",
        module="piecework",
        action="reverse",
        sort_order=71,
    ),
    PermissionSeed(
        code="purchase:approve",
        name="采购·审核",
        module="purchase",
        action="approve",
        sort_order=72,
    ),
    PermissionSeed(
        code="purchase:arrival",
        name="采购·到货登记",
        module="purchase",
        action="arrival",
        sort_order=73,
    ),
    PermissionSeed(
        code="purchase:arrival:reverse",
        name="采购·到货红冲",
        module="purchase",
        action="arrival:reverse",
        sort_order=74,
    ),
    PermissionSeed(
        code="purchase:cancel", name="采购·作废", module="purchase", action="cancel", sort_order=75
    ),
    PermissionSeed(
        code="purchase:create", name="采购·新建", module="purchase", action="create", sort_order=76
    ),
    PermissionSeed(
        code="purchase:export", name="采购·导出", module="purchase", action="export", sort_order=77
    ),
    PermissionSeed(
        code="purchase:import", name="采购·导入", module="purchase", action="import", sort_order=78
    ),
    PermissionSeed(
        code="purchase:ledger:read",
        name="采购·采购台账查看",
        module="purchase",
        action="ledger:read",
        sort_order=79,
    ),
    PermissionSeed(
        code="purchase:read", name="采购·查看", module="purchase", action="read", sort_order=80
    ),
    PermissionSeed(
        code="purchase:reject", name="采购·驳回", module="purchase", action="reject", sort_order=81
    ),
    PermissionSeed(
        code="purchase:reverse",
        name="采购·反审核",
        module="purchase",
        action="reverse",
        sort_order=82,
    ),
    PermissionSeed(
        code="purchase:submit", name="采购·提交", module="purchase", action="submit", sort_order=83
    ),
    PermissionSeed(
        code="purchase:update", name="采购·修改", module="purchase", action="update", sort_order=84
    ),
    PermissionSeed(
        code="sales:approve", name="销售·审核", module="sales", action="approve", sort_order=85
    ),
    PermissionSeed(
        code="sales:cancel", name="销售·作废", module="sales", action="cancel", sort_order=86
    ),
    PermissionSeed(
        code="sales:export", name="销售·导出", module="sales", action="export", sort_order=87
    ),
    PermissionSeed(
        code="sales:import", name="销售·导入", module="sales", action="import", sort_order=88
    ),
    PermissionSeed(
        code="sales:order:create",
        name="销售·新建订单",
        module="sales",
        action="order:create",
        sort_order=89,
    ),
    PermissionSeed(
        code="sales:read", name="销售·查看", module="sales", action="read", sort_order=90
    ),
    PermissionSeed(
        code="sales:reject", name="销售·驳回", module="sales", action="reject", sort_order=91
    ),
    PermissionSeed(
        code="sales:reverse", name="销售·反审核", module="sales", action="reverse", sort_order=92
    ),
    PermissionSeed(
        code="sales:ship", name="销售·发货", module="sales", action="ship", sort_order=93
    ),
    PermissionSeed(
        code="sales:submit", name="销售·提交", module="sales", action="submit", sort_order=94
    ),
    PermissionSeed(
        code="sales:update", name="销售·修改", module="sales", action="update", sort_order=95
    ),
    PermissionSeed(
        code="sales:withdraw", name="销售·撤回", module="sales", action="withdraw", sort_order=96
    ),
    PermissionSeed(
        code="self:payroll:read",
        name="员工自助·我的工资",
        module="self",
        action="payroll:read",
        sort_order=97,
    ),
    PermissionSeed(
        code="self:piecework:bind",
        name="员工自助·绑定计件工序",
        module="self",
        action="piecework:bind",
        sort_order=98,
    ),
    PermissionSeed(
        code="self:piecework:read",
        name="员工自助·piecework:read",
        module="self",
        action="piecework:read",
        sort_order=99,
    ),
    PermissionSeed(
        code="self:profile:read",
        name="员工自助·个人信息查看",
        module="self",
        action="profile:read",
        sort_order=100,
    ),
    PermissionSeed(
        code="stock:approve", name="库存·审核", module="stock", action="approve", sort_order=101
    ),
    PermissionSeed(
        code="stock:cancel", name="库存·作废", module="stock", action="cancel", sort_order=102
    ),
    PermissionSeed(
        code="stock:cost:view",
        name="库存·查看成本",
        module="stock",
        action="cost:view",
        sort_order=103,
    ),
    PermissionSeed(
        code="stock:create", name="库存·新建", module="stock", action="create", sort_order=104
    ),
    PermissionSeed(
        code="stock:export", name="库存·导出", module="stock", action="export", sort_order=105
    ),
    PermissionSeed(code="stock:in", name="库存·入库", module="stock", action="in", sort_order=106),
    PermissionSeed(
        code="stock:in:manual",
        name="库存·手动完单入库",
        module="stock",
        action="in:manual",
        sort_order=107,
    ),
    PermissionSeed(
        code="stock:out", name="库存·出库", module="stock", action="out", sort_order=108
    ),
    PermissionSeed(
        code="stock:read", name="库存·查看", module="stock", action="read", sort_order=109
    ),
    PermissionSeed(
        code="stock:reverse", name="库存·反审核", module="stock", action="reverse", sort_order=110
    ),
    PermissionSeed(
        code="stock:stocktake", name="库存·盘点", module="stock", action="stocktake", sort_order=111
    ),
    PermissionSeed(
        code="stock:submit", name="库存·提交", module="stock", action="submit", sort_order=112
    ),
    PermissionSeed(
        code="stock:transfer", name="库存·调拨", module="stock", action="transfer", sort_order=113
    ),
    PermissionSeed(
        code="stock:update", name="库存·修改", module="stock", action="update", sort_order=114
    ),
    PermissionSeed(
        code="stock:wip:read",
        name="库存·在制品查看",
        module="stock",
        action="wip:read",
        sort_order=115,
    ),
    PermissionSeed(
        code="system:config:manage",
        name="系统·系统配置管理",
        module="system",
        action="config:manage",
        sort_order=116,
    ),
    PermissionSeed(
        code="system:export:manage",
        name="系统·导出总开关",
        module="system",
        action="export:manage",
        sort_order=117,
    ),
    PermissionSeed(
        code="system:import:manage",
        name="系统·导入总开关",
        module="system",
        action="import:manage",
        sort_order=118,
    ),
    PermissionSeed(
        code="system:log:view",
        name="系统·日志查看",
        module="system",
        action="log:view",
        sort_order=119,
    ),
    PermissionSeed(
        code="system:role:manage",
        name="系统·角色管理",
        module="system",
        action="role:manage",
        sort_order=120,
    ),
    PermissionSeed(
        code="system:user:manage",
        name="系统·用户管理",
        module="system",
        action="user:manage",
        sort_order=121,
    ),
    PermissionSeed(
        code="system:workshop:manage",
        name="系统·车间管理",
        module="system",
        action="workshop:manage",
        sort_order=122,
    ),
)


def permission_codes() -> frozenset[str]:
    """全部权限点 code 集合。"""
    return frozenset(item.code for item in PERMISSIONS)


def permissions_by_module() -> dict[str, tuple[PermissionSeed, ...]]:
    """按模块分组，便于前端生成下拉与角色配置树。"""
    grouped: dict[str, list[PermissionSeed]] = {}
    for item in PERMISSIONS:
        grouped.setdefault(item.module, []).append(item)
    return {module: tuple(items) for module, items in grouped.items()}


#: 员工自助权限点（代码内置于 AuthContext，不经角色表授权，docs/07 §2.2）
SELF_PERMISSION_CODES: frozenset[str] = frozenset(
    item.code for item in PERMISSIONS if item.module == "self"
)


ROLES: tuple[RoleSeed, ...] = (
    RoleSeed(
        code="super_admin",
        name="系统管理员",
        data_scope=DataScope.FACTORY,
        description="所有权限，操作全审计，不可删除",
        permissions=None,
    ),
    RoleSeed(
        code="factory_manager",
        name="厂长",
        data_scope=DataScope.FACTORY,
        description="全部业务只读 + 审批终审，无系统管理",
        permissions=(
            "base:read",
            "cutting:read",
            "cutting:approve",
            "cutting:reject",
            "bundling:read",
            "bundling:approve",
            "bundling:reject",
            "piecework:read",
            "payroll:read",
            "payroll:approve",
            "stock:read",
            "sales:read",
            "finance:ar:read",
            "finance:ap:read",
            "purchase:read",
            "system:log:view",
        ),
    ),
    RoleSeed(
        code="workshop_supervisor",
        name="车间主管",
        data_scope=DataScope.WORKSHOP,
        description="本车间单据审批、计件确认",
        permissions=(
            "base:read",
            "base:update",
            "base:ratio:manage",
            "cutting:read",
            "cutting:approve",
            "cutting:reject",
            "bundling:read",
            "bundling:approve",
            "bundling:reject",
            "bundling:print",
            "piecework:read",
            "stock:read",
            "purchase:read",
        ),
    ),
    RoleSeed(
        code="line_leader",
        name="组长",
        data_scope=DataScope.GROUP,
        description="本组计件录入与查看",
        permissions=("base:read", "piecework:read", "cutting:read", "bundling:read"),
    ),
    RoleSeed(
        code="warehouse_keeper",
        name="仓管",
        data_scope=DataScope.FACTORY,
        description="出入库/调拨/盘点，**无 stock:cost:view**（成本不可见）",
        permissions=(
            "base:read",
            "base:create",
            "base:update",
            "base:import",
            "base:export",
            "stock:read",
            "stock:in",
            "stock:out",
            "stock:transfer",
            "stock:stocktake",
            "purchase:read",
        ),
    ),
    RoleSeed(
        code="accountant",
        name="财务",
        data_scope=DataScope.FACTORY,
        description="往来、收付款、凭证",
        permissions=(
            "base:read",
            "base:create",
            "base:update",
            "base:export",
            "finance:ar:read",
            "finance:ap:read",
            "finance:receipt",
            "finance:payment",
            "finance:settle",
            "finance:statement",
            "finance:export",
            "finance:voucher:read",
            "finance:voucher:create",
            "finance:voucher:update",
            "finance:voucher:post",
            "finance:voucher:reverse",
            "finance:period:close",
            "finance:period:reopen",
            "sales:read",
            "purchase:read",
            "stock:read",
        ),
    ),
    RoleSeed(
        code="purchaser",
        name="采购",
        data_scope=DataScope.FACTORY,
        description="采购单、供应商；颜色/尺码只读",
        permissions=(
            "base:read",
            "base:create",
            "base:update",
            "base:import",
            "base:export",
            "purchase:read",
            "purchase:create",
            "purchase:update",
            "purchase:submit",
            "purchase:approve",
            "purchase:arrival",
            "purchase:arrival:reverse",
            "purchase:import",
            "purchase:export",
            "purchase:ledger:read",
        ),
    ),
    RoleSeed(
        code="merchandiser",
        name="跟单",
        data_scope=DataScope.SELF,
        description="自己款号进度（款号维度隔离靠 styles.merchandiser_id）",
        permissions=("base:read", "sales:read", "cutting:read", "bundling:read"),
    ),
    RoleSeed(
        code="piecework_settler",
        name="计件员",
        data_scope=DataScope.FACTORY,
        description="单价维护、工资结算、款号工序模板复制",
        permissions=(
            "base:read",
            "base:update",
            "base:rate_template:manage",
            "base:export",
            "piecework:read",
            "piecework:rate:manage",
            "piecework:export",
            "payroll:read",
            "payroll:create",
            "payroll:update",
            "payroll:submit",
            "payroll:settle",
            "payroll:export",
            "payroll:period:manage",
        ),
    ),
    RoleSeed(
        code="employee",
        name="员工",
        data_scope=DataScope.SELF,
        description="仅员工端自助接口（/api/v1/self/**）",
        permissions=(),
    ),
)


def role_codes() -> frozenset[str]:
    """全部内置角色 code 集合。"""
    return frozenset(role.code for role in ROLES)


def resolve_role_permissions(role: RoleSeed) -> tuple[str, ...]:
    """展开某角色实际拥有的权限点 code。

    ``permissions=None`` 表示"全部权限"（仅 super_admin，docs/07 §2.3）。
    """
    if role.permissions is None:
        return tuple(sorted(permission_codes()))
    return tuple(sorted(role.permissions))
