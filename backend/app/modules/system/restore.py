"""「恢复内置库」的接口层（docs/04 §7.4 规则第 4 条 / ADR-0025 §决策 3）。

## 为什么需要它

用户真删了内置行之后（业务方确认「删掉就是不要了」），``seed_baseline`` 绝不能
把它们悄悄插回来；但用户点「恢复内置库」时又应该能找回。``restore_builtin`` 原本
**只有 CLI**（``app/cli/restore_builtin.py``），而那个文件自己的注释写着
"用户点『恢复内置库』" —— 说明这一层设计时就预期要有界面，只是没做。

## 怎么把 ``AsyncSession`` 变成 ``AsyncConnection``

CLI 的函数签名写的是 ``AsyncConnection``（它们从 ``create_async_engine().connect()`` 拿）。
而接口层手里是 ``AsyncSession``。**不要**去改 CLI 的签名 —— 那会把三个 CLI 模块
都拖进 diff，而且 ``AsyncSession.execute`` 返回的 ``Result`` 没有 ``rowcount``，
CLI 里那几行就会变成类型错误（实测 mypy 直接报 8 处）。

正确做法是 ``await session.connection()``：拿到的是**这个 session 正在用的那条
``AsyncConnection``**，执行裸 SQL 与 session 共用同一个事务，而 ``unit_of_work``
的 commit 照常生效。既不改 CLI 签名，也不用 ``cast`` 骗类型检查器。

## 为什么不重写一份逻辑

``restore()`` 已经在 CLI 里跑着，并且带着两条**顺序敏感**的规则：
先解除墓碑、再 seed（反了会出现"记录说恢复了、数据其实没回来"）。重写一遍
必然会出现两份实现漂移。这里直接把 CLI 的函数接上。

⚠️ CLI 那些函数只用到 ``execute`` / ``scalar``，**没有** ``commit`` / ``begin`` /
``rollback`` —— 所以直接把它们要的那条连接喂进去即可，事务边界仍由
:func:`app.core.db.unit_of_work` 统一管（AGENTS §2.1：service 层之外禁止 commit）。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.cli.restore_builtin import clear_tombstones, list_missing, list_missing_dicts
from app.cli.seed_baseline import seed_permissions, seed_role_permissions, seed_roles
from app.cli.seed_dicts import DICT_DOC_TYPES, seed_dict_library
from app.core.db import unit_of_work
from app.core.errors import BusinessError, ErrorCode
from app.core.permissions import AuthContext

#: 字典表的展示顺序（固定，不随查询结果变化 —— 前端要按这个顺序渲染）。
DICT_TABLES: tuple[str, ...] = tuple(DICT_DOC_TYPES)


@dataclass(frozen=True, slots=True)
class BuiltinMissing:
    """缺失（= 被真删且未恢复）的内置项清单。"""

    permissions: list[str]
    roles: list[str]
    dicts: dict[str, list[str]]

    def total(self) -> int:
        return (
            len(self.permissions)
            + len(self.roles)
            + sum(len(codes) for codes in self.dicts.values())
        )


@dataclass(frozen=True, slots=True)
class BuiltinRestoreResult:
    restored_permissions: int
    restored_roles: int
    restored_role_bindings: int
    restored_dicts: int
    dict_detail: dict[str, int]


class BuiltinRestoreService:
    """恢复内置库。**全部走接口白名单**，没有第二条真删路径（AGENTS §2.1）。"""

    def __init__(self, session: AsyncSession, ctx: AuthContext | None = None) -> None:
        self.session = session
        # CLI 调用时为 None（没有用户上下文）；接口层一定传真实 ctx，
        # 否则 document_logs 里的 operator 是随机 UUID —— 「谁恢复的」就查不出来了。
        self.ctx = ctx

    def _operator(self) -> dict[str, str | None]:
        if self.ctx is None:
            return {}
        return {"operator_id": str(self.ctx.user_id), "operator_name": self.ctx.name}

    async def _conn(self) -> AsyncConnection:
        """把 session 换成 CLI 期望的 ``AsyncConnection``（**同一个事务**）。"""
        return await self.session.connection()

    async def list_missing(self) -> BuiltinMissing:
        conn = await self._conn()
        missing_permissions, missing_roles = await list_missing(conn)
        missing_dicts = await list_missing_dicts(conn)
        return BuiltinMissing(
            permissions=list(missing_permissions),
            roles=list(missing_roles),
            dicts={table: list(missing_dicts.get(table, [])) for table in DICT_TABLES},
        )

    async def restore(
        self,
        *,
        permissions: bool = False,
        roles: bool = False,
        dicts: bool = True,
    ) -> BuiltinRestoreResult:
        """恢复内置项。

        :param permissions: 恢复缺失的权限点
        :param roles: 恢复缺失的内置角色（并重新绑权限）
        :param dicts: 恢复字典内置库。**默认开** —— 这是用户在界面上点的那个按钮

        ⚠️ 三类都没勾时要明确报错而不是"什么都不做还返回成功" ——
        用户点了按钮却看到"已恢复 0 条"，会以为是 bug。
        """
        if not (permissions or roles or dicts):
            raise BusinessError(ErrorCode.MISSING_BUSINESS_PARAM, "请至少勾选一类要恢复的内置数据")

        before = await self.list_missing()

        async with unit_of_work(self.session):
            conn = await self._conn()
            # 顺序敏感：先解除墓碑、再 seed（CLI 的 restore() 里已注明反了的症状，
            # 这里不复述 —— 但**不能**改成"只调 seed_dict_library"，
            # 那会导致墓碑仍在、本次 seed 跳过、只留下一条 RESTORE 记录）
            perm_added = role_added = binding_added = 0
            if permissions and before.permissions:
                await clear_tombstones(conn, before.permissions, "Permission", **self._operator())
            if roles and before.roles:
                await clear_tombstones(conn, before.roles, "Role", **self._operator())

            if permissions or roles:
                perm_result = await seed_permissions(conn)
                role_result = await seed_roles(conn)
                binding_count = await seed_role_permissions(conn)
                perm_added = perm_result[1]
                role_added = role_result[1]
                binding_added = binding_count

            dict_detail: dict[str, int] = {}
            if dicts:
                for table, codes in before.dicts.items():
                    if codes:
                        await clear_tombstones(
                            conn, codes, DICT_DOC_TYPES[table], **self._operator()
                        )
                dict_detail = await seed_dict_library(conn)

        return BuiltinRestoreResult(
            restored_permissions=perm_added,
            restored_roles=role_added,
            restored_role_bindings=binding_added,
            restored_dicts=sum(dict_detail.values()),
            dict_detail=dict_detail,
        )


__all__ = [
    "DICT_TABLES",
    "BuiltinMissing",
    "BuiltinRestoreResult",
    "BuiltinRestoreService",
]
