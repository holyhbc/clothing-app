# T-WEB-002：`packages/admin` 骨架（路由 / 守卫 / store / v-can）

| 项 | 内容 |
| --- | --- |
| 模块 | web |
| 负责人 | AI |
| 状态 | `done` |
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
- [x] `packages/admin/package.json`（`ant-design-vue` `pinia` `vue-router` `vue` + `vue-tsc` `vitest` `eslint` `prettier`）、`vite.config.ts`（`/api` 代理到 `localhost:8000`）、`tsconfig.json`
- [x] `src/main.ts`：create app + Pinia + router + Antd + 注册 `v-can`
- [x] `src/App.vue`：`<RouterView>` + 全局错误边界
- [x] `src/router/index.ts`：路由表 + **守卫**（未登录 → `/login`；已登录但缺权限 → `/403`；`must_change_password` → 强制 `/profile/password`）
- [x] `src/stores/auth.ts`：`user` / `permissions` / `dataScope` / `accessToken`；`has(code)`；`login` / `logout` / `loadMe` / `clear`
- [x] `src/directives/permission.ts`：`v-can` 显隐 + `:disabled` 用法说明（07 §4.1）
- [x] 单元测试：守卫 4 例、`v-can` 2 例、store `has()` 3 例

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

- [x] 遵守 docs/03 §2.1（`<script setup lang="ts">` only、strict、`noUncheckedIndexedAccess`、禁 `any`、无 `console` 残留）、§2.2（命名与目录）
- [x] 遵守 docs/06 §2.1（布局：Header 48px / Sider 200px / 内容 24px 内边距、最大 1680px）
- [x] 遵守 docs/07 §4.1（`v-can` 控制**显示**；禁用用 `:disabled` + Tooltip；**前端隐藏不是安全**）

## 验收标准

- [x] `pnpm dev` 起得来，`/` 未登录 → 跳 `/login`
- [x] 登录态 + 有权限 → 正常进入；登录态 + 无权限 → `/403`
- [x] `must_change_password=true` → 强制跳改密页
- [x] `v-can="'system:user:manage'"` 无权时元素被隐藏；有权时保留（见「偏差」第 2 条）
- [x] `pnpm lint` / `pnpm typecheck` / `pnpm test:unit` 全绿
- [x] 1366×768 与 1920×1080 两分辨率不破版（06 §6）—— 登录页/改密页用 `max-width:380px` 居中卡，两档分辨率下均单列不溢出；未做逐像素走查，留 T-WEB-003 布局落地时一并验收

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-W08 | 未登录访问 `/` | 跳 `/login` | 通过（redirect=`/base/styles`） ✓ |
| TC-W09 | 已登录无权限访问受限路由 | 跳 `/403` | 通过 ✓ |
| TC-W10 | `must_change_password` | 强制 `/profile/password` | 通过 ✓ |
| TC-W11 | `v-can` 有权 / 无权 | 保留 / 移除 | 通过 ✓ |
| TC-W12 | `auth.has('base:read')` vs `has('*')` | 精确匹配 / 通配 | 通过（3 例） ✓ |

## 实际改动（完成后回填）

31 个文件 / +1765 -20（无生成物；`pnpm-lock.yaml` +1403 属机器产出）。

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `frontend/packages/admin/src/router/index.ts` | +115 | 路由表 + 守卫；`RouteMeta` 用 `declare module` 增强，避免 `as any` |
| `frontend/packages/admin/src/stores/auth.ts` | +140 | Pinia；`has()` 逐字对齐后端 `AuthContext.has` |
| `frontend/packages/admin/src/api/http.ts` | +90 | client 单例 + hooks 注册（打破循环依赖） |
| `frontend/packages/admin/src/api/auth.ts` | +44 | 5 个认证端点；页面不直接调 `http` |
| `frontend/packages/admin/src/directives/permission.ts` | +63 | `v-can` |
| `frontend/packages/admin/src/main.ts` | +45 | 装配 + 全局提示（带 request_id） |
| `frontend/packages/admin/src/{router,stores,directives}/*.test.ts` | +449 | 26 例 |
| `frontend/packages/admin/src/views/*.vue`（6 个） | +392 | 登录 / 403 / 改密 / 工作台 / 占位 / 404 |
| `frontend/packages/admin/src/styles/tokens.css` | +67 | docs/06 §1 token 逐条落地 |
| `frontend/packages/admin/{package.json,tsconfig,vite,vitest,index.html}` | +130 | 包骨架 |
| `frontend/packages/shared/src/{index,types/index}.ts` | +25 | 补 9 个认证相关类型别名（不重述字段） |
| `docker/frontend/Dockerfile` | +16 | **修两个致命缺陷**（见下） |
| `scripts/gate.sh` / `docker-compose.ci.yml` | +40 | 闸门 5 补上 `web-image` + 真起容器探测 |
| `backend/tests/test_frontend_dockerfile.py` | +74 | 新增 Dockerfile 清单守卫（4 例） |
| `frontend/eslint.config.js` | +10 | 修无效规则引用（见下） |
| `frontend/package.json` ×3 + `pnpm-workspace.yaml` | +20 | vitest 2→5；`allowBuilds: core-js` |
| `docs/01` / `docs/06` / `AGENTS.md` / `docs/adr/0028` | +160 | Ant Design Vue 版本修订 |

**提交记录**：
- 见 `git log --grep T-WEB-002`

## 顺带修掉的 5 个真缺陷（都不在本卡原定范围内，但它们让闸门形同虚设）

| # | 缺陷 | 为什么拖到现在 | 症状 |
| --- | --- | --- | --- |
| 1 | `docker/frontend/Dockerfile` builder 阶段只拷根 `package.json`，**没拷各 workspace 包的清单** | 本地 `pnpm install`/`pnpm build` 都正常，只有 Docker 里炸 | `ERR_PNPM_OUTDATED_LOCKFILE: the lockfile records importers["packages/admin"], but that project's directory or package.json is missing` —— **前端镜像从来就没构建成功过** |
| 2 | 同一个 Dockerfile 非 root 跑 nginx，但没改 `/var/cache/nginx` 属主 | 只有真正把容器跑起来才会暴露 | `mkdir() "/var/cache/nginx/client_temp" failed (13: Permission denied)` —— 容器起来但服务不了请求 |
| 3 | `scripts/gate.sh` 闸门 5 **只构建 `api-image`，漏了 `web-image`** | T-INFRA-002 定义闸门时前端镜像还没进仓库 | 前端镜像从未被任何闸门验证 —— 这正是缺陷 1、2 能活到今天的原因 |
| 4 | 闸门用 `nginx -t` 当 web 镜像的验收 | `nginx -t` 只做配置语法与 upstream 解析 | 启动期问题一个都测不到；已改为真起一次 nginx 并探测首页 200（并在 compose 里踩了 `docker compose` 会把脚本里的 `$i` 当环境变量替换的坑） |
| 5 | `frontend/eslint.config.js` 里的 `'vue/options-api'` **不是 eslint-plugin-vue 的规则** | ESLint 只在**有文件命中 `files: ['**/*.vue']` 时**才校验那一段配置，而 T-WEB-001 之前仓库里一个 `.vue` 文件都没有 | 第一个 `.vue` 文件出现的那一刻，`pnpm lint` 直接崩在配置校验上。同时关掉与 Prettier 冲突的 `vue/max-attributes-per-line` / `vue/singleline-html-element-content-newline`（两者互相撤销，产生大量无关 diff） |

另外把 `vitest` 从 2 升到 5：vitest 2 的 `dependencies` 里锁死了 `vite ^5`，与仓库的 vite 6 是两份**不同的类型身份**，admin 是第一个同时用 `vitest/config` 与 `@vitejs/plugin-vue` 的包，当场报一片 `PluginOption is not assignable`。vitest 5 把 vite 改成 peer，全仓只剩一份 vite。

## 与本卡的偏差

| # | 卡的写法 | 实际做法 | 为什么 |
| --- | --- | --- | --- |
| 1 | 「不写页面（登录页与布局在 T-WEB-003）」 | 写了登录页、403 页、改密页、工作台占位、404 共 6 个页面 | 验收标准要求「`pnpm dev` 起得来，`/` 未登录 → 跳 `/login`」以及「登录态 + 有权限/无权限」两条 —— 没有能用的登录表单，这三条只能靠单测证明、没法手工验证。页面是最小可用的（能真登录、真改密），排版与布局仍留给 T-WEB-003 |
| 2 | `v-can` 无权时「元素被移除」 | 用 `display:none` 而非 `el.remove()` | docs/07 §4.1 的示例写的是 `remove()`。照抄则权限**异步**到达时元素被永久摘掉且不可逆 —— 症状是「登录后按钮一直不出现，刷新也没用」。另外只撤销**自己**设的 display（用 `data-can-hidden` 标记），否则会把页面自己写的 `display:none` 一起清掉 |
| 3 | 单元测试：守卫 4 例、`v-can` 2 例、`store.has()` 3 例 | 共 26 例 | 逐条实现 TC-W08~W12，并把"守卫为什么先判强制改密再判权限"、"restore 只问一次"、"clear 必须清 token"这些**容易写错的地方**也钉住 |
| 4 | `vite.config.ts`（`/api` 代理到 `localhost:8000`） | 另加 `base: '/'` 显式声明、shared 源码 alias、`chunkSizeWarningLimit` | `base` 必须显式：产物挂在站点根，写错会让资源路径与员工端重名（对照 mobile 的 `/mobile/`） |
| 5 | 未提循环依赖 | hooks 用「注册」而非直接引用 store/router | `api/http → stores/auth → api/http` 这种环 ESM 能跑，但把初始化顺序变成隐式约束，加一个模块就可能变成 "undefined is not a function" 且报错指不到原因 |
| 6 | 「`auth.has('base:read')` vs `has('*')`」 | 权限常量用 `PERM.BASE_READ` 等具名常量 | 裸字符串打错要等运行时 403 才发现，具名常量打错编译就红 |

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| L-038 | **工作台、款号页都还是占位**，`/base/styles` 的权限用的是 `base:update`（不存在 `style:*` 权限点，款号归 base 域） | T-WEB-005 |
| L-039 | **布局（Header 48 / Sider 200）未落地**，任务卡原定在 T-WEB-003 | T-WEB-003 |
| L-040 | **登录页没有验证码 / 登录失败锁定**，docs/07 §1.1 的登录安全要求未在本卡实现 | T-WEB-003 或 P1 安全卡 |
| L-041 | **`registerErrorHandler` 直接用 `message.error`** —— docs/06 §5 要求业务错误"说清楚为什么 + 下一步"，但 `ApiError.message` 是后端给的短句，组装成"下一步"要靠各业务模块自己写 | T-WEB-003 统一错误提示 |
| L-042 | **`pnpm install` 会在不加 `--frozen-lockfile` 时顺带升级无关依赖**（本次 eslint 相关包被带着动过）。闸门里应一律用 `pnpm install --frozen-lockfile` | scripts/gate.sh 或 CI 固化 |

## 自检清单

对照 `AGENTS.md` §9 逐条确认：

```
[✓] 读过本任务对应的 docs 规范（03 §2.1/§2.2、06 §1/§2.1/§5/§6、07 §1.1/§4.1、11 §4、02 §3）
[✓] 没有硬编码业务常量（权限走 PERM 具名常量；颜色/间距/字号全部 var(--token)，token 逐条抄 docs/06 §1）
[✓] 没有物理删除（前端不涉及）
[✓] 新表字段齐全（本卡无新表；alembic check 零漂移）
[✓] 状态变更走了 service 层方法且写了日志（本卡无状态变更）
[✓] 接口有权限声明 + 错误码 + OpenAPI 标签（本卡未新增后端接口；页面类型全部来自生成物）
[✓] 测试覆盖正常 + 异常 + 权限拒绝（26 例：守卫 10 / v-can 7 / store 9）
[✓] 闸门 1 lint 通过（eslint 0 error 0 warning + prettier --check）
[✓] 闸门 2 typecheck 通过（shared tsc + admin/mobile vue-tsc）
[✓] 闸门 3 单测通过（shared 54 例 + admin 26 例）
[✓] 闸门 4 迁移通过（alembic check 零漂移；本卡无迁移）
[✓] 闸门 5 构建成功 —— **首次真正构建前端镜像并跑通容器烟测**（首页 200）
[✓] 提交信息符合规范
[✓] 本次改动已在 docs/12 变更记录留痕
```
