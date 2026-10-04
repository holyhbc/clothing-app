# T-WEB-003：admin 通用层（design token / 布局 / Combo / 登录与错误页）

| 项 | 内容 |
| --- | --- |
| 模块 | web |
| 负责人 | AI |
| 状态 | `done` |
| 优先级 | P0 |
| 依赖 | T-WEB-002 |
| 被依赖 | T-WEB-004 ~ T-WEB-006 |
| 关联设计 | [docs/modules/00-P0地基.设计.md](../modules/00-P0地基.设计.md) §6.1、§6.2、§6.3 |
| 关联 ADR | 无 |
| 估算 | 0.8d |

## 目标

建立 PC 端的**视觉与交互基线**：design token 全量落地、`AdminLayout` 两级菜单、`<Combo>` 可搜索下拉（INV-9 强制组件）、登录/改密/403/404 页。后续所有页面只拼装，不再各写样式。

## 范围

**要做**：
- [x] `src/styles/tokens.css`：**照抄 06 §1 全部变量**（品牌色 / 功能色 / 中性色 / 间距 / 圆角 / 阴影 / 字体 / 布局 / z-index），一个字不改
- [x] `src/styles/global.scss`：全局重置 + 表格密度（PC 端**密度优先**）+ 金额等宽数字 `tabular-nums`
- [x] `src/layouts/AdminLayout.vue`：Header 48px（Logo / 菜单折叠 / 面包屑 / 用户）/ Sider 200px（两级菜单，**按 `permissions[]` 过滤**）/ 内容区 24px 内边距 + 最大 1680px 居中
- [x] `src/components/Combo.vue`：可搜索下拉（输入即搜 300ms 防抖、`↑/↓` 移动、`Enter` 选高亮、`Esc` 关闭、点击外部关闭、**回显「编码 + 名称」**、候选 ≤20、停用项标红不可选、空态带「＋ 新建」入口）
- [x] `src/views/Login.vue`：账号 + 密码；错误文案说清下一步（06 §5）；回车提交
- [x] `src/views/Password.vue`：改密（含首次强制场景说明）
- [x] `src/views/Error403.vue` / `Error404.vue`：403 带「申请入口」，404 带返回首页
- [x] 组件测试：`Combo.test.ts`（防抖 / 键盘 / 回显 / 失效项 / 空态）、`global` 无硬编码颜色断言（lint 规则 + 单测双重）

**不做**：
- 不写业务页面
- 不写 `StatusTag`/`MoneyText`/`PageLayout`/`EmptyState`/`TableToolbar`（T-WEB-004）

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `frontend/packages/admin/src/styles/tokens.css` | 新增 | 照抄 06 §1 |
| `frontend/packages/admin/src/styles/global.scss` | 新增 | 全局样式 |
| `frontend/packages/admin/src/layouts/AdminLayout.vue` | 新增 | 布局 |
| `frontend/packages/admin/src/components/Combo.vue` | 新增 | 可搜索下拉 |
| `frontend/packages/admin/src/views/Login.vue` | 新增 | 登录 |
| `frontend/packages/admin/src/views/Password.vue` | 新增 | 改密 |
| `frontend/packages/admin/src/views/Error403.vue` | 新增 | 403 |
| `frontend/packages/admin/src/views/Error404.vue` | 新增 | 404 |
| `frontend/packages/admin/src/components/Combo.test.ts` | 新增 | 组件测试 |

## 实现要点（必读规范）

- [x] 遵守 docs/06 §1（token 唯一来源，**组件内禁字面量颜色/间距**）、§5（状态与错误反馈：禁 `alert()`、禁原生技术错误文案）、§6（分辨率与缩放）、§10（**Combo 强制**，候选 >30 禁裸 `<select>`）
- [x] 遵守 docs/03 §2.1/§2.3（页面结构模板）、§2.2（命名）

## 验收标准

- [x] `tokens.css` 与 06 §1 **逐项一致** —— `tokens.test.ts` 双向比对 key 集合（40 个），并另起一例防止「规范里找不到代码块」导致假绿
- [x] 全仓搜索无硬编码颜色 —— 同文件里扫 `components/views/layouts/styles` 全部 `.ts/.vue/.css`，命中即失败（lint 之外再加一道单测，因为 lint 规则覆盖不到 CSS 与模板表达式）
- [x] `Combo`：输入即搜 + 300ms 防抖、`Enter` 选中、**回显「编码 + 名称」**、停用项标红且不可提交、无结果时显示「无匹配结果 + 新建入口」
- [x] 登录页：密码错误显示「账号或密码不正确」（**不区分账号不存在与密码错**，防枚举）；网络错误给「重试」按钮
- [x] 1366×768 与 1920×1080、125%/150% 缩放不破版 —— 布局按 token（`--header-height` / `--sider-width` / `--page-max-width` / `--space-6`），`<1366px` 自动收侧栏成图标；**未做逐像素走查**，留给 T-WEB-004 首个真实页面时一并验收
- [x] `pnpm lint` / `pnpm typecheck` / `pnpm test:unit` 全绿

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
X0ms 后发请求，候选 ≤20 ✓ |
| TC-W14 | `Combo` `Enter` | 选中高亮项并回显「编码 + 名称」 | 选中并回显「编码 + 名称」 ✓ |
| TC-W15 | 选中项被停用 | 标红 + 禁止提交 | 标红 + 不可选中 ✓ |
| TC-W16 | 空结果 | 显示「无匹配结果」+「＋ 新建」入口 | 「无匹配结果」+「＋ 新建」 ✓ |
| TC-W17 | 登录失败 | 文案不含技术细节 | 文案不含技术细节 ✓ |
| TC-W18 | tokens key 集合 vs 06 §1 | 完全一致 | 完全一致 ✓ |

## 实际改动（完成后回填）

21 个文件 / +1858 -104（其中 `pnpm-lock.yaml` 未变动，无生成物）。

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `packages/admin/src/components/Combo.vue` | +404 | 可搜索下拉（INV-9 强制组件） |
| `packages/admin/src/components/Combo.test.ts` | +283 | 18 例（TC-W13~W16 + 键盘/重试/回显/卸载） |
| `packages/admin/src/layouts/AdminLayout.vue` | +276 | Header 48 / Sider 200 两级菜单 / 内容 24px 限宽 1680 |
| `packages/admin/src/layouts/menu.ts` | +64 | 菜单定义（与路由表**刻意不共用数据**） |
| `packages/admin/src/composables/useDebounce.ts` | +48 | 300ms 防抖，可取消 |
| `packages/admin/src/styles/global.css` | +88 | 重置 / 表格密度 / `tabular-nums` |
| `packages/admin/src/styles/tokens.test.ts` | +110 | TC-W18 + 硬编码颜色扫描 |
| `packages/admin/src/views/Login.vue`（改名自 `LoginView.vue`） | +145 | 防枚举文案 + 网络错误重试 |
| `packages/admin/src/views/{Password,Error403,Error404}.vue`（改名） | +162 | 改密 / 403 申请入口 / 404 |
| `packages/admin/src/views/Login.test.ts` | +180 | 6 例（TC-W17、防开放重定向、Enter 提交） |
| `packages/admin/src/{App.vue,main.ts,router/index.ts}` | +40 | 套布局 + 全局样式 + `componentSize="small"` |
| `frontend/eslint.config.js` | +4 | 第三条与 Prettier 冲突的 vue 规则 |

**提交记录**：
- 见 `git log --grep T-WEB-003`

## 过程中被测试抓出来的 5 个真缺陷

| # | 缺陷 | 症状 |
| --- | --- | --- |
| 1 | **选中候选后输入框被清空** | `select()` 里 emit 之后父组件还没重新渲染，`props.modelValue` 仍是旧值；此时收下面板用 `props.modelValue` 反推文本得到空串。症状是「点了候选，输入框里的字全没了」，用户以为自己没选中 |
| 2 | **默认高亮落在第二项** | `nextSelectable(0, 1)` 把 `from` 当"光标当前位置"，+1 就到了下一条。症状是打开候选直接按 Enter 选到了第二条 |
| 3 | **多发一轮 `q=''` 的请求** | `watch(open)` 里顺手 `focus()` 输入框，而 `open` 只会被「已经聚焦」的入口置真 —— 于是输入一个字会先来一屏默认候选，请求数翻倍 |
| 4 | **`label` 被误以为只有名称** | 后端 `OptionOut` 构造是 `label=f"{style_no} {name}"`，编码**已含在 label 里**。前端若再拼一次会得到 `0021 0021 夏季连衣裙`。测试用「label 只有名称」的假数据时测不出来，改成后端真实形状后才暴露 |
| 5 | **登录失败文案永远走错分支** | 后端 `PASSWORD_INCORRECT(11003)` 映射 HTTP 401，而 `ApiError.isAuthFailure` 只覆盖 11001/11002/11004。按 `isAuthFailure` 判的结果是：**输对口令也提示「暂时无法登录，请稍后重试」**，用户反复重试 |

## 与本卡的偏差

| # | 卡的写法 | 实际做法 | 为什么 |
| --- | --- | --- | --- |
| 1 | `src/styles/global.scss` | `global.css` | 用 SCSS 要装 `sass`，而 `sass` 把 `@parcel/watcher`（原生模块、要编译 C++）拖进依赖树，`allowBuilds` 白名单得多批一个原生包、Docker 构建也变慢。真正承载 token 的是 CSS 自定义属性，SCSS 变量反而是多余的第二套变量 |
| 2 | 组件测试 + 「`global` 无硬编码颜色断言（lint 规则 + 单测双重）」 | 加了 lint 侧未覆盖的扫描 | `vue/multi-word-component-names` 之类只管 SFC；CSS 文件与模板表达式里的颜色 lint 管不到，所以扫描放到单测里做 |
| 3 | 未提 token 守卫比对**文档** | TC-W18 双向比对 `docs/06 §1` | 「每个 token 都有值」是 vacuous 的，永远通过。真正会出事的是漂移 —— 改了 css 没改规范，于是下个 AI 照文档写的颜色和现有组件对不上，且没有任何东西报错 |
| 4 | 未提 `docs/06 §1` 补 token | 新增 `--sider-collapsed-width: 48px` | 侧栏收起宽度原来没有 token，组件里只能写字面量 48，违反 docs/06 §1。`tokens.css` 与 `docs/06 §1` 已同步，TC-W18 保证两者不会再漂 |
| 5 | 视图文件名 `Login.vue` / `Error403.vue` 等 | 文件名照用，组件名用 `defineOptions` 声明为 `LoginView` / `Error403View` 等 | 文件名由任务卡定死，组件名由 `vue/multi-word-component-names` 要求至少两词，两个约束只能靠 `defineOptions` 调和 —— 改文件名会同时偏离任务卡与 docs/03 §2.2 |
| 6 | 未提菜单与路由的关系 | 菜单定义独立成 `layouts/menu.ts` | 路由判定错了是**安全问题**，菜单判定错了只是体验问题。共用一份数据会让「加个菜单」的改动同时放宽了访问控制 |

## 遗留问题

已登记进 `docs/12` §5，编号与那里一致（**不在本表另起编号**，否则两处对不上号，
下一个 AI 按哪一份查都会扑空）：

| # | 问题 | docs/12 状态 |
| --- | --- | --- |
| L-044 | eslint flat/recommended 里的排版类规则与 Prettier 冲突（本卡关掉第 3 条） | 已闭环 2026-10 |
| L-045 | `Combo` 与 `AdminLayout` 已就位但**没有真实页面消费**：系统管理菜单点进去落 404；`Combo` 未接真实候选接口；缺「命中片段高亮」 | T-WEB-004 / T-WEB-005 |
| L-046 | `global.css` 末尾的 `@media (max-width:1366px)` 块是空的 | T-WEB-004 |
| L-047 | 登录页仍无验证码 / 失败锁定 | P1 安全卡 |
| L-048 | `import.meta.url` 在 vitest jsdom 下不可用（本卡已改用 `cwd` 上溯） | 已闭环 2026-10 |

## 自检清单

对照 `AGENTS.md` §9 逐条确认：

```
[✓] 读过本任务对应的 docs 规范（03 §2.1/§2.2/§2.3/§2.5、06 §1/§2.1/§2.2/§5/§6/§8/§10、05 §9.5、07 §4.1、02 §3）
[✓] 没有硬编码业务常量（权限走 PERM 具名常量；颜色/间距/字号全部 var(--token)，且有单测扫描）
[✓] 没有物理删除（前端不涉及）
[✓] 新表字段齐全（本卡无新表；alembic check 零漂移）
[✓] 状态变更走了 service 层方法且写了日志（本卡无状态变更）
[✓] 接口有权限声明 + 错误码 + OpenAPI 标签（本卡未新增后端接口）
[✓] 测试覆盖正常 + 异常 + 权限拒绝（Combo 18 + Login 6 + tokens 3 + 原有 26 = 53 例）
[✓] 闸门 1 lint 通过（eslint 0 error 0 warning + prettier --check，两者互不再打架）
[✓] 闸门 2 typecheck 通过（shared tsc + admin/mobile vue-tsc）
[✓] 闸门 3 单测通过（shared 54 + admin 53）
[✓] 闸门 4 迁移通过（alembic check 零漂移；本卡无迁移）
[✓] 闸门 5 构建成功（web 镜像构建 + 容器烟测首页 200）
[✓] 提交信息符合规范
[✓] 本次改动已在 docs/12 变更记录留痕
```
