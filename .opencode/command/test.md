---
description: 阶段 4 测试：跑 5 道闸门，补齐缺失用例（权限矩阵/状态机/并发/反审核还原），出测试报告
agent: build
---

先读 `AGENTS.md` §9、`docs/10-测试规范.md` 全文、目标模块的 `docs/modules/<模块>.md` §11 测试清单。

范围：$ARGUMENTS

执行：

1. **闸门 1-3**，逐条输出命令与结果：
   - `cd backend && uv run ruff check . && uv run ruff format --check .`
   - `cd backend && uv run mypy app`
   - `cd backend && uv run pytest -q --cov=app --cov-fail-under=80`
   - `cd frontend && pnpm lint && pnpm typecheck && pnpm test:unit`
2. **对缺口清单**，核对模块文档 §11，标出缺失用例并补齐。强制覆盖：
   - 权限矩阵 5 条：401 / 403 无操作权限 / 403 越权数据范围 / 403 按 ID 直查越权 / 200 正常
   - 状态机 8 条（08 §5）：合法迁移、非法迁移、乐观锁冲突、必填原因、自审拒绝、**审核→反审核数据完全还原**、重复反审核、作废终态
   - 并发 4 类（10 §5）：20 并发同码计件只落 1 条、并发出库不为负、同周期并发结算只 1 张、并发过账同一凭证
     - 必须用 `asyncio.gather` + **每任务独立 session**，禁止单 session
3. **闸门 4**：迁移 `up → down → up` 往返验证。
4. **闸门 5**：`docker compose build` 或 CI 构建。
5. 覆盖率不足时：指出**具体未覆盖的行**，判断是缺测试还是代码冗余，不要无脑补断言。
6. 输出测试报告（`docs/10-测试规范.md` §9 格式）+ 遗留问题。

禁止：为了通过而放宽覆盖率阈值、改断言、给用例打 skip 标记。
