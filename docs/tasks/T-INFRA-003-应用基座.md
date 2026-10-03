# T-INFRA-003：应用基座（统一响应 / 错误码 / request_id / 公共模型）

| 项 | 内容 |
| --- | --- |
| 模块 | infra |
| 负责人 | AI |
| 状态 | **`done`**（2026-10-03；闸门 1/2/3 全绿，覆盖率 100%） |
| 优先级 | P0 |
| 依赖 | T-INFRA-001 |
| 被依赖 | T-INFRA-004, T-AUTH-001, T-AUTH-002, T-BASE-001 |
| 关联设计 | [docs/modules/00-P0地基.设计.md](../modules/00-P0地基.设计.md) §4.1、§9（TC-I01~I08） |
| 关联 ADR | ADR-0001, ADR-0002 |
| 估算 | 0.7d |

## 目标

让 `/healthz` 能返回 200，且**任何一个接口抛出的异常都会变成 `{"code","message","data","details","request_id"}`**（05 §3/§4），所有模型自动带齐 04 §2 公共字段。此后每个模块只写业务，不再各写一遍包装与异常。

## 范围

**要做**：
- [ ] `app/core/config.py`：`pydantic-settings` 读环境变量（11 §2 全键）；`@lru_cache` 单例；**禁止硬编码任何密钥默认值**
- [ ] `app/core/db.py`：`create_async_engine`（asyncpg）+ `async_sessionmaker` + `get_db` 依赖；连接池参数按 01 §3
- [ ] `app/core/errors.py`：`ErrorCode`（`IntEnum`，**只含 05 §4 已登记的值**）+ `BusinessError(code, message, details)` + `HTTP_STATUS_BY_CODE` 映射
- [ ] `app/core/responses.py`：`ApiResponse[T]`（泛型）+ `PageData[T]` + `ok()` + `page_ok()`
- [ ] `app/core/middleware.py`：`RequestIdMiddleware`（生成 ULID 写 `request.state.request_id` + 响应头 `X-Request-ID`）、`ExceptionMiddleware`
- [ ] `app/common/models.py`：`Base` + `AuditMixin` + `SoftDeleteMixin` + `VersionMixin` + `TimestampMixin`，**照抄 04 §2**
- [ ] `app/common/enums.py`：`DataScope`（SELF/GROUP/WORKSHOP/FACTORY）、`SizeClass`、`DocumentStatus`、`IdempotencyAction` 等 P0 用到的枚举；**集中一处，禁止散落 Literal**
- [ ] `app/main.py`：`create_app()` 工厂（**测试要能反复创建**）+ 生命周期 + 中间件注册 + `BusinessError`/`RequestValidationError`/`Exception` 三个异常处理器 + `GET /healthz`、`GET /readyz`、`/api/v1/healthz`、`/api/v1/readyz`（Q-P0-07 双路径）+ `/api/v1/events/stream` 占位返回 `501`

**不做**：
- 不写任何业务路由（`/api/v1/**` 只有健康检查与占位）
- 不建 alembic、不建表（T-INFRA-004）
- 不实现鉴权与数据范围（T-AUTH-002）
- 不接 Redis / 不写 worker（本卡 `readyz` 只探测 DB 与进程存活；Redis 探测在 T-INFRA-004 补）

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `backend/app/__init__.py` | 新增 | 空包标记 |
| `backend/app/core/__init__.py` | 新增 | 空 |
| `backend/app/common/__init__.py` | 新增 | 空 |
| `backend/app/core/config.py` | 新增 | pydantic-settings |
| `backend/app/core/db.py` | 新增 | engine / session / get_db |
| `backend/app/core/errors.py` | 新增 | ErrorCode + BusinessError |
| `backend/app/core/responses.py` | 新增 | ApiResponse / PageData |
| `backend/app/core/middleware.py` | 新增 | RequestId + Exception |
| `backend/app/common/models.py` | 新增 | Base + 4 个 mixin |
| `backend/app/common/enums.py` | 新增 | 枚举集中 |
| `backend/app/main.py` | 新增 | create_app + 健康检查 |

> 文件数 11 > 8。**拆法**：本卡只做 `core/*` + `common/models.py` + `common/enums.py` + 3 个 `__init__.py` + `main.py`；若仍超限，`app/core/__init__.py` / `common/__init__.py` 与 `db.py` 归入 T-INFRA-004（004 本就要动 `alembic/env.py`，需 `db.py`）。

## 实现要点（必读规范）

- [ ] 遵守 docs/03 §1.1（10 条硬规则：类型标注、`Decimal` 禁 float、异常分层、事务边界唯一、枚举集中、日志用 `extra`）
- [ ] 遵守 docs/04 §2（公共字段**逐字照抄**）、§3（PG enum 集中）、§4（金额 `numeric`）
- [ ] 遵守 docs/05 §3（响应包装与分页结构**照抄代码块**）、§4（错误码表：`ErrorCode` 只能是 05 §4 的子集）
- [ ] 遵守 docs/11 §2（密钥只进环境变量）
- [ ] `create_app()` 必须是**工厂函数**，供 `tests/conftest.py` 反复创建 + `dependency_overrides`

## 验收标准

- [ ] `GET /healthz` → 200 `{"status":"alive"}`（带 `X-Request-ID` 头）
- [ ] 造一个抛 `BusinessError(ErrorCode.PIECEWORK_NOT_BOUND, "该员工未绑定此工序", {...})` 的临时路由 → 响应体含 `code=32002`、HTTP 403/409 按映射表、`details`、`request_id` 与响应头一致
- [ ] 请求体带未知字段（`extra="forbid"`）→ `422` + `code=10001`
- [ ] `uv run mypy app` **0 error**（strict 首次跑通）
- [ ] `uv run ruff check . && uv run ruff format --check .` 0 error
- [ ] 单测 TC-I01/I03/I05/I08 全绿；`app/core` 与 `app/common` 行覆盖 ≥ 90%
- [ ] 单元测试断言：`ErrorCode` 成员集合 ⊆ `docs/05 §4` 表格中登记的码（解析 Markdown 断言，TC-I04）

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-I01 | `BaseModel` 子类列集合 | 恰为 04 §2 的 8 个公共字段 | ✅ |
| TC-I01b | 公共字段可空性 | `deleted_at`/`remark` 可空；审计四件 + `version` 非空 | ✅ |
| TC-I01c | 主键 | 单列 `id`（uuid） | ✅ |
| TC-I01d | `version` server_default | `"1"` | ✅ |
| TC-I01e | **守卫**：解析 docs/04 §2 SQL 块 | 与 `EXPECTED_COMMON_COLUMNS` 一致；不一致则失败提醒同步 models.py | ✅ |
| TC-I03 | `ok` / `page_ok` / `PageData` 边界 / `ApiResponse` 默认值 | 与 docs/05 §3 一致；`page≥1`、`page_size≤200` | ✅ |
| TC-I04 | `ErrorCode` 取值 vs docs/05 §4 表格（解析 74 个码） | 全部已登记，无编造 | ✅ |
| TC-I04b | 10xxx/11xxx/12xxx 段 | 文档登记的必须全部实现，不许缺 | ✅ |
| TC-I04c | 25 个已登记码的 HTTP 状态 | 与文档 HTTP 列逐项一致 | ✅ |
| TC-I05 | `request_id` 为 26 位 Crockford ULID、单调、请求间唯一、响应头存在 | 全部满足 | ✅ |
| TC-I08 | 业务异常 / 校验异常 / 兜底异常 | 统一响应体 + 映射状态 + `request_id` 头体一致；兜底**不泄漏** `postgres://…hunter2` | ✅ |
| TC-I09 | `create_app()` 连续两次 | 独立实例、中间件数量一致、openapi 一致 | ✅ |
| 新增 | 未知路由 `POST /api/v1/does-not-exist` | 统一包装 + `10008` + 无 `detail` 字段 + `request_id` 一致 | ✅ |
| 新增 | 方法不允许 `POST /healthz` | HTTP 405 + 统一包装 | ✅ |
| 新增 | `/healthz` 与 `/readyz` 语义区分 | 依赖全挂时 healthz 仍 200、readyz 503 | ✅ |
| 新增 | 生产环境缺 `JWT_SECRET`/`DATABASE_URL` | `Settings()` 抛 `ValueError` 并给出 `openssl rand -hex 32` 提示 | ✅ |
| 新增 | `SecretStr` 不泄漏 | `repr(settings)` 不含明文密钥 | ✅ |
| 新增 | engine/session factory 单例、`get_db` 关闭、重复 dispose | 幂等、不建连接 | ✅ |
| 新增 | 中间件边界：非 JSON / 无 code 键 / 坏 JSON / 空 body | 一律原样放行，不注入 | ✅ |
| 新增 | 枚举值全 ASCII、稳定不改名 | 通过（防 enum 值漂移） | ✅ |

**闸门结果**：闸门 1 `ruff check` + `ruff format --check` ✅ ｜ 闸门 2 `mypy app`（strict）✅ ｜
闸门 3 `pytest -q --cov=app --cov-fail-under=80` → **83 passed / 覆盖率 100%** ✅

**真实起服务验证**（uvicorn 127.0.0.1:18000，已停）：
`/healthz` 200 带 `x-request-id: 01M405CDKGVX2HJGEMNAX4BQBD`；
`/readyz` 无数据库时 503 + `{"database":"not_configured","redis":"not_configured"}`；
未知路由 404 + 统一包装 + 头体 `request_id` 一致；`/openapi.json` 可导出（3 路径 / tags 仅「系统」）。

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `backend/app/__init__.py`、`core/__init__.py`、`common/__init__.py` | +0 | 包标记 |
| `backend/app/common/enums.py` | +83 | 6 个枚举集中（DataScope / SizeClass / DocumentStatus / AuthChannel / ExternalIdentityProvider / DocumentAction） |
| `backend/app/common/models.py` | +104 | `Base` + `IdMixin`/`AuditMixin`/`SoftDeleteMixin`/`VersionMixin` + `BaseModel`（照抄 04 §2）+ `as_dict_for_log` |
| `backend/app/core/errors.py` | +169 | `ErrorCode`（25 个已登记码）+ `HTTP_STATUS_BY_CODE` + `BusinessError` + 4 个便捷子类 |
| `backend/app/core/responses.py` | +68 | `ApiResponse[T]` / `PageData[T]` / `ok` / `page_ok` |
| `backend/app/core/config.py` | +106 | pydantic-settings + `SecretStr` + 生产密钥强校验 + `require_database_url` |
| `backend/app/core/db.py` | +87 | engine 单例（`pool_pre_ping`）+ session factory + `get_db` + `dispose_engine` |
| `backend/app/core/middleware.py` | +229 | ULID 生成器 + 纯 ASGI `RequestIdMiddleware`（注入 body + 头）+ 4 个异常处理器 |
| `backend/app/main.py` | +170 | `create_app()` 工厂 + 生命周期 + `/healthz` `/readyz`（含带前缀别名）+ SSE 占位 501 |
| `backend/tests/conftest.py` | +47 | 环境变量 + app 工厂 + 内存客户端 |
| `backend/tests/test_errors_registry.py` | +101 | 错误码与 docs/05 §4 双向断言 |
| `backend/tests/test_app_base.py` | +290 | 响应包装 / request_id / 异常 / 公共字段 / 探针 |
| `backend/tests/test_core_internals.py` | +285 | 配置 / db / 枚举 / 模型辅助 / 异常子类 / 中间件边界 |
| `backend/pyproject.toml` | 修改 | 新增 `allowed-confusables`（中文标点白名单） |

合计新增约 1740 行（测试 723 行 / 实现 1017 行），单次提交 < 800 行业务代码上限的分两次提交处理。

## 与卡片的偏差（3 处，均为实测后的改进）

| # | 卡片原写 | 实际做法 | 原因 |
| --- | --- | --- | --- |
| 1 | `/readyz` 只探测 DB | 同时探测 Redis，且**「未配置」与「连不上」同样算 503** | 首版逻辑不自洽（DB 分支只接受 `up`，Redis 分支接受 `not_configured`）；且「没配数据库却报 ready」是撒谎，会让编排器继续打流量 |
| 2 | `Generic[T]` 写法（docs/05 §3 代码块） | 改用 PEP 695 `class PageData[T](BaseModel)` | ruff `UP046` 强制；语义与响应结构完全一致，仅类声明语法不同 |
| 3 | 不接 Redis | 探测 Redis 连通性（不引 worker） | 「就绪」必须覆盖真实依赖，否则名不副实 |

## 实测抓到的 3 个真缺陷

| # | 缺陷 | 现象 | 修法 |
| --- | --- | --- | --- |
| 1 | `/readyz` 判定逻辑不自洽 | DB 分支要求 `up`，Redis 分支允许 `not_configured` | 统一为「全部 `up` 才 200」，原因写进 `checks` |
| 2 | **未知路由返回 Starlette 默认形状** | `{"detail":"Not Found"}`，无 `request_id`、不走统一包装（docs/05 §3 违规） | 新增 `StarletteHTTPException` 处理器，保持 HTTP 404/405 原状 |
| 3 | **兜底 500 缺 `X-Request-ID` 头** | 500 由 Starlette 最外层 `ServerErrorMiddleware` 生成，绕过本中间件 | 三个异常处理器显式带头；中间件改为「尚未设置才追加」避免重复头 |

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| L-020 | 「路由不存在 / 方法不允许」无专用错误码，暂用 `10008` + 保持 HTTP 404/405（唯一例外） | docs/12 §5 L-020 |
| L-021 | ruff `allowed-confusables` 中文标点白名单需回写 docs/03 §1.6 | docs/12 §5 L-021 |
| W2 | append-only 表豁免 04 §2 公共字段，需回写 04 §2 | 设计稿 §10.1 W2，归档阶段 |
| — | `app/modules/` 与 `/api/v1` 业务路由为空；T-AUTH-001/T-BASE-001 起逐步挂载 | T-AUTH-001 |

**提交记录**：
- `<hash>` feat(infra): 应用基座（统一响应/错误码/request_id/公共模型）

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。
