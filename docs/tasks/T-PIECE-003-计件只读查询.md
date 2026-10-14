# T-PIECE-003：计件只读查询（列表 / 详情 / 导出 / 汇总 / 工位机今日）

| 项 | 内容 |
| --- | --- |
| 模块 | piecework |
| 负责人 | backend-dev |
| 状态 | `done`（2026-10-13） |
| 优先级 | P1（P2 计件**第 3 张卡**；主管与计件员看数的地方） |
| 依赖 | T-PIECE-002（链路 A 已有数据可查） |
| 被依赖 | 工资结算卡（结算单要挑未结算流水）、前端计件台账页 |
| 关联设计 | [`modules/04-计件.md`](../modules/04-计件.md) §6.1 后 5 行、§8（查询口径）、§10 |
| 关联规范 | `docs/05 §2/§9.1`、`docs/07 §3.2`（导出与列表共用 service）、`docs/10 §2.2` |
| 关联 ADR | ADR-0009（单价）、ADR-0016（手数与件数并列） |
| 估算 | 0.5d |

## 目标

把 04-计件 §6.1 的 5 个**读**端点落成代码。**不涉及任何业务决策** —— 筛选条件、
列清单、汇总口径在规范里已逐项写死，本卡只是搬运。

## 验收标准

- [ ] `GET /piecework-logs` 分页，`piecework:read`，**数据范围由 service 注入**（07 §3.2 铁律 1）
- [ ] 每行含 §6.1 列出的 20 个字段（含 `color_name` / `size_name` / `operation_name` / `employee_name` 的**联表取值**）
- [ ] `GET /piecework-logs/{log_id}` 详情 + `bundle` 子对象 + `reversal_of` / `reversed_by`；越权 → `12002`
- [ ] `GET /piecework-logs/export` CSV（UTF-8 **BOM**），**与列表共用同一个 service 筛选路径**
- [ ] `GET /piecework/summary` 按 `employee|style|operation` 汇总，**`total_hands` 与 `total_qty` 并列**（BR-PW-31）
- [ ] `GET /piecework/stations/{station_no}/today` 工位机大字卡数据
- [ ] 排序走**白名单**（`sort_by` 不可直接拼 SQL）
- [ ] `piecework-logs` 的 `bundle_no` **完整不截断**（BR-PW-30）

## 实现前先确认的两处规范口径

| # | 事项 | 结论 |
| --- | --- | --- |
| 1 | 导出用哪个权限点 | `04-计件 §6.1` 写 `piecework:read`，但 `piecework:export` **已在 `permissions_registry` 注册并授予 `piecework_settler`**（07 §2.2）→ 用 **`piecework:export` + `system:export:manage`**（导出总开关，07 §2.2），并同步修正 §6.1 那一格。理由与 Q-PW13 同型：**权限点早就登记了，§6.1 那一格没跟上** |
| 2 | `is_settled` 怎么算 | **不是布尔列**：判据是 `payroll_id IS NULL`（04 §7.1 要点）。筛选按 `payroll_id` 的 IS NULL / IS NOT NULL，响应里的 `is_settled` 也是算出来的 —— 两个来源必然漂移（04 §7.1 原文） |

## 不做什么

| 项 | 原因 |
| --- | --- |
| 红冲 / 手工补录 / 员工端 | T-PIECE-002b / 依赖 Q-PW03 / Q-PW09 |
| SSE `piecework_counted` | 需要 Redis Stream + 订阅端，独立卡 |
| 工资结算单 | 工资阶段 |

## 风险

| 风险 | 缓解 |
| --- | --- |
| 另写一条导出查询 → 列表与导出对不上 | 导出与列表**共用同一个 service 方法**（07 §3.2 铁律 3）；测试断言「同一筛选下两者行数一致」 |
| `sort_by` 直接拼 SQL → 注入 | 走白名单字典（抄 `STYLE_SORT_WHITELIST`），非法值 → `10001` |
| 汇总只给件数、丢了手数 | BR-PW-31 要求 `total_hands` 与 `total_qty` **并列**；车间的主计数是手 |
| 单文件超 400 行 | SQL 走 `query.py`、编排走 `query_service.py`、端点走 `query_router.py`，都不塞进 `service.py` / `router.py` |

## 实施结果（2026-10-13 收尾）

### 落地时发现的两个**真缺陷**（只有真连库才会暴露）

**① `sizes` 的唯一键是 `(size_code, size_class)`，按 `size_code` 单列 JOIN 会把行乘出来**

初版列表 / 导出 / 详情都 `LEFT JOIN sizes ON sizes.size_code = bundles.size_code`。
测试里灌 3 条流水，列表回 `total: 6`、同一条 `log_id` 出现两次。根因不是分页坏了、
也不是重复写入，而是 **`XL` 在上衣类与裤装类各有一行**（`uq_sizes_code_class` 允许），
单列 JOIN 让每条流水翻成两行。`colors` 同理。

修法：**不 JOIN 字典表**，改走 `fetch_size_color_names` 的批量 `IN` 查询（与车间名同一
套路），「按码取一个名字」，绝不参与行数乘法。详情端点原本会直接抛
`MultipleResultsFound`（`one_or_none` 拿到两行）—— 那是这条缺陷最直白的症状。

⚠️ **这类缺陷在 code review 里基本看不出来**：JOIN 写法完全合法、单测如果只铺一个
尺码类的数据也永远绿。只有「同一个码出现在两个尺码类」时才现形。

**② `page_ok` 的响应体是 `PageData`，不是 `list`**

列表端点初版写成 `ApiResponse[list[LogRowOut]]`，而 `page_ok` 返回的是
`{items,total,page,page_size}` → FastAPI 校验失败（`Input should be a valid list`）。
必须与 `base` 的款号列表一样写 `ApiResponse[PageData[LogRowOut]]`。

### 一处**路由顺序**陷阱

`/piecework-logs/export` 与 `/piecework-logs/{log_id}` 同级。FastAPI 按**声明顺序**匹配，
而 `log_id` 声明成 `UUID` —— 若 `export` 排在 `{log_id}` 之后，导出端点**恒回 422**
（"export" 不是合法 UUID）。而打开 OpenAPI 看路径列表完全正常（那里是字典序，
与注册顺序无关），排查会一路走到「是不是 log_id 类型写错了」。已在文件头写明。

### 一处**权限口径修正**（未自行发明规则）

`04-计件 §6.1` 原写导出用 `piecework:read`，但 `piecework:export` **早已在
`permissions_registry` 注册并授予 `piecework_settler`**（07 §2.2）。与 Q-PW13 同型：
**权限点早就登记了，§6.1 那一格没跟上**。本次改用
`piecework:export + system:export:manage`（后者是导出总开关，防拖库），并同步 §6.1。

### 测试写法上的一处**纠正**

`auth_headers(role="custom", ...)` 的 `custom` 角色在同一个 `db_session` 里是**复用**的，
而 `grant_permissions` **只加不减**。于是「先建一个只有 `piecework:read` 的用户、
断言导出 403」这条用例，会因为前面某条用例已经给 `custom` 加了 `piecework:export`，
而**测出 200** —— 那种绿是假的。本卡为权限敏感的用例单建**唯一 code 的角色**
（`_hdr` 辅助函数），权限集就是断言要的那一组。

### 闸门

| 闸门 | 结果 |
| --- | --- |
| 1 lint | ✅ ruff check / format |
| 2 typecheck | ✅ mypy 184 文件 + `vue-tsc` |
| 3 单测 | ✅ **1068 passed**，覆盖率 **91.37%**（新增 17 条） |
| 4 迁移 | ✅ `alembic check` 无漂移（本卡无新迁移） |
| 5 镜像 | ✅ docker 构建（与 T-PIECE-002 同一镜像，本卡未动依赖） |
| 前端契约 | ✅ `generate:api` 重生成 `schema.d.ts`，typecheck 全绿 |
