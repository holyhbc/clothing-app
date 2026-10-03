# ADR-0002：数据库选型：PostgreSQL 16 而非 MySQL

| 项 | 内容 |
| --- | --- |
| 状态 | `Accepted` |
| 日期 | 2026-10-01 |
| 决策人 | 规范维护者 |
| 影响范围 | 迁移 / 接口 / 部署 |
| 关联 | [04-数据库规范.md](../04-数据库规范.md)、[01-技术选型与架构.md](../01-技术选型与架构.md) |

---

## 背景

`piecework_logs` 是本系统唯一的"钱的事实来源"，同时也是唯一的超高频写入表：50 QPS、必须同步返回、不得丢失、不得重复。工资汇总、库存台账、凭证借贷平衡三类查询都要求一条 SQL 出结果。

- 现状：项目无数据库实例，ADR-0001 已定 Python 栈，数据库待定。
- 痛点/风险：
  1. 计件流水并发写入需要行级锁 + 唯一索引兜底幂等，若数据库不提供，应用层要自己串行化，50 QPS 下会成为瓶颈。
  2. 工资要按「款号 × 工序 × 员工 × 期间」分组汇总，量级百万行级，不能在 Python 里拉数据再聚合。
  3. 金额 `numeric(18,4)` 存库、Python 侧 `Decimal`；若数据库用浮点，尾差无法收敛到分。
  4. 软删（INV-7）意味着索引里长期躺着已删行，索引会膨胀到影响写入吞吐。
  5. 单据扩展字段（打菲的裁片配置、销售的价格条款）变动频繁，改表成本高。
  6. 慢查询治理需要观测手段，否则性能问题只能靠猜。
- 硬约束：2C/12G 单机，`shared_buffers=1GB`；无 DBA，只能靠内置工具 + 简单 SQL 治理。

---

## 决策

**我们决定：存储主库用 PostgreSQL 16，不使用 MySQL/MariaDB。**

具体能力约定（每条对应一个本项目场景）：

```sql
-- ① 计件并发写：行级锁串行化同 bundle_no，ON CONFLICT 兜底幂等
SELECT id, employee_id FROM piecework_bindings
 WHERE user_id = :uid AND style_no = :style AND operation_no = :op
   AND work_date = :d FOR UPDATE;
INSERT INTO piecework_logs (...) VALUES (...)
ON CONFLICT (bundle_no, operation_no, employee_id, work_date, log_type)
DO NOTHING RETURNING id;          -- 未 RETURN 到行 = 重复提交，幂等返回既有 id

-- ② 工资汇总：窗口函数一条 SQL 出「按员工×工序×款号」的当期汇总 + 累计
SELECT employee_id, operation_no, style_no,
       SUM(amount) AS period_amount,
       SUM(SUM(amount)) OVER (PARTITION BY employee_id ORDER BY work_date) AS running_total
  FROM piecework_logs
 WHERE work_date BETWEEN :begin AND :end AND deleted_at IS NULL
   AND log_type <> 'REVERSAL'
 GROUP BY employee_id, operation_no, style_no;

-- ③④ 金额与时间：禁止 float / 无时区类型
amount numeric(18,4) NOT NULL,   -- Python 侧 Decimal
created_at timestamptz NOT NULL DEFAULT now()

-- ⑤ 单据扩展字段：语义固定、允许 NULL，不常查
extra jsonb NOT NULL DEFAULT '{}',   -- 仅在必要时 GIN 索引，禁止当主存储

-- ⑥ 软删 + 部分索引：索引只含未删行，保持紧凑（04 §5）
CREATE INDEX idx_piecework_logs_work_date_workshop_id_style_no
  ON piecework_logs (work_date DESC, workshop_id, style_no)
 WHERE deleted_at IS NULL;

-- ⑦ 慢查询治理：必须开启并纳入发布前巡检
shared_preload_libraries = pg_stat_statements   -- top total_time
```

```sql
-- 凭证借贷平衡：视图非空 = 0 行才算通过（INV-5）
CREATE VIEW v_voucher_balance AS
SELECT voucher_id FROM voucher_entries GROUP BY voucher_id
HAVING SUM(debit) <> SUM(credit);
```

---

## 理由

| # | 理由 |
| --- | --- |
| 1 | `SELECT ... FOR UPDATE` + `ON CONFLICT DO NOTHING` 让幂等落在数据库，唯一约束 `uq_piecework_logs_idem` 成为唯一真源，符合 `00` §6「禁止先查后插」。 |
| 2 | 窗口函数让 300 万行计件流水的工资汇总一条 SQL 完成，MySQL 8 有窗口函数但缺递归 CTE/部分索引的等价能力，且优化器对 `SUM() OVER (PARTITION BY ...)` 的执行计划更不稳定。 |
| 3 | `numeric(18,4)` 端到端精确，凭证借贷相等可由 `numeric` 直接判定，不用容差。 |
| 4 | `timestamptz` 消除跨时区歧义（WAL 归档、备份恢复、跨地出差签核场景）。 |
| 5 | `jsonb` 存打菲裁片配置 / 销售价格条款，字段语义固定，避免为一次性属性建列。 |
| 6 | 部分索引 `WHERE deleted_at IS NULL` 在软删成为默认行为的项目里是必需品，MySQL 无等价语法。 |
| 7 | `pg_stat_statements` 无需第三方 agent，`shared_preload_libraries` 一行开启，2C12G 上零额外进程。 |

---

## 备选方案与否决原因

| 方案 | 是否考虑 | 否决原因 |
| --- | --- | --- |
| MySQL 8 | ✅ | 无部分索引 → 软删行永久占索引，`piecework_logs` 一年后写入吞吐明显下降；`FOR UPDATE NOWAIT/SKIP LOCKED` 支持弱，计件锁粒度只能靠应用层串行化；JSON 不能单列索引，扩展字段要落 TEXT 再 LIKE 查询；`pg_stat_statements` 等价物需装 `performance_schema` 额外配置，且无 query id 归一化。 |
| MariaDB 10.6 | ✅ | 与 MySQL 同源，缺 `jsonb`、缺部分索引、窗口函数优化弱，且无 pg 系生态工具。 |
| SQLite | ✅ | 单机嵌入式，恰好贴合 2C12G，但无行级写锁（整库写锁），50 QPS 计件写入会串行阻塞；无 `numeric(18,4)`、无 `timestamptz`、无 `ON CONFLICT` 的并发兜底语义。 |
| MongoDB | ✅ | 工资汇总与凭证借贷平衡需要跨文档事务与强 schema；本项目 60%+ 表是强关系单据结构，关系库更省事。 |

---

## 影响

| 项 | 说明 |
| --- | --- |
| 正面 | 并发计件、精确金额、窗口函数汇总、部分索引、慢查询观测五项需求一次满足；与 `04` 规范零冲突。 |
| 负面/代价 | 团队需熟悉 PG 特有能力（部分索引、JSONB、CTE）；备份/恢复必须用 `pg_dump` + WAL 归档而非 mysqldump。 |
| 风险 | 有人顺手用 MySQL 特有或 PG 特有特性导致未来迁移困难（`01` §5 红线）——用 `revoke` 权限与 code review 兜。 |
| 迁移路径 | 阶段一：Docker 起 PG16，迁移脚本幂等插科目/权限/模板。阶段二：接 WAL 归档到对象存储。无需停机窗口（当前无生产数据）。 |
| 回滚方式 | 换库需新写 ADR + 全量重导；`04` §6.1 硬删禁令下不做数据回退。 |
| 对规范的影响 | `04` 全文（字段类型、索引、`pg_stat_statements`）、`11`（备份恢复）。新增错误码：无。新增权限点：无。 |
| 对测试的影响 | 必须有并发计件测试（50 QPS 唯一约束不重复）、`v_voucher_balance` 0 行对账测试、部分索引命中测试（`EXPLAIN` 走 Index Scan 而非 Seq Scan）。 |

---

## 验证方式

| 指标 | 目标值 | 观测方式 |
| --- | --- | --- |
| 计件写入并发 | 50 QPS，重复率 0，丢失 0 | 压测脚本 + `uq_piecework_logs_idem` 冲突计数 |
| 计件落库 P95 | < 150ms | 压测延迟分布 |
| 工资汇总（300 万行） | < 3s | `pg_stat_statements` 中该 query 的 `mean_time` |
| 部分索引体积 | 与全量索引相比 < 40% | `pg_relation_size` 对比 |
| 慢查询（`pg_stat_statements`） | 单条 `mean_time` < 500ms 的语句数 = 0 | 发布前巡检 SQL |
| `v_voucher_balance` | 0 行 | 每次发布必跑 |

---

## 关联

- 规范：[04-数据库规范.md](../04-数据库规范.md) §4/§5/§7、[01-技术选型与架构.md](../01-技术选型与架构.md) §5.3
- 模块：[modules/04-计件.md](../modules/04-计件.md)、[modules/05-工资.md](../modules/05-工资.md)、[modules/09-财务与凭证.md](../modules/09-财务与凭证.md)
- 关联 ADR：ADR-0001（技术栈）、ADR-0005（计件 append-only）
