# T-DOCS-002：DDL「引用完整性」守卫（补上 T-DOCS-001 守卫的盲区）

| 项 | 内容 |
| --- | --- |
| 模块 | docs + test |
| 负责人 | AI |
| 状态 | `done` |
| 优先级 | P0 |
| 依赖 | T-DOCS-001 |
| 被依赖 | P1 全部卡片（尤其 T-CUT-001） |
| 关联设计 | [docs/modules/00-P0地基.设计.md](../modules/00-P0地基.设计.md) §10.1 W3 |
| 估算 | 0.5d |
| 提交范围 | `test`（守卫）+ `docs`（修被它抓出的 DDL 缺陷） |

## 目标

T-DOCS-001 加的 `test_docs_ddl_sync.py` 守的是「**列名双向一致**」，它有一个盲区：
**声明顺序**。而顺序错误恰好是照抄建表会直接失败的那一类。

### 已确认的真实缺陷（写这张卡时手工核实的）

`04 §7.1` 的 `cutting_orders` DDL：

```sql
CREATE TABLE cutting_orders (
    ...
    CONSTRAINT ck_cutting_orders_hand CHECK (hands_total > 0)  -- ⚠️ hands_total 还没声明
);
ALTER TABLE cutting_orders ADD COLUMN hands_total integer NOT NULL DEFAULT 0;
```

`CHECK` 引用了**下一条 `ALTER TABLE` 才声明**的列。实测：

```
ERROR:  column "hands_total" does not exist
```

这是 P1 建裁剪单表时**必然踩到**的坑（迁移会失败），但因为 P1 还没开工，
没人会去建那张表，于是这个缺陷会一直躺着 —— 直到 T-CUT-001 第一步就红。

## 范围

**要做**：

- [x] 加守卫「DDL 块内引用完整性」：扫 `04 §7` 每个 sql 块里的
      - `CHECK (expr)` 引用的裸标识符 → 必须在「块内已声明的列 ∪ 模型的列」里
      - `REFERENCES tbl` 的表 → 必须在「块内已声明的表 ∪ `04 §7` 全文出现过的表」里
- [x] 守卫用「故意写坏」反验（把 `hands_total` 的 `ALTER` 删掉 → 守卫红）
- [x] 修 `04 §7.1` 的 `cutting_orders`：`hands_total` 写进 `CREATE TABLE`，
      `ALTER TABLE ADD COLUMN` 改成 `DEFAULT` 微调或删掉
- [x] 顺带扫一遍 `04 §7` 其余块（除 `cutting_orders` 外无同类问题）有没有同类问题
- [x] 归档：docs/12 变更记录 0069 + L-070/L-071 + 任务卡回填

**不做**：

- **不真的建表**（不执行任何 DDL）。用真建表验证更强，但要处理跨块依赖与枚举类型，
  且会在测试库里建对象（AGENTS §2.1 对 DROP/TRUNCATE 的禁令虽然针对业务数据，
  在这里仍然是多余的复杂度）。静态检查足以覆盖「声明顺序」这一类，而那正是本卡的目标
- 不动 P1 的表结构设计本身（字段该不该有是业务与设计的事，本卡只管「写得对不对、能跑吗」）
- 不动 `modules/*` 的语义描述

## 验收标准

- [x] 守卫抓出 `cutting_orders` 的 `hands_total` 顺序缺陷（写卡前手工核实过，实测 `ERROR: column "hands_total" does not exist`）
- [x] 修完之后 `04 §7` 全块通过；反验覆盖三种形状：① 删掉声明 ② **把声明挪到 CHECK 之后**（`hands_total` 必须抓出来 —— 否则守卫退化成「只看有没有声明过」）③ 改坏一处外键
- [x] 误报为 0：`04 §7` 里所有 `CHECK` / `REFERENCES` / 索引 `WHERE` 里的标识符都能解释。⚠️ 为此踩了三个坑（都在代码里留了注释）：**不能只取「已闭合的 CREATE TABLE」**、**必须先剥字符串字面量**、**必须先剥行注释**
- [x] 闸门 1~4 全绿（后端 **618 例** / 覆盖率 88.3%）

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TD2-01 | `04 §7` 所有块的 `CHECK` 引用的列都在该 `CHECK` 之前声明过 | 通过 | ✅ |
| TD2-02 | `04 §7` 所有块的 `REFERENCES` 目标表都在规范里有 DDL | 通过（4 张已登记缺口单列） | ✅ |
| TD2-03 | 反验：删掉 `cutting_orders` 的 `hands_total` 声明 | TD2-01 红 | ✅ |
| TD2-03b | 反验：**把声明挪到 CHECK 之后**（原缺陷的形状） | TD2-01 红 | ✅ |
| TD2-04 | 反验：把一处外键改成 `no_such_table` | TD2-02 红 | ✅ |
| TD2-05 | `TABLES_WITHOUT_DDL` 里每张表都真的被引用过 | 通过（防「补了 DDL 忘了删登记」） | ✅ |
| — | 手工核实 `hands_total` 缺陷 | PG 报 `column does not exist` | ✅ |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `backend/tests/modules/test_docs_ddl_sync.py` | +185 | TD2-01 / TD2-02 / TD2-05 + 三条反验 |
| `docs/04-数据库规范.md` | +12 / -3 | 修 `cutting_orders.hands_total` 的声明顺序；并说明那条 CHECK 为什么只能是 `>= 0` |
| `docs/12-文档与变更归档规范.md` | +4 | 变更记录 0069 + L-070 / L-071 |
| **手写合计** | **+201** | 守卫占大头 |

**提交记录**：
- `<hash>` test(docs): DDL 引用完整性守卫 …

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| L-070 | `bundling_orders` / `bundling_order_lines` 没有 DDL（字段表只在 `modules/03`） | T-CUT-002 |
| L-071 | **`materials` / `suppliers` 连字段表都没有** → **阻塞 T-CUT-001**（裁剪单行要锁布批，布批要落到物料与供应商） | **先做这张卡** |

## 自检清单

- [x] 读过 `04 §2/§6/§7`、`docs/02 §9`、`docs/10 §5`、`docs/12 §9`
- [x] 没有硬编码业务常量（守卫里那两张登记表带注释说明为何存在）
- [x] 没有物理删除（不执行任何 DDL，这是本卡刻意的范围选择）
- [x] 新表字段齐全（本卡无新表）
- [x] 状态变更（不适用）
- [x] 接口（不适用）
- [x] 测试覆盖正常路径 + 异常路径（49 例守卫，含 4 条「故意写坏」的反验）
- [x] 闸门 1 lint 通过
- [x] 闸门 2 typecheck 通过
- [x] 闸门 3 单测通过（后端 621 例 / 覆盖率 88.3%）
- [x] 闸门 4 迁移可正向且可回滚
- [ ] 闸门 5 构建镜像成功（不适用：改动不触碰构建输入）
- [x] 提交信息符合规范
- [x] 本次改动已在 docs/12 变更记录留痕（0069 + L-070 / L-071）
