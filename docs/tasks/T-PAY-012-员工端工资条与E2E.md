# T-PAY-012：员工端 `self` 接口 + 工资条 H5 + E2E 全链路（P2 出口）

| 项 | 内容 |
| --- | --- |
| 模块 | payroll |
| 负责人 | backend-dev + frontend-dev + qa |
| 状态 | `todo` |
| 优先级 | P2（**P2 出口卡**） |
| 依赖 | T-PAY-009、T-PAY-011；**T-MOBILE-001**（员工端脚手架，Q-PL10 决策 2026-10-17）——未交付则该卡移动端部分阻塞 |
| 被依赖 | —（P3 可开工） |
| 关联设计 | [`modules/05-工资.md`](../modules/05-工资.md) §6.2、§11 TC-19 |
| 关联规范 | `docs/07 §1.2/§5`（`self:payroll:read` + `SELF` 恒等）、`docs/06 §3.1/§3.2`（预估标注）、`docs/05 §8` |
| 关联 ADR | ADR-0003（员工端） |
| 估算 | 1d |

## 目标

员工在 H5 看自己的工资（`period_code` + 区间 + 明细 + 实发），并跑通 P2 全链路 E2E。

## 范围

**要做**：
- [ ] 后端 `/self/payroll/sheets`、`/self/payroll/current`（`05 §6.2`）：权限 `self:payroll:read`，
      `employee_id` **从 token 取**、`extra="forbid"` 拒传 `employee_id`；未审核期返回 `is_approved=false` + `estimate_flag=true`
- [ ] 员工端页面 `mobile/views/payroll/Slip.vue`（Vant 4）。⚠️ `mobile` 目前仅占位 `main.ts` → **先由 `T-MOBILE-001` 交付脚手架**（登录/布局/路由/stores/auth），本卡只挂工资条页；SSE 消费用 `@garment/shared` 的 `EventStreamClient`（T-PIECE-011），不假设存在 `useSSE`
- [ ] 前端必须显示「含未审核，以最终为准」标注（`06 §3.2` / Q-PL03 口径）
- [ ] E2E（Playwright）：建周期 → 计件 → 结算 → 工资审核 → 发放 → 导出代发文件，金额一致（TC-19 / E2E-03）

**不做**：
- 不做员工端写操作（工资只读）
- 不做凭证 E2E（M9 未建）

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `backend/app/modules/payroll/self_router.py` | 新增 | 员工端端点 |
| `backend/tests/modules/test_payroll_self.py` | 新增 | self 权限/范围 |
| `frontend/packages/mobile/src/views/payroll/Slip.vue` | 新增 | 工资条页 |
| `frontend/packages/mobile/src/{main.ts,router,layouts,stores/auth.ts}` | 新增/修改 | ⚠️ 脚手架（Q-PL10）；若单开 `T-MOBILE-001` 则不在本卡 |
| `frontend/e2e/p2-payroll-flow.spec.ts` | 新增 | E2E |

## 实现要点

- [ ] `employee_id` 一律从 token，数据范围恒 `SELF`（`07 §1.2`）
- [ ] 跨员工访问返回 403 `12001` / 只返本人（测试断言「换 token 真的只看到自己」）
- [ ] E2E 审核人须与制单人不同（`10005`，参考 T-BUND E2E 的 `E2EAUDITOR`）
- [ ] E2E 金额以服务端为准，不前端累加

## 验收标准

- [ ] `test_payroll_self.py` 全过；`extra="forbid"` 传 `employee_id` → 422
- [ ] `pnpm test:unit` E2E 相关全绿
- [ ] `playwright` 全链路 1 次通过（含种子库守卫）
- [ ] 闸门 1-5 全绿

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-19 | E2E 全链路 | 金额一致 | |
| TC-PAY-SL01 | 员工越界传 `employee_id` | 422 | |
| TC-PAY-SL02 | 员工只看本人 | 换 token 只返本人 | |
| TC-PAY-SL03 | 未审核期显示预估 + 标注 | 通过 | |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| | +0 / -0 | |

**提交记录**：
- `<hash>` feat(payroll): 员工端工资条 + E2E（T-PAY-012）

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| Q-PL09 凭证缺位（E2E 不含凭证断言） | docs/12 L-124 |
| Q-PL10 mobile 脚手架未就绪（阻塞本卡移动端部分） | modules/P2 §8 / docs/12 L-125 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。

## 变更记录

| 日期 | 变更内容 | 操作人 |
| --- | --- | --- |
| 2026-10-17 | 初版 | AI |
