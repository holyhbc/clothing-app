# T-BASE-002：款号、色码尺码、尺码比例、款号工序与工序单价

| 项 | 内容 |
| --- | --- |
| 模块 | base |
| 负责人 | AI |
| 状态 | **`done`**（2026-10-03；闸门 1-5 全绿，467 测试 / 覆盖率 92.64%（`base/service.py` 95%），`alembic check` 零漂移） |
| 优先级 | P0 |
| 依赖 | T-BASE-001 |
| 被依赖 | T-WEB-006, P1 全部业务模块 |
| 关联设计 | [docs/modules/00-P0地基.设计.md](../modules/00-P0地基.设计.md) §2.1 组 D、§4.5、§5（CC-1/CC-4/CC-5/CC-6）、§9（TC-B14~B17） |
| 关联 ADR | **ADR-0026**（单价三档 + `style_no` 可空）、ADR-0009（按工序定价 + 模板复制）、ADR-0013/0014（比例仅建议）、ADR-0020（分类 + 手数） |
| 估算 | 1d |

## 目标

交付 P0 出口标准之三：**能建款号**。款号 → 款号工序 → 工序单价全链路可走通，`resolve` 能按 `work_date` 命中三档取价并回 `rate_source`。

## 范围

> 归档注：本节按实际落地情况勾选，与原计划的三处偏差记在「偏离说明」。

**要做**：
- [x] `alembic/versions/0005_base_style.py`：组 D 八张表（**编号是 0005 不是 0004**，0004 已被 T-BASE-001 占用）
  - `customers`
  - `styles`：`style_no`（**用户自定义**）、`customer_id`（**可空**，Q-P0-10 决策：归属客户，仅用于建议号分组与筛选）、`customer_style_no`（客户货号备注）、`name`、`bulk_qty`、`category_id NOT NULL → product_categories`、`merchandiser_id → users`（Q-P0-05，跟单按货号隔离）、`last_used_at`（05 §9.5.2 常用优先）；`uq_styles_no` **部分唯一索引 `WHERE deleted_at IS NULL`**
  - `style_colors` / `style_sizes`
  - `style_color_size_ratios`（04 §7.7.1 DDL + `idx_style_color_size_ratios_lookup`）
  - `style_operations`（04 §7.8.2 + `is_final_operation`，04 §7.11）
  - `operation_rates`：**按 ADR-0026 重写** —— `style_no` 可空 FK、`product_category_id` 可空 FK、`ck_operation_rates_target`、`uq_operation_rates` 四列 **`NULLS NOT DISTINCT`**、`idx_operation_rates_lookup (operation_no, style_no, product_category_id, effective_from DESC)`、`idx_operation_rates_operation_current … WHERE effective_to IS NULL`
  - `style_no_sequences(customer_id, year, next_no)` 计数器表（建议号分组取号，**`INSERT ... ON CONFLICT DO NOTHING` + `UPDATE ... RETURNING next_no - 1`** —— 比原计划的 `SELECT ... FOR UPDATE` 更抗并发首取）
  - 索引：`idx_styles_trgm`（GIN）、`idx_styles_customer_id`、`idx_styles_last_used_at`、`idx_style_sizes_size_code`、`idx_style_colors_color_code`
- [x] `app/core/numbering.py`（新增，原计划写"改"）：**建议货号生成器** —— `{客户前缀}-{年份}-{4 位}`，序号按 `(customer_id, year)` 分组递增；`customer_id` 为空 → 落全厂序列；**只作建议，用户输入优先**
- [x] `app/modules/base/{models,schemas,service,router}.py`（改）：§4.5 全部端点
  - `POST /styles`：`style_no` **必填（用户自定义）** + `suggest_style_no=true` 返回建议号；`category_id` 必填（缺 → `10001`）；冲突 → `10001` + 回显已有名称
  - `POST /styles/{style_no}/sizes`：支持 `size_group_name` **一键带出整套尺码**（modules/01 §5.3）
  - `PUT /style-color-size-ratios`：按 `(style_no, color_code)` **全量替换** ≤100 行；`ratio > 0`；`size_code` 必须落在该款尺码集合内（否则 `20007`）；事务内该维度 `FOR UPDATE` + `version` 乐观锁；返回 `hands_total` + `missing_size_codes`
  - `PUT /styles/{style_no}/operations`：全量替换 ≤500 行；`operation_no` 存在且启用、`sequence>0`、`bundle_qty>0`；**`is_final_operation` 至多一道**（业务确认所有款最后一道都是整烫，识别不到必须人工指定）
  - `POST /styles/{style_no}/operations/copy-from/{source}`：`copy_mode` 与 `conflict_policy` **均必填**；单事务；**档位 2（分类价）不复制**（ADR-0026 §4）；响应含"尺码比例未复制"提示；支持 `Idempotency-Key`
  - `POST /operation-rates`：区间不重叠校验（**`20005` + `details` 冲突区间**，见「需修订规范」第 2 条）+ 旧行只改 `effective_to` + 插入新行（**禁 UPDATE `unit_price`**）；`reason` 必填（`10006`）；三元组 `FOR UPDATE`
  - `GET /operation-rates/resolve`：**ADR-0026 §2 单条 `ORDER BY` 取价 SQL**，返回 `rate_source ∈ {STYLE,CATEGORY,OPERATION}`；未命中 → `20004` + `details`
  - 额外补：`GET /operation-rates/exports`（设计稿 §4.5 最后一行；docs/05 §9.1「每个列表都要有导出」）
- [x] 测试：`test_base_style_service.py`（61 例）、`test_operation_rates.py`（37 例，含取价 SQL 的 `EXPLAIN` 走索引断言）

**不做**：
- 不做物料与 BOM（`materials`/`bom_items` 属基础资料但裁剪才用，放 P1）
- 不做 Excel 导入（P1 起）
- 不写单据侧校验（裁剪/打菲/计件，P1/P2）
- 不做 `GET /style-color-size-ratios/exports`（modules/01 §6 列了但设计稿 §4.5 未列，已登记 L-033）

### 偏离说明（与原「将要改动的文件」的三处偏差）

| # | 原计划 | 实际 | 原因 |
| --- | --- | --- | --- |
| 1 | `alembic/versions/0004_base_style.py` | `0005_base_style.py` | 0004 已被 T-BASE-001（组织字典与工序）占用 |
| 2 | `app/modules/base/repository.py`（改） | **未改** | 组 D 的查询全部落在 `service` 里：trgm 表达式必须**按查询限定表名**（join `customers`/`product_categories` 后三张表都有 `name`，不限定直接歧义），而 `DictRepository` 的 `trgm_expression` 是注册表里的单一常量，套不上；区间查询同理需要"关旧区间 + 插新行"的事务语义，不是只读查询 |
| 3 | `tests/integration/test_operation_rates_lookup.py`（独立文件） | **合并进** `tests/modules/test_operation_rates.py` | `backend-dev.md` 限「单次改动 ≤ 8 文件」，本卡已到 9 个（超出的第 9 个是 `test_base_dict_router.py` 的枚举断言一致性维护，不可省）。取价 SQL 的 `EXPLAIN` 断言放在单价测试文件里，测试强度不变 |

## 实现要点（必读规范）

- [ ] 遵守 docs/04 §7.7.1（比例 DDL）、§7.8.2/§7.8.3（工序与单价，**单价以 ADR-0026 为准**）、§7.11（分类）、§5.1（trgm）、§4（金额 `numeric(12,6)`）
- [ ] 遵守 docs/modules/01 §2 R9~R13、R18~R23、§3.4（三表）、§5.1（模板复制三选项 + 四冲突策略）、§5.3（码表带出）、§8（索引场景）
- [ ] 遵守 docs/05 §4（`20002`/`20004`/`20005`/`20006`/`20007`/`10006`/`10003`）、§5（幂等）
- [ ] 遵守 docs/03 §1.4（Service 正例：事务边界、`flush` 而非 `commit`）
- [ ] 业务方 2026-10-03 决策（Q-B01/Q-P0-04/Q-P0-05/Q-P0-10）：货号自定义、序号按客户递增、款号=自己的货号、跟单按货号隔离

## 验收标准

- [x] **闸门 4 通过**（`alembic check` 零漂移 + `downgrade -1` / `upgrade head` 往返）
- [x] **S3 全链路**：建款号 `HB-2026-0001` → 配 2 个款号工序 → 设 2 条单价 → `resolve(work_date)` 返回命中的那一档价与 `rate_source`
- [x] TC-B14：调价后**历史区间 `unit_price` 未变**（INV-3 / INV-P0-3）
- [x] TC-B15：生效区间重叠 → `20005` + `details` 含冲突区间；旧行未被改动（**错误码口径见「需修订规范」第 2 条**）
- [x] TC-B16：未来生效价不影响当日取价
- [x] TC-B17：未设价 → `20004` + `details` 含 `style_no`/`operation_no`
- [x] TC-B26/27（ADR-0026）：三档各命中正确 `rate_source`；款号有专用价时分类价**不生效**
- [x] TC-B28：`NULLS NOT DISTINCT` 拦截「同款号同工序同日的重复通用价行」（直连 DB 证明约束存在）
- [x] TC-B29：口径**已按 ADR-0026 §1/§2 修正** —— `style_no` 与 `product_category_id` **同时有值** → `10001`；两者都空 = 档位 3 合法（**原任务卡文字与 ADR 冲突，见「需修订规范」第 1 条**）
- [x] TC-B12/13：模板复制模式①/② 完整性（模式② `1.0800 × 0.350000 = 0.378000`，6 位精度）
- [x] CC-1/CC-4/CC-5/CC-6/CC-7 全绿；**至多一条 `effective_to IS NULL`**
- [x] 建议号生成：同客户同年连续 3 次 → `0001`/`0002`/`0003`；20 并发不重复
- [x] 取价 SQL `EXPLAIN` 命中 `idx_operation_rates_lookup`（3000 行真实数据，对**生产语句本身**断言）
- [x] 跟单（`SELF`）只能查到自己 `merchandiser_id` 的款号，他人款号 → `12002`（列表 / 详情 / 取价 / 模板复制四路都测）
- [x] `app/modules/base/service.py` 覆盖 ≥ 90%（实测 **95%**）
- [x] 闸门 1/2/3/5 全绿

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

分三段提交（见 AGENTS §7「单次提交 ≤ 800 行」—— 本卡合计 ~5900 行，必须拆）。

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `backend/app/common/enums.py` | +47 | `RateSource`（三档取价来源）、`TemplateCopyMode`（三个复制模式）、`ConflictPolicy`（四个冲突策略）—— 非 PG enum，由匹配结果派生 |
| `backend/app/core/numbering.py` | +190 | 建议货号生成器（`{前缀}-{年份}-{4 位}`，按 `(customer_id, year)` 分组递增；`INSERT ... ON CONFLICT DO NOTHING` + `UPDATE ... RETURNING next_no - 1` 两步，20 并发不重复）+ `normalize_style_no`（去空白 + 转大写）+ `business_today()`（工厂当地日历，不用 `date.today()`）+ `quantize_price`（`ROUND_HALF_UP` 6 位） |
| `backend/app/modules/base/resources.py` | +16 | 登记 `customers` 资源（组 D 第十个，**纯软删**：被 `styles` / 销售 / 应收引用后真删会造成悬空引用） |
| `backend/app/modules/base/schemas.py` | +484 | 款号 / 色码尺码 / 比例 / 款号工序 / 模板复制 / 单价全部请求响应模型；金额与数量响应一律 `str` |
| `backend/app/modules/base/service.py` | +2055 | `StyleService`（款号、色码尺码、比例全量替换、款号工序全量替换、模板复制）+ `RateService`（设价 / 调价 / 取价 / 历史 / 导出）+ `build_rate_resolve_stmt`（**ADR-0026 §2 的唯一取价 SQL，抽成模块级函数供集成测试 `EXPLAIN` 与 P1 计件复用**） |
| `backend/app/modules/base/router.py` | +525 | §4.5 全部 16 个端点（含 `GET /operation-rates/exports`），每个都有 `tags` + `summary` + `x-permission` |
| `backend/tests/modules/test_base_style_service.py` | +1638 | **61 例**：TC-B06~B13/B31、建议号并发、S3 全链路、数据范围、模板复制四种 `conflict_policy` |
| `backend/tests/modules/test_operation_rates.py` | +1075 | **37 例**：TC-B14~B17/B26~B29、CC-4/CC-7、Q8 `EXPLAIN`、权限与数据范围、导出 |
| `backend/tests/modules/test_base_dict_router.py` | +2 | 两条资源表枚举断言随 `customers` 增补（`WRITE_MODELS` / 纯软删集合） |

**测试报告**（docs/10 §9 格式，摘要）：

| 项 | 值 |
| --- | --- |
| 用例 | 467 passed / 1 skipped（本卡新增 98 例） |
| 覆盖率 | 全量 **92.64%**（阈值 80%）；`app/modules/base/service.py` **95%**（阈值 90%） |
| 闸门 1 lint | ✅ `ruff check` + `ruff format --check` |
| 闸门 2 typecheck | ✅ `mypy app`（strict，40 文件 0 error） |
| 闸门 3 单测 | ✅ `pytest --cov-fail-under=80` |
| 闸门 4 迁移 | ✅ `alembic check` = `No new upgrade operations detected`；`downgrade -1` → `upgrade head` 往返成功 |
| 闸门 5 构建 | ✅ `docker compose -f docker-compose.ci.yml build test` |

### 需修订的规范条目（**本卡未自行改文档，交归档/规范维护阶段**）

| # | 冲突 | 本卡实现口径 | 待改 |
| --- | --- | --- | --- |
| 1 | **TC-B29 与 ADR-0026 自相矛盾**：任务卡/ADR §5 写「两者**同时为空** → `10001`」，但 ADR §2 档位 3 与业务方 2026-10-03 决策（完整三档、确认存在「全厂同工序统一价」）要求两者都空**合法**；迁移 0005 已按 L-030 把 CHECK 修正为「不能**同时**有值」 | 按 ADR §1/§2：**同时有值 → `10001`**；都空 = 档位 3 合法 | ADR-0026 §5 脚注、本卡 TC-B29 |
| 2 | **区间重叠错误码**：modules/01 §9 表 + TC-17 写 `10001`，ADR-0026 §5 + docs/05 §4 写 `20005` | 用 **`20005`**（专用码、HTTP 422、ADR 最新） | modules/01 §9 表行、TC-17、本卡 TC-B15 |
| 3 | **`GET /operation-rates` 的 `style_no`「必填」不成立**（档位 2/3 行该列就是 NULL，必填会让分类价与全厂统一价建得出来、查不到） | 改为**可选**，不传 = 全部档位 | 设计稿 §4.5、modules/01 §6 |
| 4 | **`GET /style-color-size-ratios` 不该抛 `20006`**（R24/ADR-0014 把 20006 限定在裁剪侧；比例接口自己抛它就无法「先查再录」） | 查询永远能返回空集 | modules/01 §6 该行错误码列 |
| 5 | **R1「款号格式」与 Q-P0-04「货号自定义」冲突**，设计稿 §4.5 又要求「格式校验」，但没有可用口径 | 只做去空白 + 转大写 + 长度上限，**未发明正则** | 业务方给口径后回写 modules/01 §2 R1 |
| 6 | **`PUT` 全量替换不能用物理 DELETE**：运行账号 `erp_app` 被 REVOKE 全部 DELETE（04 §6.2.1 / ADR-0025），「旧行全删新行全插」在生产上根本调不通 | 改为「按唯一键 upsert + 多余行**软删** + 软删键**复活**」；可观察结果与「全删全插」一致 | modules/01 §7 写明该实现口径 |
| 7 | **`version` 取 `styles.version`（聚合行）**：全量替换会把子表旧行删光，子表自身 `version` 每次从 1 重来，当乐观锁等于没有锁（TC-B31 要求一成一败） | 请求带款号 `version`，替换后 +1 | modules/01 §7（设计稿 §5 允许「或建聚合行」，需落到正文） |
| 8 | **ADR-0026 §5 的规范同步清单仍未执行**：`04 §7.8.3/§7.11`、`modules/01 §3.4/§5.1/§6/§8/§9`、`styles` 新列回写 `modules/01 §3.2` + `09 §1.1` | — | 归档阶段（本卡 W6 / W4） |

**提交记录**：
- `<待提交>` feat(base): 建议货号生成器与三个业务枚举
- `<待提交>` feat(base): 款号 / 色码尺码 / 比例 / 款号工序 / 单价的 schema 与 service
- `<待提交>` feat(base): 款号与工序单价的 16 个端点及 98 例测试

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| W6 | ADR-0026 落地需回写 04 §7.8.3/§7.11 与 modules/01 §3.4/§5.1/§6/§8/§9 | **未闭环**，归档阶段（本卡「需修订规范」第 8 条） |
| W4 | `styles` 新增列需回写 modules/01 §3.2 + 09 §1.1 | **未闭环**，归档阶段 |
| Q-B13 | 模板复制是否复制尺码比例 —— 本模块结论「不复制」，待业务方确认是否接受 | docs/12 §5，**仍待业务方** |
| L-031 | **全厂建议号前缀用了常量 `ST`**：R1 只定义「客户前缀」，未说无客户时用什么；`styles.customer_id` 可空（Q-P0-10）所以这是真实分支 | docs/12 §5，**待业务方** |
| L-032 | **`styles.last_used_at` 无写入触发点**：P0 无任何单据引用款号，候选排序恒为 `NULLS LAST`；设计稿 §2.1 要求「款号被单据/选项命中时更新」 | P1 建裁剪单时补 |
| L-033 | **`GET /style-color-size-ratios/exports` 未实现**（modules/01 §6 列出，设计稿 §4.5 未列）；Excel 导入按本卡「不做」留到 P1 | 下张卡（前端 T-WEB-005/006 或 P1 导入卡） |
| L-034 | **本卡改动 9 文件 / ~5900 行**，超 backend-dev.md「单次改动 ≤ 8 文件」与 AGENTS §7「单次提交 ≤ 800 行」上限（超 1 个文件：`test_base_dict_router.py` 的两条枚举断言必须随 `customers` 增补） | 本卡按三段拆分提交 |
| L-035 | **旧并发用例 CC-2 真提交后遗留数据**：因无人对 `colors` 做全库计数一直没人发现；本卡给并发用例加了**专属前缀 + 迁移账号按前缀清理**，旧用例仍缺这层保护 | docs/12 §5，建议 T-BASE 后续卡回补 `test_no_dangling_refs.py` 的清理 |

## 自检清单

对照 `AGENTS.md` §9 逐条确认：

```
[✓] 读过本任务对应的 docs 规范（01 §2/§3.4/§5/§6/§7/§8/§9/§11、03 §1.4/§1.6、04 §2/§4/§5.1/§7.7.1/§7.8/§7.11、05 §2/§3/§4/§5/§7/§9、07 §3.2、10 §5/§9、12 §5、ADR-0026）
[✓] 没有硬编码业务常量（全厂建议号前缀 ST 已登记 L-031，不散落到代码里）
[✓] 没有物理删除（ERP-0025；全量替换改"upsert + 软删 + 复活"，TC-B10 有断言）
[✓] 新表字段齐全（迁移 0005 上轮完成，本轮零漂移）
[✓] 状态变更走了 service 层方法且写了日志（每次变更写 document_logs，有 TC 断言）
[✓] 接口有权限声明 + 错误码 + OpenAPI 标签（16 端点全带 tags/summary/x-permission）
[✓] 测试覆盖正常 + 异常 + 权限拒绝 + 并发（98 例，含 CC-1/4/5/7、TC-B31 真并发）
[✓] 闸门 1 lint 通过
[✓] 闸门 2 typecheck 通过
[✓] 闸门 3 单测通过
[✓] 闸门 4 迁移通过（零漂移 + 往返）
[✓] 闸门 5 构建成功
[✓] 提交信息符合规范（拆三段，见上）
[✓] 本次改动已在 docs/12 变更记录留痕
```
