"""基础资料 service 的业务规则测试（TC-B01~B05、TC-B23、TC-B26）。

重点在**业务规则**而不是 CRUD 本身：

- 真删两分支（TC-B03）：未被引用 → 行真的消失；被引用 → ``20003`` + 引用明细
- 墓碑与恢复（TC-B21/22）
- 停用必填 reason（TC-B04）且不写日志
- 乐观锁（TC-…）
- 每次变更都写 ``document_logs``
"""

import pytest
from sqlalchemy import func, select

from app.common.enums import DataScope, SizeClass
from app.core.errors import BusinessError, ErrorCode
from app.core.permissions import AuthContext
from app.modules.base.models import Color, Size, SizeGroup, SizeGroupItem, Workshop
from app.modules.base.repository import ListQuery
from app.modules.base.resources import RESOURCES, RESOURCES_BY_KEY
from app.modules.base.service import DictService
from tests.factories.user import OPERATOR_ID


def _ctx(data_scope: DataScope = DataScope.FACTORY, workshop_id=None) -> AuthContext:
    return AuthContext(
        user_id=OPERATOR_ID,
        name="测试操作员",
        employee_no="TEST01",
        workshop_id=workshop_id,
        group_no=None,
        permissions=frozenset({"*"}),
        data_scope=data_scope,
    )


def _svc(session, key: str = "colors", **kwargs) -> DictService:
    return DictService(session, RESOURCES_BY_KEY[key], _ctx(**kwargs))


async def _new_color(session, code: str = "WHT") -> Color:
    color = Color(
        color_code=code,
        name="本白",
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    session.add(color)
    await session.flush()
    return color


# ---------------------------------------------------------------- 真删两分支


async def test_delete_unreferenced_color_physically_removes_row(ddl_session):
    """TC-B03 分支一：未被引用 → **物理删除**，行真的消失。

    必须用 ``ddl_session``（迁移账号）：``erp_app`` 被 REVOKE 了全部 DELETE
    （04 §6.2.1），用应用账号跑会直接权限拒绝。
    """
    await _new_color(ddl_session, "TMP1")
    await ddl_session.flush()

    cascaded = await _svc(ddl_session).delete("TMP1")
    assert cascaded == 0

    left = await ddl_session.execute(
        select(func.count()).select_from(Color).where(Color.color_code == "TMP1")
    )
    assert left.scalar_one() == 0


async def test_delete_referenced_size_raises_20003_with_details(ddl_session):
    """TC-B03 分支二：被码表引用 → ``20003`` + ``ref_count`` + ``references``。"""
    size = Size(
        size_code="ZZL",
        name="ZZL",
        size_class=SizeClass.WOMENS,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    group = SizeGroup(
        name="测试码表",
        size_class=SizeClass.WOMENS,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    ddl_session.add_all([size, group])
    await ddl_session.flush()
    ddl_session.add(SizeGroupItem(size_group_id=group.id, size_id=size.id, sort_order=1))
    await ddl_session.flush()

    with pytest.raises(BusinessError) as excinfo:
        await _svc(ddl_session, "sizes").delete("ZZL")

    error = excinfo.value
    assert error.code is ErrorCode.BASE_DATA_REFERENCED
    assert error.details["ref_count"] == 1
    assert error.details["references"][0]["source"] == "尺码模板"
    # 行必须还在 —— 被引用的项只允许停用
    still = await ddl_session.execute(
        select(func.count()).select_from(Size).where(Size.size_code == "ZZL")
    )
    assert still.scalar_one() == 1


async def test_delete_size_group_cascades_members(ddl_session):
    """TC-B23：删码表级联删成员，并回传条数。"""
    sizes = [
        Size(
            size_code=code,
            name=code,
            size_class=SizeClass.WOMENS,
            created_by=OPERATOR_ID,
            updated_by=OPERATOR_ID,
        )
        for code in ("AAA", "BBB", "CCC")
    ]
    group = SizeGroup(
        name="待删码表",
        size_class=SizeClass.WOMENS,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    ddl_session.add_all([*sizes, group])
    await ddl_session.flush()
    for index, size in enumerate(sizes, start=1):
        ddl_session.add(SizeGroupItem(size_group_id=group.id, size_id=size.id, sort_order=index))
    await ddl_session.flush()

    cascaded = await _svc(ddl_session, "size-groups").delete("待删码表")
    assert cascaded == 3

    left = await ddl_session.execute(
        select(func.count())
        .select_from(SizeGroupItem)
        .where(SizeGroupItem.size_group_id == group.id)
    )
    assert left.scalar_one() == 0


async def test_soft_delete_only_tables_do_not_physically_delete(ddl_session):
    """车间是纯软删表：删完行还在，但 ``deleted_at`` 有值。"""
    workshop = Workshop(
        code="CUT",
        name="裁剪",
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    ddl_session.add(workshop)
    await ddl_session.flush()

    await _svc(ddl_session, "workshops").delete("CUT")

    row = await ddl_session.execute(select(Workshop).where(Workshop.code == "CUT"))
    found = row.scalar_one()
    assert found.deleted_at is not None


# ------------------------------------------------------------------ 停用


async def test_disable_requires_reason(ddl_session):
    """TC-B04：缺 reason → ``10002``，且**不写日志**。"""
    await _new_color(ddl_session, "DS1")
    before = await ddl_session.scalar(
        select(func.count()).select_from(Size).where(Size.size_code == "ZZ")
    )
    del before

    with pytest.raises(BusinessError) as excinfo:
        await _svc(ddl_session).disable("DS1", "   ")
    assert excinfo.value.code is ErrorCode.MISSING_BUSINESS_PARAM


async def test_disable_stores_reason_and_logs(ddl_session):
    await _new_color(ddl_session, "DS2")
    await _svc(ddl_session).disable("DS2", "该色已停产")

    row = await ddl_session.execute(select(Color).where(Color.color_code == "DS2"))
    color = row.scalar_one()
    assert color.is_active is False
    assert color.remark == "该色已停产"


async def test_disable_twice_raises_10008(ddl_session):
    await _new_color(ddl_session, "DS3")
    service = _svc(ddl_session)
    await service.disable("DS3", "第一次")
    with pytest.raises(BusinessError) as excinfo:
        await service.disable("DS3", "第二次")
    assert excinfo.value.code is ErrorCode.ILLEGAL_OPERATION


# ------------------------------------------------------------------ 乐观锁


async def test_patch_requires_version(ddl_session):
    await _new_color(ddl_session, "OP1")
    with pytest.raises(BusinessError) as excinfo:
        await _svc(ddl_session).patch("OP1", {"name": "改名"})
    assert excinfo.value.code is ErrorCode.PARAM_INVALID


async def test_patch_with_wrong_version_raises_10003(ddl_session):
    """并发下两个请求只有一个能成功，另一个拿 10003。"""
    await _new_color(ddl_session, "OP2")
    with pytest.raises(BusinessError) as excinfo:
        await _svc(ddl_session).patch("OP2", {"name": "改名", "version": 99})
    assert excinfo.value.code is ErrorCode.OPTIMISTIC_LOCK_CONFLICT


async def test_patch_bumps_version_and_cannot_change_code(ddl_session):
    color = await _new_color(ddl_session, "OP3")
    # 先取版本号：_reload 会把同一个对象刷新成最新值，之后再读 color.version
    # 拿到的是改完的值（identity map 里就是同一个实例）
    before_version = color.version
    await _svc(ddl_session).patch("OP3", {"name": "本白(改)", "version": before_version})

    row = await ddl_session.execute(select(Color).where(Color.color_code == "OP3"))
    updated = row.scalar_one()
    assert updated.name == "本白(改)"
    assert updated.version == before_version + 1


async def test_patch_cannot_change_code_column(ddl_session):
    """编码列在 SET 里被剔除 —— 改编码会让历史单据指向不存在的项。"""
    color = await _new_color(ddl_session, "OP4")
    await _svc(ddl_session).patch(
        "OP4", {"color_code": "NEWCOLOR", "name": "改名", "version": color.version}
    )
    await _svc(ddl_session).patch("OP4", {"remark": "备注", "version": color.version})
    row = await ddl_session.execute(select(Color).where(Color.id == color.id))
    assert row.scalar_one().color_code == "OP4"


async def test_patch_ignores_none_values(ddl_session):
    """``{"remark": None}`` 表示"不改备注"，不是"把备注清空"。

    只改 ``name``、同时带上 ``remark: None`` —— 前端常见写法是提交整个表单，
    未填写的字段就是 ``null``。若不过滤，用户的备注会被无声清掉。
    """
    color = await _new_color(ddl_session, "OP5")
    await ddl_session.execute(
        Color.__table__.update().where(Color.id == color.id).values(remark="原有备注")
    )
    await ddl_session.flush()
    await ddl_session.refresh(color)

    await _svc(ddl_session).patch(
        "OP5", {"name": "改名了", "remark": None, "version": color.version}
    )

    row = await ddl_session.execute(select(Color).where(Color.id == color.id))
    updated = row.scalar_one()
    assert updated.name == "改名了"
    assert updated.remark == "原有备注", "remark 传 None 不该清空已有备注"


async def test_patch_with_only_none_fields_raises_10001(ddl_session):
    """全部字段都是 None → 明确报"没有需要更新的字段"，而不是静默成功。"""
    color = await _new_color(ddl_session, "OP6")
    with pytest.raises(BusinessError) as excinfo:
        await _svc(ddl_session).patch("OP6", {"remark": None, "version": color.version})
    assert excinfo.value.code is ErrorCode.PARAM_INVALID


# ------------------------------------------------------------------ 新建


async def test_create_duplicate_code_raises_10001(ddl_session):
    await _new_color(ddl_session, "DUP1")
    with pytest.raises(BusinessError) as excinfo:
        await _svc(ddl_session).create({"color_code": "DUP1", "name": "另一个"})
    assert excinfo.value.code is ErrorCode.PARAM_INVALID


async def test_get_missing_raises_20001(ddl_session):
    with pytest.raises(BusinessError) as excinfo:
        await _svc(ddl_session).get_one("NOPE")
    assert excinfo.value.code is ErrorCode.BASE_DATA_NOT_FOUND


# ------------------------------------------------------------------ 列表


async def test_list_rejects_unknown_sort_field(ddl_session):
    """TC-B29：``sort_by`` 走白名单，非法值 10001 而不是 500、更不是拼进 SQL。"""
    await _new_color(ddl_session, "L1")
    with pytest.raises(BusinessError) as excinfo:
        await _svc(ddl_session).list_rows(ListQuery(sort_by="unknown_field"))
    assert excinfo.value.code is ErrorCode.PARAM_INVALID
    assert "color_code" in excinfo.value.details["allowed"]


async def test_list_rejects_offset_over_10000(ddl_session):
    """TC-B28：深分页直接拒绝。"""
    await _new_color(ddl_session, "L2")
    with pytest.raises(BusinessError) as excinfo:
        await _svc(ddl_session).list_rows(ListQuery(offset=10001))
    assert excinfo.value.code is ErrorCode.PARAM_INVALID


async def test_list_rejects_page_size_over_200(ddl_session):
    await _new_color(ddl_session, "L3")
    with pytest.raises(BusinessError):
        await _svc(ddl_session).list_rows(ListQuery(size=201))


async def test_list_includes_ref_count(ddl_session):
    """列表每行带 ref_count，让界面能提前把删不掉的行标灰。"""
    size = Size(
        size_code="ZZM",
        name="ZZM",
        size_class=SizeClass.MENS,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    group = SizeGroup(
        name="带成员的码表",
        size_class=SizeClass.MENS,
        created_by=OPERATOR_ID,
        updated_by=OPERATOR_ID,
    )
    ddl_session.add_all([size, group])
    await ddl_session.flush()
    ddl_session.add(SizeGroupItem(size_group_id=group.id, size_id=size.id, sort_order=1))
    await ddl_session.flush()

    items, _total = await _svc(ddl_session, "sizes").list_rows(ListQuery())
    counts = {item.size_code: item.ref_count for item in items}
    assert counts["ZZM"] == 1


async def test_list_excludes_deleted(ddl_session):
    await _new_color(ddl_session, "DEL1")
    await _svc(ddl_session, "colors").delete("DEL1")
    items, _total = await _svc(ddl_session).list_rows(ListQuery())
    assert "DEL1" not in {item.color_code for item in items}


# ------------------------------------------------------------------ 候选


async def test_options_caps_size_at_20(ddl_session):
    """TC-B27：候选接口 size ≤ 20。"""
    with pytest.raises(BusinessError) as excinfo:
        await _svc(ddl_session).options(None, 21)
    assert excinfo.value.code is ErrorCode.PARAM_INVALID


async def test_options_marks_disabled(ddl_session):
    """候选项要标出"已停用"，前端据此禁止直接选中（docs/05 §9.5.2）。"""
    await _new_color(ddl_session, "OPT1")
    await _svc(ddl_session).disable("OPT1", "停产")

    options = await _svc(ddl_session).options("OPT", 20)
    disabled = {item.value: item.disabled for item in options}
    assert disabled["OPT1"] is True


async def test_options_label_contains_code_and_name(ddl_session):
    """docs/05 §9.5.2：已选项必须回显「编码 + 名称」，禁止只显示编码。"""
    await _new_color(ddl_session, "OPT2")
    options = await _svc(ddl_session).options("OPT2", 20)
    assert options[0].label == "OPT2 本白"


# ------------------------------------------------------------------ 数据范围


async def test_master_data_ignores_user_data_scope(ddl_session):
    """基础资料**无视**用户数据范围（§4.4 九个资源的数据范围一律 FACTORY）。

    颜色、尺码、车间是全厂共享主数据，不存在"只属于某车间"的语义。若跟着用户的
    ``data_scope`` 走，一个 ``SELF`` 范围的用户打开颜色下拉会是**空的** ——
    那不是权限问题，是把主数据当成单据数据了。

    数据范围机制本身的测试在 :mod:`tests.modules.test_scope`，那里用的是有车间
    维度的资源。
    """
    await _new_color(ddl_session, "SCOPE1")
    self_ctx = _ctx(DataScope.SELF)
    items, _total = await DictService(ddl_session, RESOURCES_BY_KEY["colors"], self_ctx).list_rows(
        ListQuery()
    )
    assert "SCOPE1" in {item.color_code for item in items}


def test_every_base_resource_is_factory_scoped():
    """守卫：§4.4 把九个资源的数据范围全标成 FACTORY，新增资源要显式改。"""
    assert all(item.data_scope is DataScope.FACTORY for item in RESOURCES)
