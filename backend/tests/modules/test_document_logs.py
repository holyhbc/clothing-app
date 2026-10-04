"""变更历史查询端点 + 建议款号只读端点（T-WEB-006 的两个前置缺口）。

这两条端点都是**原本就该有、但一直没做**的东西，各自对应一处"规范要求 vs 代码现实"
的落差：

1. ``GET /document-logs`` —— docs/06 §2.3 要求详情页有「变更历史」抽屉，
   ``document_logs`` 的索引就是照着这个查询建的，但**没有任何端点读那张表**。
2. ``GET /styles/suggested-no`` —— Q-P0-04 说"系统另提供建议号供参考"，而
   唯一的实现是建档时"顺便回一个"，按钮上没法用（一点就多一个款号）。
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.common.models import DocumentLog
from app.modules.base.models import Customer, ProductCategory, Style
from tests.factories.user import OPERATOR_ID


async def _seed_customer(session, code: str = "HB") -> Customer:
    customer = Customer(
        code=code, name=f"客户{code}", created_by=OPERATOR_ID, updated_by=OPERATOR_ID
    )
    session.add(customer)
    await session.flush()
    return customer


async def _seed_category(session, code: str = "TSTCAT") -> ProductCategory:
    """款号的 ``category_id`` 是**真外键**（B-CAT-02 必填），随机 UUID 会撞 FK 违例。

    ⚠️ code 要避开内置那 6 个分类（``SET`` / ``DRESS`` / …）—— seed 已经插过了，
    再插同一个 code 就是唯一索引违例（实测踩到）。
    """
    existing = (
        await session.execute(
            select(ProductCategory).where(
                ProductCategory.code == code, ProductCategory.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    category = ProductCategory(
        code=code, name=f"分类{code}", created_by=OPERATOR_ID, updated_by=OPERATOR_ID
    )
    session.add(category)
    await session.flush()
    return category


STYLE_NO = "HB-2026-9001"


async def _seed_style(session, *, style_no: str = STYLE_NO, merchandiser_id=None) -> Style:
    category = await _seed_category(session)
    style = Style(
        style_no=style_no,
        name=f"款号测试 {style_no}",
        category_id=category.id,
        merchandiser_id=merchandiser_id,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    session.add(style)
    await session.flush()
    return style


async def _seed_log(
    session,
    *,
    doc_type: str,
    doc_no: str,
    action: str = "UPDATE",
    created_at: datetime | None = None,
) -> DocumentLog:
    """⚠️ ``created_at`` 必须由用例显式给。

    ``document_logs.created_at`` 的 server_default 是 PG 的 ``now()``，而 **now() 是
    事务开始时间** —— 同一事务里写的多条日志时间戳**完全相同**（到微秒也一样）。
    端点按 ``created_at DESC`` 排序是对的，但同刻的多条之间顺序不由数据库保证
    （id 是随机 UUID v4，无序）。所以"排序"这条只能靠显式时间戳来测，
    真实数据里同刻多条的实际顺序无意义 —— 一次操作本来就只写一条日志。
    """
    log = DocumentLog(
        doc_type=doc_type,
        doc_id=uuid4(),
        doc_no=doc_no,
        action=action,
        operator_id=OPERATOR_ID,
        operator_name="张三",
        reason="改了单价",
        changed_fields={"unit_price": "0.30"},
        **({} if created_at is None else {"created_at": created_at}),
    )
    session.add(log)
    await session.flush()
    return log


# ------------------------------------------------------------------ 变更历史


async def test_document_logs_returns_rows_newest_first(client, db_session, auth_headers):
    base = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
    await _seed_log(
        db_session,
        doc_type="Style",
        doc_no=STYLE_NO,
        action="CREATE",
        created_at=base,
    )
    await _seed_log(
        db_session,
        doc_type="Style",
        doc_no=STYLE_NO,
        action="UPDATE",
        created_at=base + timedelta(minutes=5),
    )
    headers = await auth_headers(role="super_admin")

    response = await client.get(
        "/api/v1/document-logs",
        params={"doc_type": "Style", "doc_no": STYLE_NO},
        headers=headers,
    )
    body = response.json()

    assert body["code"] == 0
    assert body["data"]["total"] == 2
    # ⚠️ 审计页必须**新→旧**：用户要看的是"刚刚发生了什么"
    assert [item["action"] for item in body["data"]["items"]] == ["UPDATE", "CREATE"]


async def test_document_logs_filters_by_doc_type_and_no(client, db_session, auth_headers):
    await _seed_log(db_session, doc_type="Style", doc_no=STYLE_NO)
    await _seed_log(db_session, doc_type="Style", doc_no="HB-2026-0002")
    headers = await auth_headers(role="super_admin")

    body = (
        await client.get(
            "/api/v1/document-logs",
            params={"doc_type": "Style", "doc_no": STYLE_NO},
            headers=headers,
        )
    ).json()

    assert body["data"]["total"] == 1
    assert body["data"]["items"][0]["doc_no"] == STYLE_NO


async def test_document_logs_response_shape_is_what_the_drawer_needs(
    client, db_session, auth_headers
):
    """抽屉要显示「时间 / 操作人 / 动作 / 从→到 / 原因」—— 字段少一个就渲染成空白。"""
    await _seed_log(db_session, doc_type="Style", doc_no=STYLE_NO)
    headers = await auth_headers(role="super_admin")

    item = (
        await client.get(
            "/api/v1/document-logs",
            params={"doc_type": "Style", "doc_no": STYLE_NO},
            headers=headers,
        )
    ).json()["data"]["items"][0]

    for field in ("created_at", "operator_name", "action", "reason", "changed_fields"):
        assert field in item, f"抽屉要显示 {field}，响应里没有"
    assert item["operator_name"] == "张三"
    # ⚠️ 刻意**不含** operator_id：界面显示的是冗余姓名，UUID 对用户没有意义，
    #    暴露它只会诱使前端去拼一条不存在的"用户详情"跳转
    assert "operator_id" not in item


async def test_document_logs_requires_doc_type_and_doc_no(client, auth_headers):
    """⚠️ 两个参数必填：审计表没有归属列，"查全部日志"在数据范围下无法表达。"""
    headers = await auth_headers(role="super_admin")

    response = await client.get("/api/v1/document-logs", headers=headers)

    assert response.json()["code"] == 10001


async def test_document_logs_respects_data_scope_for_merchandiser(client, db_session, auth_headers):
    """跟单（SELF）只能看**自己款号**的变更历史 —— Q-P0-05。"""
    # ⚠️ 必须先建用户再拿 token：`merchandiser_id` 是真外键指向 `users.id`，
    #    传随机 UUID 会撞 FK 违例，而报错出现在 seed 里、与真因隔了三层
    headers = await auth_headers(role="merchandiser", data_scope="SELF")
    await _seed_style(db_session, merchandiser_id=UUID(headers["X-Test-User-Id"]))
    await _seed_log(db_session, doc_type="Style", doc_no=STYLE_NO)

    allowed = await client.get(
        "/api/v1/document-logs",
        params={"doc_type": "Style", "doc_no": STYLE_NO},
        headers=headers,
    )
    assert allowed.json()["code"] == 0, "自己的款号应该能看"

    denied = await client.get(
        "/api/v1/document-logs",
        params={"doc_type": "Style", "doc_no": "HB-2026-9999"},
        headers=headers,
    )
    assert denied.json()["code"] in (12002, 20001), "别人的款号必须被拒"


async def test_document_logs_denies_non_style_docs_to_scoped_user(client, db_session, auth_headers):
    """非款号系的 doc_type 对跟单一律 12002 —— 没有归属列可判，只能拒。

    ⚠️ 不能"读了再过滤"：那样页面上表现为"打开详情一片空白"，用户会以为是系统坏了，
    而真实原因是无权看。
    """
    headers = await auth_headers(role="merchandiser", data_scope="SELF")
    await _seed_style(db_session, merchandiser_id=UUID(headers["X-Test-User-Id"]))
    await _seed_log(db_session, doc_type="Role", doc_no="workshop_supervisor")

    response = await client.get(
        "/api/v1/document-logs",
        params={"doc_type": "Role", "doc_no": "workshop_supervisor"},
        headers=headers,
    )

    assert response.json()["code"] == 12002


@pytest.mark.parametrize("size", [0, 101])
async def test_document_logs_rejects_out_of_range_page_size(client, auth_headers, size):
    headers = await auth_headers(role="super_admin")

    response = await client.get(
        "/api/v1/document-logs",
        params={"doc_type": "Style", "doc_no": STYLE_NO, "size": size},
        headers=headers,
    )

    assert response.json()["code"] == 10001


# ------------------------------------------------------------------ 建议款号


async def test_suggested_no_returns_a_number_without_creating_a_style(
    client, db_session, auth_headers
):
    """核心口径：取建议号**不建档**。

    ⚠️ 复用 `POST /styles?suggest_style_no=true` 的话，按钮每点一次就多一个款号。
    """
    headers = await auth_headers(role="super_admin")

    response = await client.get("/api/v1/styles/suggested-no", headers=headers)
    body = response.json()

    assert body["code"] == 0
    assert body["data"]["style_no"].startswith("ST-"), "无客户时走全厂序列（前缀 ST）"
    styles = (await db_session.execute(select(Style))).scalars().all()
    assert styles == [], "取建议号不许留下任何款号行"


async def test_suggested_no_increments_per_customer_sequence(client, db_session, auth_headers):
    """同客户连续三次 → 0001 / 0002 / 0003（Q-P0-04：序号按客户递增）。"""
    customer = await _seed_customer(db_session, "HB")
    headers = await auth_headers(role="super_admin")

    numbers = []
    for _ in range(3):
        body = (
            await client.get(
                "/api/v1/styles/suggested-no",
                params={"customer_id": str(customer.id)},
                headers=headers,
            )
        ).json()
        numbers.append(body["data"]["style_no"])

    # 客户编码做前缀（R1 的 `{客户前缀}-{年份}-{4 位}`）
    assert [number.split("-")[-1] for number in numbers] == ["0001", "0002", "0003"]
    assert all(number.startswith("HB-") for number in numbers)


async def test_suggested_no_rejects_unknown_customer(client, db_session, auth_headers):
    """客户不存在 → 20001，而不是悄悄落全厂序列。

    ⚠️ 悄悄落全厂序列的话，用户建出来的款号前缀是 `ST`，而他明明选了客户 ——
    到时候没人知道为什么这个款的建议号和其他款不一样。
    """
    headers = await auth_headers(role="super_admin")

    body = (
        await client.get(
            "/api/v1/styles/suggested-no",
            params={"customer_id": str(uuid4())},
            headers=headers,
        )
    ).json()

    assert body["code"] == 20001
    assert "客户" in body["message"]


async def test_suggested_no_requires_create_permission_not_just_read(
    client, db_session, auth_headers
):
    """⚠️ 取号会 `UPDATE style_no_sequences.next_no`，是**写操作**。

    给 `base:read` 的话，任何能看款号的人都能狂点把某一年的序号消耗光 ——
    不影响正确性，但建议号会跳得很难看，而没人知道是谁点的。
    """
    headers = await auth_headers(role="custom", permissions=("base:read",))

    response = await client.get("/api/v1/styles/suggested-no", headers=headers)

    assert response.json()["code"] == 12001


async def test_suggested_no_route_is_not_shadowed_by_style_no_path(client, auth_headers):
    """⚠️ 路由顺序：`/styles/suggested-no` 必须排在 `/styles/{style_no}` **之前**。

    反了的话 FastAPI 把它当成"款号叫 suggested-no 的那一行" → 404，
    症状是"这个接口明明在 OpenAPI 里却 404"，极难定位（字典路由踩过同一个坑）。
    """
    headers = await auth_headers(role="super_admin")

    response = await client.get("/api/v1/styles/suggested-no", headers=headers)

    assert response.status_code == 200
    assert response.json()["code"] == 0
