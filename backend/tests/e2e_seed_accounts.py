"""E2E 的**账号与库复位**（`tests/e2e_seed.py` 的前半段，ADR-0030 拆分）。

## 为什么拆出来

`e2e_seed.py` 原本一个文件同时装两件毫无关系的事：

| | 本模块（账号 / 复位） | `e2e_seed.py`（业务数据） |
| --- | --- | --- |
| 动什么 | `users` / `user_roles` / 打菲四表 / 裁剪四表 / 款号 / 布批 | 款号、工序、单价、车间、物料、已审核裁剪单 |
| 用什么 | **裸 SQL + 迁移账号**（应用账号没有 DELETE 权限，ADR-0029） | **service 层**（权限校验、状态机都在这一层） |
| 跑在哪 | 一轮跑一次，开头 | 一轮跑一次，结尾 |

两者的**安全模型不同**（一个拿迁移账号物理删、一个走业务校验），混在一个文件里时
「哪些语句是有意用裸 SQL 的」这件事看不出来 —— 而这恰恰是最需要一眼看清的地方。
拆开后两边各自都不到 400 行（ADR-0030 的硬线）。

## 三个账号位的分工

| 工号 | 角色 | 为什么需要 |
| --- | --- | --- |
| `E2EADMIN` | super_admin | 建单、提交、打印 |
| `E2EAUDITOR` | super_admin | **审核**——`10005`「制单人不能审核自己开的打菲单」 |
| `ADMIN`（基线 seed 建） | super_admin | 不用：它 `must_change_password=true`，登录后被守卫重定向去改密 |

## ⚠️ 这个模块会往库里**物理删**一行批数据

所以 :func:`refuse_non_e2e_database` 在任何写操作之前先按**库名**拦一道：
只加载了 ``/tmp/test.env`` 时它会一声不吭地连上单测库 `garment_erp_test`，
留下没人清理的残留，并把闸门 3 挂在与业务毫无关系的断言上
（真踩过：`test_scope.py::test_soft_delete_filter_is_appended_automatically`
断言 `['D001']`，实际 `['E2EADMIN', 'D001']`）。
"""

import os
import sys
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.common.enums import DataScope
from app.common.permissions_registry import permission_codes
from app.core.permissions import AuthContext
from app.core.security import hash_password
from app.modules.base.models import (
    Material,
    MaterialStock,
    OperationRate,
    Style,
    StyleOperation,
)
from app.modules.bundling.models import (
    Bundle,
    BundleLabelPrint,
    BundlingOrder,
    BundlingOrderLine,
)
from app.modules.cutting.models import (
    CuttingOrder,
    CuttingOrderLine,
    CuttingOrderLineColor,
    CuttingOrderSizeLine,
    CuttingOutput,
)

#: E2E 专用管理员。⚠️ **不复用 ADMIN**：它的 ``must_change_password=true``，
#: 登录后会被守卫重定向到改密页 —— E2E 里那不是被测行为，只会卡住流程。
E2E_EMPLOYEE_NO = "E2EADMIN"
E2E_ADMIN_NAME = "E2E 管理员"
#: E2E 专用**审核人**账号（与上面同一个口令、同一个角色）。
#:
#: ⚠️ **必须是另一个人**：审核有「制单人不能审自己的单」这条铁律
#:   （错误码 ``10005``，模块/03 §4.1）。只建一个账号的话，打菲 E2E 的
#:   「建单 → 提交 → 审核」永远停在审核那一步，而失败信息是
#:   ``制单人不能审核自己开的打菲单`` —— 看着像业务规则没实现，
#:   实际是测试数据少一个人。
#:   这条规则本身要在**后端单测**里验（那里可以构造两个 user）；
#:   E2E 这边只需要「用两个账号把全流程走一遍」，顺带就验了它。
E2E_APPROVER_NO = "E2EAUDITOR"
E2E_APPROVER_NAME = "E2E 审核员"


@dataclass(frozen=True)
class BusinessKeys:
    """:func:`clean` 要清的 E2E 业务数据主键。

    ⚠️ **由调用方传进来**而不是在这里定义常量：这些码的真源是
      ``tests/e2e_seed.py``（它还要拿去建数据），而那个文件 import 本模块 ——
      反过来再 import 回去就是循环导入，只能往这个方向单向依赖。
    """

    style_no: str
    material_code: str
    dye_lot_no: str


async def prepare(conn: AsyncConnection, keys: BusinessKeys) -> AuthContext | None:
    """一轮 E2E 的**库复位 + 账号就位**，返回管理员的 `AuthContext`。

    ⚠️ **任何一步拿不到就直接返回 None**，由调用方决定退出码 —— 不要在这里
      吞掉：少一个账号时打菲 E2E 会停在「审核」那一步，报错是
      「制单人不能审核自己开的打菲单」，看不出是种子少建了一个人。
    """
    await clean(conn, keys)
    ctx = await ensure_admin(conn)
    approver_id = await ensure_user(conn, E2E_APPROVER_NO, E2E_APPROVER_NAME)
    if ctx is None or approver_id is None:
        return None
    # 解锁放在事务外：Redis 不是事务的一部分，提交前清等于白清（回滚了就白清）
    await clear_login_lock()
    return ctx


async def clear_login_lock() -> None:
    """清掉 E2E 账号的登录失败计数（Redis）。

    ⚠️ **必须有这一步**：登录连续 5 次失败会把账号锁 15 分钟
    （`auth:login_fail:<工号>`，`10004`）。一次失败的 E2E 之后，**后面每一轮**
    都会在登录那一步失败，而报错是「账号已锁定」—— 与「上一轮是我的口令错了」
    这个真因隔了三层。所以「准备数据」要顺带把账号解锁，否则一次手滑要等 15 分钟。
    ⚠️ **两个 E2E 账号都要清**：审核人是另一个人（`10005`），
    它同样会连错、同样会被锁，清一个漏一个只是把故障挪到后面一步。
    """
    url = os.environ.get("REDIS_URL", "")
    if url == "":
        return
    try:
        import redis.asyncio as redis_asyncio

        client = redis_asyncio.from_url(url)
        try:
            await client.delete(
                f"auth:login_fail:{E2E_EMPLOYEE_NO}", f"auth:login_fail:{E2E_APPROVER_NO}"
            )
        finally:
            await client.aclose()
    except Exception as exc:  # noqa: BLE001 —— 解锁失败不该让整个 seed 失败
        print(f"WARN: 清登录锁失败（不影响 E2E 本身）：{exc}", file=sys.stderr)


async def ensure_admin(conn: AsyncConnection) -> AuthContext | None:
    """建一个**不用改密**的 E2E 管理员，返回它的 `AuthContext`。

    ⚠️ 走 `users` 表的 INSERT 而不是调 ``POST /users``：那要 token，
      而 token 又要先有账号 —— 鸡生蛋。E2E 种子是「数据库的准备」，
      这一步必须能独立完成（docs/10 §4 的精神：造数据不经过业务界面）。
    """
    user_id = await ensure_user(conn, E2E_EMPLOYEE_NO, "E2E 管理员")
    if user_id is None:
        return None
    return AuthContext(
        user_id=user_id,
        name="E2E 管理员",
        employee_no=E2E_EMPLOYEE_NO,
        workshop_id=None,
        group_no=None,
        permissions=frozenset(permission_codes()),
        data_scope=DataScope.FACTORY,
    )


async def ensure_user(conn: AsyncConnection, employee_no: str, name: str) -> UUID | None:
    """建（或复位）一个 E2E 专用账号，绑 ``super_admin`` 角色，返回它的 id。

    ⚠️ **每次都重置口令**（不只是「没有才建」）：这是 E2E **专用**账号，
      而上一轮的 E2E_ADMIN_PASSWORD 与这一轮不同是常事（临时试一个口令、
      或者 CI 上换了 secret）。不重置的表现是「昨天能跑今天全挂在登录」，
      而失败信息是 `11003 工号或口令错误` —— 看不出是自己上一轮改的。
    """
    password = os.environ.get("E2E_ADMIN_PASSWORD", "")
    if not password:
        print("FATAL: 未设置 E2E_ADMIN_PASSWORD", file=sys.stderr)
        return None
    existing = (
        await conn.execute(
            text("SELECT id FROM users WHERE employee_no = :no"), {"no": employee_no}
        )
    ).scalar_one_or_none()
    if existing is not None:
        await conn.execute(
            text(
                "UPDATE users SET password_hash = :hash, must_change_password = false, "
                "is_active = true, updated_at = now() WHERE id = :id"
            ),
            {"id": existing, "hash": hash_password(password)},
        )
        return UUID(str(existing))
    user_id = (
        await conn.execute(
            text(
                "INSERT INTO users (employee_no, name, password_hash, data_scope, "
                "is_active, must_change_password, created_by, updated_by, version) "
                # ⚠️ `created_by` 是 **UUID** 不是工号字符串 —— 传工号会得到
                #   `invalid input for query argument: invalid UUID`，而报错里完全
                #   看不出是这个字段（与 `seed_baseline` 同款写法）
                "VALUES (:no, :name, :password_hash, 'FACTORY', true, false, "
                "gen_random_uuid(), gen_random_uuid(), 1) RETURNING id"
            ),
            {"no": employee_no, "name": name, "password_hash": hash_password(password)},
        )
    ).scalar_one()
    role_id = (
        await conn.execute(text("SELECT id FROM roles WHERE code = 'super_admin'"))
    ).scalar_one()
    await conn.execute(
        # ⚠️ 表名是 `user_roles` 不是 `role_assignments`（与 `seed_baseline` 一致）：
        #   写错时 SQLAlchemy 抛 `relation does not exist`，而报错完全看不出
        #   是种子脚本自己写错了表名
        text(
            "INSERT INTO user_roles (user_id, role_id) VALUES (:user_id, :role_id) "
            "ON CONFLICT DO NOTHING"
        ),
        {"user_id": user_id, "role_id": role_id},
    )
    return UUID(str(user_id))


async def clean(conn: AsyncConnection, keys: "BusinessKeys") -> None:
    """清掉上一轮 E2E 数据。

    ⚠️ **必须用迁移账号**（``erp_ddl``）跑，**不能**用应用账号：
       ``operation_rates`` 按 ADR-0029 **没给应用账号 DELETE 权限** ——
       试过用 ``erp_app`` 清，拿到的是
       ``ProgrammingError: permission denied for table operation_rates``，
       而报错完全看不出「是你的账号权限不够，不是 SQL 写错了」。
       顺带说：这个报错本身就是 ADR-0029 生效的证据（应用账号真的删不掉单价历史）。
    """
    # ⚠️ ★ 顺序即外键依赖。两段，且**顺序不能反**：
    #
    #   ① 打菲单 → 裁剪单：打菲反过来挂在裁剪单上
    #      （`bundling_orders.source_cutting_order_id`、
    #      `bundling_order_lines.cutting_size_line_id`）。上一轮打菲 E2E 真的建过单、
    #      审过核、打过标签，于是删裁剪尺码明细行时撞
    #      `ForeignKeyViolationError: ... violates foreign key constraint
    #      "fk_bundling_order_lines_cutting_size_line"`——
    #      而 seed 是 E2E 的**第一步**，报错指向裁剪表，与「上一轮我建的那张打菲单还没删」
    #      毫无关系，于是这一轮全挂在 globalSetup 上、一条用例都没跑。
    #
    #   ② 裁剪单 → 布批/物料：裁剪行的 `cutting_order_lines.stock_id` 指向那批布，
    #      直接删布批会撞 `fk_cutting_order_lines_stock`
    #      （E2E-01 跑完之后 seed 就再也跑不动了，就是踩的这条）。
    #
    #   ⚠️ 用**物理删**而不是软删：这是测试数据复位，而且应用账号对这几张表**没有**
    #   DELETE 权限（ADR-0029），跑 seed 用的本来就是迁移账号。
    cutting_ids = [
        UUID(str(row))
        for row in (
            await conn.execute(text("SELECT id FROM cutting_orders WHERE doc_no LIKE 'CT-%'"))
        ).scalars()
    ] or [UUID(int=0)]
    # ⚠️ 打菲单用 `doc_no LIKE 'BD-%'` 而不是「挂在本轮那些裁剪单下的」：
    #   两者的差别只在「上一轮的打菲单引用的裁剪单被手工改过 doc_no」这种情形，
    #   但那恰恰是最需要被清掉的情形 —— 按来源裁剪单筛会漏掉它，
    #   于是它一直挂在库里，下次 seed 照样撞同一个外键。
    bundling_ids = [
        UUID(str(row))
        for row in (
            await conn.execute(text("SELECT id FROM bundling_orders WHERE doc_no LIKE 'BD-%'"))
        ).scalars()
    ] or [UUID(int=0)]
    for statement in (
        # ⚠️ 打菲侧自下而上：`bundle_label_prints` → `bundles` → `bundling_order_lines`
        #   → `bundling_orders`。四条 FK 全是 `ondelete=RESTRICT`，
        #   少删任何一层都是硬失败（不会静默留残根）。
        delete(BundleLabelPrint).where(BundleLabelPrint.doc_id.in_(bundling_ids)),
        delete(Bundle).where(Bundle.doc_id.in_(bundling_ids)),
        delete(BundlingOrderLine).where(BundlingOrderLine.doc_id.in_(bundling_ids)),
        delete(BundlingOrder).where(BundlingOrder.id.in_(bundling_ids)),
        delete(CuttingOrderSizeLine).where(
            CuttingOrderSizeLine.line_color_id.in_(
                select(CuttingOrderLineColor.id).where(
                    CuttingOrderLineColor.line_id.in_(
                        select(CuttingOrderLine.id).where(CuttingOrderLine.doc_id.in_(cutting_ids))
                    )
                )
            )
        ),
        delete(CuttingOrderLineColor).where(
            CuttingOrderLineColor.line_id.in_(
                select(CuttingOrderLine.id).where(CuttingOrderLine.doc_id.in_(cutting_ids))
            )
        ),
        delete(CuttingOrderLine).where(CuttingOrderLine.doc_id.in_(cutting_ids)),
        delete(CuttingOrder).where(CuttingOrder.id.in_(cutting_ids)),
        # ⚠️ `cutting_outputs` **必须先于 `styles` 删**：它按 (style_id, 色, 尺码)
        #   登记、有 `fk_cutting_outputs_style` 指向 `styles`。不删的话最后那句
        #   `DELETE FROM styles` 会撞 `ForeignKeyViolationError: ... violates
        #   foreign key constraint "fk_cutting_outputs_style"` ——
        #   而报错指向款号表，与「打菲余量登记还在」毫无关系。
        delete(CuttingOutput).where(CuttingOutput.style_no == keys.style_no),
        delete(MaterialStock).where(MaterialStock.dye_lot_no == keys.dye_lot_no),
        delete(Material).where(Material.code == keys.material_code),
        delete(OperationRate).where(OperationRate.style_no == keys.style_no),
        delete(StyleOperation).where(StyleOperation.style_no == keys.style_no),
        delete(Style).where(Style.style_no == keys.style_no),
    ):
        await conn.execute(statement)


def refuse_non_e2e_database(*urls: str) -> None:
    """拒绝往**不是 E2E 库**的库上跑本脚本（连清都别清）。

    ⚠️ 这条守卫是被真事故逼出来的：`run()` 的 ``E2E_DATABASE_URL or
      DATABASE_URL_MIGRATION`` 兜底意味着，只加载了 ``/tmp/test.env``
      （**没**加载 ``/tmp/e2e.env``）时它会**一声不吭地连上单测库
      ``garment_erp_test``**，把 E2E 账号写进去。单测每个用例都在事务里回滚，
      **看不见**这些残留，于是下一轮闸门 3 挂在
      ``test_scope.py::test_soft_delete_filter_is_appended_automatically`` 上 ——
      断言是 ``['D001']``，实际 ``['E2EADMIN', 'D001']``：

      - 报错的文件与「E2E 种子跑错了库」之间隔了三层，完全看不出因果；
      - 更糟的是它**污染单测库**这件事本身没人会发现 —— 闸门 3 只会说「6 个用例挂了」。

      所以在**任何写操作之前**就按库名拦下来，并说清是哪条命令跑错了。
    """
    for url in urls:
        if not url:
            continue
        if "e2e" not in url.rsplit("/", 1)[-1].lower():
            print(
                f"FATAL: 拒绝执行 —— 目标库 {url.rsplit('/', 1)[-1]} 不是 E2E 专用库。\n"
                "  本脚本会写用户/角色并**物理删**一批行，跑在单测库或开发库上会\n"
                "  留下没人清理的残留数据，并把闸门 3 挂在与业务毫无关系的断言上。\n"
                "  正确做法：先 `set -a && . /tmp/e2e.env && set +a`（或等价地导出\n"
                "  E2E_DATABASE_URL=…/garment_erp_e2e）再跑本脚本。",
                file=sys.stderr,
            )
            raise SystemExit(1)
