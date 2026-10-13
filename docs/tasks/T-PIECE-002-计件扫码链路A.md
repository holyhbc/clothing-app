# T-PIECE-002：计件链路 A —— 扫码计件（`POST /piecework/count`）

| 项 | 内容 |
| --- | --- |
| 模块 | piecework |
| 负责人 | backend-dev |
| 状态 | `done`（2026-10-13） |
| 优先级 | P1（P2 计件**第 2 张卡**，打菲闭环后的第一段代码） |
| 依赖 | T-PIECE-001（`piecework_logs` / `piecework_bindings` 已建，迁移 0019）、T-BUND-001（`bundles`） |
| 被依赖 | T-PIECE-003（手工补录 / 红冲）、T-PIECE-004（员工端 H5）、工资结算卡 |
| 关联设计 | [`modules/04-计件.md`](../modules/04-计件.md) §5.1（链路 A 事务 ①~⑪）、§6.1、§7.1（C1/C3/C4）、§9（错误码）、§10（权限） |
| 关联规范 | `docs/03 §1.1`、`docs/04 §7.1`、`docs/05 §3/§5/§6`、`docs/07 §2.2`、`docs/10 §5.1` |
| 关联 ADR | ADR-0016（一码一手 + 扫码得件数）、ADR-0009（单价按工序）、ADR-0026（取价只读） |
| 估算 | 0.5d |

## 目标

实现工位机 + 扫码枪的**唯一写入路径**：`POST /api/v1/piecework/count`。
把 [04-计件 §5.1](../modules/04-计件.md) 的 ①~⑪ 十二步**按原文顺序**落成代码。

## 为什么单独拆一张卡（只做链路 A）

链路 A 是**唯一被 ADR-0016 完全定死**的路径：锁码、默认整手、部分生产、超产拒绝、
一码一员工，五条规则的取值全部有权威出处。链路 B（员工端绑定）与手工补录 / 红冲
分别牵扯 **Q-PW03（返工件，红冲补录 vs 新建码 —— 仍是业务二选一）**，混进来会让
这张卡在真到了那一步时无法验收 —— 「这一半对不对」取决于另一半（ADR-0030）。

**本卡不含**：员工端 `/self/**`（T-PIECE-004）、`SUPPLEMENT` / `REVERSAL`
（依赖 Q-PW03）、列表 / 汇总 / 导出 / 取价查询、SSE 事件。

## 验收标准

### 规则（04-计件 §5.1 ①~⑪）

- [ ] ① `SELECT … FROM bundles WHERE bundle_no = :bn FOR UPDATE` —— **必须真加锁**
- [ ] ① 不存在 → `31001`；`status=VOIDED` → `31002`；`counted_at` 非空 → `32001`
- [ ] ② 款号/工序不匹配 → `20001`
- [ ] ③ 当日未绑定该工序 → `32002`
- [ ] ④ 按 `work_date` 取价命中不到 → `20004`（**不回退 0、不回退旧价**）
- [ ] ⑤ 默认 `qty = bundles.bundle_qty`；`> bundle_qty` → `32006`；非整数 → `32005`；`<= 0` → `10001`
- [ ] ⑥ `amount = qty × unit_price`（Decimal）
- [ ] ⑦ 同员工同码同日同工序已有流水 → `code=0` + `idempotent=true`，**不重复计数**
- [ ] ⑧ `INSERT piecework_logs(log_type=SCAN)`
- [ ] ⑨ 同事务 `UPDATE bundles SET counted_qty/counted_by/counted_at`
- [ ] ⑩ `UniqueViolation` → 回查 → 同员工幂等 / 他人 `32001`
- [ ] ⑪ 写 `document_logs`

### 并发（04-计件 §7.1）

- [ ] **C1** 同码同员工 20 并发 → 只落 1 条，`log_id` 全同，`counted_qty` 只写一次
- [ ] **C3** 同码不同员工并发 → 先到者成功，后者 `32001` + `counted_by_name`/`counted_at`
- [ ] **C4** 离线重传 → 逐条幂等，`work_date` 以 `scanned_at` 为准
- [ ] 部分生产 A(28) vs 他人 B 同扫 → 只有一个成功；**不允许两个部分生产叠加成 60**

### 接口（04-计件 §6.1）

- [ ] 权限 `piecework:count`，数据范围 `WORKSHOP`
- [ ] 支持 `Idempotency-Key`：同键同 body → 200 + 首次结果；同键不同 body → `10002`
- [ ] `extra="forbid"`；`employee_id` **必传**（工位机代员工计件）
- [ ] 响应含 `hands` / `hands_total_of_size` / `qty` / `bundle_qty` / `today_hands`

## 不做什么（明确划界）

| 项 | 原因 |
| --- | --- |
| 员工端 `/self/**` | 07 §2.2 阶段一不开放计件写入（Q-PW09） |
| `SUPPLEMENT` / `REVERSAL` | 依赖 **Q-PW03 返工件的业务二选一**，未定 |
| 红冲清空 `bundles.counted_*` | 同上 |
| 自动结转剩余手提醒 | Q-PW12：阶段一不做 |
| SSE `piecework_counted` | §5.1 ⑪ 的事件广播，另开卡（需要 Redis Stream + 订阅端） |

## 风险

| 风险 | 缓解 |
| --- | --- |
| 只靠业务层检查「是否已计件」→ C3 并发双写 | ① `FOR UPDATE` 锁 `bundles` 行是**硬要求**；② 真并发测试（每任务独立 session），否则按 `10 §5.4` 判**未通过** |
| `qty` 回落成 `DEFAULT 1` | 服务层**必须显式赋值**；TC 断言「不传 `qty` 时落 `bundle_qty` 而非 1」 |
| 取错列（读 `style_operations.bundle_qty`） | 权威是 `bundles.bundle_qty`（Q-PW13 / ADR-0016）；代码里**禁止**出现 `StyleOperation.bundle_qty` |
| 单文件超 400 行 | schemas / repository / service / router 分文件；service 的并发处理拆 `lock.py` |

## 实施结果（2026-10-13 收尾）

### 落地时发现的**三个实现缺陷**（设计没写死，是写代码时才暴露的）

1. **同员工重试被自己的历史拦下来**（BR-PW-16 ① 与 ① 的顺序冲突）
   ① 判「已计件」在 ⑦ 判幂等**之前**跑。① 只判「``counted_at`` 非空就抛 32001」的话，
   同员工扫码枪连按两下，第二下会报「陈美玲 09:44 已计 60 件」—— 而那个人**就是本人**。
   工位机上没人会再扫一次，卡死。修法：① 先看 ``counted_by`` 是不是自己，是就放行给 ⑦。

2. **``bundles.counted_by`` 写成了操作员**
   初版按「谁点的按钮」写 ``counted_by = operator_id``（组长）。ADR-0016 决策 3 的
   「一码一员工」说的是**干活的那个人**。写成 operator 的话，32001 的提示会变成
   「你 09:44 已计 60 件」—— 操作员正是他本人，于是**又**变成自己扫自己被判冲突。
   修法：``counted_by = employee_id``，``updated_by = operator_id``（两个 id 各司其职）。

3. **响应里同一类数字有两位小数与三位小数两种写法**
   ``qty`` 是 Python 侧算的（``"60"``），``today_qty`` 是从库 ``SUM`` 出来的（``"60.000"``，
   ``numeric(14,3)`` 的列标度）。同一个响应里两件件数写法不同，前端拿去求和会莫名其妙。
   修法：service 里按**列标度**统一 quantize（件数 3 位 / 单价 6 位 / 金额 4 位）。

### 两个「写不出来才发现」的技术约束

- **PostgreSQL 不允许 ``FOR UPDATE`` 与窗口函数共存**（`FOR UPDATE is not allowed with
  window functions`）。锁查询原本用 ``count(*) OVER (PARTITION BY …)`` 顺带算出
  ``hands_total_of_size`` 省一次查询 —— **加锁与窗口函数互斥**，只能拆成两条。
- **`FOR UPDATE` 不能作用于外连接的可空侧**。锁查询 LEFT JOIN 了
  ``colors`` / ``sizes`` / ``users``（取名字），必须写 ``FOR UPDATE OF bundles``。
  这同时也是语义要求：只锁码，不锁字典表。

### 一处**设计层面的矛盾**（未自行发明规则，留给架构）

`04-计件 §6.1` 规定本接口数据范围是 ``WORKSHOP``，§10 的真实用户是
``workshop_supervisor``（WORKSHOP）与 ``line_leader``（GROUP）。但
``app/core/scope.py`` 里 ``styles`` 的口径是「只看自己负责的款号」
（``user_column="merchandiser_id"``），于是**任何非 FACTORY 范围的人在取价时
一律 12002** —— 工位机上的组长不是款号负责人，扫任何一个码都取不到价，
**链路 A 对真实用户不可用**。

本卡按「取价的授权边界是**码**」处理：service 直接用共享的
``build_rate_resolve_stmt``（ADR-0026 §2）而不调 ``RateService.resolve``。
依据是 ``RateResolveOut`` 自己的 docstring：「**真正的取价只发生在计件流水落库那一刻**」——
取价预演接口带范围判定，写入路径不带。

⚠️ **这不等于「``styles`` 的范围口径错了」**：款号管理页「SELF = 只看我负责的款号」
大概率是有意为之。本卡只保证链路 A 能跑，**口径本身留给架构定论** → L-121。

### 并发用例的有效性自证

按 `docs/10 §5.4`，必须证明「删掉锁测试就会红」。实测把 ``with_for_update(of=Bundle)``
去掉后：C3 落 **2 条**流水、R1 落 **2 条**、R2 落 **2 条**（原本都是 1 条）。
恢复锁后 5 条全绿 —— 用例确实在验锁，不是摆设。

### 闸门

| 闸门 | 结果 |
| --- | --- |
| 1 lint | ✅ ruff check / format |
| 2 typecheck | ✅ mypy 179 文件 + `vue-tsc`（admin / mobile） |
| 3 单测 | ✅ **1051 passed**，覆盖率 **91.53%**（新增 29 条） |
| 4 迁移 | ✅ `alembic check` 无漂移；0019 downgrade → upgrade 往返通过 |
| 5 镜像 | ✅ `docker/backend/Dockerfile` 构建成功 |
| 前端契约 | ✅ `pnpm run generate:api` 后 `schema.d.ts` 重生成，typecheck 全绿 |
