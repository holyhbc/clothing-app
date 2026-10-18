"""工资模块 service 包（ADR-0031；T-PAY-003 首建，T-PAY-004 扩）。

按职责拆文件（原单文件 ``period.py`` 641 行越过 ``ADR-0030`` 的 400 行硬线）：

- :mod:`~app.modules.payroll.service.common` —— 周期常量 / 枚举 / DTO / 纯助手
- :mod:`~app.modules.payroll.service.state_mixin` —— 周期状态机（``close`` / ``reopen``）
- :mod:`~app.modules.payroll.service.period` —— 周期组合类 ``PayrollPeriodService``
- :mod:`~app.modules.payroll.service.settlement_state` —— 结算单轻状态迁移
- :mod:`~app.modules.payroll.service.settlement` —— 结算单组合类 ``PieceworkSettlementService``
  （``create`` + 继承轻状态迁移）
- :mod:`~app.modules.payroll.service.sheet_state` / ``sheet_state_mixin`` / ``sheet_state_support``
  —— 工资单状态表 / 轻迁移 / 持久化助手（T-PAY-006）
- :mod:`~app.modules.payroll.service.sheet` —— 工资单组合类 ``PayrollSheetService``
  （``create`` 汇总生成 + 继承轻状态迁移）

本包只做聚合 / 重导出，**不放业务逻辑**：外部
``from app.modules.payroll.service import X`` 的导入面稳定（ADR-0031 §包的重导出约定）。
"""

from app.modules.payroll.service.common import (
    ACTION_CLOSE,
    ACTION_REOPEN,
    DOC_TYPE_PAYROLL_PERIOD,
    PERIOD_CODE_MAX_LENGTH,
    PERIOD_MANAGE_PERMISSION,
    SOURCE_CUTTING_BOUNDARY,
    SOURCE_MANUAL,
    PeriodCreated,
    PeriodGenerateMode,
    PeriodPreview,
)
from app.modules.payroll.service.period import PayrollPeriodService
from app.modules.payroll.service.settlement import PieceworkSettlementService
from app.modules.payroll.service.sheet import PayrollSheetService
from app.modules.payroll.service.state_mixin import PeriodStateMixin

__all__ = [
    "ACTION_CLOSE",
    "ACTION_REOPEN",
    "DOC_TYPE_PAYROLL_PERIOD",
    "PERIOD_CODE_MAX_LENGTH",
    "PERIOD_MANAGE_PERMISSION",
    "SOURCE_CUTTING_BOUNDARY",
    "SOURCE_MANUAL",
    "PayrollPeriodService",
    "PayrollSheetService",
    "PeriodCreated",
    "PeriodGenerateMode",
    "PeriodPreview",
    "PeriodStateMixin",
    "PieceworkSettlementService",
]
