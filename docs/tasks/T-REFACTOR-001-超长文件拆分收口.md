# T-REFACTOR-001：cutting/system/auth/cli 拆分收口（闸门 1-5）

| 项 | 内容 |
| --- | --- |
| 模块 | 全局 |
| 负责人 | backend-dev / releaser |
| 状态 | done |
| 优先级 | P0 |
| 依赖 | T-CUT-002a, T-CUT-002b, T-CUT-002c, T-SYS-001a, T-SYS-001b, T-AUTH-004, T-INFRA-005 |
| 被依赖 | 无 |
| 关联设计 | docs/modules/cutting-system-auth-cli-拆分.设计.md §1, §8, §9, §10 |
| 关联 ADR | docs/adr/0031-模块文件结构按职责拆分.md、docs/adr/0030-提交体量上限放宽到1200行.md |
| 估算 | 0.5d |

## 目标

在全部 7 张拆分卡完成后，做一次**统一收口**：确认单文件全部 ≤400、对外导入面零改动
（唯一例外已按设计稿 §2.8 修正）、OpenAPI 零 diff、全量测试与 5 道闸门全绿。

## 范围

**要做**：
- [x] 全量收集：`uv run pytest tests/ --co -q` 无 `ImportError`/`ModuleNotFoundError`（795 collected）
- [x] 全量测试：`uv run pytest -q --cov=app --cov-fail-under=80`（795 passed / 0 skipped / 90.78%）
- [x] 逐文件核对：本次 7 个文件拆出的所有手写文件 `wc -l` ≤400（最大 381）
- [x] `uv run alembic check` 零漂移
- [x] **OpenAPI 零 diff**：`pnpm generate:api` 后
      `openapi.json` / `schema.d.ts` / `permissions.ts` / `baseDictFields.ts` 四项 `git diff --exit-code` 无输出
- [x] 闸门 1：`ruff check` + `ruff format --check` 0 error
- [x] 闸门 2：`mypy app` 0 error
- [x] 闸门 3：全量单测 + 覆盖率达标
- [x] 闸门 4：迁移 `upgrade → downgrade → upgrade` 往返并停在 head；`alembic check` 无新操作
- [x] 闸门 5：`api-image` / `web-image` 生产镜像构建成功 + 启动探针
- [x] `docs/12` §2 变更记录补齐本系列各卡归档行；按设计稿 Q-05 更新 L-081 存量清单

**不做**：
- 不在收口卡里改任何业务代码；发现问题退回对应拆分卡修
- 不做「顺手重构」无关文件

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `docs/12-文档与变更归档规范.md` | 修改 | 补齐各卡变更记录 |
| （无生产代码改动） | - | 收口验证 |

## 实现要点（必读规范）

- [ ] 遵守 docs/02-AI开发流水线规范.md §6：5 道闸门定义
- [ ] 遵守 docs/11-部署运维与发布规范.md：镜像构建与探针
- [ ] `AGENTS §7.1`：如单次提交超 1200 行或单文件超 400，必须在提交信息与 docs/12 写明构成与理由
- [ ] 收口提交前的文件清单与行数构成写进提交信息

## 验收标准

- [x] 收集无 `ImportError`
- [x] 全量测试全绿、覆盖率 ≥80%（不退步）
- [x] 本次拆出的所有手写文件单文件 ≤400 行
- [x] `alembic check` 零漂移
- [x] OpenAPI 四项零 diff
- [x] 闸门 1-5 全绿
- [x] `docs/12` 变更记录齐全

## 测试清单

| # | 用例 | 期望 | 结果 |
|---|------|------|------|
| TC-01 | `pytest --co -q` | 无导入错误 | ✅ 795 collected，无 `ImportError` |
| TC-02 | 全量 pytest | 全绿、覆盖率不退步 | ✅ 795 passed / 0 skipped / 90.78%（阈值 80%） |
| TC-03 | 单文件行数 | 全部 ≤400 | ✅ 全部 ≤400，最大 381（`cutting/models/lines.py`）；`backend/app/` 无 >400 文件 |
| TC-04 | `alembic check` | `No new upgrade operations detected.` | ✅ 逐字命中 |
| TC-05 | `generate:api` + diff | 四项零 diff | ✅ `git diff --exit-code` 无输出 |
| TC-06 | 闸门 1-5 | 全绿 | ✅ `scripts/gate.sh`（docker）全绿，含闸门 5 双镜像 + 探针 |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `app/modules/cutting/models/` | 765（5 文件，最大 381） | `models.py` 696 行 → 4 领域文件 + 包 `__init__`（T-CUT-002a） |
| `app/modules/cutting/schemas/` | 563（4 文件，最大 282） | `schemas.py` 457 行 → 3 内容文件 + 包 `__init__`（T-CUT-002b） |
| `app/modules/cutting/service/` | 1306（8 文件，最大 279） | `service.py` 1047 行 → 5 Mixin + `recalc` + 组合类 + 包 `__init__`（T-CUT-002c） |
| `app/modules/system/service/` | 994（7 文件，最大 347） | `service.py` 869 行 → 用户 3 Mixin + 角色 + common + 包 `__init__`（T-SYS-001a） |
| `app/modules/system/router/` | 584（6 文件，最大 226） | `router.py` 511 行 → 4 子 router + `deps` + 包 `__init__`（T-SYS-001b） |
| `app/modules/auth/service/` | 557（6 文件，最大 200） | `service.py` 424 行 → 查询/登录/令牌 3 Mixin + common + 组合类 + 包 `__init__`（T-AUTH-004） |
| `app/cli/seed_dicts/` | 544（4 文件，最大 271） | `seed_dicts.py` 447 行 → 数据/写入/校验 + 包 `__init__`（T-INFRA-005） |
| `docs/12-文档与变更归档规范.md` | 变更记录 0109 + L-081 现状 | 收口归档（本卡） |
| `docs/tasks/T-REFACTOR-001-超长文件拆分收口.md` | 本卡回填 | 状态置 done |

> 行数构成（拆分卡各提交合计）：原 7 文件 **4451 行**搬运；拆后 7 包共 **5313 行**，净 **+862 行**（其中 7 个包 `__init__.py` 共 **391 行**，余为内容文件 import/docstring/常量开销）。本卡**不改任何业务代码**，仅归档文档。

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| | | docs/12 §遗留问题清单 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。

## 变更记录

| 日期 | 变更内容 | 操作人 |
|------|---------|--------|
| 2026-10-06 | 初版：超长文件拆分收口卡（依赖 7 张拆分卡），照抄 T-BASE-010g | AI |
| 2026-10-06 | 收口完成：`scripts/gate.sh`（docker）5 道闸门全绿（含闸门 5 双镜像 + 探针）、OpenAPI 零 diff、`backend/app/` 无 >400 文件、L-081 存量清单同步；状态置 done | AI |
| 2026-10-06 | 回填提交 hash：`-` | AI |
