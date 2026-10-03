"""CLI 入口层测试（``run()`` / ``main()``）。

这些函数负责「建引擎 → 调逻辑 → 打印 → 返回退出码」，用 mock 掉引擎与 stdout
来验证控制流，避免真的起数据库连接。

已覆盖的逻辑层见 ``test_seed_cli.py``；这里只测入口的分支与退出码。
"""

from typing import Any

import pytest

from app.cli import restore_builtin, seed_baseline


class _FakeResult:
    def __init__(self, rowcount: int = 1, rows: tuple = ()) -> None:
        self.rowcount = rowcount
        self._rows = rows

    def scalars(self) -> "_FakeResult":
        return self

    def all(self) -> tuple:
        return self._rows

    def first(self) -> None:
        return None


class _FakeConn:
    """记录执行过的 SQL，按需返回固定结果。"""

    def __init__(self) -> None:
        self.statements: list[str] = []
        self.rowcounts: list[int] = []

    async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> _FakeResult:
        self.statements.append(str(statement))
        return _FakeResult(self.rowcounts.pop() if self.rowcounts else 1)

    async def scalar(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        self.statements.append(str(statement))
        return 0

    async def begin(self) -> Any:
        return self


class _FakeBegin:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *args: Any) -> None:
        return None


class _FakeEngine:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn
        self.disposed = False

    def begin(self) -> _FakeBegin:
        return _FakeBegin(self._conn)

    async def dispose(self) -> None:
        self.disposed = True


@pytest.fixture
def fake_conn() -> _FakeConn:
    return _FakeConn()


@pytest.fixture
def patch_engine(monkeypatch: pytest.MonkeyPatch, fake_conn: _FakeConn) -> _FakeEngine:
    """把 ``create_async_engine`` 换成返回假引擎。"""
    engine = _FakeEngine(fake_conn)

    def _factory(*args: Any, **kwargs: Any) -> _FakeEngine:
        return engine

    monkeypatch.setattr(seed_baseline, "create_async_engine", _factory)
    monkeypatch.setattr(restore_builtin, "create_async_engine", _factory)
    return engine


@pytest.fixture
def with_migration_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL_MIGRATION", "postgresql+asyncpg://erp_ddl:x@localhost:5432/db")


# ---------------------------------------------------------------------------
# seed_baseline.run
# ---------------------------------------------------------------------------


async def test_seed_run_without_url_exits_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """没配置迁移连接串时立即失败并给出可操作提示。"""
    monkeypatch.setenv("DATABASE_URL_MIGRATION", "")
    monkeypatch.setattr(seed_baseline, "get_settings", _settings_with(""))

    exit_code = await seed_baseline.run()

    assert exit_code == 1
    assert "DATABASE_URL_MIGRATION" in capsys.readouterr().err


async def test_seed_run_check_only_reports_problems(
    patch_engine: _FakeEngine,
    with_migration_url: None,
    monkeypatch: pytest.MonkeyPatch,
    fake_conn: _FakeConn,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--check 模式发现问题时退出码为 1，且把问题写到 stderr。"""
    monkeypatch.setattr(
        seed_baseline,
        "check_baseline",
        _async_return(["数据库缺少权限点 3 个"]),
    )

    exit_code = await seed_baseline.run(check_only=True)

    assert exit_code == 1
    assert "缺少权限点" in capsys.readouterr().err
    assert patch_engine.disposed is True


async def test_seed_run_check_only_passes(
    patch_engine: _FakeEngine,
    with_migration_url: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--check 模式一致时退出码 0。"""
    monkeypatch.setattr(seed_baseline, "check_baseline", _async_return([]))

    exit_code = await seed_baseline.run(check_only=True)

    assert exit_code == 0
    assert "OK" in capsys.readouterr().out


async def test_seed_run_writes_and_disposes_engine(
    patch_engine: _FakeEngine,
    with_migration_url: None,
    fake_conn: _FakeConn,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """正常写入路径：打印统计、释放引擎。"""
    exit_code = await seed_baseline.run()

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "权限点" in output and "角色" in output
    assert "幂等" in output
    assert patch_engine.disposed is True
    assert any("INSERT INTO permissions" in stmt for stmt in fake_conn.statements)


async def test_seed_run_converts_business_error_to_exit_code(
    patch_engine: _FakeEngine,
    with_migration_url: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """业务异常转成退出码 1 并写 stderr，不抛栈给运维看。"""
    from app.core.errors import BusinessError, ErrorCode

    async def _boom(conn: Any) -> tuple[int, int]:
        raise BusinessError(ErrorCode.PARAM_INVALID, "口令太弱")

    monkeypatch.setattr(seed_baseline, "seed_permissions", _boom)

    exit_code = await seed_baseline.run()

    assert exit_code == 1
    assert "口令太弱" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# restore_builtin.run
# ---------------------------------------------------------------------------


async def test_restore_run_without_url_exits_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL_MIGRATION", "")
    monkeypatch.setattr(restore_builtin, "get_settings", _settings_with(""))

    exit_code = await restore_builtin.run(do_list=True)

    assert exit_code == 1
    assert "DATABASE_URL_MIGRATION" in capsys.readouterr().err


async def test_restore_run_prints_output_and_disposes(
    patch_engine: _FakeEngine,
    with_migration_url: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """正常路径：打印 restore() 的汇总并释放引擎。"""
    monkeypatch.setattr(
        restore_builtin, "restore", _async_return("缺失权限点 0 个：无\nOK: 恢复完成")
    )

    exit_code = await restore_builtin.run(do_list=True)

    assert exit_code == 0
    assert "OK: 恢复完成" in capsys.readouterr().out
    assert patch_engine.disposed is True


def test_migration_url_prefers_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """配置里的连接串优先于环境变量（Settings 已读 env_file）。"""
    monkeypatch.setattr(
        restore_builtin, "get_settings", _settings_with("postgresql+asyncpg://a:b@h/db")
    )
    monkeypatch.setenv("DATABASE_URL_MIGRATION", "postgresql+asyncpg://ignored@h/db")
    assert restore_builtin._migration_url() == "postgresql+asyncpg://a:b@h/db"


def test_migration_url_falls_back_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(restore_builtin, "get_settings", _settings_with(""))
    monkeypatch.setenv("DATABASE_URL_MIGRATION", "postgresql+asyncpg://env@h/db")
    assert restore_builtin._migration_url() == "postgresql+asyncpg://env@h/db"


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _settings_with(url: str) -> Any:
    """构造只带 database_url_migration 的假 Settings。"""

    class _Secret:
        def __init__(self, value: str) -> None:
            self._value = value

        def get_secret_value(self) -> str:
            return self._value

    class _Settings:
        database_url_migration = _Secret(url)

    return lambda: _Settings()


def _async_return(value: Any) -> Any:
    """构造返回固定值的协程函数。"""

    async def _inner(*args: Any, **kwargs: Any) -> Any:
        return value

    return _inner
