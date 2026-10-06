"""字典内置库 seed（docs/04 §7.4 + modules/01 §3.6.4 定稿清单）。

单独成模块而不是塞进 :mod:`seed_baseline`：那里的内容是"权限点 / 角色 /
初始超管"这类**代码级常量**，而这里是**业务数据**，两者的变更频率与评审人都不一样
（业务方改一个色名不需要看权限注册的 diff）。

清单（业务方 2026-10-01 确认，REV-2026-10 定稿）：
    - 16 色基础色卡
    - 8 行尺码（**不是 6 行** —— ``L``/``XL`` 男女款各一行，ADR-0027）
    - 2 个尺码模板 + 8 条成员
    - 6 个商品分类
    - ❌ 不 seed 童装码表与 ``style_color_size_ratios``（§3.6.4 明确）

三条口径：
    1. ``ON CONFLICT DO NOTHING``，**不覆盖**用户改过的名称与 ``is_active``（R26）
    2. 墓碑跳过：被真删的内置行**不复活**（ADR-0025 决策 3）；恢复走
       :mod:`app.cli.restore_builtin`
    3. 码表成员按 ``(size_code, size_class)`` 取尺码，不是只按码取（ADR-0027）
本包按职责拆为三个子模块，本 ``__init__`` 只做聚合重导出，保持
``from app.cli.seed_dicts import X`` 的旧导入路径零改动：
    - :mod:`app.cli.seed_dicts.builtin_data`：业务数据（``BUILTIN_*`` / ``DICT_DOC_TYPES``）
    - :mod:`app.cli.seed_dicts.seeders`：写入逻辑（墓碑判定 + 6 个 ``seed_*`` + 聚合）
    - :mod:`app.cli.seed_dicts.checker`：``--check`` 完整性校验
"""

from __future__ import annotations

from .builtin_data import (
    BUILTIN_COLORS,
    BUILTIN_MATERIAL_CATEGORIES,
    BUILTIN_PRODUCT_CATEGORIES,
    BUILTIN_SIZE_GROUPS,
    BUILTIN_SIZES,
    BUILTIN_UOM_UNITS,
    DICT_DOC_TYPES,
)
from .checker import check_dict_library
from .seeders import (
    seed_colors,
    seed_dict_library,
    seed_material_categories,
    seed_product_categories,
    seed_size_groups,
    seed_sizes,
    seed_uom_units,
    tombstoned_codes,
)

__all__ = [
    "BUILTIN_COLORS",
    "BUILTIN_MATERIAL_CATEGORIES",
    "BUILTIN_PRODUCT_CATEGORIES",
    "BUILTIN_SIZES",
    "BUILTIN_SIZE_GROUPS",
    "BUILTIN_UOM_UNITS",
    "DICT_DOC_TYPES",
    "check_dict_library",
    "seed_colors",
    "seed_dict_library",
    "seed_material_categories",
    "seed_product_categories",
    "seed_size_groups",
    "seed_sizes",
    "seed_uom_units",
    "tombstoned_codes",
]
