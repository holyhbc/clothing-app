# T-BUND-004：拆分算法纯函数 + `/split` 预演（只读，不落库）

| 项 | 内容 |
| --- | --- |
| 模块 | bundling |
| 负责人 | backend-dev |
| 状态 | **`done`**（2026-10-06；Q-B13/Q-B15/Q-B16 已定版，Q-B13 仍为**暂定默认**） |
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
- [x] `backend/app/modules/bundling/service/split.py`：纯函数
  - `split_size_line(*, doc_no, size_code, hands, qty_per_hand, cutting_size_line_id, start_hand_seq)`
    → `SizeLinePreview`（每手的 `bundle_no` / `hands` / `bundle_qty` + `remainder_qty`）
  - `assert_whole(...)`：整件口径断言（`bundle_qty` 为正整数、`余数 = output - hands*qty_per_hand`）
  - `preview_order(lines)`：多行 → 单据级 `hands_total` / `planned_qty` / `balance_qty` + `conflicts[]`
- [x] `backend/app/modules/bundling/service/preview.py`：`preview_split(order_id, ctx)` 只读方法
      （读库内已有 ACTIVE 码填 `conflicts[]`，**不写库、不预占、不写日志**）
- [x] `backend/app/modules/bundling/service/preview.py`：`available_outputs(order_id, ctx)` 只读
      （读 `v_cutting_output_available`，见 T-BUND-007 暴露端点）
- [x] `backend/tests/modules/test_bundling_split.py`：纯函数边界 + 零写库断言

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

- [x] **算法权威源**：Q-B15 已于 2026-10-06 闭环 —— `03` 的两组 B 条款均已收敛于
      「**直接取 `qty_per_hand`，不 floor**」（B4/B18/B20 已改写，见 `03 §5.3`）。
      本卡按该口径实现，**全文只有一种算法**，未留 `TODO(待确认: Q-B15)`
- [x] 手号展开：`T-BUND-002` 的 `build_bundle_no`；跨布批行的手号编法按 Q-B13
      （当前 `03 §5.3` 按「同单同尺码连续唯一」执行，合规 `uq_bundles_hand`）
- [x] `hands <= 0 → 10001`；`bundle_qty` 非整/为 0 → `31003`/`10001`（照 `03 §9`）
- [x] 与审核的关系：**预演结果不作审核依据**（TOCTOU）；审核重算并跑 4 条断言
- [x] 只读方法权限点 `bundling:read`（**不需 `bundling:approve`**，因为不写库）

## 验收标准

- [x]  `uv run pytest tests/modules/test_bundling_split.py -q` 全部通过
- [x]  `hands=2, qty_per_hand=60` → 2 个码各 60；`hands=6`（1:2:2:1）→ 6 个码手号连续
- [x]  除不尽场景：`hands=5, output=12` → 5 个码、余 2（不生成第 6 个码）
- [x]  `hands=0` → `10001`，**不静默取整**
- [x]  零写库：调用前后 `bundles` / `cutting_outputs` 行数与值不变
- [x]  `conflicts[]` 正确列出手号冲突（`31005` 前置）
- [x]  单文件 ≤400 行；闸门 1-4 本地预跑通过

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-SP-01 | `hands=2, output=120` | 2 码 × 60，余 0 | ✅ 2 码各 60，余 0，bundle_no `XL01/XL02` |
| TC-SP-02 | `hands=5, output=12` | 5 码 × 2，余 2，无第 6 码 | ✅ 5 码 × 2、余 2、无 `L06` |
| TC-SP-03 | 1:2:2:1 → 6 手 | 手号 `L01/XL01/XL02/XXL01/XXL02/3XL01` | ✅ 手号 `L01/XL01/XL02/XXL01/XXL02/3XL01` |
| TC-SP-04 | 同尺码多行（ADR-0017） | 手号按 Q-B13 口径连续/分编 | ✅ 行A `XL01/XL02` + 行B `XL03`（Q-B13 连续编）；不同色各自从 1 起 |
| TC-SP-05 | `hands=0` | `10001` | ✅ `10001`；`hands=-1` / `qty_per_hand=0` 同码 |
| TC-SP-06 | 库内已有 `(doc,WHT,XL,2)` | `conflicts[]` 列出 | ✅ 非整件 `31003`、`<=0` `10001`、手序号 > 99 与尺码码 > 3 位 `10001` |
| TC-SP-07 | 预演零写库 | 行数不变 | ✅ `conflicts[]` 返回 `code=31005`，**不抛错** |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `backend/app/modules/bundling/service/split.py` | **+346（新增）** | 纯函数：`split_size_line` / `assert_whole` / `preview_order` + 5 个 frozen dataclass |
| `backend/app/modules/bundling/service/preview.py` | **+167（新增）** | `PreviewMixin`：`preview_split` / `available_outputs`，**只读不开事务** |
| `backend/app/modules/bundling/repository.py` | **+147 / -2** | 4 个只读查询 + `v_cutting_output_available` 的 Core `Table` 声明 |
| `backend/app/core/errors.py` | **+27 / -0** | 整段补齐 `31xxx`（`31001`~`31007`）+ HTTP 映射 |
| `backend/tests/modules/test_bundling_split.py` | **+395（新增）** | 17 例：纯函数边界 13 + 库用例 4（含零写库） |
| `backend/tests/test_errors_registry.py` | **+5 / -2** | `IMPLEMENTED_SEGMENTS` 加 `"31"` |
| `backend/app/modules/bundling/service/__init__.py` | **+28 / -2** | 重导出纯函数与 `PreviewMixin` |
| `backend/app/modules/bundling/service/bundling_order_service.py` | **+7 / -2** | `BundlingOrderService` 组装 `PreviewMixin` |
| **合计** | **手写 +1122 / -10** | 生成物 0；单文件最大 395 行（`test_bundling_split.py`），未破 400（ADR-0030/0031） |

⚠️ **超出卡面「将要改动的文件」清单的两处，均为必要而非顺手重构**：

1. **`app/core/errors.py` + `tests/test_errors_registry.py`** —— 卡面要求非整件报 `31003`
   （`03 §9`），而 `31xxx` 整段此前**一个码都没实现**，且守卫
   `test_unimplemented_segments_have_no_codes` 会让「只加一个码」直接变红 →
   只能整段补齐（7 个码 + HTTP 映射 + `"31"` 进 `IMPLEMENTED_SEGMENTS`）。
   本卡实际只用 `31003`；`31005` 以 `conflicts[].code` 字符串形态出现在预演结果里
   （预演**不抛** `31005`，那要等 submit/approve）。
2. **`repository.py` + `bundling_order_service.py`** —— 读库必须在 repository（`03 §1.3`
   五层分层），只读方法必须挂在 service 上（T-BUND-007 的 router 才要调得到）。

**提交记录**：
- `4215ab9` feat(bundling): 打菲拆分算法纯函数 + /split 只读预演（T-BUND-004）

## 遗留问题

| # | 问题 | 状态 / 登记到 |
| --- | --- | --- |
| Q-B15 | 每手件数权威算法 | ✅ 2026-10-06 闭环（`bundle_qty=qty_per_hand`），本卡已实现 |
| Q-B13 | 跨布批行手号编法 | 🟡 仍为**暂定默认**：本卡按「同单同尺码全局连续」实现，与 `02` Q-C11 需同答案 |
| **新-1** | **单尺码手数上限未定义** —— `bundle_no` 手序号固定 2 位（Q-B18），而 §5.3 只写「单张 `hands_total ≤ 20000`」。单尺码 > 99 手时 `XL100-0001` **能过 DB CHECK**（`[A-Z0-9]{1,3}[0-9]{2}` 把 `XL1` 当尺码码）但 `parse_bundle_no` 解析成 `size_code='XL1'/hands=00`。本卡在算法层拒单（`10001` + 「请拆单重开」）。**需业务确认**：是「单尺码 ≤ 99 手」还是改格式（手序号 3 位） | `03 §12` 待登记（本卡未改 `03`） |
| **新-2** | **`available-outputs` 未带「缸号 / 匹号」** —— `03 §6` 要求返回「所属缸号匹号」，它在 `cutting_order_lines`（尺码明细往上两层）。本卡只返回尺码级的手数 / 每手件数 / 可用量，缸号留给 T-BUND-007 组合 | `03 §6` |
| **新-3** | **`hands_total ≤ 20000` 上限未落地** —— §5.3 要求「上限值走基础资料 / 配置，**不硬编码在 service**」，而配置项尚不存在。本卡纯函数**刻意不加**该校验（加了就得硬编码），留给 T-BUND-005b 与配置表 | `03 §5.3` |
| **新-4** | **余数非 0 的调用方未定** —— `split_size_line(output_qty=...)` 已支持且 TC-SP-02 覆盖，但**谁**在什么场景传它未定：§5.3 说「打菲不承接裁剪余额」，而 §5.5 的余数公式留了口子。本卡预演恒传 `None`（余数恒 0） | T-BUND-005b |

⚠️ **偏离卡面的一处**：卡面把 `available_outputs` 放在 `service/preview.py` **或**并入
`split.py`。本卡选了 `preview.py` —— `split.py` 必须保持**无 DB、无 session**，
把查询塞进去会让「审核可复用纯函数」这件事当场失效。

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。

## 变更记录

| 日期 | 变更内容 | 操作人 |
| --- | --- | --- |
| 2026-10-06 | 初版：拆分纯函数 + 只读预演，审核共用同源 | AI |
| 2026-10-06 | 回填：17 例全绿；闸门 1-4 通过（857 例 / 覆盖 90.75%）；补记 4 条遗留与 1 处偏离 | backend-dev |
