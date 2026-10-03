# T-INFRA-003：应用基座（统一响应 / 错误码 / request_id / 公共模型）

| 项 | 内容 |
| --- | --- |
| 模块 | infra |
| 负责人 | AI |
| 状态 | `todo` |
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
| TC-I01 | 自定义模型继承 `Base` 后检查列集合 | 含 `id`/`created_at`/`created_by`/`updated_at`/`updated_by`/`deleted_at`/`version`/`remark`；`version` 默认 1 | |
| TC-I03 | `ok({...})` / `page_ok([], 0, 1, 20)` | 结构 = 05 §3 | |
| TC-I04 | `set(ErrorCode)` ⊆ 05 §4 登记码 | 通过；且 05 §4 中 `10xxx`/`11xxx` 通用码均有实现 | |
| TC-I05 | 请求 → 响应头与 body 的 `request_id` | 一致且为合法 ULID | |
| TC-I08 | 逐个错误码 → HTTP 状态 | `11001`→401、`12001`→403、`10003`→409、`10001`→422、`20001`→404、`99999`→500 | |
| TC-I09 | `create_app()` 连续创建 3 次 | 无全局状态污染（中间件/异常处理器不重复注册） | |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| | +0 / -0 | |

**提交记录**：
- `<hash>` feat(infra): 应用基座（统一响应/错误码/request_id/公共模型） …

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| | | docs/12 §遗留问题清单 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。
