"""INV-T-WEB-005：基础资料前端契约的三层一致性守卫。

T-WEB-005 的任务卡留了三个待定口径，其中两个的**答案就是这三条断言**：

缺口②（前端不要手写 72 个函数 → 写声明式注册表）：
    前端 ``RESOURCE_REGISTRY`` 的 key 集合必须与后端 :data:`RESOURCES` 对得上，
    且差集能被**显式解释**（本文件 :data:`PENDING_FRONTEND`）。多一个 = 前端会去请求
    一个不存在的路径（静默 404）；少一个 = 那个资源没有页面而没人发现。

缺口③（九个 Form 的必填字段清单放哪）：
    真相在后端 :data:`WRITE_MODELS`（pydantic），经生成器落到
    ``packages/shared/src/enums/baseDictFields.ts``。本文件断言**生成物 == 后端模型**，
    前端那一侧由 ``packages/admin/src/api/base.test.ts`` 断言「注册表不得自己写必填」。

⚠️ **为什么生成物能被 ``json.loads`` 直接读**：``.prettierrc.json`` 给那个文件加了
   ``singleQuote:false`` / ``quoteProps:preserve`` / ``trailingComma:none`` 三条覆盖，
   于是对象体是**合法 JSON**。不这么做的话这里就只能用正则解析 TS —— 而正则解析
   生成物正是本仓已经踩过的坑（``permissions.ts`` 那个守卫靠正则，改个引号风格就会
   悄悄少一项，而少一项的表现是「按钮该隐藏却还显示」= 越权）。
"""

import json
from decimal import Decimal
from enum import Enum
from pathlib import Path
from types import UnionType
from typing import Any, Union, get_args, get_origin
from uuid import UUID

import pytest

from app.modules.base.resources import RESOURCES
from app.modules.base.schemas import WRITE_MODELS

REPO_ROOT = Path(__file__).resolve().parents[3]
GENERATED_TS = (
    REPO_ROOT / "frontend" / "packages" / "shared" / "src" / "enums" / "baseDictFields.ts"
)
REGISTRY_TS = REPO_ROOT / "frontend" / "packages" / "admin" / "src" / "api" / "base.ts"

#: 前端注册表**故意不含**的资源 → 原因。
#:
#: ⚠️ 这一项必须显式登记，不能靠"少一个就红"：``customers`` 的后端端点齐全
#:    （T-BASE-002 组 D），但客户页不在 T-WEB-005 范围内（九页是任务卡固定的）。
#:    不登记的话，要么守卫常年红，要么有人为了让它变绿把断言改成"前端 ⊆ 后端"——
#:    那条更宽松的断言正好丢掉缺口②要防的东西（漏做页面没人知道）。
PENDING_FRONTEND: dict[str, str] = {
    "customers": "客户页不在 T-WEB-005 范围（任务卡固定九页）；端点已由 T-BASE-002 交付",
}

_ACTIONS = ("create", "update", "disable", "delete", "export")
_SCALARS: dict[Any, str] = {
    str: "string",
    bool: "bool",
    int: "int",
    Decimal: "decimal",
    UUID: "uuid",
}


def _split_optional(annotation: Any) -> tuple[Any, bool]:
    origin = get_origin(annotation)
    if origin is Union or origin is UnionType:
        args = [arg for arg in get_args(annotation) if arg is not type(None)]
        if len(args) != len(get_args(annotation)):
            return args[0], True
    return annotation, False


def _type_of(annotation: Any) -> dict[str, Any]:
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return {"type": "enum", "values": [member.value for member in annotation]}
    if get_origin(annotation) is list:
        return {"type": "list"}
    return {"type": _SCALARS[annotation]}


def _expected_contract() -> dict[str, Any]:
    """从后端模型算出契约，**逐字复刻生成器的算法**。

    复刻而不是 import 脚本：生成器是 Node 侧的独立实现，这里要与它对照 ——
    两边算出同一个结果才说明生成器没漂。
    """
    result: dict[str, Any] = {}
    for resource in RESOURCES:

        def spec(model: Any) -> dict[str, Any]:
            fields: list[dict[str, Any]] = []
            required: list[str] = []
            for name, info in model.model_fields.items():
                inner, nullable = _split_optional(info.annotation)
                entry: dict[str, Any] = {"name": name, "nullable": nullable, **_type_of(inner)}
                if not info.is_required() and info.default is not None:
                    # 与生成器同一条规则：Decimal 出字符串，其余原样。
                    # ⚠️ 判据是**映射后的类型**不是 isinstance —— `inner` 是类对象，
                    #    isinstance(Decimal, Decimal) 恒为 False，于是 Decimal 的默认值
                    #    会以 Decimal 实例进 JSON，报 "not JSON serializable"。
                    entry["default"] = (
                        str(info.default) if entry["type"] == "decimal" else info.default
                    )
                fields.append(entry)
                if info.is_required():
                    required.append(name)
            return {"required": required, "fields": fields}

        result[resource.key] = {
            "key": resource.key,
            "codeColumn": resource.path_column,
            "nameColumn": resource.name_column,
            "hasBuiltinFlag": resource.has_builtin_flag,
            "allowPhysicalDelete": resource.allow_physical_delete,
            "refCheckers": [checker.label for checker in resource.ref_checkers],
            "permissions": {action: resource.permission(action) for action in _ACTIONS},
            "create": spec(WRITE_MODELS[resource.key][0]),
            "patch": spec(WRITE_MODELS[resource.key][1]),
        }
    return result


def _load_generated() -> dict[str, Any]:
    assert GENERATED_TS.is_file(), (
        f"{GENERATED_TS} 不存在：在 frontend 下跑 `node scripts/generate-frontend-contract.mjs`"
    )
    text = GENERATED_TS.read_text(encoding="utf-8")
    marker = "export const BASE_DICT_CONTRACT = "
    suffix = " as const satisfies"
    start = text.index(marker) + len(marker)
    end = text.index(suffix)
    return dict(json.loads(text[start:end]))


GENERATED = _load_generated()


# ---------------------------------------------------------------------------
# 守卫 0：解析器自身
# ---------------------------------------------------------------------------


def test_generated_file_parses_as_json_and_is_not_empty() -> None:
    """守卫解析器：文件被 prettier 改了形状时（如尾逗号回来），这里先炸。"""
    assert GENERATED, "从 baseDictFields.ts 解出来的对象是空的"
    assert "colors" in GENERATED, "至少要包含 colors（这是本页要防的第一个拼写漂移）"


# ---------------------------------------------------------------------------
# 一致性 1：生成物 == 后端模型（缺口③ 的根部）
# ---------------------------------------------------------------------------


def test_generated_contract_matches_backend_models() -> None:
    expected = _expected_contract()
    assert set(GENERATED) == set(expected), (
        f"资源 key 不一致：生成物多 {sorted(set(GENERATED) - set(expected))}，"
        f"缺 {sorted(set(expected) - set(GENERATED))}"
    )
    for key, contract in expected.items():
        assert GENERATED[key] == contract, f"{key} 的契约与后端模型不一致，请重跑生成器"


@pytest.mark.parametrize("key", sorted(r.key for r in RESOURCES))
def test_patch_requires_version(key: str) -> None:
    """PATCH 必传 ``version``（§4.4）—— 缺了它前端就少一道乐观锁。"""
    contract = GENERATED[key]
    assert "version" in contract["patch"]["required"], f"{key} 的 PATCH 必须要求 version"
    assert contract["patch"]["fields"][0]["name"] == "version", (
        f"{key} 的 version 应是 patch 模型的第一个字段（生成顺序 = 声明顺序）"
    )


def test_code_field_is_not_in_create_required_for_operations() -> None:
    """``operations.operation_no`` 可改是禁止的（§4.4）。

    PATCH 模型里**没有** ``operation_no`` —— 前端"编辑时置灰"是从这个事实推出来的，
    而不是另写一条规则。这里把它钉住：哪天有人给 OperationPatch 加了 operation_no，
    历史单据的字符串引用就会指向一个不存在的工序。
    """
    patch_names = {field["name"] for field in GENERATED["operations"]["patch"]["fields"]}
    assert "operation_no" not in patch_names
    assert "operation_no" in GENERATED["operations"]["create"]["required"]


def test_dict_resources_require_code_and_name() -> None:
    """字典三表（颜色 / 尺码 / 码表）的必填字段各有各的形态，别被统一掉。"""
    assert GENERATED["colors"]["create"]["required"] == ["color_code", "name"]
    assert GENERATED["sizes"]["create"]["required"] == ["size_code", "name", "size_class"]
    # 码表没有编码列：`name` 本身就是唯一键兼路径参数（04 §7.4 取消了 group_code）
    assert GENERATED["size-groups"]["codeColumn"] == "name"
    assert "items" in GENERATED["size-groups"]["create"]["required"]


def test_physical_delete_flag_matches_adr_0025() -> None:
    """字典三表真删、其余软删（ADR-0025 / §4.4）。前端删除确认框的措辞读它。"""
    physical = {key for key, value in GENERATED.items() if value["allowPhysicalDelete"]}
    assert physical == {"colors", "sizes", "size-groups"}


def test_write_permissions_differ_for_category_and_operation() -> None:
    """分类与工序的写权限点不是 ``base:*``（§4.4）。

    前端的按钮显隐读这里，写成 ``base:create`` 的后果是**有分类权限的人被前端藏掉按钮**
    （而真越权的人反而看得见）。
    """
    assert GENERATED["product-categories"]["permissions"]["create"] == "base:category:manage"
    assert GENERATED["operations"]["permissions"]["create"] == "base:operation:manage"
    assert GENERATED["colors"]["permissions"]["create"] == "base:create"


# ---------------------------------------------------------------------------
# 一致性 2：前端注册表 vs 后端 RESOURCES（缺口②）
# ---------------------------------------------------------------------------


def _registry_keys() -> set[str]:
    """从前端注册表里抠出 ``key: 'xxx'``。

    ⚠️ 正则只匹配**恰好** ``key: '<kebab>'`` 的那一处（每条声明只有一个），
    所以多写一行注释里的 ``key:`` 不会把守卫带偏 —— 而 key 用 ``key: '...'`` 这个
    写法是生成器之外的硬约定，改了这里就会红（这正是我们要的：改约定要有人同意）。
    """
    import re

    assert REGISTRY_TS.is_file(), f"{REGISTRY_TS} 不存在"
    content = REGISTRY_TS.read_text(encoding="utf-8")
    return set(re.findall(r"^\s{4}key: '([a-z][a-z-]*)',$", content, re.MULTILINE))


def test_frontend_registry_keys_are_explained() -> None:
    """前端注册表的 key 集合 == 后端 RESOURCES 减去已登记的待做资源。"""
    frontend = _registry_keys()
    backend = {resource.key for resource in RESOURCES}
    assert frontend, "从前端注册表里没解析出任何 key —— 正则或文件格式变了"

    unknown = frontend - backend
    assert not unknown, (
        f"前端注册表里有后端不存在的资源：{sorted(unknown)}。"
        f"那会去请求一个 404 的路径（页面上表现为「没有数据」，零报错）"
    )

    missing = backend - frontend - set(PENDING_FRONTEND)
    assert not missing, (
        f"后端已交付但前端没有页面的资源：{sorted(missing)}。"
        f"要么补页面，要么在 PENDING_FRONTEND 里登记原因（别为了让守卫变绿而放宽断言）"
    )
    stale = set(PENDING_FRONTEND) - backend
    assert not stale, f"PENDING_FRONTEND 里有后端已不存在的资源：{sorted(stale)}"


def test_frontend_registry_does_not_declare_required_fields() -> None:
    """注册表**不得**自己声明必填（缺口③）。

    断言方式是源码级扫描：注册表里不能出现 ``required:`` 字样。
    真出现时那条声明就是第二份真相 —— 后端加必填字段时它不会跟着变，
    而用户要填完提交才被 ``10001`` 拒。
    """
    content = REGISTRY_TS.read_text(encoding="utf-8")
    offenders = [
        line.strip()
        for line in content.splitlines()
        if "required:" in line and "REQUIRED_FIELD" not in line
    ]
    assert not offenders, (
        "前端注册表里出现了 required 声明："
        f"{offenders}。必填清单的唯一来源是 BASE_DICT_CONTRACT[...].create.required"
    )


def test_registry_covers_every_required_field_of_each_resource() -> None:
    """反向：**每个必填字段都要在注册表的表单字段里出现**。

    只断言"注册表不多写"是不够的 —— 后端加了一个必填字段而前端表单里没有它，
    用户就永远填不到那个值，提交必被拒，而表单看上去毫无异常。
    """
    import re

    content = REGISTRY_TS.read_text(encoding="utf-8")
    # 每条声明形如 `  colors: {` / `  'workshop-groups': {`（kebab-case 的 key 加引号）
    starts = list(re.finditer(r"^  '?([a-z][a-z-]*)'?: \{$", content, re.MULTILINE))
    form_field_names: dict[str, set[str]] = {}
    for index, match in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(content)
        form_field_names[match.group(1)] = set(
            re.findall(r"\{ name: '([a-z_]+)'", content[match.end() : end])
        )

    for key, contract in GENERATED.items():
        if key in PENDING_FRONTEND:
            continue
        declared = form_field_names.get(key)
        assert declared is not None, f"前端注册表里找不到 {key} 的声明块"
        missing = set(contract["create"]["required"]) - declared
        assert not missing, (
            f"{key} 的必填字段 {sorted(missing)} 没在表单里出现 —— "
            f"用户没有地方填，提交必然被 10001 拒"
        )
