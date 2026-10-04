"""用户与角色管理的数据范围口径与共享工具（docs/07 §3.2）。

## `data_scope` 为什么**不在角色上合并**

`users.data_scope` 是**用户的属性**，角色上的 `data_scope` 只是「建号时的默认值」。
现有登录链路（`auth/service.py::_issue_tokens`）就是直接读 `users.data_scope` 塞进
token 的 —— 所以"多角色怎么合并 `data_scope`"这个问题**已经由实现回答了：不存在合并**。
本模块沿用同一口径，不引入第二套规则。

（这一点原本是 T-AUTH-003 卡上登记的待确认问题 Q-A2。实现前先搜代码，
发现既有实现已经给出了答案 —— 按 AGENTS §2.3 第 4 步"搜代码里的同类实现，
跟随既有模式"，不再上交业务方。）
"""

from app.common.enums import DataScope

#: 数据范围的"宽窄"排序。仅用于**展示**（角色列表里标出谁的范围更大）与
#: 自助排查，不参与任何权限判定 —— 判定只看 `users.data_scope` 本身。
SCOPE_WIDTH: dict[DataScope, int] = {
    DataScope.SELF: 1,
    DataScope.GROUP: 2,
    DataScope.WORKSHOP: 3,
    DataScope.FACTORY: 4,
}


def scope_text(scope: DataScope) -> str:
    """数据范围的中文名（前端不再自己 switch）。"""
    return {
        DataScope.SELF: "仅本人",
        DataScope.GROUP: "本组",
        DataScope.WORKSHOP: "本车间及授权车间",
        DataScope.FACTORY: "全厂",
    }[scope]
