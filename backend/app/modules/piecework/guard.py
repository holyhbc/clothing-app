"""§5.1 ①⑤ 的纯判定逻辑（**不碰 session**）。

## 为什么单独拆一个文件

两个原因，不只是「行数超了」：

1. **可测性**：这里三个函数全是纯判定，给一组 ``(qty, bundle_qty)`` 就能断言，
   不必铺一整个车间世界去跑一次事务。而 ① 的两条判定（作废 / 已计）恰恰是
   「文案要长什么样」的问题 —— 文案一长，测试里塞满搭场景的行就盖住了要断言的那行。
2. **它们是本卡最容易被后人改坏的地方**：⑤ 里三条判定的**顺序**是钉死的
   （整件 → 超产 → 正数），而顺序错了只是文案变了、规则没变，很容易被当成
   「无关紧要的整理」顺手调一下。单独成文件并在 docstring 里写死理由，
   比散在 service 的 400 行里更容易被看见。

## ① 为什么不查流水表

ADR-0016 决策 3 把「一码一员工」钉在**码**上：``bundles.counted_at`` 非空 = 这手已被
计过。「查 ``piecework_logs`` 里有没有别人的行」是**两个来源**（流水表 vs 码上的回写），
两者会漂移 —— 红冲流水后码上仍是已计、或者码上清了而流水还在。**判据只有一个**。
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from app.core.errors import BusinessError, ErrorCode
from app.core.numbering import BUSINESS_TZ

#: 锁查询那一行的形状（字段见 ``repository._LOCK_COLUMNS``）。
type LockedBundle = Mapping[str, Any]


def guard_bundle(row: LockedBundle, employee_id: UUID) -> LockedBundle:
    """§5.1 ① 的三道判定。**必须在 ``FOR UPDATE`` 之内调用。**

    ⚠️ 调用方已经判过「不存在」（31001），这里只判作废与已计 —— 把 31001 也放进来
    看着更「完整」，但它其实是「行没查到」，与「查到了但不能动」是两类判定，
    混在一个函数里会让人以为 31001 也需要先加锁。

    ## ★ 已计判定**必须先看是不是同一个人**

    BR-PW-16 ①：同一员工重试走幂等 200，**永不返回 32001**。而 ① 在 ⑦ 之前跑，
    所以这里若只判「``counted_at`` 非空就抛 32001」，同员工重试会被自己的历史
    拦下来 —— 症状是扫码枪按两下，第二下报「陈美玲 09:44 已计 60 件」，
    而那个人**就是当前操作员本人**。工位机上没人会再扫一次，计件就卡死了。

    ⚠️ 这里**不代替 ⑦ 判幂等**，只是放行：放行后 ⑦ 回查得到流水就返 200；
    万一查不到（码上写了 counted_by 而流水被物理删了 —— 本不该发生），
    service 侧还有一道兜底，见 :func:`.service.PieceworkService.count_piecework` ⑦。
    """
    from app.modules.bundling.models import BundleStatus

    if row["status"] == BundleStatus.VOIDED:
        raise BusinessError(
            ErrorCode.BUNDLE_ALREADY_VOIDED, f"打菲号 {row['bundle_no']} 已作废，请重新扫码"
        )
    if row["counted_at"] is not None and row["counted_by"] != employee_id:
        raise BusinessError(
            ErrorCode.PIECEWORK_COUNTED_BY_OTHER,
            already_counted_message(row),
            details={
                "counted_by": str(row["counted_by"]) if row["counted_by"] else None,
                "counted_at": row["counted_at"].isoformat() if row["counted_at"] else None,
                "counted_qty": str(row["counted_qty"]),
                "bundle_qty": str(row["bundle_qty"]),
                "hands": row["hands"],
            },
        )
    return row


def already_counted_message(row: LockedBundle) -> str:
    """``32001`` 的文案（04-计件 §6.1「响应要点」）：**一行说完，不弹窗打断**（06 §2.5 第 3 条）。

    ⚠️ 姓名取自锁查询里 LEFT JOIN 来的 ``counted_by_name``，**不在这里补查**：
    补查要再碰一次 ``users``，而这条信息必须在锁内拿到 —— 锁外补查的那个时刻，
    码上的 ``counted_by`` 可能已经被另一次操作改掉了。
    """
    who = row["counted_by_name"] or "其他员工"
    when = (
        row["counted_at"].astimezone(BUSINESS_TZ).strftime("%H:%M") if row["counted_at"] else "更早"
    )
    return f"{row['style_no']} 第 {row['hands']} 手，{who} {when} 已计 {int(row['counted_qty'])} 件"


def resolve_qty(qty: Decimal | None, bundle_qty: Decimal) -> Decimal:
    """§5.1 ⑤ **BR-PW-26/27/28**。⚠️ 严禁回落 ``DEFAULT 1``（那是「扫码 +1 件」的旧口径）。

    ## 顺序是钉死的：整件 → 超产 → 正数

    ``32005``（非整件）比 ``32006``（超产）更基础。先判超产会把「60.5 件」报成超产，
    而它真正的问题是「本系统按整件计」—— 让人以为该去追加手，白白多裁一批货。

    ⚠️ ``<= 0`` 放在最后，是因为它其实**到不了**：``qty`` 由 Pydantic 的 ``gt=0``
    在 schema 层挡掉了（回 ``10001``）。这里保留是**防御**：本函数将来可能被
    手工补录（``/piecework-logs``，入参不同）复用，那里没有 ``gt=0``。
    """
    value = bundle_qty if qty is None else qty
    if value != value.to_integral_value(rounding=ROUND_HALF_UP):
        raise BusinessError(
            ErrorCode.PIECEWORK_QTY_NOT_INTEGER,
            "本系统按整件计，尾数不计件也不入库（docs/09 §4.2）",
            details={"qty": str(value)},
        )
    if value > bundle_qty:
        raise BusinessError(
            ErrorCode.PIECEWORK_OVER_PRODUCTION,
            f"该手只有 {int(bundle_qty)} 件，请追加手（新裁剪 + 新打菲）后再计",
            details={"qty": str(value), "bundle_qty": str(bundle_qty)},
        )
    if value <= 0:
        raise BusinessError(
            ErrorCode.PARAM_INVALID, "计件数量必须大于 0", details={"qty": str(value)}
        )
    return value


def counted_by_self(row: LockedBundle, employee_id: UUID) -> bool:
    """这手码已计，且计件人就是 ``employee_id``（01 放行、⑦ 又查不到流水时的兜底）。"""
    return row["counted_at"] is not None and row["counted_by"] == employee_id


__all__ = [
    "LockedBundle",
    "already_counted_message",
    "counted_by_self",
    "guard_bundle",
    "resolve_qty",
]
