# T-INFRA-006：把 E2E 接进 CI

| 项 | 内容 |
| --- | --- |
| 模块 | infra |
| 负责人 | qa + backend-dev |
| 状态 | `done` |
| 优先级 | P1（**回归保护**：不进 CI 的测试等于没有） |
| 依赖 | T-INFRA-002（CI 骨架）、T-CUT-001c-4 / T-WEB-006 / T-BUND-010（E2E 已有） |
| 被依赖 | — |
| 关联设计 | [`docs/02`](../docs/02-AI开发流水线规范.md) §1 流水线图（阶段 4「覆盖率 + **E2E** + 闸门」）、[`docs/10`](../docs/10-测试规范.md) §4（E2E 要求）、[`docs/11`](../docs/11-部署运维与发布规范.md) §4 |
| 估算 | 0.25d |

## 目标

`ci.yml` 里加一个 `e2e` job，让 `p0-main-flow` / `p1-cutting-flow` / `p1-bundling-flow`
在**每次 push / PR** 上真跑。

## 为什么要做（现状）

`ci.yml` 只有 `backend`（闸门 1-4）、`frontend`（闸门 1-3）、`consistency`、`release`（构建镜像）。
**没有 E2E job** —— 而 `docs/02` §1 的流水线图里阶段 4 明写「覆盖率报告 + **E2E** + 闸门」。

后果不是「少跑几个测试」，而是：

- 2026-10-12 那次打菲 E2E 挖出的 **8 个缺陷**（款号传 UUID、标签预览取 `res.data`、
  `bwip-js` 在浏览器构建上没有 `toDataURL`、详情页按手列表是占位……）在 CI 里**全绿通过过**；
- `p0-main-flow` 那条「单跑绿、全量红」的菜单用例，本地连跑 3 轮才复现 ——
  这种 flaky 在没有 CI 的情况下连「已知问题」都算不上，因为没人知道它红过。

单测能守住「点得动」，守不住「跨层一致」—— 那正是 E2E 的唯一职责。

## 范围

**要做**：
- [x] `ci.yml` 加 `e2e` job：起 pg/redis → 建 E2E 库 → 装 chromium → 跑 Playwright → 传制品
- [x] 复用 `docker-compose.ci.yml` 的 postgres/redis（端口 15432/16379、角色初始化脚本、
      授权口径全在这一个文件里）
- [x] `uv sync --frozen`：`create-e2e-db.sh` 与 `start-backend.sh` 都走 `.venv/bin/python`
- [x] 失败时上传 `playwright-report` 与 `test-results`（trace/截图/录像）

**不做**：
- 不改 `docs/10` 的 E2E 清单、不加新用例（加用例另开卡）
- 不动 `release` job 的触发条件（见「遗留问题」）
- 不把 E2E 塞进现有 job（前端 job 与它没有共享的 pg/redis 生命周期，混在一起只会更难定位失败）

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `.github/workflows/ci.yml` | 修改 | 新增 `e2e` job |

## 实现要点

- [x] **复用 compose 而不是 `services:`**。GitHub Actions 的 `services:` 不支持挂载
      volume，`docker/postgres/init/01-create-roles.sh` 就跑不起来 —— 而没有它
      `erp_app` / `erp_ddl` 两个角色不存在，`create-e2e-db.sh` 直接 `:?` 退出。
      另起一份 `services:` 配置还意味着**授权口径与 `docker-compose.ci.yml` 各写一份**，
      必然漂移（docs/04 §6.2.1：E2E 必须照抄生产的授权口径，否则测不出权限问题）。
- [x] **`uv sync --frozen` 不能省**：`e2e/global-setup.ts` 与 `e2e/start-backend.sh`
      都硬依赖 `backend/.venv/bin/python`，找不到就抛
      「先在 backend/ 装好虚拟环境（uv sync）」。
- [x] **端口显式给 `PGPORT=15432` / `REDIS_URL=…:16379/1`**：`create-e2e-db.sh` 的
      `PGPORT` 默认 5432，而 compose 映射的是 15432 —— 不给就静默连到宿主机上的
      另一个 postgres 上（开发机上正好有）。
- [x] **CI 里不许设 `E2E_CHROMIUM_PATH`**：那是本机 arm64 的逃生口，设了会让
      Playwright 跳过自己的浏览器下载，直接用不存在的路径启动失败。
- [x] **`APP_ENV=test`**：`AppEnv` 只允许 `local/test/production`，E2E 不是合法值。

## 验收标准

- [x] 本机按 CI 的命令序列（compose 起库 → `create-e2e-db.sh` → `test:e2e`）跑通 6/6
- [x] `ci.yml` 语法通过（`actionlint` 0 error）
- [x] `consistency` job 的 4 条守卫全绿 —— ⚠️ 第 1 条**本来就是红的**
      （ADR-0032/0033 未登记索引，红了 5 天），顺带补上（变更 0140）
- [x] 变更记录留痕；发现的问题登记进 `docs/12` 遗留清单（L-118 / L-119）

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-INFRA-06-01 | compose 起 pg/redis，`create-e2e-db.sh` 建库 | 角色齐全、迁移到 head | ✅ 49 张表授权、迁移到 0018 |
| TC-INFRA-06-02 | `pnpm test:e2e` | 6 passed | ✅ 37.7s |
| TC-INFRA-06-03 | 故意把 `toHaveCount(2)` 改成 `(999)`，`CI=true` 跑 | 失败且产出 trace.zip | ✅ 产出 `trace.zip` + 截图 + `error-context.md` |
| TC-INFRA-06-04 | `actionlint .github/workflows/ci.yml` | 0 error | ✅ |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `.github/workflows/ci.yml` | +92 | 新增 `e2e` job（11 步） |
| `docs/01-技术选型与架构.md` | +5 | 补 ADR 索引 0032/0033（`consistency` 守卫 1 从红转绿） |
| `docs/12-文档与变更归档规范.md` | +3 | 变更 0139/0140 + L-118/L-119 |

**行数构成**：手写 +100 / −0，**无生成物**，单文件最大 179 行（`ci.yml`），未超 1200 / 400 两条线。

**提交记录**：
- `<hash>` ci: 把 E2E 接进 CI —— 顺带补回 ADR 索引（consistency 红了 5 天）

## 本机验证的边界（要说清楚）

⚠️ **只有「浏览器二进制」这一步没在本机验过**：`pnpm exec playwright install chromium`
在本机（ubuntu20.04 / arm64）直接报 `Playwright does not support chromium on
ubuntu20.04-arm64` —— 这正是 `E2E_CHROMIUM_PATH` 逃生口存在的原因。
所以本机是**带着 `E2E_CHROMIUM_PATH` 跑完全流程**（库/Redis/JWT/账号口令全部用 CI 的
那套环境变量，只换浏览器路径），6/6 通过；CI 上 `ubuntu-latest` 是 x86_64，
`playwright install --with-deps chromium` 会正常装上。

其余每一步都在本机按 CI 的命令序列实跑过：compose 起库 → `create-e2e-db.sh` →
`test:e2e` → 故意改坏断言验 trace → `actionlint`。

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| `ci.yml` 的 `on.push` 只有 `branches: [main]`、**没有 `tags:`** —— `release` job 的
  `if: startsWith(github.ref, 'refs/tags/v')` 因此**永远为假**，镜像发布流程按现状
  根本不会触发 | `docs/12` L-118（**不在本卡改**：加上 tags 会让每次打 tag 立刻构建并
  推送镜像，是要确认的行为变更） |
| E2E 仍是**共用一个库 + 串行**（`fullyParallel: false`），与 `docs/10` §4
  「每个 E2E 用独立库，CI 可并行」不符。当前 3 条用例不值得为此建库 | 待排期 |

## 自检清单

对照 `AGENTS.md` §9 逐条确认。

## 变更记录

| 日期 | 变更内容 | 操作人 |
| --- | --- | --- |
| 2026-10-12 | 初版：把 E2E 接进 CI | AI |
| 2026-10-12 | 置 `done`；顺带补 ADR 0032/0033 索引（`consistency` 守卫 1 红了 5 天）；发布流程与 CI 观测两个问题转入 L-118/L-119 | AI |