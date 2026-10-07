"""打菲模块 service 入口（T-BUND-003 / T-BUND-004 / T-BUND-005a）。

导出服务类、只读预演 Mixin、状态机 Mixin 与拆分纯函数。**``split_size_line`` 等
纯函数被审核（T-BUND-005b）与预演共用**，从这里导入是稳定契约；
``StateMixin`` 里的 ``submit`` / ``reject`` / ``withdraw`` / ``cancel`` 同样是
T-BUND-005b 的调用入口。
"""

from .bundling_order_service import BundlingOrderService
from .common import CommonMixin
from .numbering import (
    build_bundle_no,
    is_valid_bundle_no,
    next_doc_no,
    parse_bundle_no,
)
from .preview import AvailableOutput, PreviewMixin
from .split import (
    HAND_DUPLICATED_CODE,
    MAX_HAND_SEQ,
    HandConflict,
    OrderSplitPreview,
    SizeLineSplit,
    SplitBundle,
    SplitLineInput,
    assert_whole,
    preview_order,
    split_size_line,
)
from .state_guard import OutputPlan, StateGuardMixin
from .state_mixin import StateMixin

__all__ = [
    "HAND_DUPLICATED_CODE",
    "MAX_HAND_SEQ",
    "AvailableOutput",
    "BundlingOrderService",
    "CommonMixin",
    "HandConflict",
    "OrderSplitPreview",
    "OutputPlan",
    "PreviewMixin",
    "SizeLineSplit",
    "SplitBundle",
    "SplitLineInput",
    "StateGuardMixin",
    "StateMixin",
    "assert_whole",
    "build_bundle_no",
    "is_valid_bundle_no",
    "next_doc_no",
    "parse_bundle_no",
    "preview_order",
    "split_size_line",
]
