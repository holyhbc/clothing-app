# T-BUND-004：拆分算法纯函数 + `/split` 预演（只读，不落库）

| 项 | 内容 |
| --- | --- |
| 模块 | bundling |
| 负责人 | backend-dev |
| 状态 | `todo`（Q-B13/Q-B15/Q-B16 已于 2026-10-06 定版，可开工） |
| 优先级 | P1 |
| 依赖 | T-BUND-003 |
| 被依赖 | T-BUND-005a（submit 码数=手数预检）、T-BUND-005b（approve 复用同一算法） |
| 关联设计 | [`modules/03-打菲.md`](../modules/03-打菲.md) §5.3、§5.5、B18/B20/B23；[`P1-打菲-实施说明.md`](../modules/P1-打菲-实施说明.md) §7 Q-B15/Q-B16/Q-B13 |
| 关联 ADR | [ADR-0016](../adr/0016-打菲按手与扫码得件数.md)、[ADR-0020](../adr/0020-商品分类与手数直接输入.md) |
| 估算 | 0.5d |

## 目标

把「一条 `cutting_order_size_lines` → 若干手码」的拆分逻辑做成**纯函数**，
审核（T-BUND-005b）与预演（本卡）**共用同一份**，杜绝「预演与审核各算一遍导致口径分叉」。

## 范围

**要做**：
- [ ] `backend/app/modules/bundling/service/split.py`：纯函数
  - `split_size_line(*, doc_no, size_code, hands, qty_per_hand, cutting_size_line_id, start_hand_seq)`
    → `SizeLinePreview`（每手的 `bundle_no` / `hands` / `bundle_qty` + `remainder_qty`）
  - `assert_whole(...)`：整件口径断言（`bundle_qty` 为正整数、`余数 = output - hands*qty_per_hand`）
  - `preview_order(lines)`：多行 → 单据级 `hands_total` / `planned_qty` / `balance_qty` + `conflicts[]`
- [ ] `backend/app/modules/bundling/service.py`：`preview_split(order_or_lines)` 只读方法
      （读库内已有 ACTIVE 码填 `conflicts[]`，**不写库、不预占、不写日志**）
- [ ] `backend/app/modules/bundling/service/preview.py` 或并入 `split.py`：`available_outputs(order)` 只读
      （读 `v_cutting_output_available`，见 T-BUND-007 暴露端点）
- [ ] `backend/tests/modules/test_bundling_split.py`：纯函数边界 + 零写库断言

**不做**：
- 不写 HTTP 端点（→ T-BUND-007；`03 §6` 的 `GET /{id}/split` 与 `GET /{id}/available-outputs`）
- **不落库**：`bundles` / `cutting_outputs` 零变动（测试显式断言）
- 不实现 `approve` 的批量 INSERT（→ T-BUND-005b，复用本卡纯函数）

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `backend/app/modules/bundling/service/split.py` | 新增 | 拆分纯函数（审核/预演共用） |
| `backend/app/modules/bundling/service/preview.py` | 新增 | `preview_split` / `available_outputs` 只读方法（`BundlingOrderService` 的 Mixin 或独立模块） |
| `backend/app/modules/bundling/service/__init__.py` | 修改 | 重导出 `split_size_line` 等纯函数 |
| `backend/tests/modules/test_bundling_split.py` | 新增 | 纯函数 + 零写库 |

## 实现要点（必读规范）

- [ ] **算法权威源待定**：`03` 第一组 B4/B18/B20 写 `floor(output_qty ÷ hands)`，
      第二组 B23 与 `08 §2.2` 写「直接取 `qty_per_hand`」。**本卡按 Q-B15 结论实现**；
      闭环前先按 `08 §2.2` + ADR-0020 实现并留 `TODO(待确认: Q-B15)`，不得两种都写
- [ ] 手号展开：`T-BUND-002` 的 `build_bundle_no`；跨布批行的手号编法按 Q-B13
      （当前 `03 §5.3` 按「同单同尺码连续唯一」执行，合规 `uq_bundles_hand`）
- [ ] `hands <= 0 → 10001`；`bundle_qty` 非整/为 0 → `31003`/`10001`（照 `03 §9`）
- [ ] 与审核的关系：**预演结果不作审核依据**（TOCTOU）；审核重算并跑 4 条断言
- [ ] 只读方法权限点 `bundling:read`（**不需 `bundling:approve`**，因为不写库）

## 验收标准

- [ ] `uv run pytest tests/modules/test_bundling_split.py -q` 全部通过
- [ ] `hands=2, qty_per_hand=60` → 2 个码各 60；`hands=6`（1:2:2:1）→ 6 个码手号连续
- [ ] 除不尽场景：`hands=5, output=12` → 5 个码、余 2（不生成第 6 个码）
- [ ] `hands=0` → `10001`，**不静默取整**
- [ ] 零写库：调用前后 `bundles` / `cutting_outputs` 行数与值不变
- [ ] `conflicts[]` 正确列出手号冲突（`31005` 前置）
- [ ] 单文件 ≤400 行；闸门 1-4 本地预跑通过

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-SP-01 | `hands=2, output=120` | 2 码 × 60，余 0 | |
| TC-SP-02 | `hands=5, output=12` | 5 码 × 2，余 2，无第 6 码 | |
| TC-SP-03 | 1:2:2:1 → 6 手 | 手号 `L01/XL01/XL02/XXL01/XXL02/3XL01` | |
| TC-SP-04 | 同尺码多行（ADR-0017） | 手号按 Q-B13 口径连续/分编 | |
| TC-SP-05 | `hands=0` | `10001` | |
| TC-SP-06 | 库内已有 `(doc,WHT,XL,2)` | `conflicts[]` 列出 | |
| TC-SP-07 | 预演零写库 | 行数不变 | |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| 待回填 | | |

**提交记录**：
- `<hash>` feat(bundling): 拆分算法纯函数 + `/split` 预演（只读）

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| Q-B15 | 每手件数权威算法 | `P1-打菲-实施说明.md` §7 |
| Q-B13 | 跨布批行手号编法 | `03 §12` Q-B13 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。

## 变更记录

| 日期 | 变更内容 | 操作人 |
| --- | --- | --- |
| 2026-10-06 | 初版：拆分纯函数 + 只读预演，审核共用同源 | AI |
