---
description: 只读规范审查：对照 AGENTS.md 与 docs 规范审查改动，列出违规项与风险，不改任何文件
agent: review
---

先读 `AGENTS.md` 全文、`docs/03-代码规范.md`、`docs/04-数据库规范.md`、`docs/05-接口设计规范.md`、`docs/07-认证与权限规范.md`、`docs/08-单据状态机规范.md`、`docs/12-文档与变更归档规范.md` §9。

审查范围：$ARGUMENTS

执行：

1. `git diff`（无参数则看工作区改动；也可传 commit range）确定改动集合。
2. 逐项对照检查清单，每条给出 **文件:行号 + 原文 + 违反哪条规范（引章节号）**：
   - 审计字段齐全（`deleted_at` / `version` / `created_by` 等）
   - 金额用 Decimal/numeric，无 float
   - 状态字段只在状态机迁移方法里被写
   - 审核副作用与反审核反向副作用成对
   - 事务边界只在 service 第一行
   - 数据范围过滤在 service 层（`apply_data_scope`）
   - 新接口有权限声明 + 响应包装 + 已登记错误码 + OpenAPI tags/summary
   - 新权限点已登记在 `docs/07` §2.2；新错误码已登记在 `docs/05` §4；新枚举值/迁移动作已登记在 `docs/08` §1.1；新单据前缀已登记在 `docs/09` §2.1 且不重复
   - 无硬编码业务常量、无 `print`/`console.log`、无 `any` 逃逸
   - 测试覆盖：权限 5 条 + 状态机 8 条 + 并发 4 类 + 反审核还原
   - 迁移可正可回；无 `DROP`（除 ADR 说明的 down）
   - 提交信息符合规范
3. 按严重度分级：**🔴 阻塞合并** / 🟡 必须修 / 🔵 建议。
4. 给出最小修改方案（改哪个文件哪一行），**不要自己改**。

只输出审查报告，不改文件。
