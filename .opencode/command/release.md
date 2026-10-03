---
description: 阶段 6 发布：按 docs/11 走发布单，每条命令先列给用户确认，带备份校验与回滚预案
agent: build
---

先读 `AGENTS.md` §2.1（红线）、`docs/02-AI开发流水线规范.md` §2.7、`docs/11-部署运维与发布规范.md` 全文、`docs/templates/发布单模板.md`。

发布版本：$ARGUMENTS

执行（**每一步执行前先把完整命令列出来，等用户确认**）：

1. 生成发布单草稿到 `docs/releases/REL-YYYYMMDD-NN.md`：变更集、迁移计划、**回滚方案**、回滚判据。
2. 确认发布前检查 7 项全过（闸门 1-5 + 变更记录 + 文档同步）。
3. 记录当前版本（回滚要用）。
4. 执行备份，**必须看到输出含 `OK <文件> <大小>`**，否则停止。
5. 在生产数据副本上演练迁移 `up → down → up`。
6. 拉取镜像 → `alembic upgrade head` → 滚动重启 api 再 web。
7. 健康检查 `/readyz` 连续 3 次。
8. 冒烟验证（docs/11 §6）：
   - `v_stock_reconciliation` 必须 0 行
   - `v_voucher_balance` 必须 0 行
   - `alembic current` = 期望版本
9. 提示用户完成 3 项人工确认（PC 登录看单据 / 员工端看计件 / 库存台账核对）。
10. 回填发布单执行记录 + 镜像 digest，保留旧镜像至少 2 周。

**回滚判据**（满足任一立即回滚，不要自行继续）：
`/readyz` 连续失败 3 次 / 计件接口 P95 > 500ms / 对账视图非空 / 凭证不平 / 冒烟失败。

**红线（docs/11 §10）**：
- 禁止 `docker compose down -v`、`docker volume rm`、`dropdb`、`TRUNCATE`、无 WHERE 的 `DELETE FROM`
- 禁止跳过备份、禁止打印 `.env` 内容、禁止 force-push、禁止自动改已发布 tag
- 迁移失败时**停止并报告**，禁止手工改表"救活"
- 破坏性 DDL 必须 ADR 说明回滚 + 二次确认
