"""``CuttingOrderService`` 组合入口：仅继承 5 个内部 Mixin + ``__init__``。

5 个 Mixin 是「把一个 850 行类拆到多文件」的手段，不承载跨模块通用逻辑
（设计稿 §2.3）。对外类型名与构造签名保持不变（``CuttingOrderService(session)``）。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from .common import CommonMixin
from .insert_mixin import InsertMixin
from .line_mixin import LineMixin
from .order_mixin import OrderMixin
from .ratio_mixin import RatioMixin


class CuttingOrderService(OrderMixin, LineMixin, InsertMixin, RatioMixin, CommonMixin):
    """裁剪单服务。**事务边界唯一入口**（docs/03 §1.4）。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
