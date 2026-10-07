"""打菲模块 service 入口（T-BUND-003 / T-BUND-004 / T-BUND-005a / T-BUND-005b）。

导出服务类、只读预演 Mixin、状态机 Mixin、审核 Mixin 与拆分纯函数。**``split_size_line``
等纯函数被审核与预演共用**，从这里导入是稳定契约；``StateMixin`` 的 ``submit`` /
``reject`` / ``withdraw`` / ``cancel`` 与 ``ApproveMixin`` 的 ``approve`` / ``reverse``
同为状态动作入口。
"""

from .approve_assert import ApproveAssertMixin
from .approve_mixin import HAND_CONFLICT_CONSTRAINTS, ApproveMixin
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
    "HAND_CONFLICT_CONSTRAINTS",
    "HAND_DUPLICATED_CODE",
    "MAX_HAND_SEQ",
    "ApproveAssertMixin",
    "ApproveMixin",
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
