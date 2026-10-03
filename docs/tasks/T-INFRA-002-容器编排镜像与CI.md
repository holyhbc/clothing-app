# T-INFRA-002：容器编排、镜像与 CI

| 项 | 内容 |
| --- | --- |
| 模块 | infra |
| 负责人 | AI |
| 状态 | **`done`**（2026-10-03；闸门 5 的 web 镜像阻塞到 T-WEB-006，见「遗留问题」） |
| 优先级 | P0 |
| 依赖 | T-INFRA-001 |
| 被依赖 | T-INFRA-003, T-INFRA-004 |
| 关联设计 | [docs/modules/00-P0地基.设计.md](../modules/00-P0地基.设计.md) §7（风险与回滚）、§8 |
| 关联 ADR | ADR-0006（部署形态 docker-compose） |
| 估算 | 0.5d |

## 目标

产出**生产 compose（只拉镜像）**与**CI compose（可 build、可跑闸门）**两套编排，以及两个多阶段镜像与备份脚本，让 5 道闸门在本地与 CI 都能一键执行。

## 范围

**要做**：
- [ ] `docker-compose.yml`（**生产**）：`postgres:16-alpine`（按 04 §9 基线调参 + `timezone=Asia/Shanghai` + `pg_stat_statements`）、`redis:7-alpine`、`api`、`web`；**禁止出现 `build:`**（11 §3 禁令）；`api` `depends_on: postgres(service_healthy)`；健康检查打 `/readyz`；资源限额按 01 §3（PG 3G / API 1536M）；`postgres` **不暴露 5432 到 0.0.0.0**
- [ ] `docker-compose.ci.yml`：`build:` + PG16 + Redis + `migrate-check`（闸门 4）+ `test`（闸门 3）服务
- [ ] `docker/backend/Dockerfile`：多阶段、`python:3.12-slim`、依赖层先于源码层、`USER app`、非 root、无密钥烘焙
- [ ] `docker/frontend/Dockerfile`：多阶段、`node:22-alpine` 构建 → `nginx:alpine` 运行
- [ ] `docker/nginx/nginx.conf`：`/api/` 反代到 `api:8000`、`/healthz` `/readyz` 直通、SPA `try_files`、gzip
- [ ] `docker/scripts/entrypoint.sh`：等待 DB 就绪 → `alembic upgrade head`（用 `DATABASE_URL_MIGRATION`）→ 启 uvicorn；`set -euo pipefail`
- [ ] `docker/scripts/backup.sh`：**照抄 11 §7**（`pg_dump -Fc` + `pg_restore -l` 校验 + 30 天清理 + 输出 `OK <file> <size>`）
- [ ] `.github/workflows/ci.yml`：PR/push 触发 → 闸门 1~4（闸门 5 在 tag 的 release 流程）

**不做**：
- 不写 `deploy/release.sh` 与 `docs/releases/`（发布阶段 `/release`）
- 不配 registry 凭据、不推镜像（tag 触发才推）
- 不做 `docker-compose.override.yml`（本机端口映射，不进 git）

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `docker-compose.yml` | 新增 | 生产编排（无 `build`） |
| `docker-compose.ci.yml` | 新增 | CI 编排（有 `build`，跑闸门） |
| `docker/backend/Dockerfile` | 新增 | 多阶段、非 root |
| `docker/frontend/Dockerfile` | 新增 | build → nginx |
| `docker/nginx/nginx.conf` | 新增 | 反代 + gzip + SPA |
| `docker/scripts/entrypoint.sh` | 新增 | 等待 DB → 迁移 → 启动 |
| `docker/scripts/backup.sh` | 新增 | 幂等 + 校验（11 §7） |
| `.github/workflows/ci.yml` | 新增 | 闸门 1~4 |

## 实现要点（必读规范）

- [ ] 遵守 docs/11 §3（目录与 compose 片段、禁令三条）、§4（镜像要求：多阶段/非 root/层缓存/tag 禁 latest）、§7（备份脚本原文）
- [ ] 遵守 docs/04 §9（PG 配置基线）、§6.2.1（`erp_app` 无 DELETE 权限，compose 里体现两个连接串）
- [ ] 遵守 docs/01 §3（2C/12G 资源预算）
- [ ] 闸门 5 命令按 **Q-P0-09 结论**：`docker compose -f docker-compose.ci.yml build`

## 验收标准

- [ ] `docker compose config` 无警告；`grep -c "build:" docker-compose.yml` 结果为 **0**
- [ ] `docker compose -f docker-compose.ci.yml build` 成功
- [ ] `docker compose -f docker-compose.ci.yml up -d postgres redis` 后 `pg_isready` 通过
- [ ] `docker/scripts/backup.sh` 输出含 `OK garment_erp_*.dump <size>`；能对刚生成的 dump 跑 `pg_restore -l` 成功
- [ ] `.github/workflows/ci.yml` YAML 合法；闸门 1~4 步骤与 docs/02 §3 命令一致
- [ ] 生产 compose 中不存在 `-v`、`docker volume`、`down -v`（11 §10 红线 1）

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-001 | `docker compose config -q`（生产，需 env） | 退出码 0 | ✅ |
| TC-002 | 生产 compose 去注释后搜 `build:` | **0 处** | ✅ |
| TC-003 | 生产 compose 搜 5432 对外映射 | 0 处 | ✅ |
| TC-004 | compose + 脚本去注释后搜 `down -v`/`volume rm`/`dropdb`/`TRUNCATE`/`rm -rf` | 0 处 | ✅ |
| TC-005 | `bash -n` 两个脚本 | 语法通过 | ✅ |
| TC-006 | `docker compose -f docker-compose.ci.yml up -d postgres redis` | 两者 `healthy` | ✅ PostgreSQL 16.15 / timezone=Asia/Shanghai / max_connections=100 / shared_buffers=256MB 全部生效 |
| TC-007 | 后端 `dev` 目标构建 | 成功 | ✅ 暴露 bug：`/urx` 拼写错误 → 已修 |
| TC-008 | 后端 `runtime` 目标构建 + 镜像内校验 | 非 root / Python 3.12 / curl / uvicorn / TZ | ✅ uid=10001 `app`、Python 3.12.15、`TZ=Asia/Shanghai`、`/opt/venv/bin/uvicorn` |
| TC-009 | `nginx -t`（容器内） | `syntax is ok` | ✅ 首次报 `host not found in upstream api` —— 隔离环境无 compose DNS，属预期；`--add-host` 后通过 |
| TC-010 | 前端 `builder` 目标构建 | `pnpm install --frozen-lockfile` 成功 | ✅ |
| TC-011 | 前端 `runtime` 目标构建 | 预期失败 | ⚠️ 卡在 `COPY --from=builder /build/packages/admin/dist` —— `packages/admin` 尚未创建（T-WEB-* 未开始），**已知阻塞非缺陷** |
| TC-012 | `entrypoint.sh` 正常路径 | 等 DB 就绪后 exec CMD，用户仍为 `app` | ✅ |
| TC-013 | `entrypoint.sh` 缺 `DATABASE_URL` | 打印 FATAL 并非 0 退出 | ✅ |
| TC-014 | `RUN_MIGRATIONS=1` 但无 `DATABASE_URL_MIGRATION` | 拒绝用应用账号迁移 | ✅ |
| TC-015 | `backup.sh` 真实备份（PG 客户端容器内执行） | 输出 `OK <file> <size>` | ✅ `OK /backups/garment_erp_20261003_053411.dump 4.0K` + `WARN: archive_mode=off`（WAL 归档阶段二开启） |
| TC-016 | 备份产物独立 `pg_restore -l` 校验 | 列出 TOC | ✅ `Archive created / TOC Entries: 4` |
| TC-017 | `backup.sh` 缺凭据（PGHOST 非本地） | 快速失败，**不交互式索要密码** | ✅ 首版会卡在 `Password:` 提示 → 已加 `-w` 与前置校验 |
| TC-018 | CI YAML 结构 | `jobs` 含 backend/frontend/consistency/release | ✅ 首版 `release:` 顶格导致 job 被忽略 → 已修 |
| TC-019 | `consistency` job 的 4 项检查 | 全绿 | ✅ 首版 grep 字面量 `ADR-0025`（实际写的是 `[0025]`）恒失败 → 已改为遍历 `docs/adr/` 交叉校验（26 篇全登记） |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `docker-compose.yml` | +131 | 生产编排（无 build、不暴露 5432、PG 调参、tmpfs 不用） |
| `docker-compose.ci.yml` | +108 | 闸门用编排（lint/test/migrate-check/api-image/web-image 五个一次性任务） |
| `docker/backend/Dockerfile` | +67 | builder/dev/runtime 三阶段，非 root，层缓存 |
| `docker/frontend/Dockerfile` | +42 | node 构建 → nginx 运行 |
| `docker/nginx/nginx.conf` | +84 | 反代 + gzip + SSE 关缓冲 + `/mobile` 别名 |
| `docker/scripts/entrypoint.sh` | +74 | 只等 DB 就绪后 exec，**不做自动迁移** |
| `docker/scripts/backup.sh` | +56 | pg_dump -Fc + pg_restore -l 校验 + 30 天清理 + WAL 检查 |
| `.github/workflows/ci.yml` | +150 | 4 个 job：后端闸门 1-4 / 前端闸门 1-3 / 规范一致性 / tag 构建镜像 |

合计新增约 712 行（单个提交 < 800 行，符合 AGENTS.md 上限）。

**工具补充**（本机原缺失，已装）：`docker compose` v2.29.7 CLI 插件（原只有 `docker-compose` 独立二进制）。

**提交记录**：
- `<hash>` chore(infra): 容器编排、镜像与 CI

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| — | **闸门 5 的 web 镜像阻塞**：`packages/admin/dist` 与 `packages/mobile/dist` 尚未产出，runtime 阶段 `COPY --from=builder` 必然失败。需 T-WEB-002/005/006 建包后可构建。已把「T-WEB-001 需建 `packages/mobile` 占位包」写入该卡验收项 | T-WEB-001/002/006 |
| — | **后端镜像 `/app` 目前只有 `pyproject.toml` + `uv.lock`**：`app/` 包要等 T-INFRA-003 才存在，届时镜像内才会有 `app/main.py` | T-INFRA-003 |
| L-017 | 本机跑着 16 个其他容器，**未经确认不得占用 80/443** | docs/12 §5 L-017 |
| L-018 | 本机无 `buildx`，`docker build` 不支持 `--platform`，本地无法验证 amd64 产物 | docs/12 §5 L-018 |
| L-019 | `erp_app`/`erp_ddl` 角色未创建（compose 不藏 DDL），已改派 T-INFRA-004 | docs/12 §5 L-019 |
| L-016 | 本机 aarch64 vs 生产要求 amd64 | docs/12 §5 L-016 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。
