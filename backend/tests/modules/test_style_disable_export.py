"""款号停用与导出的测试（T-WEB-006 补的三个规范缺口）。

背景：T-WEB-006 要做款号详情页，任务卡要求右上角有「停用」，而
``docs/05 §9.1`` 要求列表页都能导出 —— 但**这两个端点一直不存在**。
前端只能把按钮藏掉（用户没法停用一个款号）、列表只能不给导出（对账时得手抄）。
这两条都属于「漏了也不会报错、只是功能少一块」的那一类，所以要有测试钉住。

======================  =================================================
停用                    原因必填（``10002``）；重复停用 → ``10001``；
                        ``version`` 不匹配 → ``10003``；停用写日志；
                        停用后 ``is_active=false`` 且历史可查
导出                    需要两个权限点；与列表同一套筛选（行数一致）；
                        停用的款号**默认也导得出**（要按 is_active 筛才筛掉）
详情字段                列表行带 ``customer_style_no`` / ``bulk_qty`` /
                        ``merchandiser_name``（详情页要能编辑回显）
======================  =================================================

⚠️ **停用必须有 version**：款号有工序/单价/比例四张子表，是共享档案，
必须乐观锁 —— 详见 ``StyleService.disable`` 的注释。
"""

import json
from uuid import uuid4

from sqlalchemy import select

from app.common.enums import DataScope
from app.common.models import DocumentLog
from app.core.errors import ErrorCode
from app.modules.auth.models import User
from app.modules.base.models import (
    Customer,
    ProductCategory,
    Style,
    Workshop,
)
from app.modules.base.router import EXPORT_GLOBAL_PERMISSION

OPERATOR_ID = uuid4()
STYLE_NO = "HB-2026-7001"


# ------------------------------------------------------------------ 种子


async def _seed_category(session, code: str = "TSTCAT7") -> ProductCategory:
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


async def _seed_customer(session, code: str = "HB7") -> Customer:
    existing = (
        await session.execute(
            select(Customer).where(Customer.code == code, Customer.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    customer = Customer(
        code=code, name=f"客户{code}", created_by=OPERATOR_ID, updated_by=OPERATOR_ID
    )
    session.add(customer)
    await session.flush()
    return customer


async def _seed_user(session, employee_no: str = "E7001") -> User:
    existing = (
        await session.execute(
            select(User).where(User.employee_no == employee_no, User.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    workshop = (
        await session.execute(select(Workshop).where(Workshop.code == "CUT").limit(1))
    ).scalar_one_or_none()
    user = User(
        employee_no=employee_no,
        name="李跟单",
        workshop_id=workshop.id if workshop else None,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_style(session, *, style_no: str = STYLE_NO, **overrides) -> Style:
    category = await _seed_category(session)
    style = Style(
        style_no=style_no,
        name=f"款号测试 {style_no}",
        category_id=category.id,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
        **overrides,
    )
    session.add(style)
    await session.flush()
    return style


# ------------------------------------------------------------------ 停用


async def test_http_disable_style_requires_reason(client, db_session, auth_headers):
    """⚠️ 停用**必须**填原因（docs/06 §5）：不填就停用，用户半年后查不出谁停的。"""
    style = await _seed_style(db_session)
    headers = await auth_headers(role="super_admin")

    response = await client.post(
        f"/api/v1/styles/{style.style_no}/disables",
        json={"version": style.version},
        headers=headers,
    )

    # ⚠️ 422 的响应体形状与业务错误**不同**：它是请求校验失败的包装，
    #    没有 `error.code`（那是 docs/05 §4 的业务错误码才有的）
    assert response.status_code == 422
    assert "error" not in response.json()
    assert "reason" in json.dumps(response.json())


async def test_http_disable_style_writes_log_and_flips_flag(client, db_session, auth_headers):
    """停用必须写 ``document_logs``（AGENTS §2.2）：谁、何时、为什么。"""
    style = await _seed_style(db_session)
    headers = await auth_headers(role="super_admin")
    # ⚠️ 先把 version 存下来：db_session 与 app 共用同一个会话，请求成功后
    #    `style.version` 已经变成新值了，事后再算 `+ 1` 就会算错
    before = style.version

    response = await client.post(
        f"/api/v1/styles/{style.style_no}/disables",
        json={"version": before, "reason": "客户取消订单，款号不再使用"},
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["is_active"] is False
    assert body["version"] == before + 1

    await db_session.refresh(style)
    assert style.is_active is False
    # 停用**不是删除**：软删字段必须还是空的，否则历史单据按 style_id 查不到档案
    assert style.deleted_at is None

    log = (
        (
            await db_session.execute(
                select(DocumentLog).where(
                    DocumentLog.doc_no == style.style_no, DocumentLog.action == "UPDATE"
                )
            )
        )
        .scalars()
        .all()
    )
    reasons = [item.reason for item in log]
    assert "客户取消订单，款号不再使用" in reasons
    assert any(item.changed_fields.get("is_active") is False for item in log)


async def test_http_disable_style_rejects_stale_version(client, db_session, auth_headers):
    """⚠️ ``version`` 不匹配 → ``10003``：否则会把别人刚改完的款号停掉。

    款号是共享档案 —— 有人在款号详情页改了款名并保存了，别人那个还开着
    「停用」按钮的页面点下去，会把新改的内容一起停掉，且界面完全看不出。
    """
    style = await _seed_style(db_session)
    headers = await auth_headers(role="super_admin")
    stale = style.version
    # 别人的那次修改会把 version 推进；本用例故意用**旧值**停用

    await client.patch(
        f"/api/v1/styles/{style.style_no}",
        json={"version": stale, "name": "被别人改过的款名"},
        headers=headers,
    )

    response = await client.post(
        f"/api/v1/styles/{style.style_no}/disables",
        json={"version": stale, "reason": "停用它"},
        headers=headers,
    )

    assert response.status_code == 409
    assert response.json()["code"] == int(ErrorCode.OPTIMISTIC_LOCK_CONFLICT)
    await db_session.refresh(style)
    assert style.is_active is True, "乐观锁失败时不该真的停用"


async def test_http_disable_style_twice_is_rejected(client, db_session, auth_headers):
    """重复停用 → ``10001``。

    ⚠️ 判据是「已经处于目标状态」而不是「与目标不同」：写成
    ``style.is_active is not False`` 的话，停用一个**启用中**的款号会命中
    ``True is not False`` → 抛「已经是停用状态」，于是**停用功能从来没成功过**
    （与 ``UserService.disable_user`` 同一个坑，那里已经踩过一次）。
    """
    style = await _seed_style(db_session)
    headers = await auth_headers(role="super_admin")

    before = style.version
    first = await client.post(
        f"/api/v1/styles/{style.style_no}/disables",
        json={"version": before, "reason": "第一次停用"},
        headers=headers,
    )
    assert first.status_code == 200

    second = await client.post(
        f"/api/v1/styles/{style.style_no}/disables",
        json={"version": before + 1, "reason": "第二次停用"},
        headers=headers,
    )

    # ⚠️ 409：``ILLEGAL_OPERATION``(10008) 映射到 CONFLICT —— 「重复停用」是状态冲突，
    #    不是请求写错了（400）。别按直觉写 400
    assert second.status_code == 409
    assert second.json()["code"] == int(ErrorCode.ILLEGAL_OPERATION)
    assert "已经" in second.json()["message"]


async def test_http_disable_style_requires_permission(client, db_session, auth_headers):
    """⚠️ 停用要 ``base:disable``，**不能**只靠 ``base:update`` 放行。

    款号是档案主体，停用它的影响面（不允许新建裁剪单）比改款名大得多。
    """
    style = await _seed_style(db_session)
    headers = await auth_headers(role="custom", permissions=["base:read", "base:update"])

    response = await client.post(
        f"/api/v1/styles/{style.style_no}/disables",
        json={"version": style.version, "reason": "只有修改权"},
        headers=headers,
    )

    assert response.status_code == 403
    assert response.json()["code"] == int(ErrorCode.PERMISSION_DENIED)
    await db_session.refresh(style)
    assert style.is_active is True


async def test_http_disable_style_rejects_unknown_style(client, auth_headers):
    headers = await auth_headers(role="super_admin")

    response = await client.post(
        "/api/v1/styles/HB-9999-9999/disables",
        json={"version": 1, "reason": "停用一个不存在的款号"},
        headers=headers,
    )

    assert response.status_code == 404
    assert response.json()["code"] == int(ErrorCode.BASE_DATA_NOT_FOUND)


# ------------------------------------------------------------------ 导出


async def test_http_export_styles_requires_two_permissions(client, db_session, auth_headers):
    """⚠️ 需要 ``base:export`` **和** ``system:export:manage`` 两个权限点。

    导出是绕过界面直接拿数据的入口，比界面本身更容易泄露。
    """
    await _seed_style(db_session)
    headers = await auth_headers(role="custom", permissions=("base:read", "base:export"))

    response = await client.get("/api/v1/styles/exports", headers=headers)

    assert response.status_code == 403
    assert response.json()["code"] == int(ErrorCode.PERMISSION_DENIED)
    assert EXPORT_GLOBAL_PERMISSION in response.json()["message"]


async def test_http_export_styles_uses_same_filters_as_list(client, db_session, auth_headers):
    """导出与列表**同一套筛选**（docs/07 §3.2 铁律 3）：行数必须一致。"""
    category = await _seed_category(db_session)
    await _seed_style(db_session, style_no="HB-2026-7001")
    await _seed_style(db_session, style_no="HB-2026-7002")
    await _seed_style(db_session, style_no="HB-2026-7003", is_active=False)
    headers = await auth_headers(role="super_admin")

    listed = await client.get(
        "/api/v1/styles", params={"category_id": str(category.id)}, headers=headers
    )
    exported = await client.get(
        "/api/v1/styles/exports", params={"category_id": str(category.id)}, headers=headers
    )

    assert exported.status_code == 200
    assert exported.headers["X-Row-Count"] == str(listed.json()["data"]["total"])
    assert exported.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument"
    )
    assert "styles-" in exported.headers["content-disposition"]


async def test_http_export_styles_includes_inactive_unless_filtered(
    client, db_session, auth_headers
):
    """⚠️ **默认连停用的款号一起导出**（is_active 不传 = 不筛）。

    停用是「不再用于新单据」，历史款号对账时**恰恰要看** —— 默认藏起来的话，
    导出结果与业务事实不符，而且没有任何提示。
    """
    await _seed_style(db_session, style_no="HB-2026-7011", is_active=False)
    headers = await auth_headers(role="super_admin")

    exported = await client.get("/api/v1/styles/exports", headers=headers)
    assert exported.headers["X-Row-Count"] == "1"

    only_active = await client.get(
        "/api/v1/styles/exports", params={"is_active": True}, headers=headers
    )
    assert only_active.headers["X-Row-Count"] == "0"


async def test_http_export_styles_respects_data_scope(client, db_session, auth_headers):
    """⚠️ 跟单（SELF）导出**也只有本人款号** —— 导出比界面更容易泄露。"""
    other = await _seed_user(db_session, employee_no="E7002")
    await _seed_style(db_session, style_no="HB-2026-7021", merchandiser_id=other.id)
    # ⚠️ 内置 ``merchandiser`` 角色**没有**导出权限，所以这里自建角色：既要
    #    ``base:export``，数据范围又要 SELF —— 否则测到的会是 403 而不是范围隔离
    headers = await auth_headers(
        role="custom",
        permissions=("base:read", "base:export", EXPORT_GLOBAL_PERMISSION),
        data_scope=DataScope.SELF,
    )

    listed = await client.get("/api/v1/styles", headers=headers)
    response = await client.get("/api/v1/styles/exports", headers=headers)

    assert listed.status_code == 200
    assert listed.json()["data"]["total"] == 0, "前提：列表本来就看不到别人的款号"
    assert response.status_code == 200
    assert response.headers["X-Row-Count"] == "0", "⚠️ 导出必须与列表同范围"


# ------------------------------------------------------------------ 详情字段


async def test_style_list_row_carries_customer_style_no_and_bulk_qty(
    client, db_session, auth_headers
):
    """⚠️ 列表/详情行必须带 ``customer_style_no`` 与 ``bulk_qty``。

    这两个不是「列表页装饰」：客户货号印在唛头上，大货数量是出布头的前提。
    缺了它们，详情页的编辑表单**无法回显**，界面上只能写「详情接口未返回」——
    那把「接口少返字段」误报成了「功能没做」，用户会去问业务而不是问接口。
    """
    customer = await _seed_customer(db_session)
    await _seed_style(
        db_session,
        style_no="HB-2026-7031",
        customer_id=customer.id,
        customer_style_no="PO-8899",
        bulk_qty=1200,
    )
    headers = await auth_headers(role="super_admin")

    listed = await client.get("/api/v1/styles", headers=headers)
    row = next(
        item for item in listed.json()["data"]["items"] if item["style_no"] == "HB-2026-7031"
    )
    assert row["customer_style_no"] == "PO-8899"
    assert row["bulk_qty"] == 1200

    detail = await client.get("/api/v1/styles/HB-2026-7031", headers=headers)
    assert detail.json()["data"]["style"]["customer_style_no"] == "PO-8899"
    assert detail.json()["data"]["style"]["bulk_qty"] == 1200


async def test_style_list_row_carries_merchandiser_name(client, db_session, auth_headers):
    """跟单员要显示**姓名**而不是 UUID —— 列表页没人会去逐个点开看。"""
    user = await _seed_user(db_session, employee_no="E7003")
    await _seed_style(db_session, style_no="HB-2026-7041", merchandiser_id=user.id)
    headers = await auth_headers(role="super_admin")

    listed = await client.get("/api/v1/styles", headers=headers)
    row = next(
        item for item in listed.json()["data"]["items"] if item["style_no"] == "HB-2026-7041"
    )

    assert row["merchandiser_id"] == str(user.id)
    assert row["merchandiser_name"] == "李跟单"


async def test_style_list_row_survives_null_merchandiser(client, db_session, auth_headers):
    """⚠️ 没指定跟单员时 LEFT JOIN 出 ``NULL`` —— 不能 500。

    ``merchandiser_id`` 可空（跟单归属是可选的），所以必须 ``outerjoin``；
    写成 ``join`` 的话，未指定跟单的款号在列表里**直接消失** —— 用户会以为
    款号被删了。
    """
    await _seed_style(db_session, style_no="HB-2026-7051", merchandiser_id=None)
    headers = await auth_headers(role="super_admin")

    response = await client.get("/api/v1/styles", params={"q": "HB-2026-7051"}, headers=headers)

    assert response.status_code == 200
    row = response.json()["data"]["items"][0]
    assert row["merchandiser_id"] is None
    assert row["merchandiser_name"] is None


# ------------------------------------------------------------------ 路由顺序


async def test_styles_exports_route_is_not_shadowed_by_style_no_path(client, auth_headers):
    """⚠️ ``/styles/exports`` 必须注册在 ``/styles/{style_no}`` **之前**。

    排在后面的话 FastAPI 会把 ``exports`` 当成款号去查 → 404，
    表现是「导出按钮点了报 404」，而根因只是注册顺序（T-BASE-002 已踩过同一个坑）。
    """
    headers = await auth_headers(role="super_admin")

    response = await client.get("/api/v1/styles/exports", headers=headers)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument"
    )


def _session(client):
    """从 ASGI client 里拿测试会话（client fixture 已把 db 绑好）。"""
    return client.app.state.db_session
