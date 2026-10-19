"""工资单凭证的**唯一收敛点**（T-PAY-007；Q-PL09 选项 A）。

全仓库后端提到「计提 / 付款凭证」这一财务概念的代码**只有本文件**：

- 本文件**不 import finance 模块**（M9 财务凭证模块尚未建表，``backend/app/modules/``
  里没有 ``finance``）；
- **不建凭证引擎、不建凭证表、不登记 ``60001``**（借贷平衡归 M9，"60" 段保持未实现）；
- :meth:`~app.modules.payroll.service.sheet_approve.SheetApproveMixin.approve` /
  ``reverse`` 只调用 :class:`VoucherPort` 的两个方法，其余工资代码不认识凭证。

将来 M9 建表后，只需在本文件实现一个 ``SqlVoucherGateway`` 并在构造
:class:`~app.modules.payroll.service.sheet.PayrollSheetService` 时注入它 ——
**工资侧其余代码一行不改**，这正是「单一收敛点」的全部意义。

## 为什么是 ``async def``（而非同步方法）

``docs/08 §1.2 R4`` 要求「审核 = 生效」：凭证生成必须与状态迁移**同一事务**。
本项目的数据库访问一律 async（``AsyncSession``），M9 的真实实现需要在**同一个**
事务里写凭证行，故端口方法必须是协程；若声明成同步方法，M9 接入时既改不了事务
边界、又得连调用点一起改，收敛点就名存实亡了。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:  # 只在类型检查时引用工资单模型，避免运行期循环 import
    from app.modules.payroll.models import PayrollSheet

logger = logging.getLogger("app.payroll")


@runtime_checkable
class VoucherPort(Protocol):
    """工资单 → 财务凭证的**单向端口**（M9 接入时实现）。

    ``approve`` 调 :meth:`create_accrual` 生成计提凭证；``reverse`` 调
    :meth:`reverse_accrual` 红冲计提凭证；``pay`` 调 :meth:`create_payment` 生成付款凭证
    （T-PAY-008）。三者都返回凭证号，未接入时返回 ``None``。
    """

    async def create_accrual(self, sheet: PayrollSheet) -> str | None:
        """生成计提凭证，返回凭证号（``VO-...``）；未接入 → ``None``。"""
        ...  # pragma: no cover —— Protocol 桩不可执行，不计覆盖（同 AuthProvider）

    async def reverse_accrual(self, sheet: PayrollSheet) -> str | None:
        """红冲计提凭证（红字反向，不 UPDATE 原凭证），返回红冲凭证号；未接入 → ``None``。"""
        ...  # pragma: no cover —— Protocol 桩不可执行，不计覆盖（同 AuthProvider）

    async def create_payment(self, sheet: PayrollSheet) -> str | None:
        """生成**付款**凭证（借 应付职工薪酬 / 贷 银行存款），返回凭证号；未接入 → ``None``。

        ⚠️ 与 :meth:`create_accrual` 同事务：``pay`` 在把状态迁到 ``PAID`` 的同一个事务里
        调它，端口抛错则整体回滚，绝不留下「已 PAID 而没凭证」的半个状态（08 §1.2 R4）。
        """
        ...  # pragma: no cover —— Protocol 桩不可执行，不计覆盖（同 AuthProvider）


class NullVoucherGateway:
    """空适配器（P2 阶段默认）：不生成凭证，返回 ``None`` 并记 WARN，**不抛异常**。

    台账未接 M9 时发放流程必须能继续（``P2-工资-实施说明 §8 Q-PL09`` 选项 A：
    "发放仍可置 ``PAID`` 出代发文件"），故这里**不得静默吞错，也不得抛异常**。

    TODO(业务待确认: Q-PL09)：M9 建立凭证表后替换为真实适配器；「是否允许无凭证置
    ``PAID``」「M9 建表后是否回补历史计提」属财务决策，见 ``docs/12`` L-124。
    """

    async def create_accrual(self, sheet: PayrollSheet) -> str | None:
        logger.warning(
            "计提凭证未生成：财务凭证模块（M9）未接入（TODO Q-PL09）",
            extra={"doc_no": sheet.doc_no, "total_gross": str(sheet.total_gross)},
        )
        return None

    async def reverse_accrual(self, sheet: PayrollSheet) -> str | None:
        logger.warning(
            "计提凭证红冲未生成：财务凭证模块（M9）未接入（TODO Q-PL09）",
            extra={"doc_no": sheet.doc_no, "accrual_voucher_no": sheet.accrual_voucher_no},
        )
        return None

    async def create_payment(self, sheet: PayrollSheet) -> str | None:
        logger.warning(
            "付款凭证未生成：财务凭证模块（M9）未接入（TODO Q-PL09）",
            extra={"doc_no": sheet.doc_no, "total_net": str(sheet.total_net)},
        )
        return None


__all__ = ["NullVoucherGateway", "VoucherPort"]
