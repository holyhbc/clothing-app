"""款号、色码尺码、比例、款号工序与模板复制的业务规则测试（T-BASE-002）。

覆盖任务卡里与款号侧有关的用例：

======================  =====================================================
TC-B06                  款号传小写 → 存库转大写，唯一性按大写判
TC-B07                  款号重复 → ``10001`` + **回显已有款名**
TC-B08                  建款号不传 ``category_id`` → ``10001``
TC-B09                  选码表一键带出尺码 → 4 条 ``style_sizes``，``sort_no`` 有序
TC-B10                  比例全量替换 → 旧行全删新行全插 + ``hands_total``
                        + ``missing_size_codes``
TC-B11                  比例含款号没有的尺码 → ``20007``
TC-B12/13               模板复制模式①/②；模式② ``1.0800 × 0.350000 = 0.378000``
TC-B31                  比例全量替换真并发 → 一成一败，败者 ``10003``
S3                      建款号 → 配工序 → 设价 → ``resolve`` 全链路
建议号                  同客户同年连续 3 次 → 0001/0002/0003；20 并发不重复
数据范围                跟单（``SELF``）只查得到本人款号，他人款号 → ``12002``
======================  =====================================================

⚠️ **每条业务规则都必须有测试**，包括"看起来显然"的那几条：``is_final_operation``
至多一道、``operation_no`` 必须存在且启用、模板复制不复制档位 2 分类价。
它们都是"漏了也不会报错、只会静默算错钱"的那一类。
"""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.common.enums import ConflictPolicy, DataScope, SizeClass, TemplateCopyMode
from app.common.models import DocumentLog
from app.core.db import unit_of_work
from app.core.errors import BusinessError, ErrorCode
from app.core.numbering import suggest_style_no
from app.core.permissions import AuthContext
from app.modules.base.models import (
    Customer,
    Operation,
    OperationRate,
    ProductCategory,
    Size,
    SizeGroup,
    SizeGroupItem,
    Style,
    StyleColorSizeRatio,
    StyleOperation,
    StyleSize,
)
from app.modules.base.schemas import (
    OperationRateCreate,
    RatioItemIn,
    RatioReplaceIn,
    StyleColorCreate,
    StyleCreate,
    StyleOperationItemIn,
    StyleOperationsReplaceIn,
    StylePatch,
    StyleSizeCreate,
    TemplateCopyIn,
)
from app.modules.base.service import StyleService
from tests.factories.user import OPERATOR_ID

CONCURRENCY = 20
#: 模板复制必须附带的提示（modules/01 §5.1 前端表现）
RATIO_NOT_COPIED = "尺码比例未复制，请按目标款的销售构成为各颜色录入尺码比例"
#: 固定业务日期：docs/10 §10「测试里写死日期导致跨月失败」
D1 = datetime(2026, 8, 1, tzinfo=UTC).date()
D2 = datetime(2026, 8, 16, tzinfo=UTC).date()


def _ctx(data_scope: DataScope = DataScope.FACTORY, *, user_id: UUID = OPERATOR_ID) -> AuthContext:
    return AuthContext(
        user_id=user_id,
        name="测试操作员",
        employee_no="TEST01",
        workshop_id=None,
        group_no=None,
        permissions=frozenset({"*"}),
        data_scope=data_scope,
    )


def _svc(session, data_scope: DataScope = DataScope.FACTORY, *, user_id: UUID = OPERATOR_ID):
    return StyleService(session, _ctx(data_scope, user_id=user_id))


# ------------------------------------------------------------------ 夹具


#: 分类码 / 客户码 / 工序号 / 款号一律用随机值：``seed_baseline`` 的用例会
#: **提交**内置数据，固定编码在随机顺序下会撞唯一索引，而那种失败与被测逻辑
#: 毫无关系（docs/10 §2.1：用例零依赖，顺序随机仍通过）。
def _uniq(prefix: str) -> str:
    return f"{prefix}{uuid4().hex[:6].upper()}"


async def _category(session, code: str | None = None, *, active: bool = True) -> ProductCategory:
    resolved = code or _uniq("CAT")
    row = ProductCategory(
        code=resolved,
        name=f"分类{resolved}",
        is_active=active,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    session.add(row)
    await session.flush()
    return row


async def _customer(session, code: str | None = None) -> Customer:
    resolved = code or _uniq("CU")
    row = Customer(
        code=resolved, name=f"客户{resolved}", created_by=OPERATOR_ID, updated_by=OPERATOR_ID
    )
    session.add(row)
    await session.flush()
    return row


async def _operation(session, operation_no: str | None = None, *, active: bool = True) -> Operation:
    operation_no = operation_no or _uniq("OP")
    row = Operation(
        operation_no=operation_no,
        name=f"工序{operation_no}",
        is_active=active,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    session.add(row)
    await session.flush()
    return row


async def _style(
    session,
    style_no: str | None = None,
    *,
    category: ProductCategory | None = None,
    merchandiser_id: UUID | None = None,
    customer: Customer | None = None,
    **overrides: object,
) -> Style:
    style_no = style_no or _uniq("HB")
    payload: dict[str, object] = {
        "style_no": style_no,
        "name": f"款名 {style_no}",
        "category_id": (category or await _category(session)).id,
        "merchandiser_id": merchandiser_id,
        "customer_id": customer.id if customer else None,
        "created_by": OPERATOR_ID,
        "updated_by": OPERATOR_ID,
    }
    payload.update(overrides)
    row = Style(**payload)  # type: ignore[arg-type]
    session.add(row)
    await session.flush()
    return row


async def _size_group(session, codes: tuple[str, ...] | None = None) -> SizeGroup:
    """建一个码表 + 有序尺码。

    ⚠️ 尺码码用随机值：``seed_baseline`` 会**提交**内置的 ``S/M/L/XL (WOMENS)``，
    固定码会撞 ``uq_sizes_code_class``，而这种失败与被测逻辑无关。
    """
    codes = codes or tuple(_uniq("SZ") for _ in range(4))
    group = SizeGroup(
        name=f"女款 {uuid4().hex[:4]}",
        size_class=SizeClass.WOMENS,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    session.add(group)
    await session.flush()
    for order, code in enumerate(codes, start=1):
        size = Size(
            size_code=code,
            name=f"{code}(160/84A)",
            size_class=SizeClass.WOMENS,
            sort_order=order,
            created_by=OPERATOR_ID,
            updated_by=OPERATOR_ID,
        )
        session.add(size)
        await session.flush()
        session.add(SizeGroupItem(size_group_id=group.id, size_id=size.id, sort_order=order))
    await session.flush()
    return group


async def _add_sizes(session, style: Style, codes: tuple[str, ...] | None = None):
    codes = codes or ("S", "M", "L", "XL")
    for order, code in enumerate(codes, start=1):
        session.add(
            StyleSize(
                style_id=style.id,
                style_no=style.style_no,
                size_code=code,
                size_name=f"{code}(160/84A)",
                sort_no=order,
                created_by=OPERATOR_ID,
                updated_by=OPERATOR_ID,
            )
        )
    await session.flush()


# ------------------------------------------------------------ TC-B06/07/08


async def test_tc_b06_lowercase_style_no_is_stored_uppercase(db_session):
    """TC-B06：``hb-2026-0002`` → 存库 ``HB-2026-0002``，唯一性按大写判。

    归一在 **service 入口**做：只靠数据库唯一索引的话，"查重查得到、插不进去"。
    """
    category = await _category(db_session)
    created = await _svc(db_session).create(
        StyleCreate(style_no="  hb-2026-0002  ", name="全棉T恤", category_id=category.id)
    )
    assert created.style_no == "HB-2026-0002"
    stored = (
        await db_session.execute(select(Style).where(Style.style_no == "HB-2026-0002"))
    ).scalar_one()
    assert stored.name == "全棉T恤"


async def test_tc_b07_duplicate_style_no_returns_10001_with_existing_name(db_session):
    """TC-B07：款号重复 → ``10001`` + **回显已有款名**。

    回显而不是只说"已存在"，是因为用户需要知道撞的是哪一款。
    """
    category = await _category(db_session)
    await _style(db_session, "HB-2026-0001", category=category, name="藏青衬衫")

    with pytest.raises(BusinessError) as exc:
        await _svc(db_session).create(
            StyleCreate(style_no="hb-2026-0001", name="另一个款", category_id=category.id)
        )
    assert exc.value.code == ErrorCode.PARAM_INVALID
    assert exc.value.details["existing_name"] == "藏青衬衫"
    assert "藏青衬衫" in exc.value.message


async def test_tc_b07_duplicate_style_no_is_guarded_by_unique_index(ddl_session):
    """TC-B07 分支二：查重只给友好文案，**正确性归唯一索引**（docs/03 §1.5）。

    这里直接打数据库绕过应用层，证明"并发双插"时拦住第二个的确是索引，
    而不是碰巧查重查到了 —— 否则并发一起来的第二个请求会变成 500。
    """
    style_no = f"H{uuid4().hex[:6].upper()}"
    await _style(ddl_session, style_no)
    await ddl_session.commit()
    with pytest.raises(IntegrityError):
        await _style(ddl_session, style_no)
    await ddl_session.rollback()


async def test_tc_b08_missing_category_id_is_rejected_by_schema():
    """TC-B08：建款号不传 ``category_id`` → ``10001``。

    走 Schema 校验（HTTP 层由 ``RequestValidationError`` 处理器统一转 ``10001``），
    所以这里断言的是"模型层就拒绝"，而不是等 service 拿到 ``None``。
    """
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        StyleCreate(style_no="HB-2026-0001", name="全棉T恤")  # type: ignore[call-arg]


async def test_tc_b08_http_returns_10001_when_category_id_missing(client, auth_headers):
    """TC-B08 端到端：缺 ``category_id`` → HTTP 422 + ``code=10001``。"""
    headers = await auth_headers(role="super_admin")
    resp = await client.post(
        "/api/v1/styles",
        json={"style_no": "HB-2026-0001", "name": "全棉T恤"},
        headers=headers,
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == int(ErrorCode.PARAM_INVALID)
    assert any(item["field"] == "category_id" for item in body["details"]["fields"])


# ------------------------------------------------------------------ 建议号


async def test_suggest_style_no_increments_per_customer_and_year(db_session):
    """建议号：同客户同年连续 3 次 → ``0001`` / ``0002`` / ``0003``。"""
    customer = await _customer(db_session, "HB")
    got = []
    for _ in range(3):
        async with unit_of_work(db_session):
            got.append(
                await suggest_style_no(
                    db_session, prefix=customer.code, customer_id=customer.id, year=2026
                )
            )
    assert got == ["HB-2026-0001", "HB-2026-0002", "HB-2026-0003"]


async def test_suggest_style_no_sequences_are_independent_per_customer(db_session):
    """不同客户各从 0001 起（Q-P0-05：序号按客户分组递增）。"""
    hb = await _customer(db_session, "HB")
    nm = await _customer(db_session, "NM")
    async with unit_of_work(db_session):
        first_hb = await suggest_style_no(db_session, prefix=hb.code, customer_id=hb.id, year=2026)
    async with unit_of_work(db_session):
        first_nm = await suggest_style_no(db_session, prefix=nm.code, customer_id=nm.id, year=2026)
    assert first_hb == "HB-2026-0001"
    assert first_nm == "NM-2026-0001"


async def test_suggest_style_no_without_customer_uses_factory_sequence(db_session):
    """``customer_id`` 为空 → 落**全厂序列**（Q-P0-10：款号不该被客户绑死）。"""
    async with unit_of_work(db_session):
        first = await suggest_style_no(db_session, prefix="ST", customer_id=None, year=2026)
    async with unit_of_work(db_session):
        second = await suggest_style_no(db_session, prefix="ST", customer_id=None, year=2026)
    assert first == "ST-2026-0001"
    assert second == "ST-2026-0002"


async def test_suggest_style_no_from_create_is_advisory_only(db_session):
    """``suggest_style_no=true`` 只是**附带**建议号，``style_no`` 仍由用户给。

    业务方 2026-10-03 决策（Q-P0-04）：款号 = 用户自定义。服务端返回的建议号
    一旦变成强制的，用户就再也不能用自己的编号习惯。
    """
    customer = await _customer(db_session, "HB")
    created = await _svc(db_session).create(
        StyleCreate(
            style_no="MY-OWN-CODE",
            name="自定义号",
            category_id=(await _category(db_session)).id,
            customer_id=customer.id,
            suggest_style_no=True,
        )
    )
    assert created.style_no == "MY-OWN-CODE"
    assert created.suggested_style_no == "HB-2026-0001"


# ------------------------------------------------------------------ 尺码


async def test_tc_b09_size_group_brings_out_whole_set_in_order(db_session):
    """TC-B09：选女款模板一键带出 4 条尺码，``sort_no`` 顺序正确。"""
    group = await _size_group(db_session)
    style = await _style(db_session)
    rows = await _svc(db_session).add_sizes(
        style.style_no, StyleSizeCreate(size_group_name=group.name)
    )
    assert [row.sort_no for row in rows] == [1, 2, 3, 4]
    assert len({row.size_code for row in rows}) == 4


async def test_size_create_rejects_both_modes_at_once():
    """单码与整套带出**二选一**：两个都传是请求写错了，Schema 层就拒。"""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        StyleSizeCreate(size_group_name="女款", size_code="S", size_name="S(160/84A)")


async def test_add_single_size_after_group(db_session):
    """§5.3.2：允许在码表基础上增删本款尺码（加一个 4XL）。"""
    group = await _size_group(db_session)
    style = await _style(db_session)
    await _svc(db_session).add_sizes(style.style_no, StyleSizeCreate(size_group_name=group.name))
    extra = _uniq("SZ")
    rows = await _svc(db_session).add_sizes(
        style.style_no, StyleSizeCreate(size_code=extra, size_name="4XL(190/100A)")
    )
    # 响应是**该款全部尺码**（一键带出与单码新增同构），按 sort_no 升序
    assert [row.sort_no for row in rows] == [1, 2, 3, 4, 5]
    added = next(row for row in rows if row.size_code == extra)
    assert added.sort_no == 5


async def test_add_duplicate_size_code_returns_10001(db_session):
    """尺码重复 → ``10001``（``uq_style_sizes_style_size`` 兜底）。"""
    style = await _style(db_session)
    await _add_sizes(db_session, style, ("SQ", "MQ"))
    with pytest.raises(BusinessError) as exc:
        await _svc(db_session).add_sizes(
            style.style_no, StyleSizeCreate(size_code="SQ", size_name="S")
        )
    assert exc.value.code == ErrorCode.PARAM_INVALID


async def test_add_color_duplicate_returns_10001(db_session):
    """色码重复 → ``10001``，并说明撞的是哪个色码。"""
    style = await _style(db_session)
    svc = _svc(db_session)
    await svc.add_colors(
        style.style_no, StyleColorCreate(color_group="主色", color_code="BLK", color_name="黑")
    )
    with pytest.raises(BusinessError) as exc:
        await svc.add_colors(
            style.style_no,
            StyleColorCreate(color_group="主色", color_code="BLK", color_name="黑色"),
        )
    assert exc.value.code == ErrorCode.PARAM_INVALID
    assert "BLK" in exc.value.message


# ------------------------------------------------------------------ 比例


async def _ratio_setup(session) -> tuple[Style, str]:
    style = await _style(session)
    await _add_sizes(session, style, ("S", "M", "L", "XL"))
    return style, style.style_no


async def test_tc_b10_replace_ratios_is_full_replacement(db_session):
    """TC-B10：全量替换 → 旧行不再生效；``hands_total`` 与缺配尺码都返回。

    ⚠️ 实现是「软删 + 复用」而不是物理删（见文末注释），可观察结果与"全删全插"
    一致：查询只看到本次提交的行。
    """
    style, style_no = await _ratio_setup(db_session)
    svc = _svc(db_session)
    await svc.replace_ratios(
        RatioReplaceIn(
            style_no=style_no,
            color_code="BLK",
            version=style.version,
            items=[
                RatioItemIn(size_code="S", ratio=Decimal("1")),
                RatioItemIn(size_code="M", ratio=Decimal("2")),
                RatioItemIn(size_code="L", ratio=Decimal("2")),
                RatioItemIn(size_code="XL", ratio=Decimal("1")),
            ],
        )
    )
    refreshed = await db_session.get(Style, style.id)
    result = await svc.replace_ratios(
        RatioReplaceIn(
            style_no=style_no,
            color_code="BLK",
            version=refreshed.version,
            items=[RatioItemIn(size_code="S", ratio=Decimal("1.5"))],
        )
    )
    assert [(item.size_code, item.ratio) for item in result.items] == [("S", "1.5000")]
    assert result.hands_total == "1.5000"
    # 只配了 S，其余三个尺码缺配 → 提示但不拦（R24）
    assert result.missing_size_codes == ["L", "M", "XL"]
    left = (
        await db_session.execute(
            select(func.count())
            .select_from(StyleColorSizeRatio)
            .where(
                StyleColorSizeRatio.style_no == style_no,
                StyleColorSizeRatio.deleted_at.is_(None),
            )
        )
    ).scalar_one()
    assert left == 1
    # ⚠️ 被移除的三行是**软删**而非物理删（04 §6.2.1 / ADR-0025：运行账号连
    # DELETE 权限都没有，"全删全插"在生产上根本调不通）。它们仍占着
    # ``uq_style_color_size_ratios`` 的键，所以再提交同一个尺码时会"复活"旧行
    all_rows = (
        await db_session.execute(
            select(func.count())
            .select_from(StyleColorSizeRatio)
            .where(StyleColorSizeRatio.style_no == style_no)
        )
    ).scalar_one()
    assert all_rows == 4


async def test_tc_b11_ratio_with_unknown_size_returns_20007(db_session):
    """TC-B11：比例含款号没有的尺码 → ``20007``（硬拦，主数据脏数据）。"""
    style, style_no = await _ratio_setup(db_session)
    with pytest.raises(BusinessError) as exc:
        await _svc(db_session).replace_ratios(
            RatioReplaceIn(
                style_no=style_no,
                color_code="BLK",
                version=style.version,
                items=[RatioItemIn(size_code="5XL", ratio=Decimal("1"))],
            )
        )
    assert exc.value.code == ErrorCode.SIZE_RATIO_SIZE_MISMATCH
    assert exc.value.details["unknown_size_codes"] == ["5XL"]


async def test_ratio_zero_is_rejected_by_schema():
    """``ratio > 0``：DB 有 CHECK，Schema 也拦一道（双保险）。"""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        RatioItemIn(size_code="S", ratio=Decimal("0"))


async def test_ratio_replace_wrong_version_returns_10003(db_session):
    """版本不符 → ``10003``，且不改动任何数据。"""
    style, style_no = await _ratio_setup(db_session)
    with pytest.raises(BusinessError) as exc:
        await _svc(db_session).replace_ratios(
            RatioReplaceIn(
                style_no=style_no,
                color_code="BLK",
                version=style.version + 5,
                items=[RatioItemIn(size_code="S", ratio=Decimal("1"))],
            )
        )
    assert exc.value.code == ErrorCode.OPTIMISTIC_LOCK_CONFLICT
    left = (
        await db_session.execute(
            select(func.count())
            .select_from(StyleColorSizeRatio)
            .where(StyleColorSizeRatio.style_no == style_no)
        )
    ).scalar_one()
    assert left == 0


async def test_ratio_list_returns_all_colors_when_color_omitted(db_session):
    """不传 ``color_code`` → 返回该款全部颜色的比例。"""
    style, style_no = await _ratio_setup(db_session)
    svc = _svc(db_session)
    await svc.replace_ratios(
        RatioReplaceIn(
            style_no=style_no,
            color_code="BLK",
            version=style.version,
            items=[RatioItemIn(size_code="S", ratio=Decimal("1"))],
        )
    )
    refreshed = await db_session.get(Style, style.id)
    await svc.replace_ratios(
        RatioReplaceIn(
            style_no=style_no,
            color_code="WHT",
            version=refreshed.version,
            items=[RatioItemIn(size_code="S", ratio=Decimal("3"))],
        )
    )
    result = await svc.list_ratios(style_no)
    assert {item.color_code for item in result.items} == {"BLK", "WHT"}
    assert result.hands_total == "4.0000"
    # 不指定颜色时不返回"缺配"提示 —— 那个提示只对单个颜色有意义
    assert result.missing_size_codes == []


# ------------------------------------------------------------------ 款号工序


async def _operations_setup(session, style: Style):
    for no in ("01", "02", "03"):
        await _operation(session, no)


async def test_style_operations_require_existing_and_active_operation(db_session):
    """``operation_no`` 必须存在且启用；停用的工序建不了款号配置（modules/01 §7）。"""
    style = await _style(db_session)
    await _operation(db_session, "01")
    await _operation(db_session, "02", active=False)
    with pytest.raises(BusinessError) as exc:
        await _svc(db_session).replace_style_operations(
            style.style_no,
            StyleOperationsReplaceIn(
                version=style.version,
                items=[
                    StyleOperationItemIn(operation_no="01", sequence=1, bundle_qty=Decimal("1")),
                    StyleOperationItemIn(operation_no="02", sequence=2, bundle_qty=Decimal("1")),
                    StyleOperationItemIn(operation_no="99", sequence=3, bundle_qty=Decimal("1")),
                ],
            ),
        )
    assert exc.value.code == ErrorCode.PARAM_INVALID
    assert exc.value.details["invalid_operation_nos"] == ["02", "99"]


async def test_at_most_one_final_operation(db_session):
    """``is_final_operation`` 至多一道（业务确认最后一道都是整烫，必须人工指定）。"""
    style = await _style(db_session)
    await _operations_setup(db_session, style)
    with pytest.raises(BusinessError) as exc:
        await _svc(db_session).replace_style_operations(
            style.style_no,
            StyleOperationsReplaceIn(
                version=style.version,
                items=[
                    StyleOperationItemIn(
                        operation_no="01",
                        sequence=1,
                        bundle_qty=Decimal("1"),
                        is_final_operation=True,
                    ),
                    StyleOperationItemIn(
                        operation_no="02",
                        sequence=2,
                        bundle_qty=Decimal("1"),
                        is_final_operation=True,
                    ),
                ],
            ),
        )
    assert exc.value.code == ErrorCode.PARAM_INVALID
    assert exc.value.details["final_operation_nos"] == ["01", "02"]


async def test_replace_style_operations_sorted_by_sequence(db_session):
    """全量替换后按 ``sequence`` 升序返回，且工序名一并带出。"""
    style = await _style(db_session)
    await _operations_setup(db_session, style)
    result = await _svc(db_session).replace_style_operations(
        style.style_no,
        StyleOperationsReplaceIn(
            version=style.version,
            items=[
                StyleOperationItemIn(operation_no="03", sequence=3, bundle_qty=Decimal("2")),
                StyleOperationItemIn(operation_no="01", sequence=1, bundle_qty=Decimal("1")),
                StyleOperationItemIn(
                    operation_no="02",
                    sequence=2,
                    bundle_qty=Decimal("12"),
                    is_final_operation=True,
                ),
            ],
        ),
    )
    assert [item.operation_no for item in result.items] == ["01", "02", "03"]
    assert result.items[1].operation_name == "工序02"
    # ``numeric(14,3)`` 回读带 3 位小数（docs/05 §3：响应一律字符串）
    assert result.items[1].bundle_qty == "12.000"
    assert result.document_log_id is not None


# ------------------------------------------------------------------ 数据范围


async def test_merchandiser_self_scope_sees_only_own_styles(db_session):
    """跟单（``SELF``）只查得到自己 ``merchandiser_id`` 的款号。

    ⚠️ 断言的是**别人查不到**（``SELF`` 的人看到的不是全厂），只测"本人能看到"
    的实现哪怕把 ``apply_data_scope`` 整个删掉也能通过。
    """
    from app.modules.base.service import StyleQuery
    from tests.factories.user import UserFactory

    category = await _category(db_session)
    mine = await UserFactory.create(db_session, employee_no=_uniq("E"), data_scope=DataScope.SELF)
    other = await UserFactory.create(db_session, employee_no=_uniq("E"), data_scope=DataScope.SELF)
    mine_no = _uniq("HB")
    await _style(db_session, mine_no, category=category, merchandiser_id=mine.id)
    await _style(db_session, _uniq("HB"), category=category, merchandiser_id=other.id)

    items, total = await _svc(db_session, DataScope.SELF, user_id=mine.id).list_styles(StyleQuery())
    assert total == 1
    assert [item.style_no for item in items] == [mine_no]


async def test_merchandiser_accessing_other_style_returns_12002(db_session):
    """越权按款号直查 → ``12002``（INV-8：前端隐藏 ≠ 安全）。"""
    from tests.factories.user import UserFactory

    other = await UserFactory.create(db_session, employee_no=_uniq("E"), data_scope=DataScope.SELF)
    mine = await UserFactory.create(db_session, employee_no=_uniq("E"), data_scope=DataScope.SELF)
    style = await _style(db_session, merchandiser_id=other.id)
    with pytest.raises(BusinessError) as exc:
        await _svc(db_session, DataScope.SELF, user_id=mine.id).get_detail(style.style_no)
    assert exc.value.code == ErrorCode.DATA_SCOPE_DENIED


async def test_merchandiser_cannot_replace_other_style_ratios(db_session):
    """子表写入同样要过数据范围：改不了别人的比例（``12002``）。"""
    from tests.factories.user import UserFactory

    other = await UserFactory.create(db_session, employee_no=_uniq("E"), data_scope=DataScope.SELF)
    mine = await UserFactory.create(db_session, employee_no=_uniq("E"), data_scope=DataScope.SELF)
    style, style_no = await _ratio_setup(db_session)
    assert other.id != mine.id
    with pytest.raises(BusinessError) as exc:
        await StyleService(db_session, _ctx(DataScope.SELF, user_id=mine.id)).replace_ratios(
            RatioReplaceIn(
                style_no=style_no,
                color_code="BLK",
                version=style.version,
                items=[RatioItemIn(size_code="S", ratio=Decimal("1"))],
            )
        )
    assert exc.value.code == ErrorCode.DATA_SCOPE_DENIED


async def test_patch_style_with_stale_version_returns_10003(db_session):
    """PATCH 必传 version 且必须匹配（``10003``）。"""
    style = await _style(db_session)
    with pytest.raises(BusinessError) as exc:
        await _svc(db_session).patch(style.style_no, StylePatch(version=99, name="改名"))
    assert exc.value.code == ErrorCode.OPTIMISTIC_LOCK_CONFLICT


async def test_patch_style_cannot_change_style_no(db_session):
    """``style_no`` 不可改：Schema 里根本没有这个字段。"""
    style = await _style(db_session)
    assert "style_no" not in StylePatch.model_fields
    updated = await _svc(db_session).patch(
        style.style_no, StylePatch(version=style.version, name="新款名")
    )
    assert updated.style_no == style.style_no
    assert updated.name == "新款名"


async def test_style_with_inactive_category_is_rejected(db_session):
    """分类停用后不能建新款号（04 §7.11）。"""
    category = await _category(db_session, "OFF", active=False)
    with pytest.raises(BusinessError) as exc:
        await _svc(db_session).create(
            StyleCreate(style_no="HB-2026-0009", name="X", category_id=category.id)
        )
    assert exc.value.code == ErrorCode.PARAM_INVALID


async def test_create_style_writes_document_log(db_session):
    """每次变更写 ``document_logs``（AGENTS.md §2.2）。"""
    category = await _category(db_session)
    created = await _svc(db_session).create(
        StyleCreate(style_no="HB-2026-0001", name="藏青衬衫", category_id=category.id)
    )
    logs = (
        (
            await db_session.execute(
                select(DocumentLog).where(
                    DocumentLog.doc_type == "Style", DocumentLog.doc_id == created.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert [item.action for item in logs] == ["CREATE"]
    assert logs[0].doc_no == "HB-2026-0001"


# ------------------------------------------------------------------ 模板复制


async def _copy_source(session) -> tuple[Style, Style]:
    """源款：2 道工序 + 档位1 单价（0.350000）+ 档位3 全厂统一价（0.300000）。"""
    source = await _style(session, "HB-2026-0001")
    await _operation(session, "01")
    await _operation(session, "02")
    session.add_all(
        [
            StyleOperation(
                style_id=source.id,
                style_no=source.style_no,
                operation_no="01",
                sequence=1,
                bundle_qty=Decimal("1"),
                created_by=OPERATOR_ID,
                updated_by=OPERATOR_ID,
            ),
            StyleOperation(
                style_id=source.id,
                style_no=source.style_no,
                operation_no="02",
                sequence=2,
                bundle_qty=Decimal("12"),
                is_final_operation=True,
                created_by=OPERATOR_ID,
                updated_by=OPERATOR_ID,
            ),
        ]
    )
    session.add_all(
        [
            OperationRate(
                operation_no="01",
                style_id=source.id,
                style_no=source.style_no,
                effective_from=D1,
                unit_price=Decimal("0.350000"),
                created_by=OPERATOR_ID,
                updated_by=OPERATOR_ID,
            ),
            OperationRate(
                operation_no="02",
                effective_from=D1,
                unit_price=Decimal("0.300000"),
                created_by=OPERATOR_ID,
                updated_by=OPERATOR_ID,
            ),
        ]
    )
    await session.flush()
    target = await _style(session, "HB-2026-0002")
    return source, target


async def test_tc_b12_copy_price_as_is_copies_structure_and_current_price(db_session):
    """TC-B12：模式① 沿用源价 → 工序结构 + 当前有效价整套复制。"""
    source, target = await _copy_source(db_session)
    result = await _svc(db_session).copy_template(
        target.style_no,
        source.style_no,
        TemplateCopyIn(
            copy_mode=TemplateCopyMode.COPY_PRICE_AS_IS,
            conflict_policy=ConflictPolicy.SKIP,
        ),
    )
    assert result.structure_written == 2
    assert result.price_written == 2
    assert [item.operation_no for item in result.operations] == ["01", "02"]
    assert [item.unit_price for item in result.rates] == ["0.350000", "0.300000"]
    assert RATIO_NOT_COPIED in result.messages


async def test_tc_b13_copy_with_ratio_rounds_to_six_places(db_session):
    """TC-B13：模式② ``1.0800 × 0.350000 = 0.378000``（6 位精度，逐项回显）。"""
    source, target = await _copy_source(db_session)
    result = await _svc(db_session).copy_template(
        target.style_no,
        source.style_no,
        TemplateCopyIn(
            copy_mode=TemplateCopyMode.COPY_PRICE_WITH_RATIO,
            conflict_policy=ConflictPolicy.SKIP,
            price_ratio=Decimal("1.0800"),
        ),
    )
    assert result.price_written == 2
    first = next(item for item in result.prices if item.operation_no == "01")
    assert first.source_unit_price == "0.350000"
    assert first.target_unit_price == "0.378000"
    # 档位3（全厂统一价）被搬成了目标款的档位1
    second = next(item for item in result.prices if item.operation_no == "02")
    assert second.rate_source == "OPERATION"
    assert second.target_unit_price == "0.324000"


async def test_copy_price_with_ratio_requires_ratio():
    """模式② 缺 ``price_ratio`` → Schema 就拒（不设系统默认）。"""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TemplateCopyIn(
            copy_mode=TemplateCopyMode.COPY_PRICE_WITH_RATIO,
            conflict_policy=ConflictPolicy.SKIP,
        )


async def test_copy_structure_only_skips_prices(db_session):
    """模式③ 只复制工序结构，不写单价。"""
    source, target = await _copy_source(db_session)
    result = await _svc(db_session).copy_template(
        target.style_no,
        source.style_no,
        TemplateCopyIn(
            copy_mode=TemplateCopyMode.COPY_STRUCTURE_ONLY,
            conflict_policy=ConflictPolicy.SKIP,
        ),
    )
    assert result.structure_written == 2
    assert result.price_written == 0
    assert result.rates == []


async def test_copy_does_not_copy_category_rates(db_session):
    """ADR-0026 §4：**档位 2（分类价）不复制**，且进 ``skipped``。

    复制一款顺手改掉全厂工资口径是静默事故，比不复制危险得多。
    """
    source, target = await _copy_source(db_session)
    category = await _category(db_session, _uniq("DRESS"))
    db_session.add(
        OperationRate(
            operation_no="01",
            style_id=None,
            style_no=None,
            product_category_id=category.id,
            effective_from=D1,
            unit_price=Decimal("0.500000"),
            created_by=OPERATOR_ID,
            updated_by=OPERATOR_ID,
        )
    )
    await db_session.flush()
    result = await _svc(db_session).copy_template(
        target.style_no,
        source.style_no,
        TemplateCopyIn(
            copy_mode=TemplateCopyMode.COPY_PRICE_AS_IS,
            conflict_policy=ConflictPolicy.SKIP,
        ),
    )
    assert any(item["reason"] == "CATEGORY_RATE_NOT_COPIED" for item in result.skipped)
    copied = (
        await db_session.execute(
            select(func.count())
            .select_from(OperationRate)
            .where(
                OperationRate.style_no == target.style_no,
                OperationRate.product_category_id.is_not(None),
            )
        )
    ).scalar_one()
    assert copied == 0


async def test_copy_with_abort_policy_rolls_back_entirely(db_session):
    """``ABORT``：发现冲突整单失败，不写一半（modules/01 §5.1 单事务）。"""
    source, target = await _copy_source(db_session)
    db_session.add(
        StyleOperation(
            style_id=target.id,
            style_no=target.style_no,
            operation_no="01",
            sequence=9,
            bundle_qty=Decimal("1"),
            created_by=OPERATOR_ID,
            updated_by=OPERATOR_ID,
        )
    )
    await db_session.flush()
    with pytest.raises(BusinessError) as exc:
        await _svc(db_session).copy_template(
            target.style_no,
            source.style_no,
            TemplateCopyIn(
                copy_mode=TemplateCopyMode.COPY_PRICE_AS_IS,
                conflict_policy=ConflictPolicy.ABORT,
            ),
        )
    assert exc.value.code == ErrorCode.PARAM_INVALID
    assert exc.value.details["conflicts"]


async def test_copy_with_skip_policy_keeps_target_rows(db_session):
    """``SKIP``：目标已有的工序与价都保留，跳过项进 ``skipped``。"""
    source, target = await _copy_source(db_session)
    db_session.add(
        StyleOperation(
            style_id=target.id,
            style_no=target.style_no,
            operation_no="01",
            sequence=9,
            bundle_qty=Decimal("5"),
            created_by=OPERATOR_ID,
            updated_by=OPERATOR_ID,
        )
    )
    db_session.add(
        OperationRate(
            operation_no="01",
            style_id=target.id,
            style_no=target.style_no,
            effective_from=D1,
            unit_price=Decimal("0.999000"),
            created_by=OPERATOR_ID,
            updated_by=OPERATOR_ID,
        )
    )
    await db_session.flush()
    result = await _svc(db_session).copy_template(
        target.style_no,
        source.style_no,
        TemplateCopyIn(
            copy_mode=TemplateCopyMode.COPY_PRICE_AS_IS,
            conflict_policy=ConflictPolicy.SKIP,
        ),
    )
    assert result.structure_written == 1
    assert result.structure_overwritten == 0
    # 工序结构与单价各记一条跳过原因；同一个 operation_no 会出现两条，
    # 所以断言要按"集合里出现过"而不是按 dict 取值（后者会被后一条覆盖）
    reasons = {item["reason"] for item in result.skipped}
    assert reasons == {"TARGET_HAS_OPERATION", "TARGET_HAS_RATE"}
    kept = (
        await db_session.execute(
            select(OperationRate).where(
                OperationRate.style_no == target.style_no,
                OperationRate.operation_no == "01",
                OperationRate.effective_to.is_(None),
            )
        )
    ).scalar_one()
    assert kept.unit_price == Decimal("0.999000")


async def test_copy_from_missing_source_returns_20001(db_session):
    """源款号不存在 → ``20001``。"""
    target = await _style(db_session, "HB-2026-0002")
    with pytest.raises(BusinessError) as exc:
        await _svc(db_session).copy_template(
            target.style_no,
            "HB-2026-9999",
            TemplateCopyIn(
                copy_mode=TemplateCopyMode.COPY_STRUCTURE_ONLY,
                conflict_policy=ConflictPolicy.SKIP,
            ),
        )
    assert exc.value.code == ErrorCode.BASE_DATA_NOT_FOUND


async def test_copy_from_self_returns_10001(db_session):
    """源与目标同一个款号 → ``10001``（否则会把自己的行锁死）。"""
    style = await _style(db_session)
    with pytest.raises(BusinessError) as exc:
        await _svc(db_session).copy_template(
            style.style_no,
            style.style_no,
            TemplateCopyIn(
                copy_mode=TemplateCopyMode.COPY_STRUCTURE_ONLY,
                conflict_policy=ConflictPolicy.SKIP,
            ),
        )
    assert exc.value.code == ErrorCode.PARAM_INVALID


# ------------------------------------------------------------------ S3 全链路


async def test_s3_full_chain_create_style_operations_prices_and_resolve(db_session):
    """S3 全链路：建款号 → 配 2 个工序 → 设 2 条单价 → ``resolve`` 命中并回档位。"""
    from app.modules.base.service import RateService

    svc = _svc(db_session)
    category = await _category(db_session)
    created = await svc.create(
        StyleCreate(style_no="HB-2026-0001", name="全棉圆领T恤", category_id=category.id)
    )
    await _operation(db_session, "01")
    await _operation(db_session, "02")
    refreshed = await db_session.get(Style, created.id)
    await svc.replace_style_operations(
        created.style_no,
        StyleOperationsReplaceIn(
            version=refreshed.version,
            items=[
                StyleOperationItemIn(operation_no="01", sequence=1, bundle_qty=Decimal("1")),
                StyleOperationItemIn(
                    operation_no="02",
                    sequence=2,
                    bundle_qty=Decimal("12"),
                    is_final_operation=True,
                ),
            ],
        ),
    )
    rates = RateService(db_session, _ctx())
    await rates.set_rate(
        OperationRateCreate(
            operation_no="01",
            style_no=created.style_no,
            unit_price=Decimal("0.350000"),
            effective_from=D1,
        )
    )
    await rates.set_rate(
        OperationRateCreate(
            operation_no="02",
            style_no=created.style_no,
            unit_price=Decimal("0.120000"),
            effective_from=D1,
        )
    )
    hit = await rates.resolve(created.style_no, "01", D2)
    assert hit.unit_price == "0.350000"
    assert hit.rate_source == "STYLE"
    assert hit.effective_from == D1
    assert hit.effective_to is None


# ------------------------------------------------------------ 建议号并发


#: 并发用例里造数的**专属前缀**。清理逻辑按前缀删，只删本文件造的行。
#:
#: ⚠️ 为什么要清理：并发用例必须**真提交**（不提交的话同一个 session 上的语句
#: 会被 PostgreSQL 串行化，测出来的是顺序执行，而顺序执行永远不出问题）。
#: 真提交就意味着数据留在库里，不清理的话 ``test_seed_cli`` 的"内置分类数量
#: == 6"、``test_scope`` 的"通用工序只有一条"这类**全库计数**断言会随机失败 ——
#: 而且失败原因离真正的元凶十万八千里。
CONC_CATEGORY_PREFIX = "CCCAT"
CONC_STYLE_PREFIX = "CCHB"
CONC_OPERATION_PREFIX = "CCOP"
CONC_CUSTOMER_PREFIX = "CCCUS"


@pytest.fixture
async def concurrent_sessions(app_database_url: str, migration_url: str):
    """每个并发任务一条**独立连接**，并在结束后清掉造的数据。

    ⚠️ 不能共用 session：同一连接上的语句会被 PostgreSQL 串行化，那样测出来的
    是"顺序执行"，而顺序执行永远不会有并发问题。

    :param migration_url: 清理要用**迁移账号** —— ``erp_app`` 被 REVOKE 了全部
        DELETE（04 §6.2.1），而下面要删的正是业务数据。
    """
    engine = create_async_engine(app_database_url, pool_pre_ping=True, pool_size=CONCURRENCY + 2)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    sessions = [factory() for _ in range(CONCURRENCY + 4)]
    try:
        yield sessions
    finally:
        for session in sessions:
            await session.close()
        await engine.dispose()
        await _purge_concurrency_rows(migration_url)


async def _purge_concurrency_rows(migration_url: str) -> None:
    """删除本文件并发用例造出来的行（按专属前缀，依赖顺序从子表到父表）。

    ⚠️ 全部用**绑定参数**，不拼字符串：``docs/03 §1.5`` 禁止把变量拼进 SQL，
    哪怕这个变量是模块常量 —— 今天的"安全"取决于没人后来把它改成用户输入。
    """
    style_pattern = f"{CONC_STYLE_PREFIX}%"
    operation_pattern = f"{CONC_OPERATION_PREFIX}%"
    child_tables = ("style_operations", "style_color_size_ratios", "style_sizes", "style_colors")
    statements = [
        (
            "DELETE FROM operation_rates WHERE style_no LIKE :style "
            "OR operation_no LIKE :operation",
            {"style": style_pattern, "operation": operation_pattern},
        ),
        # 表名是模块内写死的字面量集合（不是输入），值一律走绑定参数
        *[
            (f"DELETE FROM {table} WHERE style_no LIKE :style", {"style": style_pattern})  # noqa: S608
            for table in child_tables
        ],
        ("DELETE FROM styles WHERE style_no LIKE :style", {"style": style_pattern}),
        (
            "DELETE FROM operations WHERE operation_no LIKE :operation",
            {"operation": operation_pattern},
        ),
        (
            "DELETE FROM product_categories WHERE code LIKE :category",
            {"category": f"{CONC_CATEGORY_PREFIX}%"},
        ),
        (
            "DELETE FROM customers WHERE code LIKE :customer",
            {"customer": f"{CONC_CUSTOMER_PREFIX}%"},
        ),
        ("DELETE FROM style_no_sequences", {}),
    ]
    engine = create_async_engine(migration_url, pool_pre_ping=True)
    try:
        async with engine.begin() as conn:
            for sql, params in statements:
                await conn.execute(text(sql).bindparams(**params))
    finally:
        await engine.dispose()


# ------------------------------------------------ 款号 / 比例真并发


async def test_concurrent_create_same_style_no_yields_one_row(concurrent_sessions):
    """真并发：``CONCURRENCY`` 个独立连接建同一款号 → 恰好 1 行，其余 ``20002``。

    唯一索引 ``uq_styles_style_no`` 才是兜底（R15：禁止"先查后插"）；应用层的
    ``_find_by_no`` 只负责给友好文案。

    并发下**两条合法的重复检测路径都会出现**：
    - 抢在赢家提交前完成查重的任务 → 都不撞唯一键，随后插入时被唯一索引拦下，
      由 ``_duplicate_style_error`` 翻成 ``20002``（``STYLE_ALREADY_EXISTS``）；
    - 赢家提交后才查重的任务 → ``_find_by_no`` 直接看到已存在行，走预检
      ``10001``（``PARAM_INVALID``，带已有款名）。

    两者都是**重复类业务码**；本用例断言恰 1 成功、其余全部落在这两类里、
    最终恰好 1 行 —— 不允许出现任何未翻译的 ``IntegrityError`` 或别的码。
    """
    style_no = f"{CONC_STYLE_PREFIX}{uuid4().hex[:6].upper()}"
    setup = concurrent_sessions[0]
    category = ProductCategory(
        code=f"{CONC_CATEGORY_PREFIX}{uuid4().hex[:6].upper()}",
        name="并发分类",
        is_active=True,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    setup.add(category)
    await setup.commit()
    category_id = category.id

    barrier = asyncio.Barrier(CONCURRENCY)

    async def _create(index: int) -> str:
        session = concurrent_sessions[index]
        await barrier.wait()
        try:
            await _svc(session).create(
                StyleCreate(style_no=style_no, name=f"并发款{index}", category_id=category_id)
            )
            return "created"
        except BusinessError as exc:
            return f"rejected:{int(exc.code)}"

    results = await asyncio.gather(*(_create(index) for index in range(CONCURRENCY)))
    assert results.count("created") == 1, results
    duplicate_codes = {int(ErrorCode.PARAM_INVALID), int(ErrorCode.STYLE_ALREADY_EXISTS)}
    failures = [item for item in results if item != "created"]
    assert len(failures) == CONCURRENCY - 1, results
    assert {int(item.split(":")[1]) for item in failures} <= duplicate_codes, (
        f"失败必须都是重复类业务码（10001/20002），实际 {results}"
    )

    async with concurrent_sessions[CONCURRENCY + 1] as verify:
        rows = int(
            await verify.scalar(
                select(func.count()).select_from(Style).where(Style.style_no == style_no)
            )
        )
    assert rows == 1, f"库里应恰好 1 行，实际 {rows} 行"


async def test_concurrent_replace_ratios_same_version_is_one_success_one_conflict(
    concurrent_sessions,
):
    """真并发：同一聚合 ``version`` 的两次全量替换 → 1 成功 / 1 ``10003``（TC-B31）。

    ``replace_ratios`` 事务内先 ``SELECT ... FOR UPDATE`` 锁 ``styles`` 聚合行，
    **再**校验 ``version``：后到者拿到锁时先到者已提交、聚合 ``version`` 已 +1，
    校验必然不符 → ``10003``。若反过来"先校验再加锁"，两者会读到同一个 version
    并双双成功，乐观锁等于没有。
    """
    style_no = f"{CONC_STYLE_PREFIX}{uuid4().hex[:6].upper()}"
    setup = concurrent_sessions[0]
    category = ProductCategory(
        code=f"{CONC_CATEGORY_PREFIX}{uuid4().hex[:6].upper()}",
        name="并发分类",
        is_active=True,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    setup.add(category)
    await setup.flush()
    style = Style(
        style_no=style_no,
        name="并发比例款",
        category_id=category.id,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    setup.add(style)
    await setup.flush()
    for order, size_code in enumerate(("S", "M"), start=1):
        setup.add(
            StyleSize(
                style_id=style.id,
                style_no=style_no,
                size_code=size_code,
                size_name=size_code,
                sort_no=order,
                created_by=OPERATOR_ID,
                updated_by=OPERATOR_ID,
            )
        )
    await setup.commit()
    version = style.version

    barrier = asyncio.Barrier(2)

    async def _replace(index: int) -> str:
        session = concurrent_sessions[index]
        await barrier.wait()
        try:
            await _svc(session).replace_ratios(
                RatioReplaceIn(
                    style_no=style_no,
                    color_code="BLK",
                    version=version,
                    items=[
                        RatioItemIn(size_code="S", ratio=Decimal("1")),
                        RatioItemIn(size_code="M", ratio=Decimal("2")),
                    ],
                )
            )
            return "ok"
        except BusinessError as exc:
            return f"rejected:{int(exc.code)}"

    results = await asyncio.gather(_replace(1), _replace(2))
    assert results.count("ok") == 1, results
    assert results.count(f"rejected:{int(ErrorCode.OPTIMISTIC_LOCK_CONFLICT)}") == 1, results

    async with concurrent_sessions[CONCURRENCY + 1] as verify:
        live = (
            (
                await verify.execute(
                    select(StyleColorSizeRatio).where(
                        StyleColorSizeRatio.style_no == style_no,
                        StyleColorSizeRatio.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
    assert {row.size_code for row in live} == {"S", "M"}, "最终必须是**其中一份完整比例**"


# ------------------------------------------------ 款号详情 / 列表 / 建议号


async def test_get_detail_returns_colors_sizes_operations_and_current_rates(db_session):
    """款号详情一次返回四块：款号 + 色组 + 尺码 + 工序 + 现行价（设计稿 §4.5）。

    少任何一块前端就得再发一次请求，而"新建裁剪单"页面四个区块要一起渲染。
    """
    from app.modules.base.schemas import OperationRateCreate
    from app.modules.base.service import RateService

    style = await _style(db_session)
    op = await _operation(db_session)
    svc = _svc(db_session)
    await svc.add_colors(
        style.style_no,
        StyleColorCreate(color_group="主色", color_code="BLK", color_name="黑"),
    )
    await _add_sizes(db_session, style, ("S", "M"))
    await svc.replace_style_operations(
        style.style_no,
        StyleOperationsReplaceIn(
            version=(await db_session.get(Style, style.id)).version,
            items=[
                StyleOperationItemIn(
                    operation_no=op.operation_no, sequence=1, bundle_qty=Decimal("1")
                )
            ],
        ),
    )
    await RateService(db_session, _ctx()).set_rate(
        OperationRateCreate(
            operation_no=op.operation_no,
            style_no=style.style_no,
            unit_price=Decimal("0.350000"),
            effective_from=D1,
        )
    )
    detail = await svc.get_detail(style.style_no)
    assert detail.style.style_no == style.style_no
    assert [item.color_code for item in detail.colors] == ["BLK"]
    assert [item.size_code for item in detail.sizes] == ["S", "M"]
    assert [item.operation_no for item in detail.operations] == [op.operation_no]
    assert [item.unit_price for item in detail.current_rates] == ["0.350000"]


async def test_get_detail_for_missing_style_returns_20001(db_session):
    with pytest.raises(BusinessError) as exc:
        await _svc(db_session).get_detail(_uniq("NOPE"))
    assert exc.value.code == ErrorCode.BASE_DATA_NOT_FOUND


async def test_list_styles_supports_keyword_and_filters(db_session):
    """列表支持模糊关键字 + 客户 / 分类 / 启用状态筛选。"""
    from app.modules.base.service import StyleQuery

    category = await _category(db_session)
    other_category = await _category(db_session)
    customer = await _customer(db_session)
    wanted = _uniq("HB")
    await _style(db_session, wanted, category=category, customer=customer)
    await _style(db_session, _uniq("HB"), category=other_category)

    svc = _svc(db_session)
    items, total = await svc.list_styles(StyleQuery(q=wanted))
    assert total == 1
    assert items[0].style_no == wanted
    assert items[0].customer_name is not None

    _items, by_category = await svc.list_styles(StyleQuery(category_id=other_category.id))
    assert by_category == 1
    _items, by_customer = await svc.list_styles(StyleQuery(customer_id=customer.id))
    assert by_customer == 1
    _items, active_only = await svc.list_styles(StyleQuery(is_active=True))
    assert active_only == 2

    with pytest.raises(BusinessError) as exc:
        await svc.list_styles(StyleQuery(page=0))
    assert exc.value.code == ErrorCode.PARAM_INVALID
    with pytest.raises(BusinessError) as exc:
        await svc.list_styles(StyleQuery(sort_order="up"))
    assert exc.value.code == ErrorCode.PARAM_INVALID


async def test_list_style_options_rejects_oversize(db_session):
    """候选接口 ``size`` 上限 20（docs/05 §9.5.2）。"""
    with pytest.raises(BusinessError) as exc:
        await _svc(db_session).list_options(None, size=500)
    assert exc.value.code == ErrorCode.PARAM_INVALID


async def test_list_style_options_orders_by_last_used_at_desc_nulls_last(db_session):
    """常用优先（05 §9.5.2）：``last_used_at DESC NULLS LAST``。

    ⚠️ 用过一次的款排在没用过的前面，而"没用过"的之间保持款号升序（稳定）。
    """
    fresh, stale, never = _uniq("HB"), _uniq("HB"), _uniq("HB")
    await _style(db_session, fresh, last_used_at=datetime(2026, 9, 1, tzinfo=UTC))
    await _style(db_session, stale, last_used_at=datetime(2026, 1, 1, tzinfo=UTC))
    await _style(db_session, never)
    options = await _svc(db_session).list_options(None, size=10)
    assert [item.value for item in options] == [fresh, stale, never]
    assert all(item.disabled is False for item in options)


async def test_suggest_style_no_on_create_uses_factory_prefix_without_customer(db_session):
    """无客户建款 + ``suggest_style_no`` → 落**全厂序列**（Q-P0-10）。"""
    created = await _svc(db_session).create(
        StyleCreate(
            style_no=_uniq("HB"),
            name="无客户款",
            category_id=(await _category(db_session)).id,
            suggest_style_no=True,
        )
    )
    assert created.suggested_style_no is not None
    assert created.suggested_style_no.endswith("-0001")


async def test_suggest_style_no_with_unknown_customer_returns_20001(db_session):
    """``customer_id`` 指向不存在的客户 → ``20001``，而不是拼个前缀蒙过去。"""
    with pytest.raises(BusinessError) as exc:
        await _svc(db_session).create(
            StyleCreate(
                style_no=_uniq("HB"),
                name="X",
                category_id=(await _category(db_session)).id,
                customer_id=uuid4(),
                suggest_style_no=True,
            )
        )
    assert exc.value.code == ErrorCode.BASE_DATA_NOT_FOUND


async def test_patch_style_without_changes_returns_10001(db_session):
    """只传 version、没传任何要改的字段 → ``10001``（否则会静默成功）。"""
    style = await _style(db_session)
    with pytest.raises(BusinessError) as exc:
        await _svc(db_session).patch(style.style_no, StylePatch(version=style.version))
    assert exc.value.code == ErrorCode.PARAM_INVALID


# ----------------------------------------------------- 尺码 / 工序边界


async def test_add_sizes_with_unknown_group_returns_20001(db_session):
    """码表名不存在 → ``20001``（提示去先建码表）。"""
    style = await _style(db_session)
    with pytest.raises(BusinessError) as exc:
        await _svc(db_session).add_sizes(
            style.style_no, StyleSizeCreate(size_group_name=_uniq("GRP"))
        )
    assert exc.value.code == ErrorCode.BASE_DATA_NOT_FOUND


async def test_add_sizes_with_empty_group_returns_20001(db_session):
    """码表存在但**没有成员** → ``20001``（提示先给码表加尺码）。"""
    empty = SizeGroup(
        name=_uniq("空码表"),
        size_class=SizeClass.WOMENS,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    db_session.add(empty)
    await db_session.flush()
    style = await _style(db_session)
    with pytest.raises(BusinessError) as exc:
        await _svc(db_session).add_sizes(
            style.style_no, StyleSizeCreate(size_group_name=empty.name)
        )
    assert exc.value.code == ErrorCode.BASE_DATA_NOT_FOUND


async def test_replace_ratios_over_100_rows_is_rejected_twice(db_session):
    """比例一次最多 100 行（modules/01 §6）：**Schema 与 service 双保险**。

    Schema 层拦的是正常路径；service 层那道是防绕过 Schema 的直调（未来的 Excel
    导入 / CLI 批处理）。两层都要有测试，否则第二层就是死代码。
    """
    from pydantic import ValidationError

    style, style_no = await _ratio_setup(db_session)
    items = [RatioItemIn(size_code=f"SZ{index}", ratio=Decimal("1")) for index in range(101)]
    with pytest.raises(ValidationError):
        RatioReplaceIn(style_no=style_no, color_code="BLK", version=style.version, items=items)
    # 绕过 Schema 直调 service
    payload = RatioReplaceIn.model_construct(
        style_no=style_no, color_code="BLK", version=style.version, items=items
    )
    with pytest.raises(BusinessError) as exc:
        await _svc(db_session).replace_ratios(payload)
    assert exc.value.code == ErrorCode.PARAM_INVALID
    assert exc.value.details["max_items"] == 100


async def test_replace_ratios_rejects_duplicated_size_code(db_session):
    """同一次提交里尺码重复 → ``10001``（整批不落库）。"""
    style, style_no = await _ratio_setup(db_session)
    with pytest.raises(BusinessError) as exc:
        await _svc(db_session).replace_ratios(
            RatioReplaceIn(
                style_no=style_no,
                color_code="BLK",
                version=style.version,
                items=[
                    RatioItemIn(size_code="S", ratio=Decimal("1")),
                    RatioItemIn(size_code="S", ratio=Decimal("2")),
                ],
            )
        )
    assert exc.value.details["duplicated_size_codes"] == ["S"]


async def test_replace_style_operations_empty_returns_10001(db_session):
    """款号工序不能是空集 —— 全量替换传空数组等于"把工序路线清空"。"""
    style = await _style(db_session)
    with pytest.raises(BusinessError) as exc:
        await _svc(db_session).replace_style_operations(
            style.style_no, StyleOperationsReplaceIn(version=style.version, items=[])
        )
    assert exc.value.code == ErrorCode.PARAM_INVALID


async def test_replace_style_operations_over_500_rows_is_rejected_twice(db_session):
    """款号工序一次最多 500 行：Schema 与 service 双保险（同上）。"""
    from pydantic import ValidationError

    style = await _style(db_session)
    items = [
        StyleOperationItemIn(operation_no=f"OP{index}", sequence=index + 1, bundle_qty=Decimal("1"))
        for index in range(501)
    ]
    with pytest.raises(ValidationError):
        StyleOperationsReplaceIn(version=style.version, items=items)
    payload = StyleOperationsReplaceIn.model_construct(version=style.version, items=items)
    with pytest.raises(BusinessError) as exc:
        await _svc(db_session).replace_style_operations(style.style_no, payload)
    assert exc.value.details["max_items"] == 500


async def test_replace_style_operations_rejects_duplicate_operation(db_session):
    style = await _style(db_session)
    await _operation(db_session, "DUP")
    with pytest.raises(BusinessError) as exc:
        await _svc(db_session).replace_style_operations(
            style.style_no,
            StyleOperationsReplaceIn(
                version=style.version,
                items=[
                    StyleOperationItemIn(operation_no="DUP", sequence=1, bundle_qty=Decimal("1")),
                    StyleOperationItemIn(operation_no="DUP", sequence=2, bundle_qty=Decimal("1")),
                ],
            ),
        )
    assert exc.value.details["duplicated_operation_nos"] == ["DUP"]


async def test_replace_style_operations_updates_existing_rows_and_soft_deletes_rest(db_session):
    """二次全量替换：同工序**就地更新**，消失的工序**软删**（不是物理删）。

    这是"不物理删"实现的另一半：键还在（唯一索引不是部分索引），所以
    下次再提交同一个工序号会**复活**那一行，而不是插出第二行。
    """
    style = await _style(db_session)
    await _operation(db_session, "A")
    await _operation(db_session, "B")
    svc = _svc(db_session)
    await svc.replace_style_operations(
        style.style_no,
        StyleOperationsReplaceIn(
            version=style.version,
            items=[
                StyleOperationItemIn(operation_no="A", sequence=1, bundle_qty=Decimal("1")),
                StyleOperationItemIn(operation_no="B", sequence=2, bundle_qty=Decimal("1")),
            ],
        ),
    )
    refreshed = await db_session.get(Style, style.id)
    result = await svc.replace_style_operations(
        style.style_no,
        StyleOperationsReplaceIn(
            version=refreshed.version,
            items=[StyleOperationItemIn(operation_no="A", sequence=5, bundle_qty=Decimal("6"))],
        ),
    )
    assert [(item.operation_no, item.sequence) for item in result.items] == [("A", 5)]
    live = (
        await db_session.execute(
            select(func.count())
            .select_from(StyleOperation)
            .where(StyleOperation.style_no == style.style_no, StyleOperation.deleted_at.is_(None))
        )
    ).scalar_one()
    assert live == 1
    dead = (
        await db_session.execute(
            select(func.count())
            .select_from(StyleOperation)
            .where(
                StyleOperation.style_no == style.style_no, StyleOperation.deleted_at.is_not(None)
            )
        )
    ).scalar_one()
    assert dead == 1


async def test_list_style_operations_can_filter_by_is_piecework(db_session):
    style = await _style(db_session)
    await _operation(db_session, "P1")
    await _operation(db_session, "N1")
    svc = _svc(db_session)
    await svc.replace_style_operations(
        style.style_no,
        StyleOperationsReplaceIn(
            version=style.version,
            items=[
                StyleOperationItemIn(operation_no="P1", sequence=1, bundle_qty=Decimal("1")),
                StyleOperationItemIn(
                    operation_no="N1",
                    sequence=2,
                    bundle_qty=Decimal("1"),
                    is_piecework=False,
                ),
            ],
        ),
    )
    rows = await svc.list_style_operations(style.style_no, is_piecework=True)
    assert [item.operation_no for item in rows] == ["P1"]


async def test_list_style_operations_hides_disabled_dictionary_entry(db_session):
    """工序字典停用后款号配置默认不展示，但仍能查（``include_inactive=True``）。"""
    style = await _style(db_session)
    op = await _operation(db_session, "OFF", active=False)
    db_session.add(
        StyleOperation(
            style_id=style.id,
            style_no=style.style_no,
            operation_no=op.operation_no,
            sequence=1,
            created_by=OPERATOR_ID,
            updated_by=OPERATOR_ID,
        )
    )
    await db_session.flush()
    svc = _svc(db_session)
    assert await svc.list_style_operations(style.style_no) == []
    assert len(await svc.list_style_operations(style.style_no, include_inactive=True)) == 1


# ---------------------------------------------------- 复制：OVERWRITE / MERGE


async def test_copy_with_overwrite_replaces_existing_structure_and_price(db_session):
    """``OVERWRITE``：工序结构就地改写，单价走「关旧区间 + 插新区间」。

    ⚠️ 单价那一步**绝不** ``UPDATE unit_price``（R11），所以旧行仍在库里。
    """
    source, target = await _copy_source(db_session)
    db_session.add(
        StyleOperation(
            style_id=target.id,
            style_no=target.style_no,
            operation_no="01",
            sequence=9,
            bundle_qty=Decimal("5"),
            created_by=OPERATOR_ID,
            updated_by=OPERATOR_ID,
        )
    )
    db_session.add(
        OperationRate(
            operation_no="01",
            style_id=target.id,
            style_no=target.style_no,
            effective_from=D1,
            unit_price=Decimal("0.999000"),
            created_by=OPERATOR_ID,
            updated_by=OPERATOR_ID,
        )
    )
    await db_session.flush()
    result = await _svc(db_session).copy_template(
        target.style_no,
        source.style_no,
        TemplateCopyIn(
            copy_mode=TemplateCopyMode.COPY_PRICE_AS_IS,
            conflict_policy=ConflictPolicy.OVERWRITE,
        ),
    )
    assert result.structure_overwritten == 1
    assert result.structure_written == 1
    assert result.price_overwritten == 1
    structure = next(item for item in result.operations if item.operation_no == "01")
    assert structure.bundle_qty == "1.000"
    rows = (
        (
            await db_session.execute(
                select(OperationRate)
                .where(
                    OperationRate.style_no == target.style_no,
                    OperationRate.operation_no == "01",
                )
                .order_by(OperationRate.effective_from)
            )
        )
        .scalars()
        .all()
    )
    # 旧行仍在（价格未被改写），只是被关了区间
    assert [str(row.unit_price) for row in rows] == ["0.999000", "0.350000"]
    assert rows[0].effective_to is not None
    assert rows[1].effective_to is None


async def test_copy_with_merge_keeps_existing_price_and_only_fills_missing(db_session):
    """``MERGE``：工序结构合并；单价**一律不覆盖**，目标缺的工序才补价。"""
    source, target = await _copy_source(db_session)
    db_session.add(
        OperationRate(
            operation_no="01",
            style_id=target.id,
            style_no=target.style_no,
            effective_from=D1,
            unit_price=Decimal("0.999000"),
            created_by=OPERATOR_ID,
            updated_by=OPERATOR_ID,
        )
    )
    await db_session.flush()
    result = await _svc(db_session).copy_template(
        target.style_no,
        source.style_no,
        TemplateCopyIn(
            copy_mode=TemplateCopyMode.COPY_PRICE_AS_IS,
            conflict_policy=ConflictPolicy.MERGE,
        ),
    )
    assert result.price_overwritten == 0
    reasons = {item["reason"] for item in result.skipped}
    assert reasons == {"TARGET_HAS_RATE"}
    # 工序结构照常合并进来（MERGE 只对**单价**"一律不覆盖"，工序仍要补齐）
    assert {item.operation_no for item in result.operations} == {"01", "02"}
    prices = {item.operation_no: item.unit_price for item in result.rates}
    assert prices["01"] == "0.999000"
    assert prices["02"] == "0.300000"


async def test_copy_from_stopped_source_returns_20001(db_session):
    """源款号已停用 → ``20001``（停用款不能当模板来源）。"""
    source = await _style(db_session, is_active=False)
    target = await _style(db_session)
    with pytest.raises(BusinessError) as exc:
        await _svc(db_session).copy_template(
            target.style_no,
            source.style_no,
            TemplateCopyIn(
                copy_mode=TemplateCopyMode.COPY_STRUCTURE_ONLY,
                conflict_policy=ConflictPolicy.SKIP,
            ),
        )
    assert exc.value.code == ErrorCode.BASE_DATA_NOT_FOUND


async def test_copy_from_other_merchandiser_style_returns_12002(db_session):
    """跟单不能拿别人的款号当模板来源（``12002``）。"""
    from tests.factories.user import UserFactory

    other = await UserFactory.create(db_session, employee_no=_uniq("E"), data_scope=DataScope.SELF)
    mine = await UserFactory.create(db_session, employee_no=_uniq("E"), data_scope=DataScope.SELF)
    source = await _style(db_session, merchandiser_id=other.id)
    target = await _style(db_session, merchandiser_id=mine.id)
    with pytest.raises(BusinessError) as exc:
        await _svc(db_session, DataScope.SELF, user_id=mine.id).copy_template(
            target.style_no,
            source.style_no,
            TemplateCopyIn(
                copy_mode=TemplateCopyMode.COPY_STRUCTURE_ONLY,
                conflict_policy=ConflictPolicy.SKIP,
            ),
        )
    assert exc.value.code == ErrorCode.DATA_SCOPE_DENIED
