"""基座内部件测试：配置、数据库、枚举、公共模型辅助方法、异常子类、中间件边界。

这些路径在 T-INFRA-003 里属于"没有被 HTTP 层覆盖但必须正确"的代码
（docs/10 §8：新增代码行覆盖率必须达标，不允许留没测的分支）。
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.common.enums import (
    AuthChannel,
    DataScope,
    DocumentAction,
    DocumentStatus,
    ExternalIdentityProvider,
    SizeClass,
)
from app.common.models import BaseModel
from app.core.config import Settings, get_settings, require_database_url
from app.core.db import (
    create_db_engine,
    dispose_engine,
    get_engine,
    get_session_factory,
)
from app.core.errors import (
    BusinessError,
    ErrorCode,
    NotFoundError,
    OptimisticLockError,
    PermissionDeniedError,
    ReasonRequiredError,
)
from app.core.middleware import _inject_into_json_body, new_request_id

# ---------------------------------------------------------------------------
# 枚举（app/common/enums.py）
# ---------------------------------------------------------------------------


def test_enum_values_are_stable_strings() -> None:
    """枚举值即入库字符串，禁止随手改名（docs/04 §3：enum 只能追加不能改）。"""
    assert DocumentStatus.DRAFT == "DRAFT"
    assert {s.value for s in DataScope} == {"SELF", "GROUP", "WORKSHOP", "FACTORY"}
    assert {s.value for s in SizeClass} == {"MENS", "WOMENS", "KIDS"}
    assert {c.value for c in AuthChannel} >= {"PC", "H5_SMS"}
    assert {p.value for p in ExternalIdentityProvider} >= {"wecom", "wechat_mp"}
    assert DocumentAction.RESTORE == "RESTORE"


def test_size_class_has_no_chinese_value() -> None:
    """枚举值必须能直接入库与传输，不允许中文值。"""
    for enum_cls in (DocumentStatus, DataScope, SizeClass, DocumentAction):
        for member in enum_cls:
            assert member.value.isascii(), f"{enum_cls.__name__}.{member.name} 的值含非 ASCII 字符"


# ---------------------------------------------------------------------------
# 公共模型辅助方法（app/common/models.py）
# ---------------------------------------------------------------------------


def test_as_dict_for_log_excludes_bookkeeping_columns() -> None:
    """变更快照排除 updated_*/version，否则每次 diff 都会被这些字段污染。

    依据 docs/04 §7.9：``document_logs.changed_fields`` 记录的是字段级 diff。
    """

    class _ProbeSnapshot(BaseModel):
        __tablename__ = "_probe_snapshot"

    probe = _ProbeSnapshot(remark="备注")
    snapshot = probe.as_dict_for_log()
    for excluded in ("updated_at", "updated_by", "version"):
        assert excluded not in snapshot
    assert "remark" in snapshot
    assert "created_at" in snapshot


# ---------------------------------------------------------------------------
# 配置（app/core/config.py）
# ---------------------------------------------------------------------------


def test_settings_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """配置只从环境变量读（docs/11 §2）。"""
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "30")
    get_settings.cache_clear()
    settings = Settings()
    assert settings.jwt_expire_minutes == 30
    assert settings.is_production is False


def test_production_requires_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """生产环境缺 JWT_SECRET / DATABASE_URL 必须启动即失败，不许用假密钥。"""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET", "")
    monkeypatch.setenv("DATABASE_URL", "")
    get_settings.cache_clear()
    with pytest.raises(ValueError, match="JWT_SECRET"):
        Settings()

    monkeypatch.setenv("JWT_SECRET", "x" * 64)
    with pytest.raises(ValueError, match="DATABASE_URL"):
        Settings()

    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://erp_app:pw@postgres:5432/garment_erp")
    settings = Settings()
    assert settings.is_production is True


def test_non_production_allows_empty_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """非生产允许空密钥：便于只跑 /healthz 与纯单元测试。"""
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("JWT_SECRET", "")
    monkeypatch.setenv("DATABASE_URL", "")
    get_settings.cache_clear()
    settings = Settings()
    assert settings.jwt_secret.get_secret_value() == ""


def test_require_database_url_raises_business_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """缺连接串时抛业务异常而不是返回空串（避免下游拿到空 URL 去连库）。"""
    monkeypatch.setenv("DATABASE_URL", "")
    get_settings.cache_clear()
    with pytest.raises(BusinessError) as exc:
        require_database_url()
    assert exc.value.code == ErrorCode.INTERNAL
    assert "DATABASE_URL" in exc.value.message


def test_require_database_url_returns_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """正常路径返回连接串原文。"""
    url = "postgresql+asyncpg://erp_app:pw@postgres:5432/garment_erp"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    assert require_database_url() == url


def test_secrets_are_not_leaked_by_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    """密钥字段用 SecretStr，repr 里不得出现明文（docs/11 §8.1）。"""
    monkeypatch.setenv("JWT_SECRET", "super-secret-value")
    settings = Settings()
    assert "super-secret-value" not in repr(settings)
    assert settings.jwt_secret.get_secret_value() == "super-secret-value"


# ---------------------------------------------------------------------------
# 数据库（app/core/db.py）—— 只验证构造，不连库
# ---------------------------------------------------------------------------


def test_create_db_engine_requires_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """没配连接串就建 engine 必须立即报错，而不是等到第一次查询。"""
    monkeypatch.setenv("DATABASE_URL", "")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        create_db_engine()


def test_create_db_engine_is_lazy(monkeypatch: pytest.MonkeyPatch) -> None:
    """建 engine 不发起连接（否则 /healthz 会被数据库拖死）。"""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://erp_app:pw@localhost:5432/nodb")
    get_settings.cache_clear()
    engine = create_db_engine()
    assert isinstance(engine, AsyncEngine)
    assert engine.url.database == "nodb"


async def test_engine_and_session_factory_are_singletons(monkeypatch: pytest.MonkeyPatch) -> None:
    """engine / session factory 进程内单例，避免每个请求新建连接池。"""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://erp_app:pw@localhost:5432/nodb")
    get_settings.cache_clear()
    await dispose_engine()

    first = get_engine()
    second = get_engine()
    assert first is second

    factory = get_session_factory()
    assert isinstance(factory, async_sessionmaker)
    assert get_session_factory() is factory


async def test_get_db_yields_session_and_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_db 每请求一个 session，退出时关闭（不 commit、不开事务）。"""
    from app.core.db import get_db

    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://erp_app:pw@localhost:5432/nodb")
    get_settings.cache_clear()
    await dispose_engine()

    generator = get_db()
    session = await anext(generator)
    assert isinstance(session, AsyncSession)
    with pytest.raises(StopAsyncIteration):
        await anext(generator)


async def test_dispose_engine_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """重复关闭不报错（应用关闭路径可能被调用两次）。"""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://erp_app:pw@localhost:5432/nodb")
    get_settings.cache_clear()
    await dispose_engine()
    await dispose_engine()
    assert get_engine() is not None  # 关闭后应可重建


# ---------------------------------------------------------------------------
# 异常子类（app/core/errors.py）
# ---------------------------------------------------------------------------


def test_not_found_error_uses_20001() -> None:
    err = NotFoundError("颜色 WHT 不存在")
    assert err.code == ErrorCode.BASE_DATA_NOT_FOUND
    assert err.http_status == 404


def test_permission_denied_error_distinguishes_scope() -> None:
    """12001（无操作权限）与 12002（无数据权限）必须可区分，前端文案不同。"""
    no_op = PermissionDeniedError("无权限执行该操作：base:update")
    no_scope = PermissionDeniedError("超出数据范围", scope_denied=True)
    assert no_op.code == ErrorCode.PERMISSION_DENIED
    assert no_scope.code == ErrorCode.DATA_SCOPE_DENIED
    assert no_op.http_status == no_scope.http_status == 403


def test_optimistic_lock_and_reason_errors() -> None:
    assert OptimisticLockError().code == ErrorCode.OPTIMISTIC_LOCK_CONFLICT
    assert OptimisticLockError().http_status == 409
    assert ReasonRequiredError().code == ErrorCode.REASON_REQUIRED
    assert ReasonRequiredError().http_status == 422


def test_business_error_accepts_custom_message() -> None:
    err = BusinessError(ErrorCode.ILLEGAL_OPERATION, "已审核后不可提交")
    assert err.message == "已审核后不可提交"
    assert err.details == {}


# ---------------------------------------------------------------------------
# 中间件边界（app/core/middleware.py）
# ---------------------------------------------------------------------------


def _body_message(body: bytes, content_type: str = "application/json") -> dict:
    return {
        "type": "http.response.body",
        "body": body,
        "more_body": False,
        "headers": [(b"content-type", content_type.encode())],
    }


def test_inject_skips_non_json_content_type() -> None:
    """非 JSON 响应（如 HTML、SSE）一律不动，避免破坏流式输出。"""
    original = _body_message(b"<html></html>", "text/html")
    assert _inject_into_json_body(original, "01ABC") is original


def test_inject_skips_payload_without_code_key() -> None:
    """没有 code 键的 JSON 不是统一响应体，不注入。"""
    original = _body_message(b'{"foo":1}')
    assert _inject_into_json_body(original, "01ABC") is original


def test_inject_skips_invalid_json() -> None:
    """坏 JSON 直接放行，不能让中间件把 500 变成 500 的 500。"""
    original = _body_message(b"{not json")
    assert _inject_into_json_body(original, "01ABC") is original


def test_inject_skips_empty_body() -> None:
    """空 body（204 / 304）不注入。"""
    original = _body_message(b"")
    assert _inject_into_json_body(original, "01ABC") is original


def test_inject_adds_request_id_to_unified_response() -> None:
    """统一响应体（含 code 键）会被写入 request_id。"""
    import json

    message = _inject_into_json_body(_body_message(b'{"code":0,"data":null}'), "01ABC")
    payload = json.loads(message["body"])
    assert payload["request_id"] == "01ABC"
    assert payload["code"] == 0


async def test_middleware_passes_through_non_http_scope() -> None:
    """lifespan 等非 http 作用域直接透传，不生成 request_id。"""
    from app.core.middleware import RequestIdMiddleware

    seen: list[str] = []

    async def inner(scope: object, receive: object, send: object) -> None:
        seen.append("called")

    async def sender(message: object) -> None:
        del message

    middleware = RequestIdMiddleware(inner)  # type: ignore[arg-type]
    await middleware({"type": "lifespan"}, None, sender)  # type: ignore[arg-type]
    assert seen == ["called"]


def test_request_id_never_contains_confusable_chars() -> None:
    """ULID 字符集已剔除易混淆字符（I/L/O/U），便于人工抄写。"""
    for _ in range(20):
        rid = new_request_id()
        assert not ({"I", "L", "O", "U"} & set(rid))
