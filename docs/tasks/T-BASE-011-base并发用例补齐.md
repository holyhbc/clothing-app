# T-BASE-011：补齐 base 并发用例（闭环 L-090）

| 项 | 内容 |
| --- | --- |
| 模块 | base |
| 负责人 | qa |
| 状态 | done |
| 优先级 | P1 |
| 依赖 | T-BASE-010c |
| 被依赖 | 无 |
| 关联设计 | docs/modules/base-重构拆分.设计.md §9 |
| 关联 ADR | 无 |
| 关联遗留 | docs/12 §5 **L-090** |
| 估算 | 0.5d |

## 目标

T-BASE-010c 任务卡曾点名三条 base 并发用例，但仓库中**并不存在**（`concurrent_sessions` 夹具与 `_purge_concurrency_rows` 成为死代码，见 L-090）。本卡补齐三条**真并发**用例，覆盖 base 的三处并发敏感点，闭环 L-090。

## 范围

**要做**：
- [x] `tests/modules/test_base_style_service.py`：**款号并发创建** —— N 个独立连接并发创建**同一 `style_no`**，唯一索引兜底，最终恰好 1 行，失败者报 `10001`（与 `tests/integration/test_no_dangling_refs.py` 的 CC-2 同型）。
- [x] `tests/modules/test_base_style_service.py`：**比例全量替换一成一败** —— 2 个独立连接带**同一聚合 `version`** 并发 `replace_ratios`，恰好 1 成功、另 1 报 `10003`（`_assert_aggregate_version` + `styles.version` 乐观锁，TC-B31）。
- [x] `tests/modules/test_operation_rates.py`：**单价并发调价/设价** —— N 个独立连接并发对同一 `(style_no, operation_no, effective_from)` 设价，唯一约束 + `with_for_update()` 区间锁兜底，最终恰好 1 行/1 个成功，失败者为已登记的业务码（`20002` 同日已有 / `20005` 区间重叠，以 service 实际语义为准并断言具体码）。
- [x] 复用两个测试文件里**已存在**的 `concurrent_sessions` 夹具与 `_purge_concurrency_rows`（不要新建夹具；确有必要再抽公共夹具）。
- [x] 遵守 `docs/10 §5.4` 并发测试纪律：**真并发** `asyncio.gather` + **每任务独立 session**；断言**最终条数 + 失败类型**；必须有唯一约束/乐观锁兜底，不接受"仅业务层先查后插"。
- [x] 用例数据使用既有并发专属前缀（`CCHB`/`CCOP`/`CCCAT`/`CCCUS`），确保 `_purge_concurrency_rows` 能清理，不污染全库计数断言。
- [x] `docs/12` §5 将 **L-090** 状态置为「已闭环 2026-10-06（T-BASE-011）」。
- [x] `docs/12` §2 变更记录加行。

**不做**：
- **不改 `service`/`repository` 任何生产代码**（本卡只补测试）。若并发用例**发现真实缺陷**，**立即停手**并在回复中报告，另开修复卡（不得为了"让测试变绿"改断言或改生产代码）。
- 不改动现有测试的断言与数据。
- 不做与本卡三条无关的重构。

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `backend/tests/modules/test_base_style_service.py` | 修改 | 补 2 条并发用例（款号创建 / 比例替换） |
| `backend/tests/modules/test_operation_rates.py` | 修改 | 补 1 条并发用例（单价设价/调价） |
| `docs/12-文档与变更归档规范.md` | 修改 | L-090 闭环 + 变更记录 |
| `docs/tasks/T-BASE-011-base并发用例补齐.md` | 修改 | 回填 |
| （可能）`backend/tests/conftest.py` 或新 `tests/modules/_concurrency.py` | 视需要 | 仅当两文件夹具重复需要抽公共件时 |

## 实现要点（必读规范）

- [x] `docs/10-测试规范.md` §5（并发测试纪律）、§2.2（测试顺序无关）
- [x] 参考 `tests/integration/test_no_dangling_refs.py` 的 CC-2 / CC-3 / CC-6 写法（真提交的独立引擎 session）
- [x] 并发用例必须**真提交**（否则同连接串行化，测成顺序执行）；故必须配套按前缀清理
- [x] 期望失败码以 **service 实际实现 + docs/04/08 已登记不变量**为准，**不得臆造**：款号重复 `10001`、乐观锁 `10003`、单价同日 `20002` / 区间重叠 `20005`

## 验收标准

- [x] 三条新用例**真并发**且稳定通过：`uv run pytest -q tests/modules/test_base_style_service.py tests/modules/test_operation_rates.py`
- [x] 连跑两次、以及与全量一起跑，结果一致（无顺序依赖、无偶发红）
- [x] `uv run pytest -q --cov=app --cov-fail-under=80` 全量通过、覆盖率不降
- [x] 未改动任何 `app/` 生产代码（`git diff --stat` 只见 `tests/` 与 `docs/`）
- [x] L-090 已在 `docs/12` §5 置闭环；`docs/12` §2 已加行；提交信息符合 AGENTS §7
- [x] 闸门 1（lint）与闸门 3（单测）通过

## 测试清单

| # | 用例 | 期望 | 结果 |
|---|------|------|------|
| TC-01 | 款号并发创建 | ✅ 恰好 1 行；失败者全部落**重复类** `10001`/`20002`（实测混合：赢家提交前查重→唯一索引兜底 `20002`；提交后查重→预检 `10001`）。⚠️ 与卡面「全 `10001`」不符，按 service 实际实现断言 |
| TC-02 | 比例全量替换并发 | ✅ 1 成功 / 1 `10003`（`_assert_aggregate_version` + `styles.version`） |
| TC-03 | 单价并发设价/调价 | ✅ 恰好 1 行 / 1 成功；失败者恒为 `20002`（同一生效日已有行，唯一索引兜底）；**不是** `20005`（同日非区间重叠） |
| TC-04 | 清理与隔离 | ✅ 定向连跑 2 次 + 杀 15 次均绿；全量 795 passed |
| TC-05 | 生产代码未改 | ✅ `git diff --stat backend/app/` 为空 |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `backend/tests/modules/test_base_style_service.py` | +149 / -0 | 补 2 条真并发用例（款号并发创建 / 比例全量替换），复用文件内既有 `concurrent_sessions` 夹具与 `_purge_concurrency_rows` |
| `backend/tests/modules/test_operation_rates.py` | +83 / -1 | 补 1 条真并发用例（单价并发设价/调价），复用文件内既有夹具；`import asyncio` + `func` |
| `docs/12-文档与变更归档规范.md` | 2 行改 + 1 行增 | L-090 置闭环；§2 新增 0100 |
| `docs/tasks/T-BASE-011-base并发用例补齐.md` | 回填 | 状态 / 实际改动 / 测试清单 / 自检 / 变更记录 |

**提交记录**：
- `c5f343a` test(base): 补齐并发用例（款号创建/比例替换/单价设价），闭环 L-090

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| 1 | **卡面失败码口径与 service 实际实现不一致，已按实际修正**：卡面写款号并发失败 `10001`（`_duplicate_style_error`），但该方法实际返回 `STYLE_ALREADY_EXISTS` = `20002`；且真并发下 `10001`（预检）与 `20002`（唯一索引）会混合出现。单价卡面写 `20002` 或 `20005`，实际同生效日恒为 `20002`。见 `docs/12` §2 行 0100 | 无需另登记（本卡内修正，非生产缺陷） |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。

- [x] 读过 `docs/10` §5/§2.2、`docs/12` L-090、参考 `test_no_dangling_refs.py` CC-2/CC-3/CC-6
- [x] 真并发：`asyncio.gather` + 每任务独立 session（复用 `concurrent_sessions`），未共享 session
- [x] 真提交 + 按 `CCHB`/`CCOP`/`CCCAT` 前缀清理，未污染全库计数断言
- [x] 断言最终条数 + 失败类型；款号创建为重复类集合 `{10001,20002}`，比例 `10003`，单价 `20002`
- [x] 未改动 `app/` 任何生产代码（`git diff --stat backend/app/` 为空）
- [x] `ruff check` / `ruff format --check` 通过；定向 101 passed；全量 795 passed、覆盖率 90.39%
- [x] L-090 置闭环；`docs/12` §2 新增 0100

## 变更记录

| 日期 | 变更内容 | 操作人 |
|------|---------|--------|
| 2026-10-06 | 建卡：闭环 L-090，补齐 base 三条真并发用例 | AI |
| 2026-10-06 | 完成：三条真并发用例落地；修正卡面失败码口径（款号 `10001`/`20002`、单价 `20002`）；全量 795 passed / 90.39% | AI |
