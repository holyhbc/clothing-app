# T-WEB-003：admin 通用层（design token / 布局 / Combo / 登录与错误页）

| 项 | 内容 |
| --- | --- |
| 模块 | web |
| 负责人 | AI |
| 状态 | `todo` |
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
- [ ] `src/styles/tokens.css`：**照抄 06 §1 全部变量**（品牌色 / 功能色 / 中性色 / 间距 / 圆角 / 阴影 / 字体 / 布局 / z-index），一个字不改
- [ ] `src/styles/global.scss`：全局重置 + 表格密度（PC 端**密度优先**）+ 金额等宽数字 `tabular-nums`
- [ ] `src/layouts/AdminLayout.vue`：Header 48px（Logo / 菜单折叠 / 面包屑 / 用户）/ Sider 200px（两级菜单，**按 `permissions[]` 过滤**）/ 内容区 24px 内边距 + 最大 1680px 居中
- [ ] `src/components/Combo.vue`：可搜索下拉（输入即搜 300ms 防抖、`↑/↓` 移动、`Enter` 选高亮、`Esc` 关闭、点击外部关闭、**回显「编码 + 名称」**、候选 ≤20、停用项标红不可选、空态带「＋ 新建」入口）
- [ ] `src/views/Login.vue`：账号 + 密码；错误文案说清下一步（06 §5）；回车提交
- [ ] `src/views/Password.vue`：改密（含首次强制场景说明）
- [ ] `src/views/Error403.vue` / `Error404.vue`：403 带「申请入口」，404 带返回首页
- [ ] 组件测试：`Combo.test.ts`（防抖 / 键盘 / 回显 / 失效项 / 空态）、`global` 无硬编码颜色断言（lint 规则 + 单测双重）

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

- [ ] 遵守 docs/06 §1（token 唯一来源，**组件内禁字面量颜色/间距**）、§5（状态与错误反馈：禁 `alert()`、禁原生技术错误文案）、§6（分辨率与缩放）、§10（**Combo 强制**，候选 >30 禁裸 `<select>`）
- [ ] 遵守 docs/03 §2.1/§2.3（页面结构模板）、§2.2（命名）

## 验收标准

- [ ] `tokens.css` 与 06 §1 **逐项一致**（写单测比对 key 集合）
- [ ] 全仓搜索无 `#1668dc`、`rgb(`、`16px` 等字面量（lint 拦截）
- [ ] `Combo`：输入即搜 + 300ms 防抖、`Enter` 选中、**回显「编码 + 名称」**、停用项标红且不可提交、无结果时显示「无匹配结果 + 新建入口」
- [ ] 登录页：密码错误显示「账号或密码不正确」（**不区分账号不存在与密码错**，防枚举）；网络错误给「重试」按钮
- [ ] 1366×768 与 1920×1080、浏览器 125%/150% 缩放不破版
- [ ] `pnpm lint` / `pnpm typecheck` / `pnpm test:unit` 全绿

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-W13 | `Combo` 输入 `0021` | 300ms 后发请求，候选 ≤20 | |
| TC-W14 | `Combo` `Enter` | 选中高亮项并回显「编码 + 名称」 | |
| TC-W15 | 选中项被停用 | 标红 + 禁止提交 | |
| TC-W16 | 空结果 | 显示「无匹配结果」+「＋ 新建」入口 | |
| TC-W17 | 登录失败 | 文案不含技术细节 | |
| TC-W18 | tokens key 集合 vs 06 §1 | 完全一致 | |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| | +0 / -0 | |

**提交记录**：
- `<hash>` feat(web): admin 通用层（token/布局/Combo/登录与错误页） …

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| | | docs/12 §遗留问题清单 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。
