"""打菲模块 service 入口（T-BUND-003）。

导出服务类与纯函数。
"""

from .bundling_order_service import BundlingOrderService
from .common import CommonMixin
from .numbering import (
    build_bundle_no,
    is_valid_bundle_no,
    next_doc_no,
    parse_bundle_no,
)

__all__ = [
    "BundlingOrderService",
    "CommonMixin",
    "build_bundle_no",
    "is_valid_bundle_no",
    "next_doc_no",
    "parse_bundle_no",
]
