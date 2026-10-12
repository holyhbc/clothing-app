"""新建打菲单的来源选择：出参（modules/03 §5.1 第 2 步）。

⚠️ ``SourceOrderOut`` **没有余量字段** —— ``CuttingOutput`` 按
（款号 + 色 + 尺码）登记、没有 ``cutting_order_id``，余量是款号/色/尺码级别的，
多张同款同色的裁剪单共享同一份，**算不出「这张单还剩多少」**。
余量在 :class:`SourceLineOut` 上，按尺码明细行给。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.core.pydantic_types import Str


class SourceOrderOut(BaseModel):
    """候选来源裁剪单（**只列** ``APPROVED``，B8 + 03 §5.1）。"""

    model_config = ConfigDict(from_attributes=True)

    id: Annotated[UUID, Field(description="★ 裁剪单 id（填进 source_cutting_order_id）")]
    doc_no: Annotated[Str, Field(description="裁剪单号")]
    doc_date: Annotated[Str, Field(description="单据日期")]
    style_no: Annotated[Str, Field(description="款号")]
    workshop_id: Annotated[
        UUID | None, Field(default=None, description="车间（数据范围按它过滤，INV-8）")
    ] = None


class SourceLineOut(BaseModel):
    """选定来源裁剪单后，该色下每个尺码的可打菲来源明细。

    与 :class:`~app.modules.bundling.schemas.code_schemas.AvailableOutputOut` 同口径，
    只是入口不同（那个以已存在的打菲单为根）。
    """

    model_config = ConfigDict(from_attributes=True)

    cutting_size_line_id: Annotated[UUID, Field(description="★ 填进明细行的 cutting_size_line_id")]
    size_code: Annotated[Str, Field(description="尺码")]
    hands: Annotated[int, Field(description="该尺码裁剪手数")]
    qty_per_hand: Annotated[Str, Field(description="每手件数")]
    output_qty: Annotated[Str, Field(description="该尺码出数")]
    available_qty: Annotated[
        Str, Field(description="现在还能打多少（款号+色+尺码级别；余量为 0 也在列表里）")
    ]


__all__ = ["SourceLineOut", "SourceOrderOut"]
