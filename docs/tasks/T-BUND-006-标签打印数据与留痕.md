# T-BUND-006：标签打印数据 + `bundle_label_prints` 留痕

| 项 | 内容 |
| --- | --- |
| 模块 | bundling |
| 负责人 | backend-dev |
| 状态 | `done` |
| 优先级 | P1 |
| 依赖 | T-BUND-005b（码已生成）、T-BUND-001（表） |
| 被依赖 | T-BUND-010（打印前端/E2E） |
| 关联设计 | [`modules/03-打菲.md`](../modules/03-打菲.md) §5.4、§6 标签两接口、B14/B15/B25 |
| 关联 ADR | [ADR-0016](../adr/0016-打菲按手与扫码得件数.md)（必印手号）、[ADR-0004](../adr/0004-打菲二维码编码方案.md) |
| 估算 | 0.5d |

## 目标

提供「取标签数据」与「登记打印痕迹」两个能力：导出含**第 N 手 / 共 M 手**与**该手件数**，
每次打印（含重打）写 `bundle_label_prints`，重打写 `document_logs`。

## 范围

**要做**：
- [ ] `service/label.py`：`export_labels(order_id, from_hands, to_hands, size_code, format)` →
      数据数组 / CSV（款号/色/码/工序/手号/件数/二维码内容/条码内容）
- [ ] `service/label.py`：`register_print(order_id, *, hands_seq, hands_total_of_size,
      from_hands, to_hands, printed_qty, is_reprint, print_seq)` → 追加 `bundle_label_prints`
- [ ] `bundle_label_prints` 只追加；重打累加 `bundling_orders.label_print_qty`；重打写日志
- [ ] `backend/tests/modules/test_bundling_label.py`

**不做**：
- 不做前端打印模板/浏览器打印（→ T-BUND-010）
- 字段定义照 `modules/03 §3.4`，本卡不新增字段（公共字段口径见 Q-B14）
- 标签导出与打印登记**不合并**（`03 §6` 明确两个接口）

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `backend/app/modules/bundling/service/label.py` | 新增 | 导出 + 登记 |
| `backend/app/modules/bundling/schemas.py` | 修改 | 标签出参/入参 |
| `backend/app/modules/bundling/service/__init__.py` | 修改 | 导出面 |
| `backend/tests/modules/test_bundling_label.py` | 新增 | 导出/登记/重打 |

## 实现要点（必读规范）

- [x] `qr_content == bundle_no`；条码 Code128 同内容（`bundle_label_prints` 不含条码列）
- [x] 入参 **`hands_seq` 必传**，缺失 → `10002`
- [x] 重打 `is_reprint=true`，追加新行（**不 UPDATE 旧行**）
- [x] 打印记录幂等键 + 只追加（`03 §7`）；导出权限 `bundling:print`

## 验收标准

- [x] `uv run pytest tests/modules/test_bundling_label.py -q` 全部通过（24 passed）
- [x] 导出内容含「第 N 手 / 共 M 手」与「该手件数」；`hands_seq` 落库
- [x] 重打：新增行、`is_reprint=true`、日志有记录（`DocumentAction.REPRINT`）、`label_print_qty` 累加
- [x] 缺 `hands_seq` → `10002`
- [x] 单文件 ≤400 行（`label.py` 395 / `label_guard.py` 144 / `label_csv.py` 50 / `label_repository.py` 190 / `schemas.py` 353 / 测试 400）；闸门 1-4 预跑通过

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-LB-01 | 导出按手区间 | 只含区间内手号 | ✅ `test_export_labels_filters_by_size_and_range`
| TC-LB-02 | 登记 `hands_seq=3` | 落库 `hands_seq=3` | ✅ `test_register_print_appends_row_and_bumps_qty`
| TC-LB-03 | 缺 `hands_seq` | `10002` | ✅ `test_register_print_requires_hands_seq`
| TC-LB-04 | 重打 | 新增行 + 日志 | ✅ `test_reprint_appends_row_and_writes_log`
| TC-LB-05 | 字段完整性 | 印手号/件数/码 | ✅ `test_export_labels_carries_hand_seq_total_and_qty`

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `backend/app/modules/bundling/service/label.py` | 新增 395 | `LabelMixin`：`export_labels`（只读）/ `register_print`（锁表头 + 逐手追加 + 重算累计 + 写日志） |
| `backend/app/modules/bundling/service/label_guard.py` | 新增 144 | 手号区间归一 / 「共 M 手」快照核对 / 已审核与可打印断言 / 单次手数上限 |
| `backend/app/modules/bundling/service/label_csv.py` | 新增 50 | 中文表头 + `StringIO` 内存渲染 |
| `backend/app/modules/bundling/label_repository.py` | 新增 190 | 标签取码 / 各尺码总手数 / `print_seq` 已用查询 / 只追加插入 / 累计张数求和 |
| `backend/app/modules/bundling/schemas.py` | +118（353） | `LabelExportQuery` / `LabelItemOut` / `LabelExportOut` / `LabelPrintIn` / `LabelPrintOut` |
| `backend/app/common/enums.py` | +18（148） | `DocumentAction.PRINT` / `REPRINT`；`LabelExportFormat` |
| `backend/app/modules/bundling/service/__init__.py` | +19（-6） | 重导出标签能力 |
| `backend/app/modules/bundling/service/bundling_order_service.py` | +5（-3） | 组合 `LabelMixin` |
| `backend/tests/modules/test_bundling_label.py` | 新增 400 | 24 个用例 |

**提交记录**：
- `<hash>` feat(bundling): 标签数据导出 + 打印痕迹登记（T-BUND-006）

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| Q-B14 | ✅ **已闭环 2026-10-06**：append-only，公共字段仅 `id` + `created_at` + `created_by`（变更 0111，DDL 见 `04 §7.16`） | — |
| L-100 | **本卡实现与 `03 §6` 的 6 处口径差**：`format` 取 `data\|csv`（html 属前端）、两接口补 `30001`、响应改 `print_ids` 列表、重打必带 `print_seq`、`register_print` 补 `size_code`、导出单次手数上限 1 万；另 `04 §7.16` 的 `print_seq` **无唯一索引**（幂等靠锁表头 + `Idempotency-Key`） | docs/12 §5 L-100 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。

## 变更记录

| 日期 | 变更内容 | 操作人 |
| --- | --- | --- |
| 2026-10-06 | 初版：标签导出 + 打印登记 + 重打留痕 | AI |
| 2026-10-07 | 实现 `export_labels` / `register_print` + 24 个用例；闸门 1-4 通过（变更 0118） | AI |
