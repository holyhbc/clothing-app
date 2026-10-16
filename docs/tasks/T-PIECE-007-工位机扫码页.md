# T-PIECE-007：工位机扫码页（PC 端，全局扫码捕获）

| 项 | 内容 |
| --- | --- |
| 模块 | piecework / web（纯前端） |
| 负责人 | frontend-dev |
| 状态 | `done` |
| 优先级 | P2（链路 A 的落地点；后端 `POST /piecework/count` 与操作员候选已就绪） |
| 依赖 | T-PIECE-002（扫码链路 A）、T-PIECE-006（`GET /piecework/operators` + 角色授权，均闭环） |
| 被依赖 | T-PIECE-008（IndexedDB 离线队列，本卡**不做**） |
| 关联设计 | [`modules/04-计件.md`](../modules/04-计件.md) §5.1（链路 A 十二步 + 反馈表）、§6.1；[`06-前端与UI规范.md`](../06-前端与UI规范.md) §2.5（扫码交互）、§2.2；[`07-认证与权限规范.md`](../07-认证与权限规范.md) §7（离线，**部分**） |
| 关联规范 | `docs/09 §2.4`（扫码枪 HID 输入约定）、`docs/05 §5`（幂等键）、`docs/05 §9.5.2`（候选 label =「工号 姓名」） |
| 估算 | 1d |

## 背景：为什么单独一张卡

链路 A（`04 §5.1`）的规则与后端都已完成，但**没有页面**：后端 `POST /piecework/count`
（T-PIECE-002）与操作员候选 `GET /piecework/operators`（T-PIECE-006）都已闭环，组长却无处可扫。
本卡把 `04 §5.1` 的十二步与 §5.1 反馈表落成一个可用的工位机页面。

## 范围决策（2026-10-15，业务方 / 架构确认）

- **A. 离线队列拆卡**：`06 §2.5` 第 6 条 / `07 §7` 要求的 **IndexedDB 持久队列 + 指数退避**（PWA）
  **不在本卡**。理由是它引入仓库当前没有的运行时依赖（IndexedDB/PWA），属新架构面。
  本卡只做**在线主链路** + 一个常驻「在线 / 离线」状态条（离线时提示但**不落队列**），
  IndexedDB 队列另开 **T-PIECE-008**。
- **B. 提示音**：**Web Audio 现场合成**（`AudioContext` + oscillator），不引入音频资源文件 —— 离线可用、无网络加载路径。失败/成功音高不同。

## 目标

1. 路由 `piecework/scan`（权限 `piecework:count`）+ 菜单「生产管理 → 工位机扫码」。
2. 页面级**全局 `keydown` 捕获**（不依赖输入框焦点），扫码枪 HID 输入 → 缓冲 → `Enter` 结束。
3. 本地格式校验 `^BD-[0-9]{8}-[0-9]{6}-[A-Z0-9]{1,3}[0-9]{2}-[0-9]{4}$`（尺码码 **1~3 位**，与后端 `numbering.py::BUNDLE_NO_PATTERN` 及 `09 §2.4` 一致；⚠️ `04 §5.1` 那行示例写死 2 位是**示例偏差**），不合格式**不发请求**（`04 §5.1` 步骤 3）。
4. 顶部设置条：工位号（`workstation_no`，`localStorage` 记忆）、操作员（远程搜索 `GET /piecework/operators`，防抖 300ms）、默认工序（`/operations/options`）、「暂停扫码」开关。
5. 大字计数：**今日 N 手 · M 件 / ¥金额**（`tabular-nums`，≥ 2xl）。
6. 提交 `POST /piecework/count`（带 `Idempotency-Key`），并按 §5.1 反馈表给四种反馈：整手成功 / 部分生产 / 重复码（`32001` 黄色提示，不弹窗）/ 超产（`32006` 红色）。
7. 成功：手数 +1、件数 +qty、金额累加、短提示音、绿色边框闪 200ms。

## 不做什么

| 项 | 原因 |
| --- | --- |
| **IndexedDB 离线队列 + 指数退避** | 独立卡 T-PIECE-008（范围决策 A） |
| SSE 实时推送（`/events/stream`） | 无订阅需求，独立卡；数值一律以 `count` 接口返回为准（`05 §8`） |
| 工位号主数据表（`workstations`） | 无此表；`workstation_no` 是本页配置项，前端记忆（同 T-PIECE-006「不做」） |
| 部分生产的**自动识别**（做完没做完） | 规范未定，仅提供**手动**「部分生产」入口（输入 `qty ≤ bundle_qty`） |
| 员工端 H5 扫码页 | 独立链路 B（依赖 P2 短信登录，当前硬阻塞） |

## 设计要点

- **目录** `views/piecework/scan/`：`Scan.vue`（编排）+ `ScanSetupBar.vue`（设置条）+
  `ScanDisplay.vue`（大字/最近结果）+ `feedback.ts`（Web Audio + 闪框纯函数）+
  `useScanMachine.ts`（缓冲/校验/提交状态机）。⚠️ 每文件 ≤ 400 行（ADR-0030）。
- **幂等键**：`Idempotency-Key = bundle_no|workstation_no|operation_no|时间片`（`04 §5.1` / `09 §2.4`）。
- **重复码**：`32001` 的 `details`（`PieceworkErrorDetail`）直接渲染成一行黄色提示
  「XL 第 2 手，陈美玲 09:44 已计 60 件」，**不弹窗**（`06 §2.5` 第 3 条）。
- **失败**：红色提示 + 短音，并**清空缓冲**准备下一次（`06 §2.5` 第 4 条）。
- **暂停**：开关打开时忽略所有扫码（`06 §2.5` 第 7 条）。

## 验收标准

- [x] 路由 `piecework/scan` + 菜单项，权限 `piecework:count`
- [x] 页面加载即可扫（无需点击）；`Enter` 结束缓冲；非 `bundle_no` 格式不发请求
- [x] 成功反馈：大字卡改为服务端权威 `today_hands/today_qty/today_amount`（**不前端累加**，见风险）、短音、绿框闪 200ms
- [x] `32001`：黄色提示含「原计件人 + 时间 + 件数」，**无弹窗**（接口带 `silent`，不下发全局 toast）
- [x] `32006`：红色提示「该手件数不足，请追加手」；其他错误红色 + 清缓冲
- [x] 部分生产：可输入实际件数（`≤ bundle_qty`，由服务端校验）提交
- [x] 设置条：操作员远程搜索（`/piecework/operators`）、默认工序、工位号 `localStorage`、暂停开关
- [x] 在线/离线状态条（离线仅提示，不落队列 —— 队列见 T-PIECE-008）
- [x] 组件测试：格式校验、成功、重复（`32001`）、超产（`32006`）、暂停忽略、幂等键同窗稳定/跨窗变化
- [x] 单文件 ≤ 400 行；`pnpm typecheck / lint / test:unit / build` 全绿

## 风险

| 风险 | 缓解 |
| --- | --- |
| 全局 `keydown` 在组件卸载后仍监听 | `onUnmounted` 移除监听；测试断言卸载后不再触发 |
| Web Audio 在 jsdom 不可用 | `feedback.ts` 内 feature-detect（无 `AudioContext` 时静默跳过）；测试只测纯逻辑 |
| 大字计数与接口返回不一致 | 计数**只用接口返回的 `today_hands/today_qty/today_amount`** 覆盖，不前端累加（避免与红冲/幂等错位） |

## 实施结果

**已交付（纯前端，无后端改动 / 无迁移 / 无契约重生成）。**

新增 `frontend/packages/admin/src/views/piecework/scan/`（5 个文件 + 1 测试）：

| 文件 | 行 | 职责 |
| --- | --- | --- |
| `useScanMachine.ts` | 217 | 缓冲 / 本地正则校验 / 幂等键 / 提交状态机 / `32001`·`32006` 分支；不依赖 Vue 生命周期，可单测 |
| `Scan.vue` | 183 | 编排：`localStorage` 工位号、全局 `window` keydown 注册与卸载清理、部分生产入口、在线/离线 Alert、`defineOptions({ name: 'PieceworkScan' })` |
| `ScanDisplay.vue` | 146 | 大字卡（`tabular-nums`）+ 缓冲行 + 着色结果行 + 三色闪框（design token） |
| `ScanSetupBar.vue` | 143 | 工位号 + 操作员/工序两路远程搜索（防抖 300ms）+ 暂停开关 |
| `feedback.ts` | 54 | Web Audio 现场合成提示音 + `FLASH_MS` 纯常量 |
| `Scan.test.ts` | 212 | 13 例：正则、`isTypingTarget`、幂等键同窗稳定/跨窗变化、成功/`32001`/`32006`/暂停/非法格式 |

接线：`api/piecework.ts` 新增 `countPiecework`（带 `Idempotency-Key`，`silent: true`）与 `listPieceworkOperators`；`shared/api/client.ts` 新增 `RequestOptions.silent`；`shared/types/index.ts` + `shared/index.ts` 新增 `PieceworkCountIn/Out`、`PieceworkOperatorOptionOut` 三个别名；`router/index.ts`（`piecework/scan`，name `piecework-scan`）+ `layouts/menu.ts`（「生产管理 → 工位机扫码」）。

**实现期暴露的 4 件事**（详见 `docs/12` 变更 0150）：① 本地正则必须用后端的 **1~3 位尺码码**（`04 §5.1` 示例写死 2 位是偏差，已回填本卡目标 §3）；② 新增 `silent` 选项让 `32001`/`32006` 走行内提示而非全局 toast；③ 大字卡用服务端 `today_*` 覆盖而非前端累加；④ antd `Select`/`InputNumber` 的类型坑（`null` / explicit `undefined`）与 `tokens.test.ts` 的禁止字面量守卫。

闸门：`pnpm run typecheck` / `lint` / `test:unit`（**317 passed** = admin 261 + shared 56）/ `build` 全绿；后端未改动（无 Docker 变更）。
