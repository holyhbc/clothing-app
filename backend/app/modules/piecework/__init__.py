"""计件模块模型包（T-PIECE-001 / 迁移 0019，DDL 权威来源 ``docs/04 §7.1`` / ``§7.1.1``）。

## 两张表的关系

```
users ──┐
        ├── piecework_bindings   员工 ↔ 款号 ↔ 工序（绑定当天有效，次日重扫）
bundles ──── piecework_logs     计件流水（append-only，纠错靠红冲 + 重入）
```

## 四条贯穿两表的建模口径

1. **流水 append-only，纠错靠红冲 + 重入**（INV-2 / ADR-0005）。``piecework_logs``
   虽然带 ``deleted_at`` / ``version``（``04 §2`` 公共字段），但**业务上禁止
   ``UPDATE qty/amount/bundle_id/bundle_no``**，只有 ``remark/updated_*`` 可改 ——
   用「软删」表达「可撤销」是自相矛盾的（同 ``app/modules/stock/models.py`` 的说法）。
2. **符号用 ``log_type`` 表达，不用负金额字段**（``04 §4`` REV-2026-10 例外）：
   ``REVERSAL`` 行 ``qty`` 与 ``amount`` 为负，其余为正，由 ``ck_piecework_logs_sign``
   兜住。⚠️ 旧的 ``qty > 0`` CHECK 会拒掉红冲行，**不得**再用。
3. **外键三处**（表见 ``docs/04 §7.1`` 的「两处外键的取舍」）：``bundle_id``
   → ``bundles.id``、``reversal_of_id`` 自引用、``payroll_id`` →
   ``piecework_settlements.id``（T-PAY-001 / 迁移 0020 补上 —— 0019 建表时
   结算表还不存在，故当时刻意只建列）。
4. **公共字段与车间/用户列一律不建外键**：人事离职、车间停用重组都是合法的档案维护
   动作，建 FK 会阻止它们；数据范围过滤走 ``app/core/scope.py`` 的列名约定。

## 本文件**不包含**的东西

无 relationship、无 service / repository / router —— 那些随 T-PIECE-002 补。
现在建空壳只会让「计件模块已就位」看起来比实际完整（同 ``stock/__init__.py``）。
"""

from app.modules.piecework.models import (
    PIECEWORK_LOG_TYPE,
    PieceworkBinding,
    PieceworkLog,
    PieceworkLogType,
)

__all__ = [
    "PIECEWORK_LOG_TYPE",
    "PieceworkBinding",
    "PieceworkLog",
    "PieceworkLogType",
]
