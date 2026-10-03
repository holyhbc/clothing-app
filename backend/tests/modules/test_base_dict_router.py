"""基础资料接口测试（docs/10 §4「接口必测」）。

重点是**权限矩阵**（§4.4：三个资源的写权限点不同）与**导出**（§4.4：需要两个
权限点同时具备）。这两处最容易出现"某个资源忘了挂权限检查"—— 而那种漏洞在
功能测试里完全看不出来，因为测试用的都是 super_admin。
"""

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy import select as sa_select

from app.common.enums import DataScope, SizeClass
from app.core.errors import BusinessError, ErrorCode
from app.core.permissions import AuthContext
from app.core.scope import SCOPE_SPECS, ScopeSpec, apply_data_scope, visible_workshops
from app.modules.base.models import Color, Size, SizeGroup, SizeGroupItem, Workshop
from app.modules.base.repository import DictRepository, ListQuery
from app.modules.base.resources import RESOURCES, RESOURCES_BY_KEY
from tests.factories.user import OPERATOR_ID


def _factory_ctx(
    *,
    data_scope: DataScope = DataScope.FACTORY,
    workshop_id=None,
    allowed: frozenset = frozenset(),
) -> AuthContext:
    """数据范围测试用的全厂上下文（permissions 空，不做鉴权）。"""
    return AuthContext(
        user_id=uuid4(),
        name="t",
        employee_no="T",
        workshop_id=workshop_id,
        group_no=None,
        permissions=frozenset(),
        data_scope=data_scope,
        allowed_workshop_ids=allowed,
    )


async def _seed_color(session, code: str) -> None:
    session.add(
        Color(color_code=code, name=f"色{code}", created_by=OPERATOR_ID, updated_by=OPERATOR_ID)
    )
    await session.flush()


# ------------------------------------------------------------------ 列表


async def test_list_returns_page_envelope(client, db_session, auth_headers):
    await _seed_color(db_session, "LQ001")
    headers = await auth_headers(role="super_admin")
    response = await client.get("/api/v1/colors", headers=headers)
    body = response.json()
    assert body["code"] == 0
    assert set(body["data"]) == {"items", "total", "page", "page_size"}
    assert body["data"]["page"] == 1
    assert "LQ001" in {item["color_code"] for item in body["data"]["items"]}


async def test_list_unknown_resource_returns_404(client, auth_headers):
    """未注册的资源 → 404，不是 500（那是路由配错）。"""
    headers = await auth_headers(role="super_admin")
    response = await client.get("/api/v1/not-a-resource", headers=headers)
    assert response.status_code == 404


async def test_list_requires_token(client):
    assert (await client.get("/api/v1/colors")).status_code == 401


async def test_list_denied_without_permission(client, auth_headers):
    """无 ``base:read`` → 12001。"""
    headers = await auth_headers(role="custom", permissions=("system:log:view",))
    response = await client.get("/api/v1/colors", headers=headers)
    assert response.status_code == 403
    assert response.json()["code"] == 12001


# ------------------------------------------------------------------ 新建


async def test_create_color_success(client, db_session, auth_headers):
    headers = await auth_headers(role="super_admin")
    response = await client.post(
        "/api/v1/colors", json={"color_code": "NEW1", "name": "新色"}, headers=headers
    )
    assert response.status_code == 201
    assert response.json()["data"]["color_code"] == "NEW1"


async def test_create_rejects_unknown_field(client, auth_headers):
    """``extra="forbid"``：多余字段必须被拒，而不是静默忽略。

    静默忽略的前端 bug 特别难查：接口返回 201，界面以为存上了，实际没存。
    """
    headers = await auth_headers(role="super_admin")
    response = await client.post(
        "/api/v1/colors",
        json={"color_code": "NEW2", "name": "新色", "color_nmae": "拼错了"},
        headers=headers,
    )
    assert response.status_code == 422
    assert response.json()["code"] == 10001


async def test_create_missing_required_field_raises_10001(client, auth_headers):
    headers = await auth_headers(role="super_admin")
    response = await client.post("/api/v1/colors", json={"name": "缺编码"}, headers=headers)
    assert response.json()["code"] == 10001


async def test_operations_require_dedicated_permission(client, auth_headers):
    """§4.4：工序写权限是 ``base:operation:manage``，**不是** ``base:create``。

    给了 ``base:create`` 但没给工序专用权限 → 必须拒绝。
    """
    headers = await auth_headers(role="custom", permissions=("base:create", "base:read"))
    response = await client.post(
        "/api/v1/operations", json={"operation_no": "01", "name": "裁"}, headers=headers
    )
    assert response.status_code == 403
    assert response.json()["details"]["required_permission"] == "base:operation:manage"


async def test_operations_allowed_with_dedicated_permission(client, auth_headers):
    headers = await auth_headers(role="custom", permissions=("base:read", "base:operation:manage"))
    response = await client.post(
        "/api/v1/operations", json={"operation_no": "77", "name": "查勘"}, headers=headers
    )
    assert response.status_code == 201


async def test_product_categories_require_dedicated_permission(client, auth_headers):
    """§4.4：商品分类写权限是 ``base:category:manage``。"""
    headers = await auth_headers(role="custom", permissions=("base:create",))
    response = await client.post(
        "/api/v1/product-categories", json={"code": "SET", "name": "套装"}, headers=headers
    )
    assert response.status_code == 403
    assert response.json()["details"]["required_permission"] == "base:category:manage"


# ------------------------------------------------------------------ 停用


async def test_disable_without_reason_raises_10001(client, db_session, auth_headers):
    await _seed_color(db_session, "DSQ1")
    headers = await auth_headers(role="super_admin")
    response = await client.post("/api/v1/colors/DSQ1/disables", json={}, headers=headers)
    assert response.status_code == 422  # 10001 → 422（docs/05 §4）
    assert response.json()["code"] == 10001


async def test_disable_with_reason_success(client, db_session, auth_headers):
    await _seed_color(db_session, "DSQ2")
    headers = await auth_headers(role="super_admin")
    response = await client.post(
        "/api/v1/colors/DSQ2/disables", json={"reason": "停产"}, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["data"]["reason"] == "停产"


# ------------------------------------------------------------------ 删除


async def test_delete_referenced_returns_20003(client, db_session, auth_headers):
    """TC-B03 分支二走 HTTP：被码表引用的尺码删不掉。"""
    size = Size(
        size_code="HTTPL",
        name="HTTPL",
        size_class=SizeClass.MENS,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    group = SizeGroup(
        name="HTTP码表",
        size_class=SizeClass.MENS,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    db_session.add_all([size, group])
    await db_session.flush()
    db_session.add(SizeGroupItem(size_group_id=group.id, size_id=size.id, sort_order=1))
    await db_session.flush()

    headers = await auth_headers(role="super_admin")
    response = await client.delete("/api/v1/sizes/HTTPL", headers=headers)
    assert response.status_code == 409  # 20003 → 409（docs/05 §4，由 test_errors_registry 守卫）
    body = response.json()
    assert body["code"] == 20003
    assert body["details"]["ref_count"] == 1


async def test_delete_color_requires_delete_permission(client, db_session, auth_headers):
    await _seed_color(db_session, "DEL1")
    headers = await auth_headers(role="custom", permissions=("base:read",))
    response = await client.delete("/api/v1/colors/DEL1", headers=headers)
    assert response.status_code == 403
    assert response.json()["code"] == 12001


# ------------------------------------------------------------------ 候选


async def test_options_size_over_20_returns_10001(client, auth_headers):
    """TC-B27：候选接口强制 size ≤ 20。"""
    headers = await auth_headers(role="super_admin")
    response = await client.get("/api/v1/colors/options?size=21", headers=headers)
    assert response.json()["code"] == 10001


async def test_options_offset_over_10000_returns_10001(client, auth_headers):
    """TC-B28：深分页直接拒绝。"""
    headers = await auth_headers(role="super_admin")
    response = await client.get("/api/v1/colors/options?offset=10001", headers=headers)
    assert response.json()["code"] == 10001


async def test_options_returns_value_label_disabled(client, db_session, auth_headers):
    """docs/05 §9.5.2：候选返回 ``{value,label,sub,disabled}``。"""
    await _seed_color(db_session, "OPTX")
    headers = await auth_headers(role="super_admin")
    response = await client.get("/api/v1/colors/options?q=OPTX", headers=headers)
    items = response.json()["data"]
    assert set(items[0]) == {"value", "label", "sub", "disabled"}


# ------------------------------------------------------------------ 导出


async def test_export_denied_without_global_switch(client, db_session, auth_headers):
    """§4.4：有 ``base:export`` 但没有 ``system:export:manage`` → 拒绝。"""
    await _seed_color(db_session, "EXP1")
    headers = await auth_headers(role="custom", permissions=("base:read", "base:export"))
    denied = await client.get("/api/v1/colors/exports", headers=headers)
    assert denied.status_code == 403
    assert "system:export:manage" in denied.json()["message"]


async def test_export_denied_without_resource_export_permission(client, db_session, auth_headers):
    """§4.4：有全局开关但没有 ``base:export`` → 同样拒绝（两个条件缺一不可）。"""
    await _seed_color(db_session, "EXP1B")
    headers = await auth_headers(role="custom", permissions=("base:read", "system:export:manage"))
    denied = await client.get("/api/v1/colors/exports", headers=headers)
    assert denied.status_code == 403
    assert "base:export" in denied.json()["message"]


async def test_export_returns_xlsx(client, db_session, auth_headers):
    import io

    import openpyxl

    await _seed_color(db_session, "EXP2")
    headers = await auth_headers(
        role="custom", permissions=("base:read", "base:export", "system:export:manage")
    )
    response = await client.get("/api/v1/colors/exports", headers=headers)
    assert response.status_code == 200
    assert "spreadsheetml" in response.headers["content-type"]
    # 内置色卡有 16 条 + 本用例新增 1 条
    assert int(response.headers["x-row-count"]) >= 1

    sheet = openpyxl.load_workbook(io.BytesIO(response.content)).active
    assert [cell.value for cell in sheet[1]][:2] == ["色码", "色名"]
    assert sheet.freeze_panes == "A2"
    assert sheet.auto_filter.ref is not None
    assert "EXP2" in [row[0] for row in sheet.iter_rows(min_row=2, values_only=True)]


async def test_export_row_count_matches_list_total(client, db_session, auth_headers):
    """TC-B30：同筛选下，导出行数 == 列表 ``total``。"""
    for index in range(3):
        await _seed_color(db_session, f"CNT{index}")
    headers = await auth_headers(
        role="custom", permissions=("base:read", "base:export", "system:export:manage")
    )
    listing = await client.get("/api/v1/colors?q=CNT", headers=headers)
    exported = await client.get("/api/v1/colors/exports?q=CNT", headers=headers)
    assert exported.headers["x-row-count"] == str(listing.json()["data"]["total"])


async def test_export_filename_has_timestamp(client, db_session, auth_headers):
    """导出文件名带时间戳，否则连导两次会覆盖。"""
    await _seed_color(db_session, "FN1")
    headers = await auth_headers(
        role="custom", permissions=("base:read", "base:export", "system:export:manage")
    )
    response = await client.get("/api/v1/colors/exports", headers=headers)
    assert 'attachment; filename="colors-' in response.headers["content-disposition"]


# ------------------------------------------------------------------ 排序校验


async def test_list_bad_sort_by_returns_10001_with_allowed_list(client, auth_headers):
    """TC-B29：非法 sort_by 报 10001 并列出可用字段，绝不拼进 SQL。"""
    headers = await auth_headers(role="super_admin")
    response = await client.get("/api/v1/colors?sort_by=1;DROP+TABLE+colors", headers=headers)
    assert response.json()["code"] == 10001
    assert "color_code" in response.json()["details"]["allowed"]


# ------------------------------------------------------------------ trgm


async def test_candidate_search_uses_trgm_index(db_session):
    """docs/04 §5.1：候选搜索必须能走 GIN 三元组索引。

    ⚠️ 断言方式：**不能**直接断言默认 planner 会选 Bitmap Index Scan ——
    PG 在小表上会合理地选 Seq Scan（实测 5 万行/100 命中时默认 Seq Scan 42ms，
    强制关掉 seqscan 后 Bitmap Index Scan 2.4ms）。所以这里断言的是
    "索引表达式与查询谓词一致"，那才是索引能被用上的**真正**前提。
    """
    ctx = _factory_ctx()
    for key in ("colors", "sizes", "operations"):
        repo = DictRepository(db_session, RESOURCES_BY_KEY[key])
        stmt = repo._apply_filters(repo._base_stmt(ctx), ListQuery(q="ZZZ"))
        compiled = str(
            stmt.compile(
                dialect=db_session.bind.dialect,
                compile_kwargs={"literal_binds": True},
            )
        )
        expression = RESOURCES_BY_KEY[key].trgm_expression
        assert expression is not None, key
        assert expression in compiled, f"{key} 的候选谓词没用 trgm 表达式：{compiled}"


async def test_tables_without_trgm_fall_back_to_or(db_session):
    """没有 trgm 索引的资源（车间）走两列 OR，不影响正确性。"""
    ctx = _factory_ctx()
    repo = DictRepository(db_session, RESOURCES_BY_KEY["workshops"])
    stmt = repo._apply_filters(repo._base_stmt(ctx), ListQuery(q="ZZZ"))
    compiled = str(
        stmt.compile(dialect=db_session.bind.dialect, compile_kwargs={"literal_binds": True})
    )
    assert " OR " in compiled
    assert RESOURCES_BY_KEY["workshops"].trgm_expression is None


def test_every_resource_declares_write_models():
    """每个注册的资源都必须有对应的写入模型 —— 否则请求体无从校验。"""
    from app.modules.base.schemas import WRITE_MODELS

    assert set(WRITE_MODELS) == {item.key for item in RESOURCES}


def test_every_resource_declares_export_columns():
    from app.modules.base.router import _EXPORT_COLUMN_MAP

    assert set(_EXPORT_COLUMN_MAP) == {item.key for item in RESOURCES}


def test_resources_with_physical_delete_have_ref_checkers_or_are_declared():
    """允许真删的资源，要么配了引用检查器，要么在**显式清单**里登记待补。

    没有引用检查器的真删表 = 删掉还在被引用的行 = 悬空引用。但引用方
    （``styles`` / ``style_colors`` / ``materials`` …）要到 T-BASE-002 与后续模块
    才建，现在**确实**一个都还没有。所以用显式清单记录"谁负责补"，而不是让守卫
    静默放过 —— 否则等单据表建好、没人回头补检查器，悬空引用就已经能发生了。
    """
    pending = {
        "colors": "T-BASE-002 建 styles/style_colors 后补",
        "size-groups": "码表的引用方是款号尺码集合，T-BASE-002 补",
    }
    for item in RESOURCES:
        if item.allow_physical_delete:
            assert item.ref_checkers or item.key in pending, (
                f"{item.key} 允许真删但既没有引用检查器也没登记待补"
            )


def test_no_physical_delete_resource_relies_on_db_revoke():
    """纯软删表不靠应用层自觉，而是靠数据库没给 DELETE 权限（ADR-0025）。"""
    soft = [item.key for item in RESOURCES if not item.allow_physical_delete]
    assert set(soft) == {
        "workshops",
        "workshop-groups",
        "warehouses",
        "uom-units",
        "product-categories",
        "operations",
        # 组 D（T-BASE-002）：客户被 styles / 销售 / 应收引用，真删会造成悬空引用
        "customers",
    }


def test_visible_workshops_includes_own_workshop():
    """``visible_workshops`` = 角色授权车间与本人所属车间的并集。"""
    own = uuid4()
    granted = uuid4()
    ctx = _factory_ctx(
        data_scope=DataScope.WORKSHOP,
        workshop_id=own,
        allowed=frozenset({granted}),
    )
    assert visible_workshops(ctx) == frozenset({own, granted})


def test_scope_specs_has_workshop_groups_registered():
    """车间组别必须按车间过滤（守卫抓出来的回归）。"""
    spec = SCOPE_SPECS.get("workshop_groups")
    assert spec is not None
    assert spec.workshop_column == "workshop_id"


def test_operations_scope_includes_null_workshop():
    """工序的 ``include_null_workshop=True``：通用工序对每个车间可见。"""
    spec = SCOPE_SPECS.get("operations")
    assert spec is not None
    assert spec.include_null_workshop is True


def test_users_scope_excludes_null_workshop():
    """``users`` 的车间为空表示"非车间人员"，不能当通用行放行。"""
    spec = SCOPE_SPECS.get("users")
    assert spec is not None
    assert spec.include_null_workshop is False


def test_apply_data_scope_accepts_explicit_spec():
    """显式传 spec 时不查表名 —— 新资源可以先不登记就试用。"""
    ctx = _factory_ctx(data_scope=DataScope.WORKSHOP)
    stmt = apply_data_scope(
        sa_select(Workshop), Workshop, ctx, spec=ScopeSpec(workshop_column=None)
    )
    assert "false" in str(stmt).lower()


def test_deleted_at_filter_is_appended_for_soft_delete_models(db_session):
    ctx = _factory_ctx()
    compiled = str(
        apply_data_scope(select(Color), Color, ctx).compile(
            dialect=db_session.bind.dialect, compile_kwargs={"literal_binds": True}
        )
    )
    assert "deleted_at IS NULL" in compiled


def test_business_error_details_are_structured():
    """错误码与 details 结构固定（docs/05 §4）。"""
    error = BusinessError(ErrorCode.BASE_DATA_REFERENCED, "被引用", details={"ref_count": 2})
    assert error.details["ref_count"] == 2
    assert error.http_status == 409  # 20003 → 409（docs/05 §4）
