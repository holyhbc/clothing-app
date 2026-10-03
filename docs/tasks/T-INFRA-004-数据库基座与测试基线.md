# T-INFRA-004：数据库基座（Alembic + 扩展 + 日志表）与测试基线

| 项 | 内容 |
| --- | --- |
| 模块 | infra |
| 负责人 | AI |
| 状态 | `todo` |
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
- [ ] `app/core/numbering.py`：单号生成器骨架（计数器表 + 唯一索引兜底，09 §2.1「禁止 `count(*)+1`」；本卡只放通用件，P1 单据前缀 T-BASE 阶段接）

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
| `backend/app/core/numbering.py` | 新增 | 单号生成器骨架 |
| `backend/app/core/db.py` | 修改 | 补 Redis/连接池细节 + 测试库连接串切换 |

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
| TC-I10 | `upgrade head` 后查 `information_schema.columns` | `document_logs` 列集合 = 04 §7.9 | |
| TC-I11 | `downgrade -1` 后查表/类型/扩展 | 均不存在 | |
| TC-I12 | 两次 `upgrade head` 之间插入一行 `document_logs` 后 `downgrade -1` | 成功（表被删，属测试库，允许） | |
| TC-I13 | 用 `erp_app` 执行 `UPDATE document_logs ...` | 抛 `InsufficientPrivilege` | |
| TC-I14 | 两个测试用例各自造数据，第二个用例查不到第一个的数据 | 事务回滚生效，用例零依赖 | |
| TC-I15 | 随机顺序（`pytest -p no:randomly` 关闭后手动换序）跑 3 次 | 结果一致 | |
| TC-I16 | `numbering.next_no(prefix, date)` 同前缀同日并发 20 次 | 20 个号互不重复（唯一索引兜底） | |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| | +0 / -0 | |

**提交记录**：
- `<hash>` feat(infra): 数据库基座与测试基线 …

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| | | docs/12 §遗留问题清单 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。
