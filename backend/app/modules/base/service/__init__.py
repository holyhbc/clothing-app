"""基础资料业务逻辑（设计稿 §4.4 第一批 + §4.5 第二批）。

⚠️ **事务边界只在 service**（AGENTS.md §2.1）：所有写方法自带
``async with unit_of_work(session)``，router 不 commit。

本模块三个 service 类：

======================  ==========================================  ==================
类                     承载                                          任务卡
======================  ==========================================  ==================
:class:`DictService`   九个字典类资源的统一 CRUD                     T-BASE-001
:class:`StyleService`  款号 / 色码尺码 / 比例 / 款号工序 / 模板复制   T-BASE-002
:class:`RateService`   工序单价：设价 / 调价 / 取价 / 历史 / 导出      T-BASE-002
======================  ==========================================  ==================

第一批 9 个资源共享的 6 条业务规则，每条都对应一个测试：

1. **编码唯一靠唯一索引兜底**，不靠"先查后插"（docs/03 §1.5、modules/01 R15）。
   冲突统一报 ``10001``（参数冲突）而不是自定义码 —— docs/05 §4 没登记"编码重复"
   这个码，而 ``10001`` 的语义就是"参数不合法"。
2. **PATCH 必传 ``version``**，不匹配 → ``10003``。用**条件 UPDATE** 实现
   （``WHERE id=? AND version=?``），靠 ``rowcount=0`` 判定冲突 —— 这样并发下
   两个请求只有一个能成功，另一个拿到 10003，而不是"后写的悄悄覆盖先写的"。
3. **停用必填 ``reason``**（§4.4）。缺失由 Schema 拦（``min_length=1``），
   额外再查一次是防绕过 Schema 的直接调用。
4. **真删两分支**（ADR-0025）：未被引用 → 物理删除；被引用 → ``20003`` +
   ``details.ref_count`` + ``details.references[]``。判定用**一条 SQL 的条件
   DELETE**，不"先查后删" —— 那两步之间被并发插入引用就会留下悬空引用（CC-3）。
5. **每次变更写 ``document_logs``**（docs/04 §7.9），``doc_type`` 取资源注册表。
6. **码表删成员级联**：删 ``size_groups`` 时连带删 ``size_group_items`` 并回传条数
   （§4.4）。留孤儿成员行就是"残渣"。

第二批（款号与单价）另有 5 条**不可省**的规则：

7. **单价只 INSERT，永不 UPDATE** ``unit_price``（R11 / INV-P0-3）：调价 = 旧行
   只改 ``effective_to`` + 插入新行。TC-B14 断言历史区间 ``unit_price`` 未变。
8. **同一 ``(style_no, product_category_id, operation_no)`` 三元组内区间不重叠**，
   且**至多一条** ``effective_to IS NULL``（R18 / INV-P0-2）。区间行按
   ``effective_from`` 排序 ``FOR UPDATE``，重叠 → ``20005`` + ``details``。
9. **全量替换的两处子表操作走"聚合版本"**（``styles.version``）：比例与款号工序都
   是"旧行全删 + 新行全插"，子表自己的 ``version`` 每次从 1 重来，拿它当乐观锁
   等于没有锁（TC-B31 要求一成一败）。
10. **取价用一条 SQL**（ADR-0026 §2），档位优先 + 生效日两个维度一次性判定，
    避免"应用层按档位查三次"在并发调价时读到撕裂的组合。
11. **模板复制整批单事务**，且**档位 2（分类价）不复制**（ADR-0026 §4）——
    复制一款顺手改掉全厂工资口径是静默事故，比不复制危险得多。
"""

# ---------------------------------------------------------------------------
# ⚠️ 以上是原 ``base/service.py`` 的模块 docstring，**逐字保留**（拆分不改业务说明）。
# 拆分后本包实际为 **4 个 Service**（`DictService` / `StyleService` /
# `RateService` / `MaterialOptionsService`）+ `StyleService` 的 6 个内部 Mixin，
# 见设计稿 docs/modules/base-重构拆分.设计.md §2.1。
# 分层：``common`` 不依赖任何 mixin / service；mixin 只依赖 common / models /
# schemas / repository / core；``style_service`` / ``rate_service`` 组合 mixin；
# 本 ``__init__`` 最后 import，保证 ``from app.modules.base.service import X`` 零改动。
# ---------------------------------------------------------------------------

from .common import (
    ACTION_CREATE,
    ACTION_DELETE,
    ACTION_RESTORE,
    ACTION_UPDATE,
    RateQuery,
    StyleQuery,
    write_document_log,
)
from .dict_service import DictService
from .material_options_service import MaterialOptionsService
from .rate_helpers import build_rate_resolve_stmt
from .rate_service import RateService
from .style_service import StyleService

__all__ = [
    "ACTION_CREATE",
    "ACTION_DELETE",
    "ACTION_RESTORE",
    "ACTION_UPDATE",
    "DictService",
    "MaterialOptionsService",
    "RateQuery",
    "RateService",
    "StyleQuery",
    "StyleService",
    "build_rate_resolve_stmt",
    "write_document_log",
]
