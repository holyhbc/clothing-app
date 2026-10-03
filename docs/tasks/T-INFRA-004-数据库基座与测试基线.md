# T-INFRA-004：数据库基座（Alembic + 扩展 + 日志表）与测试基线

| 项 | 内容 |
| --- | --- |
| 模块 | infra |
| 负责人 | AI |
| 状态 | **`done`**（2026-10-03；闸门 1-4 宿主机与容器内均全绿，107 测试 / 100% 覆盖） |
| 优先级 | P0 |
| 依赖 | T-INFRA-003 |
| 被依赖 | T-AUTH-001, T-BASE-001, T-BASE-002 |
| 关联设计 | [docs/modules/00-P0地基.设计.md](../modules/00-P0地基.设计.md) §2.1 组 A、§2.4（Q15/Q16）、§9 |
| 关联 ADR | ADR-0002（PostgreSQL） |
| 估算 | 0.7d |

## 目标

打通「迁移 → 真实 PG 16 → 测试」这条链路：`alembic upgrade head` 能建出 `pg_trgm` 扩展与 `document_logs`，`downgrade` 能干净回退；`pytest` 有独立测试库 + 每测试事务回滚夹具。

## 范围

**要做**：
- [ ] `alembic.ini` + `alembic/env.py`（**异步** engine，从 `app.core.config` 读连接串；`DATABASE_URL_MIGRATION` 优先）+ `alembic/script.py.mako`
- [ ] `alembic/versions/0001_extensions_and_logs.py`：
  - `CREATE EXTENSION IF NOT EXISTS pg_trgm;`（04 §5.1 要求第一条迁移建）
  - `CREATE TYPE data_scope AS ENUM ('SELF','GROUP','WORKSHOP','FACTORY')`（07 §2.1）
  - `CREATE TABLE document_logs`（**逐字照抄 04 §7.9**，含 `idx_document_logs_doc`）
  - `downgrade()`：`DROP TABLE` → `DROP TYPE` → `DROP EXTENSION`（⚠️ 破坏性 DDL，注释写明理由与回滚，11 §10 红线 7）
- [ ] `tests/conftest.py`：**独立测试库** `garment_erp_test`（10 §2.2）；`db_session` 夹具（每测试独立连接 + 嵌套事务 + 回滚）；`client` 夹具（`ASGITransport` + `dependency_overrides[get_db]`，**用完必须 clear**）；`auth_headers` 夹具工厂（按角色参数化，本卡先返回占位，T-AUTH-002 补实）；`anyio_backend`/`asyncio_mode`
- [ ] `tests/factories/__init__.py`：工厂基类约定（**禁止在测试里手写 uuid/时间**，10 §2.1）
- [ ] `app/core/logging.py`：结构化 JSON 日志（11 §8.1：stdout、`extra={}`、禁打印密码/token）
- [ ] ~~`app/core/numbering.py`~~ → **已移交 T-BASE-002**：其唯一真实消费者是款号「建议号」生成器（Q-P0-04 已确认"序号按客户递增"），在本卡凭空造一张计数器表属于过早抽象。09 §2.1「禁止 count(*)+1、必须用序列或带唯一索引的计数器表」的口径不变，在 T-BASE-002 落地
- [ ] **`docker/postgres/init/01-create-roles.sql`（L-019）**：创建迁移账号 `erp_ddl` 与运行账号 `erp_app`，按 [04 §6.2.1](../04-数据库规范.md) 只授 `SELECT/INSERT/UPDATE`，并 `REVOKE UPDATE, DELETE ON document_logs FROM erp_app`；`docker-compose.ci.yml` 挂载 `/docker-entrypoint-initdb.d`。**理由**：compose 里不藏 DDL，但账号必须在测试库首次启动时存在，否则 T-AUTH-001 起的集成测试连不上库

**不做**：
- 不建任何业务表（auth 归 T-AUTH-001，基础资料归 T-BASE-001/002）
- 不写 `factories/user.py` 等具体工厂（T-AUTH-001）
- 不接 Redis 客户端（T-AUTH-002 的登录锁定才需要）

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `backend/alembic.ini` | 新增 | 迁移配置 |
| `backend/alembic/env.py` | 新增 | 异步迁移环境 |
| `backend/alembic/script.py.mako` | 新增 | 模板（禁止中文注释进文件名） |
| `backend/alembic/versions/0001_extensions_and_logs.py` | 新增 | 扩展 + data_scope + document_logs |
| `backend/tests/conftest.py` | 新增 | db_session / client / auth_headers |
| `backend/tests/factories/__init__.py` | 新增 | 工厂约定 |
| `backend/app/core/logging.py` | 新增 | 结构化日志 |
| `backend/app/core/db.py` | 修改 | 补 Redis/连接池细节 + 测试库连接串切换 |
| `docker/postgres/init/01-create-roles.sql` | 新增 | `erp_ddl` / `erp_app` 角色与最小授权（L-019） |
| `docker-compose.ci.yml` | 修改 | 挂载 init 脚本到 `/docker-entrypoint-initdb.d` |

## 实现要点（必读规范）

- [ ] 遵守 docs/04 §5.1（迁移**第一条**建 `pg_trgm`；闸门 4 在 PG 16 验证）、§6.2（迁移规范：已合并禁止修改、`downgrade` 必须可还原、破坏性 DDL 注释理由）
- [ ] 遵守 docs/04 §6.2.1（迁移用 owner 账号；应用连接串用 `erp_app`）
- [ ] 遵守 docs/10 §2.1/§2.2（测试隔离、事务回滚、工厂模式、**禁止连生产库**）
- [ ] 遵守 docs/03 §1.1 第 5 条（`session.begin()` 只在 service 首层；本卡不写 service）

## 验收标准

- [ ] **闸门 4 通过**：`alembic upgrade head && alembic downgrade -1 && alembic upgrade head` 三步全绿（PG 16）
- [ ] `document_logs` 表结构与 04 §7.9 逐列一致；`idx_document_logs_doc` 存在
- [ ] `SELECT * FROM pg_extension WHERE extname='pg_trgm'` 有 1 行
- [ ] 迁移用 `DATABASE_URL_MIGRATION`（owner），应用连接串用 `erp_app`；两者在 `.env.example` 区分
- [ ] `pytest` 能跑通 1 个 smoke 用例（如 TC-I01）；`client` 夹具在用例结束后 `dependency_overrides` 已清空（写一个用例断言）
- [ ] 单测断言 `erp_app` 对 `document_logs` 的 `UPDATE`/`DELETE` 被拒（04 §6.2.1 生效）
- [ ] `uv run mypy app` 与 `ruff` 0 error；`app/core` 覆盖 ≥ 90%

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-I10 | `pg_trgm` 扩展已装 | 迁移第一条就装（docs/04 §5.1 强制） | ✅ |
| TC-I10b | `data_scope` 枚举值与顺序 | `SELF,GROUP,WORKSHOP,FACTORY`（docs/07 §2.1） | ✅ |
| TC-I11 | `document_logs` 列集合 | 逐列等于 docs/04 §7.9 的 12 列 | ✅ |
| TC-I11b | `idx_document_logs_doc` 存在 | 详情页「变更历史」依赖 | ✅ |
| TC-I11c | **守卫**：解析 docs/04 §7.9 SQL 块 | 与迁移一致；规范改列即失败 | ✅ |
| TC-I12 | `erp_app` 对 `document_logs` 的 UPDATE/DELETE/TRUNCATE + `CREATE TABLE` | 4 条全部 `ProgrammingError`（权限拒绝） | ✅ |
| TC-I12b | `erp_app` 可 INSERT 审计 | 审计必须能写 | ✅ |
| TC-I13 | `UPDATE/DELETE document_logs` | `permission denied for table document_logs` | ✅ |
| TC-I14 | `db_session` 回滚隔离 | 写入→事务内可见→回滚→不可见（**自包含，不依赖用例顺序**） | ✅ |
| TC-I14b | 新会话看不到上个用例经 `db_session` 写入的行 | 事务隔离生效 | ✅ |
| TC-I16 | 迁移往返 | `upgrade → downgrade -1 → upgrade` 三步全绿（宿主机 + 容器内各一次） | ✅ |
| TC-I17 | 应用连接串必须是 `erp_app` 且不含 `erp_ddl` | 否则权限断言全部失真 | ✅ |
| 新增 | 结构化日志单行 JSON / extra 提升为顶层键 / 时间带时区 | 通过 | ✅ |
| 新增 | 敏感字段脱敏（`password` `jwt_secret` `access_token` `authorization` 及嵌套 dict） | 全部替换为 `***` | ✅ |
| 新增 | `configure_logging` 幂等 | 重复调用不叠加 handler | ✅ |
| 新增 | 异常栈只进日志 | JSON 的 `exc` 字段有栈，不进响应体 | ✅ |
| 新增 | 工厂默认值固定时间与完整字段 | 符合 docs/10 §7 | ✅ |
| 新增 | `/readyz` 两条路径（依赖可用 200 / 未配置 503）用 monkeypatch 显式控制 | **不依赖环境**，连不连库结果一致 | ✅ |

**闸门结果（宿主机）**：闸门 1 ✅ ｜ 闸门 2 ✅ ｜ 闸门 3 **107 passed / 覆盖率 100%** ✅ ｜ 闸门 4 三步往返 ✅
**闸门结果（容器内，走 CI 同一路径）**：闸门 1-2 ✅ ｜ 闸门 3 ✅ 107 passed / 100% ｜ 闸门 4 ✅

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `backend/alembic.ini` | +34 | 迁移配置；连接串**不写这里**，由 env.py 从环境读 |
| `backend/alembic/env.py` | +108 | 异步迁移环境；`pkgutil` 自动导入各模块 models（新增模块无需改这里，也不会漏表） |
| `backend/alembic/script.py.mako` | +25 | 迁移模板 |
| `backend/alembic/versions/0001_extensions_and_logs.py` | +129 | `pg_trgm` + `data_scope` 枚举 + `document_logs` + 审计表写保护 |
| `docker/postgres/init/01-create-roles.sh` | +72 | 三角色初始化（L-019） |
| `docker-compose.ci.yml` | 修改 | 引导超管改用 `postgres`、挂 init 脚本、暴露 15432/16379 给宿主机跑测试、`bash -lc` → `bash -c` |
| `docker/backend/Dockerfile` | 修改 | dev 阶段复制 `docs/`（规范一致性守卫需要） |
| `.dockerignore` | +38 | 构建上下文裁剪 300M → 22M（L-022） |
| `backend/app/core/logging.py` | +118 | 结构化 JSON 日志 + 敏感字段脱敏 |
| `backend/app/modules/__init__.py` | +0 | 模块包（env.py 的 models 自动发现入口） |
| `backend/tests/conftest.py` | +152 | session 级建 schema、function 级事务回滚、`client`、`auth_headers` 占位 |
| `backend/tests/factories/__init__.py` | +73 | 工厂基类 + `DocumentLogFactory`（固定时间） |
| `backend/tests/test_migrations.py` | +213 | 迁移产物 + 权限隔离 + 事务隔离 |
| `backend/tests/test_logging.py` | +132 | 日志格式与脱敏 |
| `backend/tests/test_app_base.py` | 修改 | `/readyz` 两条路径改为 monkeypatch 显式控制，去掉对环境的隐式依赖 |
| `backend/pyproject.toml` | 修改 | `tests/**` 增加 `S106` 豁免（测试必须喂假密钥验证脱敏） |
| `.github/workflows/ci.yml` | 修改 | 接入容器化测试（验证镜像依赖完整，而不只是宿主机能跑） |

## 实测抓到的 5 个真缺陷

| # | 缺陷 | 现象 | 修法 |
| --- | --- | --- | --- |
| 1 | init 脚本末尾的 `DO $$ ... RAISE NOTICE ... :'var'` | psql 变量**在美元引号内不插值** → 语法错误 → `ON_ERROR_STOP` 让容器起不来 | 改用 `\echo`（我先前识别过这个坑却又踩了一次） |
| 2 | `erp_ddl` 无法 `CREATE EXTENSION` | `pg_trgm` 是 trusted 扩展，只有库 owner/超管能装 | init 脚本加 `ALTER DATABASE ... OWNER TO erp_ddl`（给 owner 不给超管） |
| 3 | `erp_ddl` 无法建表 | PG 15+ 起 `public` schema 默认不对所有角色开放 CREATE | 显式 `GRANT USAGE, CREATE ON SCHEMA public` |
| 4 | 容器内 `alembic: command not found` | `bash -lc` 是**登录 shell，会重置 PATH**，丢掉 `/opt/venv/bin` | 全部改 `bash -c`（实测：登录 shell 里 0 个 venv 路径 vs 普通 shell 1 个） |
| 5 | 缺 `.dockerignore` | 每次构建传 300M+ 上下文（`prototype/node_modules` 235M） | 补 `.dockerignore`，裁剪到 22M |

另有一个设计缺陷在写的时候就避开了：**CI 里 `POSTGRES_USER` 不能用 `erp_app`**，否则它是超级用户，「无 DELETE 权限」的保证与全部权限测试都失真。已改为引导超管用 `postgres`，另建 `erp_ddl` / `erp_app`。

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| L-022 | 缺 `.dockerignore`（T-INFRA-002 遗漏） | **已闭环**（本卡补齐） |
| L-023 | `erp_ddl` 需为库 owner 才能装 trusted 扩展，建议回写 docs/04 §6.2.1 角色权限表 | docs/12 §5 L-023 |
| — | `app/core/numbering.py` 移交 T-BASE-002（唯一消费者是款号建议号生成器） | T-BASE-002 |
| — | `auth_headers` 夹具是 `NotImplementedError` 占位 | T-AUTH-002 |
| — | `client` 夹具依赖 `db_session`（照抄 docs/10 §2.2 写法），因此 HTTP 用例也需要库；无库时全部 skip | 已知取舍，CI 恒有库 |
| W2 | append-only 表豁免 04 §2 公共字段，需回写 04 §2 | 设计稿 §10.1 W2 |

**提交记录**：
- `<hash>` feat(infra): 数据库基座与测试基线 …

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。
