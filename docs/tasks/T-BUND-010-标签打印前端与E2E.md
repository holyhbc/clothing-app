# T-BUND-010：QR/标签打印前端 + P1 打菲 E2E（P1 出口）

| 项 | 内容 |
| --- | --- |
| 模块 | bundling |
| 负责人 | frontend-dev + qa |
| 状态 | `todo` |
| 优先级 | P1（P1 里程碑出口，`REQ-000 §7` 第 2 周） |
| 依赖 | T-BUND-009、T-BUND-006、T-BUND-007 |
| 被依赖 | — |
| 关联设计 | [`modules/03-打菲.md`](../modules/03-打菲.md) §5.4 标签规格、§11.5、B14/B15/B25；[`P1-打菲-实施说明.md`](../modules/P1-打菲-实施说明.md) §6 |
| 关联规范 | `docs/06 §4`（标签 40mm×40mm）、`docs/10`（E2E） |
| 估算 | 0.5d |

## 目标

标签打印前端（含二维码/Code128 与 40mm×40mm 打印样式、批量/按手区间重打），
并跑通打菲全链路 E2E：建单 → 提交 → 审核生成码 → 打印 → 计件可扫。

## 范围

**要做**：
- [ ] `views/bundling/LabelPrint.vue`：打印预览网格、按手区间选择、重打、`@page { size: 40mm 40mm; margin: 0 }`
- [ ] 二维码（纯文本 `bundle_no`，纠错 M，≥20mm）+ Code 128（同内容）
- [ ] 打印登记：调 `POST /label-prints`，**必传 `hands_seq`**；重打写日志
- [ ] `frontend/e2e/p1-bundling-flow.spec.ts`：建单 → 提交 → 审核 → 码数=手数可见 →
      标签含「第 N 手 / 共 M 手」与件数 → 重打留痕
- [ ] `backend/tests/e2e_seed.py` 补打菲所需数据（款号/工序/裁剪单已审核）

**不做**：
- 不接条码打印机驱动/标签软件（`REQ-000 §8` 非目标：浏览器打印 + CSV 导出）
- 不改后端接口（发现缺口另开修复卡）

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `frontend/packages/admin/src/views/bundling/LabelPrint.vue` | 新增 | 打印预览与登记 |
| `frontend/packages/admin/src/views/bundling/LabelPrint.test.ts` | 新增 | 单测 |
| `frontend/e2e/p1-bundling-flow.spec.ts` | 新增 | E2E |
| `backend/tests/e2e_seed.py` | 修改 | 补打菲 seed |

## 实现要点（必读规范）

- [ ] `docs/06 §4` 标签尺寸、`@media print` 隐藏交互元素、条码/二维码内容 = `bundle_no`
- [ ] **重打必须带 `hands_seq`**，缺 → `10002`（后端 T-BUND-006）
- [ ] E2E 用独立库 + 与生产一致的授权（照 T-WEB-006/CUT-001c-4 的 `create-e2e-db.sh`）
- [ ] 二维码库选型写 ADR 或复用既有前端依赖，不引入重量级包（`AGENTS §5`）
- [ ] E2E 断言页面正文时对空白归一化（antd 汉字间空格）

## 验收标准

- [ ] `pnpm test:unit` / `pnpm lint` / `pnpm typecheck` 全过
- [ ] `pnpm e2e p1-bundling-flow` 通过
- [ ] 标签打印预览含手号、件数、二维码、条码
- [ ] 重打产生 `bundle_label_prints` 新行且 `is_reprint=true`
- [ ] 单文件 ≤400 行；闸门 1-5 全绿（含镜像构建）

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-LE-01 | 打印预览含手号 | 「第 3 手 / 共 5 手」 | |
| TC-LE-02 | 打印样式 | 40mm×40mm，无交互元素 | |
| TC-LE-03 | 重打 | `is_reprint=true` + 日志 | |
| TC-LE-04 | E2E 审核 | 码数=手数 | |
| TC-LE-05 | E2E 刷新后仍在 | 数据持久化 | |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| 待回填 | | |

**提交记录**：
- `<hash>` feat(web): 打菲标签打印 + P1 E2E

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| | | `docs/12` §遗留问题清单 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。

## 变更记录

| 日期 | 变更内容 | 操作人 |
| --- | --- | --- |
| 2026-10-06 | 初版：标签打印前端 + P1 打菲 E2E 出口 | AI |
