"""打菲模块的请求 / 响应模型（docs/05 §2、§3 + modules/03 §6）。

## 三条约定（跨子模块成立）

1. **请求模型一律 ``extra="forbid"``**（05 §2）：少一个字段是 typo，多一个字段是
   「以为服务端会用而其实没用」—— 后者更贵。
2. **响应里不出现** ``deleted_at`` / ``created_by`` / ``updated_by``（05 §3）；``remark`` 保留。
3. **数量 / 金额在响应里一律 ``str``**（05 §3），而**计数与序号**（手数、手序号、
   打印张数）用 ``int`` —— 与既有出参同口径，标成字符串会让前端做字符串拼接。

## 拆包（T-BUND-007b / ADR-0031 / 闭环 L-102）

单文件已 398 行，本卡再加码详情 / 统计 / 导出的出参就会顶破 ADR-0030 的 400 行硬线，
按 ``cutting/schemas`` 同一配方拆成三个子模块（见 :mod:`.order_schemas` 的模块说明）：

| 模块 | 装什么 |
| --- | --- |
| :mod:`.order_schemas` | 建单/改单/明细替换、六个状态动作、单据与可打菲来源出参 |
| :mod:`.code_schemas` | 拆分预演、标签两接口、**码**列表/详情/单码作废 |
| :mod:`.stat_schemas` | **统计**出参 |

**对外导入面保持不变**：现有代码继续 ``from app.modules.bundling.schemas import X``，
不要改深路径（改深路径会让每个调用点都变成一次「重构过没有」的判断题）。
重导出按**字母序**排列（与 ruff 的 ``__all__`` 排序要求一致）。
"""

from .code_schemas import (
    BundleDetailOut,
    BundleListOut,
    HandConflictOut,
    LabelExportOut,
    LabelExportQuery,
    LabelItemOut,
    LabelPrintIn,
    LabelPrintOut,
    LabelPrintRecordOut,
    SplitBundleOut,
    SplitLineIn,
    SplitPreviewIn,
    SplitPreviewLineOut,
    SplitPreviewOut,
    VoidCodeIn,
    VoidCodeOut,
)
from .order_schemas import (
    MAX_LINES,
    MAX_REASON,
    ApproveIn,
    AvailableOutputOut,
    BundlingOrderCreateIn,
    BundlingOrderListOut,
    BundlingOrderOut,
    BundlingOrderPatchIn,
    CancelIn,
    LineIn,
    LineOut,
    PutLinesIn,
    RejectIn,
    ReverseIn,
)
from .stat_schemas import StatisticsOut, StatItemOut, StatTotalsOut

__all__ = [
    "MAX_LINES",
    "MAX_REASON",
    "ApproveIn",
    "AvailableOutputOut",
    "BundleDetailOut",
    "BundleListOut",
    "BundlingOrderCreateIn",
    "BundlingOrderListOut",
    "BundlingOrderOut",
    "BundlingOrderPatchIn",
    "CancelIn",
    "HandConflictOut",
    "LabelExportOut",
    "LabelExportQuery",
    "LabelItemOut",
    "LabelPrintIn",
    "LabelPrintOut",
    "LabelPrintRecordOut",
    "LineIn",
    "LineOut",
    "PutLinesIn",
    "RejectIn",
    "ReverseIn",
    "SplitBundleOut",
    "SplitLineIn",
    "SplitPreviewIn",
    "SplitPreviewLineOut",
    "SplitPreviewOut",
    "StatItemOut",
    "StatTotalsOut",
    "StatisticsOut",
    "VoidCodeIn",
    "VoidCodeOut",
]
