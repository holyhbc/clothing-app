# T-PIECE-008：工位机离线队列（IndexedDB + 指数退避）

| 项 | 内容 |
| --- | --- |
| 模块 | piecework / web + infra（前端运行时依赖） |
| 负责人 | frontend-dev |
| 状态 | `done` |
| 优先级 | P3（工厂网络差时才有感知；在线主链路 T-PIECE-007 先交付） |
| 依赖 | T-PIECE-007（工位机扫码页，在线主链路） |
| 关联设计 | [设计稿](../modules/04-计件-离线队列设计.md)、[`07-认证与权限规范.md`](../07-认证与权限规范.md) §7（本地队列 / 重试 / 状态显示 / 数据归属）；[`06-前端与UI规范.md`](../06-前端与UI规范.md) §2.5 第 6 条；[ADR-0034](../adr/0034-工位机离线队列实现选型.md) |
| 估算 | 1d |

## 背景

`07 §7` 要求工位机页（PWA/IndexedDB）在断网时缓存待提交流水、按 `1s/2s/4s/8s/30s`
指数退避重试（最多 8 次）、页面顶部常驻「在线 / 离线（缓存 N 条待提交）」，且离线缓存
必须记 `device_id` + `scanned_at`（服务端以 `scanned_at` 归属日期）。T-PIECE-007 因
引入新运行时依赖（IndexedDB/PWA）而**刻意未做**，拆到本卡。

## 待决问题（已闭环）

- ~~**Q-PW18**：离线队列用哪个实现？~~ ✅ **已闭环 2026-10-16（ADR-0034）**：选 **`idb` v8**；
  离线回放遇 `32001` 该条**标记为冲突、保留在队列、常驻计数**（不静默丢弃、不自动移出）。
- ~~回放冲突如何呈现？~~ ✅ 同上：队列内标冲突 + 顶部常驻计数 + 可展开逐条查看（人工删除或保留）。

设计见 [`modules/04-计件-离线队列设计.md`](../modules/04-计件-离线队列设计.md)（状态：已确认）。

## 不做

- PWA service worker / 离线壳（仅数据队列，不做离线页面缓存）——除非业务要求。

## 验收标准

- [x] 断网扫码进入队列，顶部显示「离线（缓存 N 条待提交）」
- [x] 恢复网络后按指数退避重试，幂等键不变
- [x] 回放冲突按 `07 §7` 提示，不静默丢弃
- [x] 队列持久化（刷新/关闭重开仍在）
- [x] 组件测试覆盖入队 / 重试 / 回放冲突 / 清理

## 实施结果

**已交付（纯前端，无后端 / 迁移 / 契约改动；新增 1 个运行时依赖 `idb@^8.0.4`）。**

新增 `frontend/packages/admin/src/views/piecework/scan/offline/`：

| 文件 | 行 | 职责 |
| --- | --- | --- |
| `queue.ts` | 192 | IndexedDB 封装（库 `garment-erp-piecework-offline` v1 / store `pending_counts`）＋退避常量＋`getDeviceId`＋`resetSyncing`＋测试用 `__resetQueueForTest` |
| `useOfflineQueue.ts` | 186 | flush 循环（模块级 `inflight` 守卫）＋错误分类 `classifyError`＋响应式计数＋人工重试/删除 |
| `QueuePanel.vue` | 118 | 冲突 / 失败展开面板（冲突文案复用 `duplicateText`，与在线提示同句） |
| `useOfflineQueue.test.ts` | 241 | 9 例：分类、持久化、成功删除、幂等键/`scanned_at` 冻结、退避 8 次上限、`32001` 冲突保留、单循环并发、`syncing` 崩溃恢复、人工处置 |
| `QueuePanel.test.ts` | 66 | 3 例：冲突渲染、派发 retry/remove、空态 |

改动：`useScanMachine.ts`（270 行，新增 `ScanDeps` 注入 + 离线/可重试失败入队 + `cached` 相位）、`Scan.vue`（249 行，接入队列、状态条计数、`QueuePanel`、`online` 事件与 15s 定时 flush）、`ScanDisplay.vue`（`cached` 文案色）、`Scan.test.ts`（+4 例离线集成）、`test-setup.ts`（`fake-indexeddb/auto`）、`admin/package.json`（`idb` + `fake-indexeddb`）。

**在线策略**：在线直连优先，可重试失败 / 离线才入队（复用同一 `Idempotency-Key` 与 `payload`，`scanned_at` 入队冻结）；`32001` / `32006` 在线即确定，不入队。见设计稿 §6.2。

**闸门**：`pnpm run typecheck` / `lint` / `test:unit`（**333 passed** = admin 277 + shared 56）/ `build` 全绿；后端未改动。**单文件最大 270 行**（`useScanMachine.ts`），未超 400（ADR-0030）。
