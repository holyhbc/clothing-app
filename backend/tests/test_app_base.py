"""应用基座行为测试（TC-I01 / I03 / I05 / I09）。

覆盖：统一响应包装、request_id 注入与一致性、异常处理器、/healthz 与 /readyz、
公共字段（docs/04 §2）、工厂函数无状态污染（docs/10 §2.1 用例零依赖）。
"""

import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict, Field
from sqlalchemy import CheckConstraint

from app.common.models import BaseModel
from app.core.errors import ErrorCode
from app.core.middleware import new_request_id
from app.core.responses import ApiResponse, PageData, ok, page_ok
from app.main import create_app

DOC_04 = Path(__file__).resolve().parents[2] / "docs" / "04-数据库规范.md"

ULID_PATTERN = re.compile(r"[0-9A-HJKMNP-TV-Z]{26}")


# ---------------------------------------------------------------------------
# TC-I03：统一响应包装（docs/05 §3）
# ---------------------------------------------------------------------------


def test_ok_shape_matches_spec() -> None:
    """成功响应 = {code: 0, message: "ok", data: ...}。"""
    assert ok({"id": "x"}) == {"code": 0, "message": "ok", "data": {"id": "x"}}
    assert ok()["data"] is None


def test_page_ok_shape_matches_spec() -> None:
    """列表响应 = {items, total, page, page_size}。"""
    payload = page_ok([{"a": 1}], total=1, page=1, page_size=20)
    assert payload["data"] == {"items": [{"a": 1}], "total": 1, "page": 1, "page_size": 20}


def test_page_data_bounds() -> None:
    """page 从 1 起、page_size 上限 200（docs/05 §2）。"""
    assert PageData[int](items=[1, 2], total=2, page=1, page_size=20).total == 2
    with pytest.raises(ValueError):
        PageData[int](items=[], total=0, page=0, page_size=20)
    with pytest.raises(ValueError):
        PageData[int](items=[], total=0, page=1, page_size=201)


def test_api_response_defaults() -> None:
    """ApiResponse 默认 code=0 / message=ok / data=None。"""
    resp = ApiResponse[dict[str, str]]()
    assert (resp.code, resp.message, resp.data) == (0, "ok", None)


# ---------------------------------------------------------------------------
# TC-I05：request_id（ULID）
# ---------------------------------------------------------------------------


def test_new_request_id_is_26_char_ulid() -> None:
    """26 位 Crockford Base32：够短、可排序、不含易混淆字符。"""
    rid = new_request_id()
    assert len(rid) == 26
    assert ULID_PATTERN.fullmatch(rid), f"含非 ULID 字符：{rid}"


def test_new_request_id_is_monotonic_within_millisecond() -> None:
    """同毫秒内连续生成必须严格递增，保证字典序 = 生成顺序。"""
    ids = [new_request_id() for _ in range(50)]
    assert len(set(ids)) == 50, "ULID 出现碰撞"
    assert ids == sorted(ids), "ULID 非单调，无法按 ID 定位先后"


async def test_response_carries_request_id_header(client: AsyncClient) -> None:
    """每个响应都带 X-Request-ID 头（docs/05 §3）。"""
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert ULID_PATTERN.fullmatch(resp.headers["X-Request-ID"])


async def test_request_id_is_unique_per_request(client: AsyncClient) -> None:
    """并发排障前提：每个请求独立 ID。"""
    first = await client.get("/healthz")
    second = await client.get("/healthz")
    assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]


# ---------------------------------------------------------------------------
# 健康检查与占位端点
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/healthz", "/api/v1/healthz"])
async def test_healthz_reports_alive(client: AsyncClient, path: str) -> None:
    """存活探针两个路径都可用：根路径（docs/05 §1）+ 带前缀别名（docs/11 §6）。"""
    resp = await client.get(path)
    assert resp.status_code == 200
    assert resp.json() == {"status": "alive"}


def _blank_out_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    """把数据库与 Redis 显式置为未配置，并刷新 Settings 单例。

    用 monkeypatch 而不是依赖环境里"恰好没有配置"，否则测试结果会随环境漂移
    （连了库就变绿、不连就变红），违背 docs/10 §2.1 的"用例零依赖"。
    """
    from app.core.config import get_settings

    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("REDIS_URL", "")
    get_settings.cache_clear()


async def test_readyz_reports_not_ready_when_dependencies_unavailable(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """依赖不可用（含"未配置"）时 /readyz 返回 503，绝不谎报 ready。

    理由：进程连不上数据库时任何业务接口都无法服务，此时报 ready 会让编排器
    继续往里打流量。``checks`` 保留具体原因供人区分"未配置"与"连不上"。
    """
    _blank_out_dependencies(monkeypatch)
    resp = await client.get("/readyz")
    assert resp.status_code == 503
    payload = resp.json()
    assert payload["status"] == "not_ready"
    assert payload["checks"] == {"database": "not_configured", "redis": "not_configured"}


async def test_healthz_stays_200_while_dependencies_down(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/healthz 只表示进程活着：依赖全挂时它仍必须 200（docs/05 §1）。

    存活与就绪必须分开，否则数据库抖动会导致容器被判定为死亡并被反复重启。
    """
    _blank_out_dependencies(monkeypatch)
    assert (await client.get("/healthz")).status_code == 200
    assert (await client.get("/readyz")).status_code == 503


async def test_unknown_route_returns_unified_envelope(client: AsyncClient) -> None:
    """未知路由也必须是统一响应体 + 带 request_id（docs/05 §3：所有响应同一形状）。"""
    resp = await client.get("/api/v1/does-not-exist")
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == ErrorCode.ILLEGAL_OPERATION
    assert body["data"] is None
    assert "detail" not in body, "不允许出现 Starlette 默认的 detail 字段形状"
    assert body["request_id"] == resp.headers["X-Request-ID"]


async def test_method_not_allowed_returns_unified_envelope(client: AsyncClient) -> None:
    """方法不允许同样收进统一包装，且保持原 HTTP 状态。"""
    resp = await client.post("/healthz")
    assert resp.status_code == 405
    body = resp.json()
    assert body["code"] == ErrorCode.ILLEGAL_OPERATION
    assert body["details"]["method"] == "POST"
    assert body["request_id"] == resp.headers["X-Request-ID"]


async def test_events_stream_returns_501_with_plan(client: AsyncClient) -> None:
    """未实现端点返回 501 并说明计划阶段，不静默 404（docs/05 §1）。"""
    resp = await client.get("/api/v1/events/stream")
    assert resp.status_code == 501
    body = resp.json()
    assert body["data"] is None
    assert body["details"]["planned_milestone"] == "P2"


# ---------------------------------------------------------------------------
# 异常处理器
# ---------------------------------------------------------------------------


async def test_unhandled_exception_hides_technical_details(app: FastAPI) -> None:
    """兜底异常 → 99999，且**绝不把连接串等内部细节吐给客户端**（docs/06 §5）。"""

    @app.get("/_boom")
    async def boom() -> None:
        raise RuntimeError("连接串 postgres://erp_app:hunter2@db 内部细节泄漏测试")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/_boom")

    assert resp.status_code == 500
    body = resp.json()
    assert body["code"] == ErrorCode.INTERNAL
    assert "hunter2" not in resp.text
    assert "postgres://" not in resp.text
    assert body["request_id"] == resp.headers["X-Request-ID"]


async def test_business_error_handler_shape(app: FastAPI) -> None:
    """业务异常 → 统一响应体 + 映射后的 HTTP 状态 + details 透传。"""
    from app.core.errors import BusinessError

    @app.get("/_biz")
    async def biz() -> None:
        raise BusinessError(
            ErrorCode.BASE_DATA_REFERENCED,
            "该尺码已被 3 处引用，请改用停用",
            {"ref_count": 3, "references": ["style_sizes", "size_group_items"]},
        )

    resp = await app_state_client(app).get("/_biz")
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == ErrorCode.BASE_DATA_REFERENCED
    assert body["message"] == "该尺码已被 3 处引用，请改用停用"
    assert body["details"]["ref_count"] == 3
    assert body["request_id"] == resp.headers["X-Request-ID"]


async def test_validation_error_returns_10001_with_field_details(app: FastAPI) -> None:
    """校验失败 → 10001 + 逐字段明细（前端行内红字的数据源，docs/06 §5）。"""

    class _Probe(PydanticBaseModel):
        model_config = ConfigDict(extra="forbid")
        name: str = Field(min_length=2, description="名称")

    @app.post("/_probe")
    async def probe(payload: _Probe) -> dict[str, str]:
        return {"name": payload.name}

    resp = await app_state_client(app).post("/_probe", json={"name": "a", "unknown": 1})
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == ErrorCode.PARAM_INVALID
    reported = {item["field"] for item in body["details"]["fields"]}
    assert {"name", "unknown"} <= reported, f"未逐项报出错误字段：{reported}"


def app_state_client(app: FastAPI) -> AsyncClient:
    """构造内存客户端（业务异常由处理器兜住，无需 raise_app_exceptions=False）。"""
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ---------------------------------------------------------------------------
# TC-I01：公共字段（docs/04 §2）
# ---------------------------------------------------------------------------

EXPECTED_COMMON_COLUMNS = {
    "id",
    "created_at",
    "created_by",
    "updated_at",
    "updated_by",
    "deleted_at",
    "version",
    "remark",
}


def test_base_model_has_all_common_columns() -> None:
    """BaseModel 子类自带 04 §2 的 8 个公共字段。"""

    class _ProbeColumns(BaseModel):
        __tablename__ = "_probe_common_columns"
        __table_args__ = (CheckConstraint("version > 0", name="ck__probe_version_positive"),)

    assert {col.name for col in _ProbeColumns.__table__.columns} == EXPECTED_COMMON_COLUMNS


def test_base_model_primary_key_is_uuid() -> None:
    """主键必须是单列 uuid（docs/04 §1）。"""

    class _ProbePk(BaseModel):
        __tablename__ = "_probe_pk"

    pk_columns = list(_ProbePk.__table__.primary_key.columns)
    assert [col.name for col in pk_columns] == ["id"]


def test_common_column_nullability_matches_spec() -> None:
    """deleted_at / remark 可空；审计四件非空（docs/04 §2）。"""

    class _ProbeNullability(BaseModel):
        __tablename__ = "_probe_nullability"

    cols = _ProbeNullability.__table__.columns
    assert cols["deleted_at"].nullable is True
    assert cols["remark"].nullable is True
    for name in ("created_at", "created_by", "updated_at", "updated_by", "version"):
        assert cols[name].nullable is False, f"{name} 应为 NOT NULL"


def test_version_server_default_is_one() -> None:
    """version 默认 1。"""

    class _ProbeVersion(BaseModel):
        __tablename__ = "_probe_version"

    server_default = _ProbeVersion.__table__.columns["version"].server_default
    assert server_default is not None
    assert str(server_default.arg) == "1"


def test_docs_04_section2_field_list_is_unchanged() -> None:
    """守卫：docs/04 §2 增删字段时本测试会失败，提醒同步 models.py（docs/12 §9 检查项 1）。"""
    section = DOC_04.read_text(encoding="utf-8").split("## 2. 公共字段")[1].split("\n---")[0]
    sql_block = section.split("```sql")[1].split("```")[0]
    documented = set(re.findall(r"^(\w+)\s+\w+", sql_block, re.MULTILINE))
    documented.discard("CONSTRAINT")
    assert documented == EXPECTED_COMMON_COLUMNS, (
        f"docs/04 §2 公共字段已变为 {sorted(documented)}，必须同步 app/common/models.py 与本测试"
    )


# ---------------------------------------------------------------------------
# TC-I09：工厂函数无状态污染（docs/10 §2.1 用例零依赖、顺序随机仍通过）
# ---------------------------------------------------------------------------


def test_create_app_returns_independent_instances() -> None:
    """连续创建应用不得共享注册状态。"""
    first = create_app()
    second = create_app()
    assert first is not second
    assert len(first.user_middleware) == len(second.user_middleware)
    assert first.openapi()["info"]["title"] == second.openapi()["info"]["title"]
