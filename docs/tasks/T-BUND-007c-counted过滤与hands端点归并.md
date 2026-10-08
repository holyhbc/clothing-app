# T-BUND-007c：补 `counted` 过滤（未计件手清单）与 `/hands` 端点归并决策

| 项 | 内容 |
| --- | --- |
| 模块 | bundling |
| 负责人 | backend-dev |
| 状态 | `todo` |
| 优先级 | P1（**TC-30 / TC-33 / T-BUND-008 与 Q-B09 都卡在这一条**） |
| 依赖 | T-BUND-007b（`GET /bundles` 已就绪） |
| 被依赖 | T-BUND-008（并发与集成测试）、T-BUND-009（前端「未计件手」清单页） |
| 关联设计 | [`modules/03-打菲.md`](../modules/03-打菲.md) §6 `/hands` 行、§7 TC-30、Q-B09 |
| 估算 | 0.25d（**小卡，别把它撑大**） |

## 目标

`03 §6` 承诺的 **`GET /{id}/hands` 按手列表（含 `counted=false` 未计件手清单）至今没实现** ——
这是 ADR-0016 的**验证方式**（「未计件手数可见」），也是 Q-B09（部分生产剩余量可见）的唯一入口。
TC-30、TC-33 与 T-BUND-008 全部依赖它。

## 本卡要做的决策（先决策，再写码）

`GET /bundles`（007b 已实现）**已经支持** `doc_id` / `style_no` / `color_code` / `size_code` 过滤，
出参也已含 `hands` / `hands_total_of_size` / `bundle_qty` / `counted_qty` / `counted_at` / `status`。

**结论：`/hands` 是 `GET /bundles?doc_id={id}` 的重复定义，应归并，不新增端点。**

理由：`doc_id` 已是现有过滤项，能力完全被覆盖；而 `/hands` 多一套路径就多一份
OpenAPI 契约、多一套前端调用、一处路由顺序坑（T-CUT-001c-1 教训），
**却没有任何 `/bundles` 做不到的事**。`03 §6` 写 `/hands` 时是当独立页设计的，
现在 `GET /bundles` 已经长成了它。

**所以本卡的实质是：给 `GET /bundles` 加 `counted` 过滤，并在 `03 §6` 记明归并。**

## 范围

**要做**：

- [ ] `GET /bundles` 增加 `counted` 查询参数（**三态**）：
      不传 = 全部；`false` = **未计件**（`counted_at IS NULL`，**必须命中
      `idx_bundles_counted_pending`**，见 `docs/04` 索引登记）；`true` = 已计件
- [ ] 校验：`counted` 只能取 `true`/`false`，非法值 → `10001`
- [ ] `03 §6`：把 `/hands` 行标注为**已归并进 `GET /bundles`**，
      写明「`?doc_id={id}&counted=false` 即等价原 `/hands`」，并同步 §7 TC-30 的指向
- [ ] 测试：`counted=false` 只回未计件手且走对索引；`counted=true` 只回已计件；
      非法值 `10001`；作废码（`status=VOIDED`）默认**要**排除还是包含？
      —— **口径：默认排除 `VOIDED`**（作废码不是「还没计件的手」），单独用 `status` 参数取
- [ ] `pnpm generate:api`（生成物**同提交**）

**不做**：
- Q-B09 的 ②「剩余手清单」独立报表、③ 到期提醒、④ 追加新手自动结转 ——
  **未获业务确认，不实现**（`03 §12` Q-B09 ⏳）
- `bundles.partial_at` 字段 —— 同上，未确认

## 实现要点

- [ ] 过滤走 `apply_data_scope` 的 `via` 通路（ADR-0032），`counted` 条件与之**并列 AND**
- [ ] 索引 `idx_bundles_counted_pending` 已在 0014 建成；用 `EXPLAIN` 确认
      `counted=false` 走它而不是全表扫 —— **把 EXPLAIN 结果写进任务卡**，否则「命中索引」只是断言
- [ ] 出参数量一律字符串（`05 §3`）；`hands` / `counted_qty` 的语义别混（`hands` 是手号不是件数）

## 验收标准

- [ ] `uv run pytest -q tests/modules/test_bundling_counted.py` 全通过
- [ ] `counted=false` 返回**全部未计件手**（含部分生产的，`counted_at` 非空但
      `counted_qty < bundle_qty` 的**也要算已计件**——按 `counted_at` 判，不按 `counted_qty`）
- [ ] **`EXPLAIN` 证据入卡**（TC-30 明写「命中 `idx_bundles_counted_pending`」）
- [ ] `03 §6` 已标注 `/hands` 归并、`03 §7` TC-30 指向已更新
- [ ] 单文件 ≤400 行；闸门 1-4 通过

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| 待回填 | | |

**提交记录**：
- `<hash>` feat(bundling): bundles 增加 counted 过滤 + /hands 端点归并（T-BUND-007c）

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。

## 变更记录

| 日期 | 变更内容 | 操作人 |
| --- | --- | --- |
| 2026-10-08 | 初版：补 `counted` 过滤（TC-30 的实质缺口）+ `/hands` 归并决策，闭环 L-104 | AI |
