"""StyleService 组合入口：仅继承 6 个内部 Mixin + ``__init__``。

6 个 Mixin 是"把一个 1415 行类拆到多文件"的手段，不承载跨模块通用逻辑（设计稿 §2.1）。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import AuthContext

from .style_child_mixin import StyleChildMixin
from .style_crud_mixin import StyleCrudMixin
from .style_operation_mixin import StyleOperationMixin
from .style_query_mixin import StyleQueryMixin
from .style_ratio_mixin import StyleRatioMixin
from .style_template_mixin import StyleTemplateMixin


class StyleService(
    StyleQueryMixin,
    StyleCrudMixin,
    StyleChildMixin,
    StyleRatioMixin,
    StyleOperationMixin,
    StyleTemplateMixin,
):
    """款号与其子表（色码 / 尺码 / 比例 / 款号工序 / 单价）的全部业务规则。"""

    def __init__(self, session: AsyncSession, ctx: AuthContext) -> None:
        self.session = session
        self.ctx = ctx
