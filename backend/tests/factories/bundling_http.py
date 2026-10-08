"""打菲**接口层**（HTTP）测试的共用助手（T-BUND-007a）。

⚠️ **为什么不放进 ``tests/factories/bundling.py``**：那个文件已经 400 行硬线（ADR-0030），
而这里的东西只有接口层用得上（service 层的用例完全不碰 httpx）。

⚠️ **为什么不互相 import 测试模块**：两个测试文件都要用它，而「工厂 import 测试模块」
是**跨文件顺序依赖**（docs/10 §2.2 禁止）—— 那个文件没先跑或库被清空时就直接失败，
报错点离真因十万八千里。
"""

from typing import Any

from httpx import AsyncClient

from tests.factories.bundling import payload as bundling_payload
from tests.factories.bundling_approve import ensure_sizes

API = "/api/v1/bundling-orders"

#: 建单 → 改 → 提交 / 驳回 所需的权限点。**审核 (``bundling:approve``) 与撤回
#: (``bundling:withdraw``) 刻意不给**：那两个动作要「另一个人」，混进来会让「制单人 ≠
#: 审核人」这条用例不小心用同一个 token，断言就假绿了。
WRITER_PERMISSIONS: tuple[str, ...] = (
    "bundling:read",
    "bundling:create",
    "bundling:update",
    "bundling:submit",
    "bundling:reject",
)


def resolve(template: str, order_id: str, bundle_no: str) -> str:
    """把 ``{order_id}`` / ``{bundle_no}`` 占位符换成可请求的具体值。"""
    return template.replace("{order_id}", order_id).replace("{bundle_no}", bundle_no)


async def create_order(
    client: AsyncClient,
    headers: dict[str, str],
    world: dict[str, Any],
    **overrides: Any,
) -> dict[str, Any]:
    """经 HTTP 建一张草稿单（不直接调 service：端点层要自己走一遍权限与包装）。"""
    response = await client.post(
        API, json=bundling_payload(world, **overrides).model_dump(mode="json"), headers=headers
    )
    assert response.status_code == 201, response.text
    return dict(response.json()["data"])


async def submitted_order(
    client: AsyncClient,
    db_session: Any,
    headers: dict[str, str],
    world: dict[str, Any],
    *,
    hands: int = 1,
) -> dict[str, Any]:
    """建一张**已提交**的单（提交需要裁剪侧有余量，先补结转行）。

    ⚠️ ``ensure_sizes`` 先把裁剪尺码明细的手数与结转余量铺好 —— 否则提交会撞 ``30002``
    （可用量 0），而报错指向打菲、根因却在测试世界。
    """
    await ensure_sizes(db_session, world, (("XL", hands),))
    size_line = next(row for row in world["cutting_size_lines"] if row.size_code == "XL")
    created = await create_order(
        client,
        headers,
        world,
        lines=[{"cutting_size_line_id": str(size_line.id), "size_code": "XL", "hands": hands}],
    )
    response = await client.post(f"{API}/{created['id']}/submissions", headers=headers)
    assert response.status_code == 200, response.text
    return dict(response.json()["data"])


__all__ = [
    "API",
    "WRITER_PERMISSIONS",
    "create_order",
    "resolve",
    "submitted_order",
]
