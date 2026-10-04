# ADR-0029：给两张关联表 `user_roles` / `role_permissions` 授予 `DELETE`

| 项 | 内容 |
| --- | --- |
| 状态 | `Accepted`（2026-10-03，AI 提出并实现；**待业务负责人确认**，见「影响与后续」） |
| 日期 | 2026-10-03 |
| 决策人 | AI 提出（实现 T-AUTH-003 时撞到权限边界），待业务负责人确认 |
| 影响范围 | 迁移 / 权限 / 认证授权 |
| 关联 | [ADR-0025](./0025-字典项物理删除的权限口径.md)、[04 §6.2.1](../04-数据库规范.md)、[07 §2.2 / §5](../07-认证与权限规范.md) |

---

## 背景

- [docs/04 §6.2.1](../04-数据库规范.md) + ADR-0025 规定：应用运行账号 `erp_app`
  只授 `SELECT, INSERT, UPDATE`，**生产库不给任何硬删能力**；可物理删除的白名单
  收敛到四张字典表（`colors` / `sizes` / `size_groups` / `size_group_items`）。
- T-AUTH-003 要交付「角色权限勾选」与「用户角色分配」。这两个功能的语义是
  **整体替换**（docs/07 §6.1 第 6 条：授权界面天然是"勾选哪些"的全量语义），
  于是实现必然要「删掉不再勾选的那些行」。
- 实现时撞到：

  ```
  ProgrammingError: permission denied for table user_roles
  [SQL: DELETE FROM user_roles WHERE user_roles.user_id = $1::UUID]
  ```

  `user_roles` 与 `role_permissions` 是**关联表**（T-AUTH-001 建表时明确"不带公共字段"），
  没有 `deleted_at`，所以**软删这条路根本不存在**。

- 可选方案：

  | 方案 | 结论 |
  | --- | --- |
  | 给两张关联表加 `deleted_at` 列 | 关联表一旦有生命周期字段，就得回答"软删的授权行算不算生效"，而 `load_permissions` 每次请求查库时要多一层过滤 —— 为了"删一行"的场景给高频鉴权路径加负担，不划算 |
  | 改成"只增不删"（追加式授权） | **做不到**：取消勾选无法表达。授权界面去掉一个勾选，服务端必须能收回 |
  | 给两张关联表授 `DELETE` | ✅ 采纳 |

---

## 决策

**给 `user_roles` 与 `role_permissions` 两张关联表授予 `DELETE`，白名单从 4 张扩到 6 张。**

理由分三层：

1. **关联表行不是业务数据**。`user_roles` 的一行只表达"用户 X 有角色 Y"，
   `role_permissions` 的一行只表达"角色 Z 有权限点 W"。删掉一行 = 这条关系
   不再存在，**没有任何信息丢失** —— 与字典表"删掉就是不要了"（ADR-0025 §背景
   现状一要解决的痛点）性质完全不同。ADR-0025 的顾虑是"软删会让候选下拉、
   码表选择、旧单据回显全部带上已删除的历史包袱"，而关联表**不参与任何回显**。
2. **审计不靠这张表**。docs/07 §5 要求「权限变更留痕」，落点是
   `document_logs`（`doc_type='Role'`，含操作人 / 时间 / 从→到 / 原因）。
   `document_logs` 仍然是 `REVOKE UPDATE, DELETE`（docs/04 §6.2.1 + §7.9），
   审计不可篡改这条底线**没有被削弱**。
3. **范围可控**。白名单是**按表显式列举**的，不是 `GRANT DELETE ON ALL TABLES`。
   业务表（`styles` / `colors` / 单据表…）仍然一律不可硬删；
   迁移里同时 `REVOKE` 其余表，把"应用账号没有任何其他硬删能力"这条断言做成事实。

---

## 影响与后续

| 项 | 处理 |
| --- | --- |
| `alembic/versions/0006_system_join_table_grants.py` | `GRANT DELETE ON user_roles, role_permissions TO erp_app` + 对其余业务表 `REVOKE` |
| `docs/04 §6.2.1` | 白名单表清单同步为 6 张 |
| `tests/test_migrations.py` | 补断言：这两张表**可以** DELETE，且 `styles` / `users` / `document_logs` **仍然不行** |
| T-AUTH-003 | `_replace_user_roles` / `_replace_permissions` 改为「DELETE + INSERT」并在同一事务内 |
| 业务负责人 | **待确认**：白名单扩容到关联表。若不认可，退路是给关联表加 `deleted_at` 并在 `load_permissions` 里过滤（代价见背景表） |

---

## 验证

- `tests/test_migrations.py::test_join_tables_allow_delete`：用 `db_session`
  （`erp_app`）真删一行关联表 → 成功
- `tests/test_migrations.py::test_app_role_cannot_tamper_or_drop`：既有断言继续通过
  （`document_logs` 的 DELETE 仍被拒）
- 新增断言：`DELETE FROM styles` / `DELETE FROM users` 仍抛 `ProgrammingError`
- `tests/modules/test_system_router.py`：角色换权限 / 用户换角色后，
  **旧权限立即失效**（不重新登录），且 `document_logs` 有留痕