---
description: 阶段 3 编码：按任务卡顺序实现 迁移→模型→Service→Router→前端→测试，测试先红后绿
agent: build
---

先读 `AGENTS.md` §1-§2、`docs/03-代码规范.md`、`docs/04-数据库规范.md`、`docs/05-接口设计规范.md`、`docs/08-单据状态机规范.md`、`docs/10-测试规范.md`、`docs/06-前端与UI规范.md`，以及任务卡指定的 `docs/modules/*.md`。

任务卡：$ARGUMENTS

执行顺序**固定**，不要跳：

1. **先复述"将要改动的文件清单"**。与任务卡不一致时，先说明原因并等用户确认。
2. Alembic 迁移（人工检查 autogenerate 结果，禁止直接提交）
3. `models.py` → `schemas.py` → `repository.py` → `service.py` → `router.py`
4. 前端：`packages/shared` 类型与 API → `packages/admin`（或 `packages/mobile`）页面
5. 测试
6. 同步更新受影响的 `docs/modules/*.md`

硬性要求（违反即回滚）：

- 新表必须有 `id`(uuid) + `created_at/by` + `updated_at/by` + `deleted_at` + `version` + `remark`
- 事务边界只在 service：`async with self.session.begin():` 是方法第一行
- 状态字段只允许由状态机迁移方法写入；每次迁移写 `document_logs`；用乐观锁 `WHERE id=? AND version=? AND status=?`
- 审核副作用与反审核反向副作用成对实现
- 金额用 `Decimal` / `numeric`；时间用带 tz 的 datetime；枚举集中在 `app/common/enums.py`
- 新接口必须有：`require_permission(资源:动作, scope=...)` + 响应包装 + 错误码（取自 05 §4，不许发明）+ OpenAPI `tags`/`summary`
- service 方法第一行必须是 `apply_data_scope(stmt, Model, ctx, ...)`
- 幂等：高频写接口用数据库唯一索引兜底，幂等命中返回 200 不报错
- **测试先写并跑出失败输出，再让它通过**。禁止改断言迁就实现。
- 不硬编码业务常量；不顺手重构；不改单次提交 > 1200 行手写或 > 3 模块；
  **单文件 > 400 行就是「该抽组件了」，不要拆提交来绕过**（ADR-0030）

完成后输出：

1. 改动文件清单（新增/修改/行数）
2. 测试报告（`docs/10-测试规范.md` §9 格式，含覆盖率与遗留）
3. 闸门 1-5 逐条结果
4. 需要归档到 `docs/12` 的变更摘要

不要自己提交 git，等用户确认。
