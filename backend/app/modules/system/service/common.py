"""用户与角色管理服务层（docs/07 §2.3 内置角色、§5 审计与合规）。

三条贯穿全文件的硬规则：

1. **权限查库，不看 token**（docs/07 §1.1）。所以改权限**立刻生效**，不需重新登录 ——
   `get_auth_context` 每次请求都从库里重建 `AuthContext`。
2. **每次变更写 `document_logs`**（docs/07 §5「权限变更留痕」），日志与数据同事务。
3. **不做物理删除**（AGENTS §2.1；`erp_app` 被 REVOKE DELETE，docs/04 §6.2.1 /
   ADR-0025）。用户与角色都是 `is_active=false` + 软删。
"""

from __future__ import annotations

import logging

from app.common.permissions_registry import PERMISSIONS
from app.modules.system.schemas import PermissionGroupOut, PermissionOut

logger = logging.getLogger(__name__)

#: ``document_logs.doc_type``（docs/08 §1.1）
DOC_TYPE_USER = "User"
DOC_TYPE_ROLE = "Role"

ACTION_CREATE = "CREATE"
ACTION_UPDATE = "UPDATE"

#: 权限点 code → 中文名（registry 是单一来源；docs/07 §6 第 5 条要求前端常量同源）
PERMISSION_NAMES: dict[str, str] = {item.code: item.name for item in PERMISSIONS}

#: 模块 code → 中文名。docs/07 §2.2 的表格顺序即此顺序。
MODULE_NAMES: dict[str, str] = {
    "base": "基础资料",
    "cutting": "裁剪",
    "bundling": "打菲",
    "piecework": "计件",
    "payroll": "工资",
    "stock": "库存",
    "sales": "销售",
    "purchase": "采购",
    "finance": "财务",
    "system": "系统",
}

MAX_OFFSET = 10000


def _permission_module(code: str) -> str:
    """``base:rate_template:manage`` → ``base``。"""
    return code.split(":", 1)[0]


def permission_groups() -> list[PermissionGroupOut]:
    """按模块分组的权限点（角色表单的勾选树用）。

    ⚠️ 数据来自 **registry** 而不是 ``permissions`` 表 —— registry 是单一来源
    （docs/07 §2.2），而表里可能还留着已作废的码。表与 registry 不一致时
    ``cli/seed_baseline.py --check`` 会报出来。

    做成模块级函数而不是 service 方法：**它完全不碰数据库**（registry 在内存里），
    挂成方法会让人以为要用实例调，于是写出 ``__new__`` 那种绕路代码。
    """
    # ⚠️ **必须按 registry 的声明顺序遍历**，不能用 `permission_codes()` ——
    #    它返回 `frozenset`，迭代顺序由哈希决定，**每次请求都可能不同**。
    #    症状是角色表单里的权限树每次打开顺序都在变，用户找不到上次勾的那一项。
    grouped: dict[str, list[PermissionOut]] = {}
    for seed in PERMISSIONS:
        grouped.setdefault(_permission_module(seed.code), []).append(
            PermissionOut(code=seed.code, name=seed.name)
        )
    # ⚠️ 模块顺序也取自 registry 的声明顺序，**不按 MODULE_NAMES 的字典顺序**。
    #    实测踩过：registry 里 `bundling` 排在 `cutting` 前面，而 MODULE_NAMES
    #    按业务分组把 cutting 写在前面 —— 于是接口输出的顺序与 registry 不一致，
    #    前端拿 registry 生成的单点常量与后端返回的树对不上（TC 对不上就报错）。
    order = list(dict.fromkeys(_permission_module(seed.code) for seed in PERMISSIONS))
    return [
        PermissionGroupOut(
            module=module,
            module_name=MODULE_NAMES.get(module, module),
            permissions=grouped[module],
        )
        for module in order
    ]
