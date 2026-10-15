# T-PIECE-004：红冲 + 补录（REVERSAL / SUPPLEMENT / MANUAL）

| 项 | 内容 |
| --- | --- |
| 模块 | piecework |
| 负责人 | backend-dev |
| 状态 | `done`（2026-10-13；Q-PW03 已决策后开工，Q-PW16 为新增非阻塞待决） |
| 优先级 | P1（P2 计件**第 4 张卡**；纠错通道，INV-2） |
| 依赖 | T-PIECE-002（链路 A 已可计件）、**Q-PW03 已决策 = 红冲 + 补录**（2026-10-13 业务方确认） |
| 被依赖 | 工资结算卡（净额 = L1 + L2 + L3）、对账审计 |
| 关联设计 | [`modules/04-计件.md`](../modules/04-计件.md) §5.3（流程 C）、§6.1（两行）、§7.3（红冲并发）、§9（32003）、§11 TC-09/10/38/45/46/50 |
| 关联规范 | `docs/03 §1.1`、`docs/05 §5`（幂等）、ADR-0005（计件流水不可变与红冲机制） |
| 关联 ADR | ADR-0005、ADR-0016 |
| 估算 | 0.5d |

## 背景：Q-PW03 的结论

**业务方 2026-10-13 定：返工件走「红冲 + 补录」。**
ADR-0016 的「一码一员工」已让第三种（原码同工序再计一次）彻底不可行（撞 `32001`）。
本卡据此实现三件事：

1. **红冲** `POST /piecework-logs/{id}/reverse`：写 1 条 `REVERSAL`（负值）+ **同事务清空 `bundles.counted_*`**（该手回到未计件，TC-45）
2. **补录** `POST /piecework-logs`（`log_type=SUPPLEMENT`）：写 1 条正确流水，`reversal_of_id` 指回原流水，**同事务重写 `bundles.counted_*`**，单价按**补录当日**取价快照
3. **手工补录**（`log_type=MANUAL`）：漏扫码专用，不需要 `reversal_of_id`，`remark` 必填

⚠️ 原 `BR-PW-19b` 的「未确认前不得实现任何返工分支」随 Q-PW03 决策**解除**。
⚠️ `SUPPLEMENT` / `MANUAL` 的 `qty` **仍须 `≤ bundles.bundle_qty`**（`32006`）—— 不得用补录绕过超产。

## 验收标准

### 红冲 `reverse`

- [ ] `reason` 必填 → 否则 `10002`；`payroll_id` 非空 → `32003`；越权 → `12002`
- [ ] 生成 `L2`：`log_type=REVERSAL`、`qty=-L1.qty`、`amount=-L1.amount`、`reversal_of_id=L1.id`
- [ ] **同事务清空 `bundles.counted_qty=0 / counted_by=NULL / counted_at=NULL`** → 回到未计件
- [ ] 红冲人 ≠ 原流水创建人（08 §4，`super_admin` 需 `force=true` 并写日志）
- [ ] 对已红冲流水再红冲 → 拒绝
- [ ] 幂等：同 `reversal_of_id` 已有 `REVERSAL` → 返回既有（§6.3）
- [ ] 响应含 `reversal_log_id` / `reversed_qty` / `reversed_amount` / `bundle_counted_reset`

### 补录 `SUPPLEMENT`

- [ ] `reversal_of_id` **必填**；`remark` 必填；`qty` 默认 `bundle_qty`，须 `≤ bundle_qty`（`32006`）
- [ ] 单价按**补录当日** `work_date` 取价快照；同事务重写 `bundles.counted_*`
- [ ] 全程无 `UPDATE qty/amount/bundle_no`（append-only）

### 手工补录 `MANUAL`

- [ ] `remark` 必填（`10002`）；`bundle_id` 可反查；校验链同 §5.4 ③

### 并发（§7.3 / TC-50）

- [ ] **红冲与他人扫同码并发**：红冲 `FOR UPDATE` 锁码；红冲先 → 他人可计；他人先 → 红冲校验 `counted_qty == 原流水 qty` 不一致则拒绝
- [ ] 同 `reversal_of_id` 并发重复红冲 → 只落 1 条

## 不做什么

| 项 | 原因 |
| --- | --- |
| 员工端 `/self/**` | 短信登录是 P2，且 Q-PW09 阶段一不开放 self 写入 |
| 返工件**新建码** | Q-PW03 已选红冲 + 补录，另一条被否 |
| `GET /piecework/rates` / SSE | 语义冲突待架构 / 无订阅端，另开卡 |
| 结算回写 `payroll_id` | 工资模块的活 |

## 风险

| 风险 | 缓解 |
| --- | --- |
| 红冲清空 `counted_*` 与他人的并发扫撞 | 红冲也必须 `FOR UPDATE` 锁码（§7.3）；测试 TC-50 真并发 |
| 补录改数绕过超产 | `qty ≤ bundle_qty` 在补录路径同样强制（`32006`），TC-46 |
| 对已红冲流水再红冲 | 拒绝；幂等按 `reversal_of_id` 返回既有 |
| service.py 顶破 400 行 | 写方法放 `reverse.py` mixin，`PieceworkService` 继承 |

## 实施结果

### 交付物

| 文件 | 作用 |
| --- | --- |
| `app/modules/piecework/reverse.py` | `ReverseMixin`：红冲（§5.3 流程 C） |
| `app/modules/piecework/manual.py` | `ManualMixin`：补录 `SUPPLEMENT` + 手工补录 `MANUAL`（§5.3 步骤 4 / §5.4 流程 D） |
| `app/modules/piecework/write_base.py` | `WriteBaseMixin`：两个写路径**共用**的锁助手（锁原流水 / 锁码 / 查绑定 / 幂等查既有） |
| `app/modules/piecework/write_router.py` | `POST /piecework-logs/{log_id}/reverse`（`piecework:reverse`）、`POST /piecework-logs`（`piecework:manual`） |
| `tests/modules/test_piecework_reverse.py` | 红冲 R01~R06（6 条） |
| `tests/modules/test_piecework_reverse_supplement.py` | 补录 / 手工补录 / TC-45 R07~R10（7 条） |
| `tests/modules/test_piecework_count_concurrency.py` | 新增 **TC-50**：红冲 vs 他人扫同码（真并发） |

### 实现期才暴露、设计稿没写死的三件事

1. **`reverse.py` 超 400 行硬线（ADR-0030）→ 拆三件。** 红冲 + 补录 + 手工补录合写一份是
   466 行，超线 ⇒ 拆 `write_base.py`（共享锁）+ `reverse.py` + `manual.py` 两个 mixin，
   `PieceworkService(ManualMixin, ReverseMixin)`。测试同理由拆两份（合计 482 行 → 212 + 270）。
2. **`build_insert_log_stmt` 原来漏了 `reversal_of_id` / `remark`** ⇒ `REVERSAL` 落库的
   `reversal_of_id` 恒为 `NULL`（幂等按它查、红冲链按它串，等于全断）。已补齐两列；
   ⚠️ **不传 `version`**（构造时是 `None`，交给库的 `server_default`）。
3. **`branding.rschemas.ReverseIn` 与 `piecework.ReverseIn` 同名** ⇒ FastAPI 会把**两份**
   组件都改名成带模块路径的 `app__modules__…__ReverseIn`，把**已发布**的
   `bundling.ReverseIn` 一起改名，是破坏性契约变更（前端 `types/index.ts` 的别名直接编译失败）。
   本卡的类显式更名 `PieceworkReverseIn`，`bundling.ReverseIn` 保持不动。

### 判据与验收

- 红冲：`qty=-L1.qty` / `amount=-L1.amount` / `reversal_of_id=L1.id`；**同事务**清空
  `counted_*`；自审 `10005`；已结算 `32003`；越权与不存在**都回 `12002`**；已红冲流水再红冲拒绝；
  同 `reversal_of_id` 幂等返回既有。
- 补录／手工补录：`remark` 空 → `10002`；`SUPPLEMENT` 缺 `reversal_of_id` → `10002`；
  已计码 → `32001`；`qty > bundle_qty` → `32006`（TC-46）；append-only（无 `UPDATE qty/amount`）。
- 并发：**TC-50 自证** —— 删掉红冲里的 `FOR UPDATE` 后，红冲与三个并发扫码会各写各的，
  终值不唯一；恢复后恒为 `0`（红冲后未计）或 `60`（B 重扫整手）。

### 两处偏离验收标准 / 待业务确认

| 项 | 处理 | 说明 |
| --- | --- | --- |
| 验收第 4 条写「`super_admin` 需 `force=true` 并写日志」 | **不实现 `force` 例外** | 沿用打菲 `approve_assert.py`：`POST /approvals` 入参里没有 `force`（03 §6），凭空发明一个入参等于绕过「制单人 ≠ 审核人」铁律。红冲人 ≠ 原流水创建人，一律 `10005`。 |
| **MANUAL 算给谁** | **按「算给操作员」实现，留 TODO** | §5.4 标题是「计件员对漏扫码补录」，而 §6.1 请求体**没有 `employee_id`**，漏扫码的码又是未计件（`counted_by` 空）—— 规范**没写**这条流水算给哪个员工。当前算给**操作员**，`ManualMixin._credit_for` 留 `TODO(业务待确认)`。**未自行发明业务规则**，见下方待决问题。 |

### 闸门与测试

- 全量后端 **1082 passed / 91.00%**（本卡新增 13 + TC-50 = 14 条）；`ruff` / `mypy app` 全绿。
- 闸门 4 `alembic check`：无新 upgrade（本卡不动表结构）；闸门 5 后端 + 前端镜像均构建成功。
- 前端契约已重生成（`openapi.json` + `schema.d.ts`），重跑无 diff；`typecheck` / `lint` /
  `test:unit`（243）/ `build` 全绿。

### 待决问题（已登记 `docs/12`）

- **Q-PW16（新增）**：`MANUAL` 补录算给谁？算给操作员，还是请求体应带 `employee_id`？
  规范缺口，需业务方拍板。