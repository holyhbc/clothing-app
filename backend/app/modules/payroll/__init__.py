"""工资模块模型包（T-PAY-001 / 迁移 0020，DDL 权威来源 ``docs/04 §7.6.1~§7.6.3``）。

```
payroll_periods                      周期（OPEN/CLOSED，非单据）
└── piecework_settlements            结算单（PW-，周期 × 车间唯一）
    └── piecework_settlement_lines   结算行（一条有唯一占用）
```

本卡**只建三张结算域表 + 补 ``piecework_logs.payroll_id`` 外键**。
``payroll_sheets`` / ``payroll_details`` / ``bank_payment_files`` 属 T-PAY-002（迁移 0021），
service / repository / router / 权限属 T-PAY-003 起。

本文件**只做聚合 / 重导出**（ADR-0031），不放业务逻辑。
"""

from app.modules.payroll.models import (
    DOCUMENT_STATUS,
    PAYROLL_PERIOD_STATUS,
    SETTLEMENT_LOG_TYPE,
    PayrollPeriod,
    PayrollPeriodStatus,
    PieceworkSettlement,
    PieceworkSettlementLine,
)

__all__ = [
    "DOCUMENT_STATUS",
    "PAYROLL_PERIOD_STATUS",
    "SETTLEMENT_LOG_TYPE",
    "PayrollPeriod",
    "PayrollPeriodStatus",
    "PieceworkSettlement",
    "PieceworkSettlementLine",
]
