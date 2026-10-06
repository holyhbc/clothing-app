# T-BUND-009：前端打菲单页（列表 / 新建 / 详情）

| 项 | 内容 |
| --- | --- |
| 模块 | bundling |
| 负责人 | frontend-dev |
| 状态 | `todo` |
| 优先级 | P1 |
| 依赖 | T-BUND-007（契约生成） |
| 被依赖 | T-BUND-010 |
| 关联设计 | [`modules/03-打菲.md`](../modules/03-打菲.md) §11.5 页面结构与溯源；[`P1-打菲-实施说明.md`](../modules/P1-打菲-实施说明.md) §6 |
| 关联需求 | [`REQ-000`](../requirements/REQ-000-工期优化与复用策略.md) §5（`ResourceList`/`ResourceForm`/`OrderedSizeItems`） |
| 关联 ADR | [ADR-0023](../adr/0023-生产单据页面重排与溯源链.md)（页面合并为 `#/pc/bundling`） |
| 估算 | 0.5d |

## 目标

按 ADR-0023 产出**一个打菲页** `#/pc/bundling`：顶部单据列表，行内展开按手列码；
新建页从裁剪明细带出、录入手数；详情只读展示溯源链。

## 范围

**要做**：
- [ ] `frontend/packages/admin/src/api/bundling.ts`：封装端点（类型来自生成物，**不手写 DTO**）
- [ ] `views/bundling/List.vue`：`ResourceList` + columns（单据号/款号/工序/商品分类/车间/
      每码件数/码数(手)/已打印/已计件/状态/来源裁剪单）
- [ ] `views/bundling/Form.vue`：`ResourceForm` + `OrderedSizeItems`；从 `available-outputs`
      选来源行（显示 `hands`/`output_qty`/每手件数预览/缸号匹号）
- [ ] `views/bundling/Detail.vue`：只读，含单内「按手列表」（手号/码/件数/打印/计件）与
      溯源面包屑 `款号 > 裁剪单 > 打菲单 > 码`
- [ ] 路由 `#/pc/bundling` 与菜单入口（「生产管理」分组）
- [ ] 单测：`List`/`Form`/`Detail` + 权限 `v-can`

**不做**：
- QR/标签打印与 E2E（→ T-BUND-010）
- 页面不做任何汇总计算（表头五列以 service 返回为准，第二份真相）
- 未实现的端点不给入口（不注册 501 占位）

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `frontend/packages/admin/src/api/bundling.ts` | 新增 | API 封装 |
| `frontend/packages/admin/src/views/bundling/List.vue` | 新增 | 列表页 |
| `frontend/packages/admin/src/views/bundling/Form.vue` | 新增 | 新建页 |
| `frontend/packages/admin/src/views/bundling/Detail.vue` | 新增 | 详情页 |
| `frontend/packages/admin/src/router.ts` / `menu.ts` | 修改 | 路由与菜单 |
| `frontend/packages/admin/src/views/bundling/*.test.ts` | 新增 | 单测 |

## 实现要点（必读规范）

- [ ] `docs/06`（token、状态 Tag、表格密度）；`ResourceList`/`ResourceForm`/
      `OrderedSizeItems` 路径见 `frontend/packages/admin/src/views/base/`
- [ ] **契约生成**：后端 OpenAPI 变更后 `pnpm generate:api`，生成物与源码**同提交**
- [ ] 候选选择用 `<Combo>` 与 `optionsById`（UUID）—— 裁剪来源行需 `cutting_size_line_id`
- [ ] 版本链：编辑/保存后必须存响应里的新 `version`
- [ ] 权限用 `v-can`（`bundling:read/create/update/submit/approve/print/code:void`）
- [ ] **界面反复说明**「一码 = 一手 = 一个员工」（`03 §11.5` 强制）

## 验收标准

- [ ] `pnpm test:unit` 全过；`pnpm lint && pnpm typecheck` 全过
- [ ] `pnpm generate:api` 后 `openapi.json`/`schema.d.ts` 无未提交 diff
- [ ] 列表行内展开显示按手列表
- [ ] 新建页能选来源裁剪行并提交草稿
- [ ] 无权限用户看不到（而非点了 403）对应按钮
- [ ] 单文件 ≤400 行；闸门 1-3 本地预跑通过

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-BW-01 | 列表渲染 + 筛选 | 数据/分页正确 | |
| TC-BW-02 | 行内展开按手列表 | 手号/件数显示 | |
| TC-BW-03 | 新建页选来源行 | 带出 `hands`/预览 | |
| TC-BW-04 | 详情溯源面包屑 | 跳父单 | |
| TC-BW-05 | `v-can` | 无权隐藏 | |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| 待回填 | | |

**提交记录**：
- `<hash>` feat(web): 打菲单页（列表/新建/详情）

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| | | `docs/12` §遗留问题清单 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。

## 变更记录

| 日期 | 变更内容 | 操作人 |
| --- | --- | --- |
| 2026-10-06 | 初版：一个打菲页（列表/新建/详情），复用 ResourceList/ResourceForm | AI |
