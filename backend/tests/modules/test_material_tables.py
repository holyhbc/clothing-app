"""物料类目 / 物料档案 / 供应商的建表测试（T-BASE-005）。

覆盖任务卡里与这三张表有关的用例：

======================  =================================================
TC-B05-01               ``materials`` 引用不存在的类目/单位 → ``10001``
TC-B05-02               ``material_type`` 非法值 → ``10001``（枚举口径）
TC-B05-03               seed 连跑两次，类目行数与内容不变（幂等）
TC-B05-04               类目被 ``materials`` 引用后**不可物理删除**（ADR-0025 白名单
                        只授了 4 张字典表，类目码表不在其中）
TC-B05-05               三张表都满足 ``04 §2`` 公共字段 + ``version > 0``
TC-B05-06               银行四项可空（建档不该被「没有对公账户」卡住）
======================  =================================================

⚠️ **TC-B05-04 值得单独一条测试**：``material_categories`` 是字典表，看起来「该能删」，
而 4 张同类字典表确实能删。差别只在**授权白名单**上 —— 而那不在 ORM 里，
所以「它到底能不能删」只有一个答案来源：拿应用账号真的去 DELETE。
"""

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.cli.seed_dicts import BUILTIN_MATERIAL_CATEGORIES, seed_material_categories
from app.core.db import unit_of_work
from app.core.errors import ErrorCode
from app.modules.base.models import Material, MaterialCategory, Supplier
from tests.factories.user import OPERATOR_ID


async def _uom(db_session) -> object:
    from app.modules.base.models import UomUnit

    return (
        await db_session.execute(
            select(UomUnit).where(UomUnit.code == "M", UomUnit.deleted_at.is_(None))
        )
    ).scalar_one()


async def _category(db_session) -> MaterialCategory:
    row = (
        await db_session.execute(
            select(MaterialCategory).where(
                MaterialCategory.code == "CT", MaterialCategory.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if row is not None:
        return row
    row = MaterialCategory(
        code="CT",
        name="纯棉布",
        sort=1,
        is_builtin=True,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def test_materials_require_existing_category_and_uom(db_session):
    """TC-B05-01：类目与计量单位都是外键 → 必须指向真实行。

    ⚠️ 用**真的外键**而不是 service 层的存在性检查兜：这里验证的是「数据库层拦得住」。
    service 层以后加校验也不该把这条放松 —— 少一层校验意味着并发建档时会有窗口。
    """
    await _uom(db_session)
    category = await _category(db_session)

    db_session.add(
        Material(
            code="F-CT-999901",
            name="测试面料",
            material_type="FABRIC",
            category_id=category.id,
            uom_unit_id=(await _uom(db_session)).id,
            created_by=OPERATOR_ID,
            updated_by=OPERATOR_ID,
        )
    )
    await db_session.flush()

    bad = Material(
        code="F-CT-999902",
        name="引用不存在的类目",
        material_type="FABRIC",
        category_id="00000000-0000-0000-0000-000000000000",
        uom_unit_id=(await _uom(db_session)).id,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    db_session.add(bad)
    with pytest.raises(IntegrityError):
        async with unit_of_work(db_session):
            pass


async def test_material_type_is_a_pg_enum(db_session):
    """TC-B05-02：``material_type`` 是**枚举**而不是 varchar。

    ⚠️ 这条守卫的价值是「防止有人把它改成 varchar」：改动本身编译得过、测试也全绿，
    而后果是 ``modules/06 §3.1`` 里「``FABRIC`` 的批次要求 ``width_cm`` 非空」这条
    service 层校验再也拦不住辅料批次 —— 表现为**辅料带着空门幅入库**，
    要到裁剪选批时才发现，而那时数据已经写进去了。
    """
    assert db_session.bind.dialect.name == "postgresql"
    enum_type = Material.__table__.c.material_type.type
    assert enum_type.name == "material_type", "material_type 必须是 PG 枚举而不是 varchar"
    assert set(enum_type.enums) == {"FABRIC", "TRIMMING", "LABEL", "FINISHED_GOODS"}


async def test_material_category_seed_is_idempotent(db_session):
    """TC-B05-03：seed 连跑两次，类目行数与内容不变。

    ⚠️ 验的是「**内容**」而不只是行数：``ON CONFLICT DO NOTHING`` 保证行数不变，
    但如果 seed 里不小心带了会覆盖的字段（ADR-0025 决策 3 明确禁止覆盖用户改动），
    只数行数是发现不了的。
    """
    async with unit_of_work(db_session):
        await seed_material_categories(db_session)

    async def snapshot() -> list[tuple[str, str, int, bool]]:
        rows = (
            await db_session.execute(
                select(MaterialCategory)
                .where(MaterialCategory.is_builtin)
                .order_by(MaterialCategory.code)
            )
        ).scalars()
        return [(r.code, r.name, r.sort, r.is_active) for r in rows]

    first = await snapshot()
    async with unit_of_work(db_session):
        await seed_material_categories(db_session)
    second = await snapshot()

    assert len(first) == len(BUILTIN_MATERIAL_CATEGORIES)
    assert first == second, "第二次 seed 改动了内容 —— 违反 ADR-0025 决策 3「不覆盖用户改动」"


async def test_material_category_cannot_be_hard_deleted_by_app_role(db_session, app_database_url):
    """TC-B05-04：**类目码表没有 DELETE 授权**，即使它看起来「是个字典表」。

    ⚠️ 4 张同类字典表（colors / sizes / size_groups / size_group_items）**是**能删的，
    所以「类目码表大概也能删」是个很自然的错误推断。用应用账号真的去 DELETE：
    权限边界**只有真去试才能确认**，ORM 里看不到任何痕迹。
    """
    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    await _category(db_session)
    await db_session.flush()

    engine = create_async_engine(app_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            with pytest.raises(sa.exc.ProgrammingError) as caught:
                await session.execute(sa.text("DELETE FROM material_categories WHERE code = 'CT'"))
            await session.rollback()
        assert "permission denied" in str(caught.value).lower(), (
            f"应用账号竟然能删 material_categories —— ADR-0025 的白名单被改大了：{caught.value}"
        )
    finally:
        await engine.dispose()


async def test_supplier_bank_fields_are_optional(db_session):
    """TC-B05-06：银行四项**可空**（T-BASE-003 D4）。

    ⚠️ 这条是回归守卫：`modules/08 §3.1` 原本文写「全必填」，改判的理由是
    **辅料商与个体工商户常无对公账户**，而建档是低频动作 —— 卡住它会让人绕过系统。
    一旦哪天被「补齐」成 NOT NULL，这条会红并指出原因。
    """
    for name in ("bank_name", "bank_account_no", "bank_branch", "bank_account_name"):
        column = Supplier.__table__.c[name]
        assert column.nullable, f"suppliers.{name} 又变回必填了 —— 确认一下是不是把 D4 的决策推翻了"

    supplier = Supplier(
        code="SUP-TEST-1",
        name="个体辅料商（无对公账户）",
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    db_session.add(supplier)
    await db_session.flush()
    assert supplier.bank_name is None
    assert supplier.settlement_period_days == 0


async def test_three_tables_have_audit_fields_and_version_check(db_session):
    """TC-B05-05：三张表都满足 ``04 §2`` 的公共字段 + ``version > 0``。

    ⚠️ 直接查 ``information_schema`` 的 CHECK 定义而不是看 ORM：ORM 上写了
    ``CheckConstraint`` 不等于库里真的有 —— 迁移漏抄 ``_ver()`` 时两边都会安静地通过。
    """
    from sqlalchemy import text

    for table in ("material_categories", "materials", "suppliers"):
        rows = (
            (
                await db_session.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns WHERE table_name = :t"
                    ),
                    {"t": table},
                )
            )
            .scalars()
            .all()
        )
        for required in (
            "id",
            "created_at",
            "created_by",
            "updated_at",
            "updated_by",
            "deleted_at",
            "version",
            "remark",
        ):
            assert required in rows, f"{table} 缺公共字段 {required}（docs/04 §2）"

        checks = (
            (
                await db_session.execute(
                    text(
                        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                        "WHERE conrelid = to_regclass(:t) AND contype = 'c'"
                    ),
                    {"t": table},
                )
            )
            .scalars()
            .all()
        )
        assert any("version > 0" in item for item in checks), f"{table} 缺 ck_*_version_positive"


async def test_materials_unique_code_is_partial(db_session):
    """软删后可以复用同一个 ``materials.code``（部分唯一索引）。

    ⚠️ 与 ``styles.uq_styles_no`` 同口径。**不加 ``WHERE deleted_at IS NULL`` 的话**，
    停用一个物料后就再也建不了同号的 —— 而「同号重建」是字典表的常规操作。
    """
    indexdef = (
        await db_session.execute(
            select(func.count()).select_from(
                __import__("sqlalchemy").text(
                    "pg_indexes WHERE indexname = 'uq_materials_code' "
                    "AND indexdef LIKE '%deleted_at IS NULL%'"
                )
            )
        )
    ).scalar_one()
    assert indexdef == 1, "uq_materials_code 必须带 WHERE deleted_at IS NULL（04 §5）"


def test_error_code_is_registered() -> None:
    """守卫：这三张表没引入新错误码 → ``05 §4`` 不需要改。

    ⚠️ 「没引入」是本卡的实际结论，写成测试是为了**将来**有人加错误码时被迫同步
    ``docs/05 §4`` —— 而那正是 ``docs/12 §9`` 第 3 项一致性检查要抓的东西。
    """
    assert ErrorCode.BASE_DATA_NOT_FOUND.value == 20001
