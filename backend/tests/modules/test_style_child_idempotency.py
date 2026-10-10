"""``style_child_router`` 的**幂等命中**回归（闭环 L-114）。

⚠️ **这个 bug 为什么能活下来**：本文件之前**根本不存在** —— `style_child_router`
的幂等命中分支一次都没被测过。四个用了 ``Idempotency-Key`` 的 router 里，
只有这一处把**整个缓存**当响应体返回：

    return idempotent.cached          # ← 错

而缓存里存的是 ``{"body_hash":…, "state":…, "response": {…}}``。
直接返回的话，FastAPI 按 ``ApiResponse`` 校验 → ``data`` 变 null →
客户端拿到「复制成功但没有数据」，**而工序单价已经写进库了**。

这是最难自查的一种不一致：**第一次正常、幂等重试拿到空 data**，
且两边 HTTP 状态都是 200、日志里也有一条成功的 ``document_logs``。
真正触发它的场景又恰恰是它本该保护的那个 —— 前端重复点击「复制模板」。
"""

from typing import Any

from app.core.errors import ErrorCode
from tests.factories.base_style import copy_source

API = "/api/v1/styles"


async def _headers(auth_headers: Any) -> dict[str, str]:
    """带真实 token 的请求头（docs/10 §2.2：按内置角色表授权，不手写权限点字面量）。"""
    return await auth_headers(role="super_admin")


async def test_copy_template_idempotent_retry_returns_first_result(
    client, db_session, auth_headers, _clean_redis_namespace
) -> None:
    """同键重试必须回**首次那一份结果**，而不是空 ``data``（闭环 L-114）。

    断言的是**两次响应逐字段相同**且 ``data`` 非空 —— 只断 HTTP 200 的话，
    ``data: null`` 同样是 200，测不出缺陷。
    """
    source, target = await copy_source(db_session)
    url = f"{API}/{target.style_no}/operations/copy-from/{source.style_no}"
    body = {"copy_mode": "COPY_PRICE_AS_IS", "conflict_policy": "SKIP"}
    headers = {**(await _headers(auth_headers)), "Idempotency-Key": "l114-copy-1"}

    first = await client.post(url, json=body, headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["code"] == 0

    second = await client.post(url, json=body, headers=headers)
    assert second.status_code == 200, second.text

    first_data = first.json()["data"]
    second_data = second.json()["data"]

    assert second_data is not None, (
        "幂等重试返回了 data=null —— 这是 L-114 的原始症状："
        "把整个缓存当响应体返回，而缓存外壳是 {body_hash,state,response}"
    )
    assert second_data == first_data, "幂等重试必须原样回首次结果（docs/05 §5）"
    assert second_data["structure_written"] == first_data["structure_written"] == 2


async def test_copy_template_idempotent_retry_does_not_write_twice(
    client, db_session, auth_headers, _clean_redis_namespace
) -> None:
    """幂等重试**不得重复写库** —— 模板复制是「覆盖目标款工序价」，写两次等于把源价再盖一遍。

    ⚠️ 这条与上一条互补：上一条断「返回什么」，这条断「库里有几份」。
    只断返回值的话，一个「回首次结果但又执行了一遍」的实现照样通过。
    """
    from sqlalchemy import func, select

    from app.modules.base.models import StyleOperation

    source, target = await copy_source(db_session)
    url = f"{API}/{target.style_no}/operations/copy-from/{source.style_no}"
    body = {"copy_mode": "COPY_PRICE_AS_IS", "conflict_policy": "SKIP"}
    headers = {**(await _headers(auth_headers)), "Idempotency-Key": "l114-copy-2"}

    await client.post(url, json=body, headers=headers)
    await client.post(url, json=body, headers=headers)

    rows = await db_session.scalar(
        select(func.count())
        .select_from(StyleOperation)
        .where(StyleOperation.style_no == target.style_no)
    )
    assert rows == 2, f"目标款应有 2 道工序（幂等重试写了 {rows} 遍）"


async def test_copy_template_same_key_different_body_rejected(
    client, db_session, auth_headers, _clean_redis_namespace
) -> None:
    """同键**不同** body → ``10002``（docs/05 §5）。

    ⚠️ 这条守住上一条的反面：释放/命中逻辑不能退化成「这个键用过就一律放行」，
    否则幂等键退化成随机数，「同键异 body」这条保证就没了。
    """
    source, target = await copy_source(db_session)
    url = f"{API}/{target.style_no}/operations/copy-from/{source.style_no}"
    headers = {**(await _headers(auth_headers)), "Idempotency-Key": "l114-copy-3"}

    first = await client.post(
        url,
        json={"copy_mode": "COPY_PRICE_AS_IS", "conflict_policy": "SKIP"},
        headers=headers,
    )
    assert first.status_code == 200, first.text

    other = await client.post(
        url,
        json={"copy_mode": "COPY_PRICE_AS_IS", "conflict_policy": "OVERWRITE"},
        headers=headers,
    )
    assert other.status_code == 400, other.text
    assert other.json()["code"] == ErrorCode.MISSING_BUSINESS_PARAM, (
        "同键不同 body 必须 10002，否则幂等键形同虚设"
    )
