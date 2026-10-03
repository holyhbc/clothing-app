# T-INFRA-002：容器编排、镜像与 CI

| 项 | 内容 |
| --- | --- |
| 模块 | infra |
| 负责人 | AI |
| 状态 | `todo` |
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
| TC-001 | `docker compose config -q` | 退出码 0 | |
| TC-002 | 生产 compose 全文搜 `build:` | 0 处 | |
| TC-003 | 生产 compose 全文搜 `5432:` 对外映射 | 0 处 | |
| TC-004 | `docker compose -f docker-compose.ci.yml build` | 成功 | |
| TC-005 | 起 PG/Redis 后跑 `backup.sh` 两次 | 均输出 `OK`，文件可 `pg_restore -l` | |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| | +0 / -0 | |

**提交记录**：
- `<hash>` chore(infra): 增加容器编排、镜像与 CI …

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| | | docs/12 §遗留问题清单 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。
