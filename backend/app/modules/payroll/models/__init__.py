"""工资模块模型包（T-PAY-001 / 迁移 0020；T-PAY-002 / 迁移 0021）。

按职责拆文件（ADR-0031；原 ``models.py`` 加三表后越过 400 行硬线）：

- :mod:`~app.modules.payroll.models.settlement` —— 结算域三表
  （``payroll_periods`` / ``piecework_settlements`` / ``piecework_settlement_lines``）
- :mod:`~app.modules.payroll.models.sheet` —— 工资单域三表
  （``payroll_sheets`` / ``payroll_details`` / ``bank_payment_files``）

本包只做聚合 / 重导出，**不放业务逻辑**：外部 ``from app.modules.payroll.models
import X`` 的导入面零改动（ADR-0031 §包的重导出约定）。
"""

from app.modules.payroll.models.settlement import (
    DOCUMENT_STATUS,
    PAYROLL_PERIOD_STATUS,
    SETTLEMENT_LOG_TYPE,
    PayrollPeriod,
    PayrollPeriodStatus,
    PieceworkSettlement,
    PieceworkSettlementLine,
)
from app.modules.payroll.models.sheet import (
    BankPaymentFile,
    PayrollDetail,
    PayrollSheet,
)

__all__ = [
    "DOCUMENT_STATUS",
    "PAYROLL_PERIOD_STATUS",
    "SETTLEMENT_LOG_TYPE",
    "BankPaymentFile",
    "PayrollDetail",
    "PayrollPeriod",
    "PayrollPeriodStatus",
    "PayrollSheet",
    "PieceworkSettlement",
    "PieceworkSettlementLine",
]
