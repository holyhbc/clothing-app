"""标签 CSV 渲染（T-BUND-006，**纯函数**）。

⚠️ CSV 在**内存里**用 ``StringIO`` 渲染，不落盘：临时文件会留在容器可写层里，
既没有清理时机也带款式信息（客人名 / 车间 / 手号都在里面）。
"""

from __future__ import annotations

import csv
from collections.abc import Sequence
from io import StringIO
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from app.modules.bundling.service.label import LabelItem

#: 标签 CSV 的**中文表头**（modules/03 §5.4 的标签内容清单，顺序即标签软件里的列序）。
#:
#: ⚠️ 中文表头是刻意的：CSV 的读者是**仓管**（导进标签软件，不是开发），而款号 / 手号 /
#: 件数这些词在车间语境里本来就是中文的。
#: ⚠️ 不加任何冗余列：多一列就多一处「两版数据对不上」的可能。
LABEL_CSV_HEADER: Final[tuple[str, ...]] = (
    "款号",
    "颜色",
    "尺码",
    "第N手",
    "共M手",
    "该手件数",
    "打菲号",
    "工序号",
    "二维码内容",
    "条码内容",
)


def render_labels_csv(items: Sequence[LabelItem]) -> str:
    """渲染成 CSV 文本：**1 行表头 + 每手 1 行**（数量按原精度出参，05 §3）。

    ⚠️ Router 侧要用 ``text/csv; charset=utf-8-sig`` 发出，否则 Excel 打开中文是乱码
    （T-BUND-007 接线时注意）；这里只管文本本身，不管传输层。
    """
    buffer = StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(LABEL_CSV_HEADER)
    for item in items:
        writer.writerow(item.csv_row())
    return buffer.getvalue()


__all__ = ["LABEL_CSV_HEADER", "render_labels_csv"]
