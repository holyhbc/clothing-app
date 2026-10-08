# T-BUND-007b：拆分预演 / 码查询 / 单码作废 / 统计导出

| 项 | 内容 |
| --- | --- |
| 模块 | bundling |
| 负责人 | backend-dev |
| 状态 | `todo` |
| 优先级 | P1 |
| 依赖 | T-BUND-006（标签 service 已就绪）、T-BUND-005b（码已生成）、T-BUND-007a（router 骨架与 `main.py` 挂载） |
| 被依赖 | T-BUND-008、T-BUND-009、T-BUND-010 |
| 关联设计 | [`modules/03-打菲.md`](../modules/03-打菲.md) §6、§10 权限矩阵、§5.2 码规则 |
| 估算 | 0.5d |

## 目标

补齐 T-BUND-007a 留下的 service 缺口，并暴露**辅助能力**端点：拆分预演、标签两接口、码查询、
单码作废、统计、导出。

## 范围

**要做**：

- [ ] **补 service 方法**（本卡的主体 —— `03 §6` 列了这些端点但 service 侧还没有）：
  - [ ] `list_bundles(...)` / `get_bundle(bundle_no)`：码列表与单码详情
        （出参照 `03 §6` 那一行的字段清单：`hands` / `hands_total_of_size` /
        `cutting_size_line_id` + 所属缸号匹号回查 / `counted_qty` / `counted_by_name` / `counted_at`）
  - [ ] `void_code(bundle_no, reason)`：**已计件 → `32003`**；码置 `VOIDED` + `voided_at` +
        `void_reason`，**行保留不删**（B12，不可恢复）
  - [ ] `statistics(...)`：按 `03` 的统计口径（先核对 `03 §6` 那行，别自行发明维度）
  - [ ] `export_orders(...)`：导出（`bundling:export` 权限点已存在）
- [ ] `backend/app/modules/bundling/router.py` 增补 7 个端点：
      `split`（`bundling:read`）、`labels` / `label-prints`（`bundling:print`）、
      `GET /bundles` / `GET /bundles/{bundle_no}`（`bundling:read`）、
      `POST /bundles/{bundle_no}/voids`（`bundling:code:void`）、
      `statistics`（`bundling:read`）、`exports`（`bundling:export`）
- [ ] `tests/modules/test_bundling_router2.py`（命名沿用仓库既有 `-2` 惯例，勿与 007a 撞名）

**不做**：
- 统计/导出的**前端**（→ T-BUND-009/010）
- 打印模板与浏览器打印（→ T-BUND-010）
- L-097 的 `super_admin` + `force=true`（单开卡，勿塞进本卡）

## 实现要点（必读规范）

- [ ] `docs/05 §3`：统一响应包装；**数量金额一律字符串**；CSV 用 `text/csv; charset=utf-8-sig`
- [ ] `docs/05 §5`：`Idempotency-Key` 用于 **voids / label-prints**
- [ ] 码的数据范围：`bundles` 无 `workshop_id`，按 **`doc_id → bundling_orders.workshop_id`** 回查后过滤
      （`core/scope.py` 的 `apply_data_scope` 是否支持这种「经关联表过滤」要先读实现再决定，
      **不要自己拼 where**）
- [ ] **路由顺序陷阱**：`/statistics`、`/exports` 必须声明在 `/{order_id}` **之前**（T-CUT-001c-1 教训）
- [ ] 单码作废与整单 `reverse` 是**两件事**：单码作废只 VOIDED 一个码，不动结转；
      `reverse` 动整单结转。混淆会造成结转与实际码数长期不一致
- [ ] `04 §7.16` 的 `print_seq` 无唯一索引，库层兜底要**另开迁移**改 `04 §7.16`
      （L-100，本卡可一并处理）

## 验收标准

- [ ] `uv run pytest -q tests/modules/test_bundling_router2.py` 全通过
- [ ] `statistics` / `exports` 的统计维度与 `03 §6` 一致（**先核对文档再写**）
- [ ] 单码作废：码 `VOIDED` 行保留；已计件 → `32003`；结转**不变**
- [ ] 越权查码 → `12002`（经 `doc_id` 回查车间后拦截）
- [ ] 重复 `Idempotency-Key` 的 voids 只生效一次
- [ ] **重跑 `pnpm generate:api` 两次无 diff**；生成物与源码**同提交**（`AGENTS §7.1` 第 1 条）
- [ ] 单文件 ≤400 行；闸门 1-4 本地预跑通过

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| 待回填 | | |

**提交记录**：
- `<hash>` feat(bundling): 码查询/单码作废/统计导出 + 预演与标签端点（T-BUND-007b）

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| L-097 | `super_admin` + `force=true` 未实现 | 待排期 |
| — | 计件流水（`piecework_logs`）属 P2，`32003` 的计件侧判定在 P2 前不可实现 | L-096 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。

## 变更记录

| 日期 | 变更内容 | 操作人 |
| --- | --- | --- |
| 2026-10-07 | 初版：接 007a 拆出的辅助能力端点 + 补 service 缺口 | AI |
