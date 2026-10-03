# T-WEB-002：`packages/admin` 骨架（路由 / 守卫 / store / v-can）

| 项 | 内容 |
| --- | --- |
| 模块 | web |
| 负责人 | AI |
| 状态 | `todo` |
| 优先级 | P0 |
| 依赖 | T-WEB-001 |
| 被依赖 | T-WEB-003 ~ T-WEB-006 |
| 关联设计 | [docs/modules/00-P0地基.设计.md](../modules/00-P0地基.设计.md) §6.1、§6.2 |
| 关联 ADR | ADR-0008（权限点） |
| 估算 | 0.7d |

## 目标

把 `packages/admin` 变成能跑的 Vue 3 应用：Ant Design Vue 5 + 路由 + 守卫 + Pinia auth store + `v-can` 指令，**无 token 跳登录、无权限跳 403**。

## 范围

**要做**：
- [ ] `packages/admin/package.json`（`ant-design-vue` `pinia` `vue-router` `vue` + `vue-tsc` `vitest` `eslint` `prettier`）、`vite.config.ts`（`/api` 代理到 `localhost:8000`）、`tsconfig.json`
- [ ] `src/main.ts`：create app + Pinia + router + Antd + 注册 `v-can`
- [ ] `src/App.vue`：`<RouterView>` + 全局错误边界
- [ ] `src/router/index.ts`：路由表 + **守卫**（未登录 → `/login`；已登录但缺权限 → `/403`；`must_change_password` → 强制 `/profile/password`）
- [ ] `src/stores/auth.ts`：`user` / `permissions` / `dataScope` / `accessToken`；`has(code)`；`login` / `logout` / `loadMe` / `clear`
- [ ] `src/directives/permission.ts`：`v-can` 显隐 + `:disabled` 用法说明（07 §4.1）
- [ ] 单元测试：守卫 4 例、`v-can` 2 例、store `has()` 3 例

**不做**：
- 不写页面（登录页与布局在 T-WEB-003）
- 不引入 `any` 逃逸、不用 Options API（03 §2.1）

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `frontend/packages/admin/package.json` | 新增 | 依赖 + 脚本 |
| `frontend/packages/admin/vite.config.ts` | 新增 | 构建 + 代理 |
| `frontend/packages/admin/tsconfig.json` | 新增 | strict |
| `frontend/packages/admin/src/main.ts` | 新增 | 应用入口 |
| `frontend/packages/admin/src/App.vue` | 新增 | 根组件 |
| `frontend/packages/admin/src/router/index.ts` | 新增 | 路由 + 守卫 |
| `frontend/packages/admin/src/stores/auth.ts` | 新增 | Pinia |
| `frontend/packages/admin/src/directives/permission.ts` | 新增 | `v-can` |
| `frontend/packages/admin/src/**/*.test.ts` | 新增 | 单测 |

## 实现要点（必读规范）

- [ ] 遵守 docs/03 §2.1（`<script setup lang="ts">` only、strict、`noUncheckedIndexedAccess`、禁 `any`、无 `console` 残留）、§2.2（命名与目录）
- [ ] 遵守 docs/06 §2.1（布局：Header 48px / Sider 200px / 内容 24px 内边距、最大 1680px）
- [ ] 遵守 docs/07 §4.1（`v-can` 控制**显示**；禁用用 `:disabled` + Tooltip；**前端隐藏不是安全**）

## 验收标准

- [ ] `pnpm dev` 起得来，`/` 未登录 → 跳 `/login`
- [ ] 登录态 + 有权限 → 正常进入；登录态 + 无权限 → `/403`
- [ ] `must_change_password=true` → 强制跳改密页
- [ ] `v-can="'system:user:manage'"` 无权时元素被移除；有权时保留
- [ ] `pnpm lint` / `pnpm typecheck` / `pnpm test:unit` 全绿
- [ ] 1366×768 与 1920×1080 两分辨率不破版（06 §6）

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-W08 | 未登录访问 `/` | 跳 `/login` | |
| TC-W09 | 已登录无权限访问受限路由 | 跳 `/403` | |
| TC-W10 | `must_change_password` | 强制 `/profile/password` | |
| TC-W11 | `v-can` 有权 / 无权 | 保留 / 移除 | |
| TC-W12 | `auth.has('base:read')` vs `has('*')` | 精确匹配 / 通配 | |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| | +0 / -0 | |

**提交记录**：
- `<hash>` feat(web): admin 骨架（路由/守卫/store/权限指令） …

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| | | docs/12 §遗留问题清单 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。
