# T-INFRA-001：仓库骨架与质量基线

| 项 | 内容 |
| --- | --- |
| 模块 | infra |
| 负责人 | AI |
| 状态 | `todo` |
| 优先级 | P0 |
| 依赖 | 无 |
| 被依赖 | T-INFRA-002, T-INFRA-003 |
| 关联设计 | [docs/modules/00-P0地基.设计.md](../modules/00-P0地基.设计.md) §8 |
| 关联 ADR | ADR-0001（技术栈） |
| 估算 | 0.5d |

## 目标

把 `backend/` 与 `frontend/` 两棵空目录变成**能跑闸门的工程**：依赖装得上、lint/typecheck 配置到位、密钥不进仓库。此后所有任务卡不再各自搭环境。

## 范围

**要做**：
- [ ] `backend/pyproject.toml`：`[project]`（Python ≥3.12，依赖见下）+ `[tool.ruff]`（照抄 03 §1.6）+ `[tool.mypy] strict` + `[tool.coverage]`（照抄 10 §8）+ `[tool.pytest.ini_options]`
- [ ] 依赖清单：运行时 `fastapi` `uvicorn[standard]` `sqlalchemy[asyncio]>=2.0` `asyncpg` `alembic` `pydantic>=2` `pydantic-settings` `pwdlib[argon2]` `pyjwt` `python-multipart` `redis` `openpyxl`；开发 `pytest` `pytest-asyncio` `pytest-cov` `httpx` `ruff` `mypy` `factory-boy`
- [ ] `uv sync` 生成 `backend/uv.lock` 并提交（锁依赖版本）
- [ ] `.gitignore`：`.env` `.venv` `node_modules/` `dist/` `__pycache__/` `*.pyc` `.pytest_cache/` `.mypy_cache/` `.ruff_cache/` `coverage.xml` `htmlcov/` `backups/` `docker-compose.override.yml` `*.local`
- [ ] `.env.example`：11 §2 全部键留空占位（`POSTGRES_USER` `POSTGRES_PASSWORD` `POSTGRES_DB` `DATABASE_URL` `DATABASE_URL_MIGRATION` `REDIS_URL` `JWT_SECRET` `JWT_EXPIRE_MINUTES` `APP_ENV` `SENTRY_DSN` `SMS_ACCESS_KEY_ID` `SMS_ACCESS_KEY_SECRET`）
- [ ] `.pre-commit-config.yaml`：`ruff`、`ruff-format`、`check-added-large-files`、`detect-private-key`、`end-of-file-fixer`、`trailing-whitespace`
- [ ] `frontend/pnpm-workspace.yaml`（`packages: - packages/*`）、`frontend/package.json`（脚本 `lint` `typecheck` `test:unit` `generate:api` `dev` `build`）、`frontend/tsconfig.base.json`（`strict` + `noUncheckedIndexedAccess` + `moduleResolution: bundler`）

**不做**（明确排除，防止顺手扩大）：
- 不建 `frontend/packages/*` 子包（T-WEB-001 起）
- 不写任何业务代码、不建 alembic、不写 Dockerfile（T-INFRA-002/003/004）
- 不配置 CI（本卡只做本地 + pre-commit；CI 在 T-INFRA-002）

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `backend/pyproject.toml` | 新增 | 依赖 + ruff/mypy/coverage/pytest 配置 |
| `backend/uv.lock` | 新增 | `uv sync` 生成并提交 |
| `.gitignore` | **已建**（0033 提交时为避免 `node_modules`/`.env` 入库先建），本卡复核并补全 | 含 `.env`（11 §2 红线）、`node_modules/`、`dist/`、缓存目录、`backups/` |
| `.env.example` | 新增 | 全键留空 |
| `.pre-commit-config.yaml` | 新增 | ruff + 基础卫生 |
| `frontend/pnpm-workspace.yaml` | 新增 | monorepo |
| `frontend/package.json` | 新增 | 脚本与 devDeps（eslint / prettier / vue-tsc / vitest / typescript） |
| `frontend/tsconfig.base.json` | 新增 | strict 基线 |

## 实现要点（必读规范）

- [ ] 遵守 docs/03-代码规范.md §1.6（ruff/mypy 基线**照抄**，不自行放宽）
- [ ] 遵守 docs/10-测试规范.md §8（coverage `fail_under = 80`，`omit` 排除 `migrations`/`workers`/`main.py`）
- [ ] 遵守 docs/11-部署运维与发布规范.md §2（`.env.example` 入 git、`.env` 不入、值留空）
- [ ] `pyproject.toml` 里 `target-version = "py312"`、`line-length = 100`

## 验收标准

- [ ] `cd backend && uv sync` 成功，`uv.lock` 已提交
- [ ] `cd backend && uv run ruff check . && uv run ruff format --check .` → 0 error
- [ ] `cd frontend && pnpm install && pnpm lint && pnpm typecheck` → 0 error（空工程也应通过；`typecheck` 在无 `.vue` 时允许空跑，但**不得报错**）
- [ ] `git check-ignore .env` 命中；`.env.example` 被 git 跟踪且所有值为空
- [ ] `pre-commit run --all-files` 通过
- [ ] 闸门 1 通过（闸门 2 的 `mypy app` 在 T-INFRA-003 首次跑通，因本卡无 `app` 包）

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-001 | `uv sync` 后 `python -c "import fastapi, sqlalchemy"` | 成功 | |
| TC-002 | `ruff check .` / `ruff format --check .` | 0 error | |
| TC-003 | 故意写一行超 100 字符到临时文件后 `ruff check` | 报错（证明规则生效），随即删除 | |
| TC-004 | `git status --porcelain` 检查 `.env` 未被跟踪 | 未出现 `.env` | |
| TC-005 | `pnpm lint` / `pnpm typecheck` | 0 error | |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| | +0 / -0 | |

**提交记录**：
- `<hash>` chore(infra): 初始化工程骨架与质量基线 …

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| | | docs/12 §遗留问题清单 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。
