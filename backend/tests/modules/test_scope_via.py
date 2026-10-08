"""``apply_data_scope`` 的 ``via`` 通路：数据范围列在**关联表**上（T-BUND-007b）。

## 为什么单独一个文件

主测试 ``test_scope.py`` 是 400 行硬线（ADR-0030）里最满的一个，而 ``via`` 是
**一项能力**（不是一两个断言）：它让「主表没有归属列」的资源（``bundles`` 的车间在
``bundling_orders`` 上，``03 §3.3``）也能被数据范围覆盖。能力有自己的文件，
后来的人才知道去哪儿找它 —— 埋在别人的文件尾部只会被当成随手加的用例。

## 两条守卫

1. **登记的父表模型必须与查询里用的同一个**（防运行时才炸的漂移）
2. **过滤真的按父表的车间生效**：本人车间看得到、他车间一条都看不到
   （漏掉这条机制的症状是车间主管能查到别的车间的码，而列表看起来一切正常）
"""

from uuid import uuid4

from app.common.enums import DataScope
from app.core.permissions import AuthContext
from app.core.scope import SCOPE_SPECS
from tests.factories.user import WorkshopFactory


def _ctx(*, data_scope: DataScope, workshop_id, allowed: frozenset) -> AuthContext:
    return AuthContext(
        user_id=uuid4(),
        name="测试",
        employee_no="A001",
        workshop_id=workshop_id,
        group_no=None,
        permissions=frozenset(),
        data_scope=data_scope,
        allowed_workshop_ids=allowed,
    )


def test_via_spec_points_at_the_same_parent_model_as_queries() -> None:
    """``via`` 登记的父表模型必须与查询里用的**同一个**。

    ⚠️ 防一种**运行时才炸**的漂移：``SCOPE_SPECS`` 与查询代码各写一个模型名的话，
    改了 A 处忘了 B 处，B 处会生成一条引用不到任何表的 SQL —— 而那要到用户点了
    「统计」才暴露，报错还完全看不出根因在 scope 层。
    """
    from app.modules.bundling.code_repository import StatQuery  # noqa: F401 —— 确保已 import
    from app.modules.bundling.models import BundlingOrder

    spec = SCOPE_SPECS["bundles"]
    assert spec.via is not None, "bundles 靠 doc_id 回查车间，必须登记 via"
    assert spec.via.model is BundlingOrder


async def test_via_scope_filters_through_parent_workshop(db_session) -> None:
    """``via`` 资源按**父表**的车间过滤（``bundles`` 自己没有 ``workshop_id``）。"""
    from app.modules.bundling.code_repository import BundleListQuery, list_bundles
    from tests.factories.bundling import build_world
    from tests.factories.bundling_approve import approve_order, prepare_order

    other = (await WorkshopFactory.create(db_session)).id
    world = await build_world(db_session, style_no="SCOPE-BD-1")
    order = await approve_order(db_session, await prepare_order(db_session, world, (("XL", 2),)))
    # ⚠️ 可见车间 = **单据真正所在**的那个（world 自带一个），另建一个当「别人的」——
    #    否则断言会因为「谁也看不见」而假绿。
    mine = world["workshop"].id
    assert order.workshop_id == mine != other

    mine_ctx = _ctx(data_scope=DataScope.WORKSHOP, workshop_id=mine, allowed=frozenset({mine}))
    rows = await list_bundles(db_session, mine_ctx, BundleListQuery())
    assert len(rows) == 2, "本人车间的码必须看得到"
    assert {row.size_code for row in rows} == {"XL"}

    other_ctx = _ctx(data_scope=DataScope.WORKSHOP, workshop_id=other, allowed=frozenset({other}))
    assert await list_bundles(db_session, other_ctx, BundleListQuery()) == [], "他车间的码查不到"
