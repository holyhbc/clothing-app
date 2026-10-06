# T-BASE-010g：运行闸门 1-5 全链路验证

| 项 | 内容 |
| --- | --- |
| 模块 | base |
| 负责人 | qa |
| 状态 | todo |
| 优先级 | P0 |
| 依赖 | T-BASE-010f |
| 被依赖 | 无（里程碑收口） |
| 关联设计 | docs/modules/base-重构拆分.设计.md §1, §8, §10 |
| 关联 ADR | docs/adr/0030-提交体量上限放宽到1200行.md |
| 估算 | 0.5d |

## 目标

执行 5 道闸门完整验证，确保 base 模块拆分重构零回归、全绿通过，并将变更落库到 `docs/12`。

## 范围

**要做**：
- [ ] 执行 `scripts/gate.sh`（或本地预跑脚本）完整跑通 5 道闸门
- [ ] 验证 `alembic check` 无漂移
- [ ] 验证 `pnpm generate:api` 生成的 `schema.d.ts` 与拆分前无 diff
- [ ] 验证前端 `admin` 编译通过（`pnpm build`）
- [ ] 验证全量测试通过、覆盖率达标
- [ ] 统计本次新增文件的实际行数，确认**每个 ≤400 行**（见下"范围口径"）
- [ ] 更新 `docs/12-文档与变更归档规范.md` 变更记录表加行
- [ ] 提交符合 AGENTS.md §7 格式的 commit

**不做**：
- 不做功能性手工测试（自动化测试已覆盖）
- 不改业务代码
- **不验证本卡范围外的其它 >400 行文件**（`cutting/service.py` 1047 等，见设计稿 §1 非目标）

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `docs/12-文档与变更归档规范.md` | 修改 | 变更记录加行 |
| 无业务代码 | - | 仅验证 |

## 实现要点（必读规范）

- [ ] 闸门 1：`cd backend && uv run ruff check . && uv run ruff format --check .` / `cd frontend && pnpm lint`
- [ ] 闸门 2：`cd backend && uv run mypy app` / `cd frontend && pnpm typecheck`
- [ ] 闸门 3：`cd backend && uv run pytest -q --cov=app --cov-fail-under=80` / `cd frontend && pnpm test:unit`
- [ ] 闸门 4：`uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head && uv run alembic check`
- [ ] 闸门 5：`scripts/gate.sh` 中的镜像构建 + 启动验证（或 `docker compose -f docker-compose.ci.yml build api-image web-image && ... run --rm`）
- [ ] **范围口径**：AGENTS §7.1 的单文件 400 行硬线按**手写行数**口径，本卡只对本次新增/拆分出的文件计数，不含生成物；非目标清单里的其它长文件不纳入本次验收
- [ ] 变更记录格式（行数按实际回填，**不再写初版的错误规模**）：`| 2026-10-06 | T-BASE-010 | base 模块拆分重构 | 7 models + 6 schemas + 15 service + 8 router + 13 permissions | 修正行数口径后按真实结构拆分，全部 ≤400 行 | AI |`

## 验收标准

- [ ] 5 道闸门全部绿
- [ ] `alembic check` 输出 `No new operations`
- [ ] `pnpm generate:api` 后 `git diff frontend/packages/shared/src/api/schema.d.ts` 无变化
- [ ] 前端 `pnpm build` 成功
- [ ] 本次新增文件全部 ≤400 行（`git diff --numstat` 逐文件核对）
- [ ] 变更记录已加行
- [ ] 提交信息符合规范

## 测试清单

| # | 检查项 | 期望 | 结果 |
|---|--------|------|------|
| TC-01 | 闸门 1 Lint | 0 error | |
| TC-02 | 闸门 2 Typecheck | 0 error | |
| TC-03 | 闸门 3 单测+覆盖率 | 全过、≥80%（核心 ≥90%） | |
| TC-04 | 闸门 4 迁移往返 | upgrade/downgrade/upgrade 全过、check 无漂移 | |
| TC-05 | 闸门 5 镜像构建 | api-image/web-image 构建成功、启动探针 200 | |
| TC-06 | OpenAPI 无 diff | `schema.d.ts` 与拆分前完全一致 | |
| TC-07 | 变更记录 | docs/12 已加行 | |
| TC-08 | 单文件行数 | 本次新增文件全部 ≤400 | |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `docs/12-文档与变更归档规范.md` | +10 / -0 | 变更记录 1 行 |

**提交记录**：
- `<hash>` chore(base): pass all 5 gates after module split

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| | | docs/12 §遗留问题清单 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。

## 变更记录

| 日期 | 变更内容 | 操作人 |
|------|---------|--------|
| 2026-10-06 | 修正行数口径：更新变更记录行内文件数与拆分描述（7 models + 6 schemas + 15 service + 8 router + 13 permissions），明确单文件 400 行仅对本次新增/拆分文件计数 | AI |
