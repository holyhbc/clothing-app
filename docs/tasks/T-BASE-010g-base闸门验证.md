# T-BASE-010g：运行闸门 1-5 全链路验证

| 项 | 内容 |
| --- | --- |
| 模块 | base |
| 负责人 | qa |
| 状态 | done |
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
- [x] 执行 `scripts/gate.sh`（或本地预跑脚本）完整跑通 5 道闸门
- [x] 验证 `alembic check` 无漂移
- [x] 验证 `pnpm generate:api` 生成的 `schema.d.ts` 与拆分前无 diff
- [x] 验证前端 `admin` 编译通过（`pnpm build`）
- [x] 验证全量测试通过、覆盖率达标
- [x] 统计本次新增文件的实际行数，确认**每个 ≤400 行**（见下"范围口径"）
- [x] 更新 `docs/12-文档与变更归档规范.md` 变更记录表加行
- [x] 提交符合 AGENTS.md §7 格式的 commit

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

- [x] 闸门 1：`cd backend && uv run ruff check . && uv run ruff format --check .` / `cd frontend && pnpm lint`
- [x] 闸门 2：`cd backend && uv run mypy app` / `cd frontend && pnpm typecheck`
- [x] 闸门 3：`cd backend && uv run pytest -q --cov=app --cov-fail-under=80` / `cd frontend && pnpm test:unit`
- [x] 闸门 4：`uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head && uv run alembic check`
- [x] 闸门 5：`scripts/gate.sh` 中的镜像构建 + 启动验证（或 `docker compose -f docker-compose.ci.yml build api-image web-image && ... run --rm`）
- [x] **范围口径**：AGENTS §7.1 的单文件 400 行硬线按**手写行数**口径，本卡只对本次新增/拆分出的文件计数，不含生成物；非目标清单里的其它长文件不纳入本次验收
- [x] 变更记录格式（行数按实际回填，**不再写初版的错误规模**）：`| 2026-10-06 | T-BASE-010 | base 模块拆分重构 | 7 models + 6 schemas + 15 service + 8 router + 13 permissions | 修正行数口径后按真实结构拆分，全部 ≤400 行 | AI |`

## 验收标准

- [x] 5 道闸门全部绿
- [x] `alembic check` 输出 `No new operations`
- [x] `pnpm generate:api` 后 `git diff frontend/packages/shared/src/api/schema.d.ts` 无变化
- [x] 前端 `pnpm build` 成功
- [x] 本次新增文件全部 ≤400 行（`git diff --numstat` 逐文件核对）
- [x] 变更记录已加行
- [x] 提交信息符合规范

## 测试清单

| # | 检查项 | 期望 | 结果 |
|---|--------|------|------|
| TC-01 | 闸门 1 Lint | 0 error | 通过（ruff `All checks passed!`、159 files already formatted） |
| TC-02 | 闸门 2 Typecheck | 0 error | 通过（`Success: no issues found in 101 source files`） |
| TC-03 | 闸门 3 单测+覆盖率 | 全过、≥80%（核心 ≥90%） | 通过（792 passed / 0 skipped / 90.15%，阈值 80%） |
| TC-04 | 闸门 4 迁移往返 | upgrade/downgrade/upgrade 全过、check 无漂移 | 通过（往返成功停在 0013 (head)；`No new upgrade operations detected.`） |
| TC-05 | 闸门 5 镜像构建 | api-image/web-image 构建成功、启动探针 200 | 通过（api-image `api image import ok`；web-image `nginx -t` ok + 首页 HTTP 200） |
| TC-06 | OpenAPI 无 diff | `schema.d.ts` 与拆分前完全一致 | 通过（4 项生成物 `git diff --exit-code` 无输出） |
| TC-07 | 变更记录 | docs/12 已加行 | 通过（§2 序号 0098） |
| TC-08 | 单文件行数 | 本次新增文件全部 ≤400 | 通过（最大 348 行 `service/style_template_mixin.py`） |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `docs/12-文档与变更归档规范.md` | +1 / -0 | §2 新增收口行 0098 |
| `docs/tasks/T-BASE-010g-base闸门验证.md` | +35 / -33 | 状态置 done、清单勾选、测试结果与实际改动回填 |

**提交记录**：
- 642c5ad chore(base): pass all 5 gates after module split

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
| 2026-10-06 | T-BASE-010g 收口：5 道闸门全链路实跑通过（含闸门 5 双镜像构建 + 启动探针），OpenAPI 零 diff、`pnpm build` 退出码 0、`alembic check` 无漂移、单文件最大 348 行 | AI |
