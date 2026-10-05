# T-CUT-001c-3b：裁剪单新建页（表头 + 三层一次提交）

| 项 | 内容 |
| --- | --- |
| 模块 | web |
| 状态 | `done` |
| 优先级 | P1 |
| 依赖 | T-CUT-001c-3a（三层编辑器）、T-BASE-007a/007b（候选端点）、T-CUT-001c-2b（列表页） |
| 被依赖 | T-CUT-001c-3c（编辑页：三层 PUT + 版本链 + 按比例带出）、T-CUT-001c-4（E2E） |
| 关联设计 | [modules/02-裁剪.md](../modules/02-裁剪.md) §5、§11.5.2、[06-前端与UI规范.md](../06-前端与UI规范.md) §2.4 |
| 估算 | 0.5d |

## 目标

裁剪单能**建出来**：表头 + 三层明细一次提交，保存为草稿并跳详情。
级联选料（供应商 → 面料 → 布批）走 T-BASE-007a/007b 的候选端点。

## 范围

**要做**：

- [x] `api/cutting.ts` 追加候选封装：`searchStockBatchOptions` / `searchSupplierOptions` /
      `searchMaterialOptions` / `searchStyleOptionsById`
- [x] `shared` 导出 `StockBatchOptionOut`（布批候选，继承 `OptionOut`）
- [x] `views/cutting/Form.vue`：表头（车间 / 款号 / 单据日期 / 交期 / 铺布层数 /
      默认录入模式 / 来源备注 / 备注）+ 三层编辑器 + 提交
- [x] 路由 `cutting-orders-new`（**必须**排在 `:orderId` 前面）、列表页「新建」按钮回来
- [x] `Form.test.ts` 4 例

**不做**：

- 编辑（改单）、按比例带出、切换录入模式（保存后）、审核 / 反审核 / 作废 / 撤回 → **T-CUT-001c-3c**
- 导出（后端 `/exports` 端点未实现）

## 三条最容易做错的

| # | 陷阱 | 正确做法 |
| --- | --- | --- |
| 1 | **汇总五列**（耗料 / 出数 / 裁损 / 尾数 / 手数）让用户填 | 后端入参**连字段都没有**，传了 `extra="forbid"` 报 `10001`。页面上显示的合计只是「让用户对得上自己录入的」，落库由 C6 重算 |
| 2 | 款号候选用 `searchStyleOptions` | 那个的 `value` 是款号**字符串**，而 `CuttingOrderCreateIn.style_id` 要 **UUID** —— 传错值格式合法、不报 422，只在建单时收 `20001` |
| 3 | 路由 `new` 排在 `:orderId` 之后 | `/cutting/orders/new` 会被当成「ID 是 new 的那一行」，后端 UUID 解析失败 → 页面 422 白屏 |

## 验收标准

- [x] `pnpm lint` / `pnpm typecheck` / `pnpm test:unit` 通过（195 例）
- [x] 无单文件超 400 行
- [x] 闸门 1-5 全绿

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-CUT-F1 | 没填布批行时提交**禁用**并列出缺什么 | `06 §2.4` | ✅ |
| TC-CUT-F2 | 行余量为负**阻断提交**（后端会收 `30002`） | C5 / C34 | ✅ |
| TC-CUT-F4 | 提交体**不含**表头汇总五列（`extra=forbid`） | `05 §3` / C6 | ✅ |
| TC-CUT-F6 | 提交体的表头 UUID + 三层结构逐层正确 | `05 §9.5` | ✅ |

## 实际改动

| 文件 | 行数 |
| --- | --- |
| `frontend/packages/admin/src/views/cutting/Form.vue` | +383 |
| `frontend/packages/admin/src/views/cutting/Form.test.ts` | +271 |
| `frontend/packages/admin/src/api/cutting.ts` | +62 |
| `frontend/packages/admin/src/router/index.ts` | +10 |
| `frontend/packages/shared/src/types/index.ts` | +13 |
| `frontend/packages/admin/src/views/cutting/List.vue` | +13 |

**手写行数 ≈ 752**（在 ADR-0030 的 1200 软上限内）。

## 抓到的三个坑

| # | 坑 | 为什么难发现 |
| --- | --- | --- |
| 1 | **行余量为负没有阻断提交**（只在 `LineEditor` 的问题清单里显示，而那份清单不参与提交判定） | 用户要填满一屏三层数据之后才被后端收 `30002`。是 TC-CUT-F2 抓出来的 |
| 2 | 模式切换原本用 `window.confirm` | `docs/06 §5` 明令禁止原生弹窗（不认 `message` 的常驻与可关闭，也没法统一措辞）。已换 `Modal.confirm` |
| 3 | 断言页面文字时被 antd 的**汉字间空格**坑住 | antd 会在两个汉字之间插空格（`少 30`），而断言前已对正文去空白 → 拿带空格的串比对永远失败，失败信息看不出是空格问题。已加 `hasText()` 统一处理 |

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| — | 车间默认值可取当前用户所在车间（前端拿不到自己的 `workshop_id`，`/auth/me` 有但页面没取） | T-CUT-001c-3c 顺手补 |
| — | 编辑 / 带出 / 状态机 / 导出 | T-CUT-001c-3c、001c-4 |
| — | MASTER 取整策略 | docs/12 L-082 |