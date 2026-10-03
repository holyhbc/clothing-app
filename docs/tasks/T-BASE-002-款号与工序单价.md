# T-BASE-002：款号、色码尺码、尺码比例、款号工序与工序单价

| 项 | 内容 |
| --- | --- |
| 模块 | base |
| 负责人 | AI |
| 状态 | `todo` |
| 优先级 | P0 |
| 依赖 | T-BASE-001 |
| 被依赖 | T-WEB-006, P1 全部业务模块 |
| 关联设计 | [docs/modules/00-P0地基.设计.md](../modules/00-P0地基.设计.md) §2.1 组 D、§4.5、§5（CC-1/CC-4/CC-5/CC-6）、§9（TC-B14~B17） |
| 关联 ADR | **ADR-0026**（单价三档 + `style_no` 可空）、ADR-0009（按工序定价 + 模板复制）、ADR-0013/0014（比例仅建议）、ADR-0020（分类 + 手数） |
| 估算 | 1d |

## 目标

交付 P0 出口标准之三：**能建款号**。款号 → 款号工序 → 工序单价全链路可走通，`resolve` 能按 `work_date` 命中三档取价并回 `rate_source`。

## 范围

**要做**：
- [ ] `alembic/versions/0004_base_style.py`：组 D 七张表
  - `customers`
  - `styles`：`style_no`（**用户自定义**）、`customer_id`（**可空**，Q-P0-10 决策：归属客户，仅用于建议号分组与筛选）、`customer_style_no`（客户货号备注）、`name`、`bulk_qty`、`category_id NOT NULL → product_categories`、`merchandiser_id → users`（Q-P0-05，跟单按货号隔离）、`last_used_at`（05 §9.5.2 常用优先）；`uq_styles_no` **部分唯一索引 `WHERE deleted_at IS NULL`**
  - `style_colors` / `style_sizes`
  - `style_color_size_ratios`（04 §7.7.1 DDL + `idx_style_color_size_ratios_lookup`）
  - `style_operations`（04 §7.8.2 + `is_final_operation`，04 §7.11）
  - `operation_rates`：**按 ADR-0026 重写** —— `style_no` 可空 FK、`product_category_id` 可空 FK、`ck_operation_rates_target`、`uq_operation_rates` 四列 **`NULLS NOT DISTINCT`**、`idx_operation_rates_lookup (operation_no, style_no, product_category_id, effective_from DESC)`、`idx_operation_rates_operation_current … WHERE effective_to IS NULL`
  - `style_no_sequences(customer_id, year, next_no)` 计数器表（建议号分组取号，`SELECT ... FOR UPDATE`）
  - 索引：`idx_styles_trgm`（GIN）、`idx_styles_customer_id`、`idx_styles_last_used_at`、`idx_style_sizes_size_code`、`idx_style_colors_color_code`
- [ ] `app/core/numbering.py`（改）：**建议货号生成器** —— `{客户前缀}-{年份}-{4 位}`，序号按 `(customer_id, year)` 分组递增；`customer_id` 为空 → 落全厂序列；**只作建议，用户输入优先**
- [ ] `app/modules/base/{models,schemas,service,router}.py`（改）：§4.5 全部端点
  - `POST /styles`：`style_no` **必填（用户自定义）** + `suggest_style_no=true` 返回建议号；`category_id` 必填（缺 → `10001`）；冲突 → `10001` + 回显已有名称
  - `POST /styles/{style_no}/sizes`：支持 `size_group_name` **一键带出整套尺码**（modules/01 §5.3）
  - `PUT /style-color-size-ratios`：按 `(style_no, color_code)` **全量替换** ≤100 行；`ratio > 0`；`size_code` 必须落在该款尺码集合内（否则 `20007`）；事务内该维度 `FOR UPDATE` + `version` 乐观锁；返回 `hands_total` + `missing_size_codes`
  - `PUT /styles/{style_no}/operations`：全量替换 ≤500 行；`operation_no` 存在且启用、`sequence>0`、`bundle_qty>0`；**`is_final_operation` 至多一道**（业务确认所有款最后一道都是整烫，识别不到必须人工指定）
  - `POST /styles/{style_no}/operations/copy-from/{source}`：`copy_mode` 与 `conflict_policy` **均必填**；单事务；**档位 2（分类价）不复制**（ADR-0026 §4）；响应含"尺码比例未复制"提示；支持 `Idempotency-Key`
  - `POST /operation-rates`：区间不重叠校验（`10001` + `details` 冲突区间）+ 旧行只改 `effective_to` + 插入新行（**禁 UPDATE `unit_price`**）；`reason` 必填（`10006`）；三元组 `FOR UPDATE`
  - `GET /operation-rates/resolve`：**ADR-0026 §2 单条 `ORDER BY` 取价 SQL**，返回 `rate_source ∈ {STYLE,CATEGORY,OPERATION}`；未命中 → `20004` + `details`
- [ ] 测试：`test_base_style_service.py`、`test_operation_rates.py`

**不做**：
- 不做物料与 BOM（`materials`/`bom_items` 属基础资料但裁剪才用，放 P1）
- 不做 Excel 导入（P1 起）
- 不写单据侧校验（裁剪/打菲/计件，P1/P2）

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `backend/alembic/versions/0004_base_style.py` | 新增 | 7 表 + 序列表 + 索引 |
| `backend/app/modules/base/models.py` | 修改 | 追加组 D 模型 |
| `backend/app/modules/base/schemas.py` | 修改 | 追加 Schema |
| `backend/app/modules/base/repository.py` | 修改 | trgm 候选 + 区间查询 |
| `backend/app/modules/base/service.py` | 修改 | 款号/比例/复制/取价 |
| `backend/app/modules/base/router.py` | 修改 | §4.5 端点 |
| `backend/app/core/numbering.py` | 修改 | 建议号生成器 |
| `backend/tests/modules/test_base_style_service.py` | 新增 | 款号/比例/复制 |
| `backend/tests/modules/test_operation_rates.py` | 新增 | 单价三档与调价 |
| `backend/tests/integration/test_operation_rates_lookup.py` | 新增 | 取价 SQL 走索引断言 |

## 实现要点（必读规范）

- [ ] 遵守 docs/04 §7.7.1（比例 DDL）、§7.8.2/§7.8.3（工序与单价，**单价以 ADR-0026 为准**）、§7.11（分类）、§5.1（trgm）、§4（金额 `numeric(12,6)`）
- [ ] 遵守 docs/modules/01 §2 R9~R13、R18~R23、§3.4（三表）、§5.1（模板复制三选项 + 四冲突策略）、§5.3（码表带出）、§8（索引场景）
- [ ] 遵守 docs/05 §4（`20002`/`20004`/`20005`/`20006`/`20007`/`10006`/`10003`）、§5（幂等）
- [ ] 遵守 docs/03 §1.4（Service 正例：事务边界、`flush` 而非 `commit`）
- [ ] 业务方 2026-10-03 决策（Q-B01/Q-P0-04/Q-P0-05/Q-P0-10）：货号自定义、序号按客户递增、款号=自己的货号、跟单按货号隔离

## 验收标准

- [ ] **闸门 4 通过**
- [ ] **S3 全链路**：建款号 `HB-2026-0001` → 配 2 个款号工序 → 设 2 条单价 → `resolve(work_date)` 返回命中的那一档价与 `rate_source`
- [ ] TC-B14：调价后**历史区间 `unit_price` 未变**（INV-3 / INV-P0-3）
- [ ] TC-B15：生效区间重叠 → `10001` + `details` 含冲突区间；旧行未被改动
- [ ] TC-B16：未来生效价不影响当日取价
- [ ] TC-B17：未设价 → `20004` + `details` 含 `style_no`/`operation_no`
- [ ] TC-B26/27（ADR-0026）：三档各命中正确 `rate_source`；款号有专用价时分类价**不生效**
- [ ] TC-B28：`NULLS NOT DISTINCT` 拦截「同款号同工序同日的重复通用价行」
- [ ] TC-B29：`style_no` 与 `product_category_id` 同时为空 → `10001`
- [ ] TC-B12/13：模板复制模式①/② 完整性（模式② `1.0800 × 0.350000 = 0.378000`，6 位精度）
- [ ] CC-1/CC-4/CC-5/CC-6 全绿；**至多一条 `effective_to IS NULL`**
- [ ] 建议号生成：同客户同年连续 3 次 → `0001`/`0002`/`0003`；20 并发不重复
- [ ] 取价 SQL `EXPLAIN` 命中 `idx_operation_rates_lookup`
- [ ] 跟单（`SELF`）只能查到自己 `merchandiser_id` 的款号，他人款号 → `12002`
- [ ] `app/modules/base/service.py` 覆盖 ≥ 90%；新增行 100%
- [ ] 闸门 1/2/3/5 全绿

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-B06 | 款号传小写 `hb-2026-0002` | 存库转大写，唯一性按大写判 | |
| TC-B07 | 款号重复 | `10001` + 回显已有名称 | |
| TC-B08 | 建款号不传 `category_id` | `10001` | |
| TC-B09 | 选女款模板一键带出尺码 | 生成 4 条 `style_sizes`，`sort_no` 顺序正确 | |
| TC-B10 | 比例全量替换 | 旧行全删新行全插；`hands_total` 正确；`missing_size_codes` 返回 | |
| TC-B11 | 比例含款号没有的尺码 | `20007` | |
| TC-B14~B17 | 调价/重叠/未来价/未设价 | 见验收 | |
| TC-B26~B29 | 三档取价与约束 | 见验收 | |
| TC-B31 | 比例替换并发（两人同时改同款同色） | 一成一败，败者 `10003`，无「删掉别人刚加的尺码」 | |
| TC-B32 | `operation_no` 重复调价并发 20 次 | 至多 1 成功，其余 `20002`/`10003` | |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| | +0 / -0 | |

**提交记录**：
- `<hash>` feat(base): 款号、尺码比例、款号工序与工序单价 …

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| W6 | ADR-0026 落地需回写 04 §7.8.3/§7.11 与 modules/01 §3.4/§5.1/§6/§8/§9 | 归档阶段 |
| W4 | `styles` 新增列需回写 modules/01 §3.2 + 09 §1.1 + 04 §7.11 | 归档阶段 |
| Q-B13 | 模板复制是否复制尺码比例 —— 本模块结论「不复制」，待业务方确认是否接受 | docs/12 §遗留问题清单 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。
