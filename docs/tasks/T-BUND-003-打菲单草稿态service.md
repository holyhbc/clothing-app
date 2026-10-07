# T-BUND-003：打菲单草稿态 service（表头 + 明细全量替换 + 乐观锁）

| 项 | 内容 |
| --- | --- |
| 模块 | bundling |
| 负责人 | backend-dev |
| 状态 | `done` |
| 优先级 | P1 |
| 依赖 | T-BUND-001 |
| 被依赖 | T-BUND-004、T-BUND-005a、T-BUND-007 |
| 关联设计 | [`modules/03-打菲.md`](../modules/03-打菲.md) §4 `create`/`update`、§3.1/§3.2、§7 并发；[`P1-打菲-实施说明.md`](../modules/P1-打菲-实施说明.md) §6 |
| 关联需求 | [`REQ-000`](../requirements/REQ-000-工期优化与复用策略.md) §2（`cutting` 三层 / 乐观锁 / 全量替换复用） |
| 关联 ADR | [ADR-0017](../adr/0017-裁剪按布批与行内多颜色.md)、[ADR-0031](../adr/0031-模块文件结构按职责拆分.md) |
| 估算 | 0.5d |

## 目标

打菲单草稿态可建、可改、可查、可列，明细按尺码整表保存（全量替换），
表头汇总由 service 重算、乐观锁用条件 UPDATE；**不碰状态机**（T-BUND-005a 起）。

## 范围

**要做**：
- [ ] `backend/app/modules/bundling/repository.py`：只查不写（照 `cutting/repository.py`）
- [ ] `backend/app/modules/bundling/schemas.py`：`BundlingOrderCreateIn` / `PatchIn` /
      `PutLinesIn` / `BundlingOrderOut` / `ListOut` / `LineIn` / `LineOut`
      （表头汇总列**不入参**，`extra="forbid"`；响应数量一律 `str`）
- [ ] `backend/app/modules/bundling/service.py`（首版单文件，超 400 行再按 ADR-0031 拆包）：
  - `create`：生成 `doc_no`（T-BUND-002 的 `next_doc_no`）；校验款号/工序启用、
    来源裁剪单 `APPROVED`（B8）、`cutting_size_line_id` 存在且尺码匹配、每行 `hands > 0`；
    重算 `hands_total` / `output_qty` / `balance_qty` 并落库
  - `update`（PATCH）：`version` 条件 UPDATE，`rowcount=0 → 10003`；仅 `DRAFT`/`REJECTED`
  - `put_lines`：整表全量替换（软删旧行 + 插新行，**不物理删除**）
  - `get` / `list_orders`：读表头 + 明细；列表带 `status`/`style_no`/`operation_no`/`color_code`/日期区间
- [ ] `backend/tests/factories/bundling.py`：建单夹具（幂等，照 `tests/factories/cutting.py`）
- [ ] `backend/tests/modules/test_bundling_service.py`：建/改/查/列表 + 乐观锁 + 软删

**不做**：
- 不做任何状态迁移（`submit`/`approve`/… → T-BUND-005a/005b）
- 不做 `/split` 预演（→ T-BUND-004）
- 不写 router（→ T-BUND-007）
- **不信任前端汇总**：`hands_total` / `output_qty` / `balance_qty` / `planned_qty` 全部 service 重算

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `backend/app/modules/bundling/repository.py` | 新增 | 只读查询 |
| `backend/app/modules/bundling/schemas.py` | 新增 | pydantic 入参/出参 |
| `backend/app/modules/bundling/service.py` | 新增 | 草稿态业务逻辑与事务边界 |
| `backend/tests/factories/bundling.py` | 新增 | 夹具 |
| `backend/tests/modules/test_bundling_service.py` | 新增 | service 单测 |

## 实现要点（必读规范）

- [ ] **复用 `cutting` 的三层/全量替换/乐观锁模式**：
      `docs/modules/cutting-system-auth-cli-拆分.设计.md` §2.3 的 5 Mixin 配方；
      乐观锁必须用**条件 UPDATE**（`WHERE version=:expected` + `RETURNING`），
      **不得**「先读后比再 commit」（T-CUT-001b-2 的假乐观锁教训）
- [ ] **复用 `AuthContext` / `apply_data_scope`**（`app/core/scope.py`）：表头 `workshop_id` 隔离
- [ ] **复用审计与日志**：`create`/`update` 写 `document_logs`（`doc_type='BundlingOrder'`）；
      状态字段只允许 service 的 `_apply_status` 写（本卡不涉及状态）
- [ ] **复用 `unit_of_work`**：事务边界只在 service；`session.commit()` 不得出现在 service 外
- [ ] `cutting_size_line_id` 是每行必填（B26）；`size_code` 允许同尺码多行（ADR-0017 §4）
- [ ] 乐观锁/全量替换的口径与字段级 diff 写 `document_logs.changed_fields`

## 验收标准

- [ ] `uv run pytest tests/modules/test_bundling_service.py -q` 全部通过
- [ ] 建单后表头汇总 = Σ明细（`hands_total` / `output_qty` / `balance_qty` 落库且正确）
- [ ] 全量替换：软删旧行 + 插新行，表头重算，返回重读（无 `MissingGreenlet`）
- [ ] 乐观锁：同版本并发写一成一败（`10003`）
- [ ] 越权：非本车间 `created_by` 不可改（`12002`）
- [ ] `hands = 0` → `10001`；来源裁剪单未审核 → 建单拒绝
- [ ] 单文件 ≤400 行；闸门 1-4 本地预跑通过

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-BS-01 | 建单三层 | 表头+明细落库，汇总正确 | |
| TC-BS-02 | 全量替换 | 软删旧行、汇总更新、旧行 `deleted_at` 非空 | |
| TC-BS-03 | 乐观锁并发 | 一成一败 `10003` | |
| TC-BS-04 | `hands = 0` | `10001`，零行写入 | |
| TC-BS-05 | 来源裁剪单 `DRAFT` | 建单拒绝 | |
| TC-BS-06 | 列表筛选 + 分页 | 按 `workshop_id`/`status`/日期 | |
| TC-BS-07 | `cutting_size_line_id` 尺码不匹配 | `10001`/`20001` | |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `backend/app/modules/bundling/service/bundling_order_service.py` | 254 | 公开方法（create/patch/put_lines/get/list_orders）+ 组合类 |
| `backend/app/modules/bundling/service/common.py` | 225 | `CommonMixin`：私有助手（校验/取单/乐观锁/明细落库/汇总/写日志）——按 ADR-0031 从 461 行单文件拆出 |
| `backend/app/modules/bundling/repository.py` | 238 | 只读查询 |
| `backend/app/modules/bundling/schemas.py` | 196 | pydantic 入参/出参 |
| `backend/alembic/versions/0017_bundling_lines_partial_unique_index.py` | 53 | 唯一约束 → 部分唯一索引 |
| `backend/app/modules/bundling/models/order.py` | 修改 | `uq_bundling_order_lines_line` 改部分索引 |
| `backend/tests/factories/bundling.py` / `test_bundling_service.py` | 346 / 398 | 夹具 + service 单测 |
| `backend/tests/conftest.py` | +38 | `bundling_world` 夹具 |

**提交记录**：
- `5585720` feat(bundling): 打菲单草稿态 service（表头 + 明细全量替换 + 乐观锁，T-BUND-003）
- `<split-hash>` refactor(bundling): 按 ADR-0031 把 461 行 service 拆为 `common.py`(CommonMixin) + 组合类（≤400 行）

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| | | `docs/12` §遗留问题清单 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。

## 变更记录

| 日期 | 变更内容 | 操作人 |
| --- | --- | --- |
| 2026-10-06 | 初版：草稿态 CRUD + 全量替换 + 条件 UPDATE 乐观锁，照 cutting 配方 | AI |
