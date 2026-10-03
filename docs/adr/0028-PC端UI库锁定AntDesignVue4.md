# ADR-0028：PC 端 UI 库锁定 Ant Design Vue 4.2.6（规范原文写的 5.x 不存在）

| 项 | 内容 |
| --- | --- |
| 状态 | `Accepted` |
| 日期 | 2026-10-03 |
| 决策人 | AI 提出、业务方确认（T-WEB-002 落地时发现规范与 npm 事实不符） |
| 影响范围 | 前端技术选型 / 构建 |
| 关联 | [01 技术选型表](../01-技术选型与架构.md)、[06 §2](../06-前端与UI规范.md)、AGENTS.md §0 |

---

## 背景

- `docs/01-技术选型与架构.md` 的技术选型表把 PC 端 UI 库写为
  「Ant Design Vue | **5.x**」，`docs/06-前端与UI规范.md` §2 与
  `AGENTS.md` §0 的概述里也写着「Ant Design Vue 5」。
- T-WEB-002 要给 `packages/admin` 装这个库时，`npm view ant-design-vue versions`
  显示已发布的 major 只有 `1` / `2` / `3` / `4`，最新为 **4.2.6**。
  **5.x 不存在。** React 侧的 `antd` 有 5.x，Vue 侧没有 —— 大概率是当初选型时
  把两者的版本号记混了。
- 因此这条规范无法执行：照它写 `ant-design-vue@^5` 会直接 install 失败。

---

## 决策

**锁定 `ant-design-vue@4.2.6`**（`^4.2.6`），并把 `docs/01` 技术选型表与
`docs/06` §2 的「5.x」「Ant Design Vue 5」全部改为 4.x。

配套两条约定：

1. **Vant（员工端）不动**，仍是 docs/01 写的 Vant 4 —— 那一档是存在的
   （`npm view vant version` 有 4.x），不需要修订。
2. **选组件前先查版本存在性**，不凭记忆写版本号。规范里的版本号是**契约**，
   写错了要么 install 失败（当场发现），要么装到一个不兼容的大版本
   （几个月后才发现，症状与原因完全无关）。

---

## 理由

| # | 理由 |
| --- | --- |
| 1 | 4.x 是**唯一可用的选项**，不是"权衡后的选择"。Vue 侧的 Ant Design Vue 没有 5.x，不存在"先用 4.x 顶着"之外的路径 |
| 2 | 4.2.6 的 peer 是 `vue >=3.2.0`，与本仓 Vue 3.5 兼容；Table / Form / Tree / Transfer / Steps / Upload 这些单据型 ERP 需要的组件 4.x 全都有（这正是 docs/01 选它的理由，与版本无关） |
| 3 | 改规范而不是改代码：AGENTS.md §8 规定"规范与代码不一致时以文档为准"，但**前提是文档说的东西存在**。这里不是口径分歧而是事实错误，唯一出路是把文档改成真的 |

### 被否掉的方案

| 方案 | 否掉原因 |
| --- | --- |
| 保留规范写 5.x，在 README 注明"实际用 4.x" | 规范与实现长期分叉，下一个 AI 照样会 `pnpm add ant-design-vue@5` 然后卡住；分叉还让"规范是唯一事实来源"这句话失去意义 |
| 换成 Element Plus（Vue 生态另一个主力） | 会连带推翻 docs/06 §2.1~§2.4 的全部组件写法（Descriptions / Card / Space / Result 的用法与 antd 基本一致，但 Table / Form 的 API 与校验写法差异大），且 docs/01 选 antd 的理由（单据型组件齐全）仍然成立 —— 没有换库的收益 |
| 换成 React 版 antd | 与 docs/01 §技术选型里"React + Ant Design 与 Vue 等价"的判断直接冲突，且整个前端技术栈要重写 |

---

## 影响与后续

| 项 | 处理 |
| --- | --- |
| `docs/01` 技术选型表 | 「Ant Design Vue \| 5.x」→「Ant Design Vue \| **4.2.x**」 |
| `docs/06` §2 标题 | 「Ant Design Vue 5」→「Ant Design Vue 4」 |
| `AGENTS.md` §0 概述 | 「PC 端 Ant Design Vue 5」→「PC 端 Ant Design Vue 4」 |
| `packages/admin/package.json` | `ant-design-vue: ^4.2.6` |
| `pnpm-workspace.yaml` | 新增 `allowBuilds: core-js` —— antd 的传递依赖，其 postinstall 只打印赞助提示 |

**若将来真的出了 ant-design-vue 5**：那是 major 升级（Vue 支持范围、组件 API、
主题机制都可能变），必须**新开一张卡 + 新 ADR 评估**，不得在业务卡里顺手升级。

---

## 验证

- `pnpm install` 成功且 `pnpm build` 产出 `dist/index.html`（实测）
- `pnpm-lock.yaml` 里 `ant-design-vue@4.2.6` 的 peer 警告为空
- `pnpm lint` / `pnpm typecheck` / `pnpm test:unit` 全绿
