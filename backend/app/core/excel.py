"""xlsx 流式导出（docs/05 §9.1）。

用 ``openpyxl`` 的 ``write_only`` 模式：普通 Workbook 会把整个文件建在内存里，
10 万行直接 OOM。write_only 模式逐行追加，配合 ``StreamingWriter`` 边生成边吐。

三个硬要求（docs/05 §9.1）：
    - 表头**冻结**（筛选时不用滚回顶部）
    - **自动筛选**（用户导出后第一件事就是筛选）
    - 数值保持数值类型 —— 写成字符串会让 Excel 里的求和变成文本
"""

from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

#: Excel 单元格的取值范围（openpyxl 认的类型 + 字符串）。
#: 用别名集中一处，ruff 的 ANN401 才能守住其他参数。
type CellValue = Any

#: Excel 单表上限 1048576 行。留 1 行给表头
MAX_ROWS = 1_048_575


class Column:
    """一列的展示定义。"""

    __slots__ = ("header", "key", "width")

    def __init__(self, key: str, header: str, width: int = 16) -> None:
        self.key = key
        self.header = header
        self.width = width


def _cell(value: CellValue) -> CellValue:
    """把 Python 值转成 Excel 能存的类型。

    ⚠️ ``Decimal`` **必须**转 ``float``：openpyxl 不认识 Decimal，直接塞会报
    ``ValueError: Cannot convert ... to Excel``。精度损失在这里是可接受的 ——
    导出是给人看的，账务精度以数据库为准（docs/04 §1 的 numeric 约束不受影响）。
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, str | int | float | bool):
        return value
    # UUID / 枚举等统一转字符串；枚举取 value（业务可读），UUID 取标准形式
    return str(value)


def stream_xlsx(columns: list[Column], rows: Iterator[dict[str, CellValue]]) -> Iterator[bytes]:
    """把行流式写成 xlsx 并按块产出。

    :param columns: 列定义（顺序即表头顺序）
    :param rows: 行字典的迭代器（**不要**传 list，20 万行会一次性进内存）
    :returns: xlsx 文件字节块，调用方边收边写进 HTTP 响应
    """
    # ⚠️ openpyxl 没有公开的 WriteOnlyWorksheet 类型（3.1 里它在私有模块
    # ``worksheet._write_only``）。写_only 模式下 create_sheet 返回的仍是 Worksheet，
    # 所以注解用公开类型即可，不去 import 私有符号 —— 那是升级就会断的做法。
    workbook = Workbook(write_only=True)
    sheet: Worksheet = workbook.create_sheet("导出")

    sheet.freeze_panes = "A2"  # 冻结首行（表头）
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(columns))}1"

    header_style = Font(bold=True)
    header_cells = [_styled_cell(sheet, column.header, header_style) for column in columns]
    for index, column in enumerate(columns, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = column.width
    sheet.append(header_cells)

    written = 0
    for row in rows:
        if written >= MAX_ROWS:
            break
        sheet.append([_cell(row.get(column.key)) for column in columns])
        written += 1

    if written == 0:
        # 表头 + 一行"无数据"。导出空文件的话，用户打开只看到一片空白，
        # 不知道是筛选没命中还是导出坏了
        sheet.append(["（无数据）"] + [None] * (len(columns) - 1))

    yield from _drain(workbook)


def _drain(workbook: Workbook) -> Iterator[bytes]:
    """把 workbook 写进内存并按块产出。

    ⚠️ xlsx 是个 zip 包，openpyxl 的 ``save()`` 只接受文件-like，所以这里只能
    先落到 ``BytesIO``。这就是为什么 ``rows`` 必须是迭代器 —— 行数据不进内存，
    只有 xlsx 文件本身在内存里（10 万行约 3~5 MB，可接受；超过 11011 上限时
    service 已经先拒了）。
    """
    import io

    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    while chunk := buffer.read(64 * 1024):
        yield chunk


def _styled_cell(sheet: Worksheet, value: CellValue, font: Font) -> CellValue:
    """write_only 模式下必须用 ``WriteOnlyCell``，普通 ``Cell`` 不可用。"""
    from openpyxl.cell import WriteOnlyCell

    cell = WriteOnlyCell(sheet, value=value)
    cell.font = font
    cell.alignment = Alignment(horizontal="center")
    return cell
