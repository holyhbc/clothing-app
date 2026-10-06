"""用户 / 角色 / 权限点管理接口（docs/07 §2、§5）。

## 权限声明

每个端点都带 ``x-permission`` 且在函数体里显式校验（docs/05 §6「每个接口必填」）。
``openapi_extra`` 只是**文档**，真正的判定在这里 —— 前端据此控制按钮显隐，
后端据此兜底（docs/03 §2.1 第 8 条「前端隐藏不是安全」）。

## 路径前缀 `/system`

而不是挂到 `/users`、`/roles` —— 后者是**全局资源**，会和将来的业务资源混淆；
`/system/*` 一眼看出是后台管理面，也给以后加 `/system/logs` 留了位置。

---- 拆分说明（T-SYS-001b）----
原 ``router.py``（511 行）拆为 4 个子 router + 本 ``__init__``：``deps``（共享依赖 /
权限助手 / Service 构造）、``user_router``（9 端点）、``role_router``（6 端点）、
``restore_router``（2 端点）、``permission_router``（1 端点）。纯代码搬运，
路径 / 权限点 / 响应结构 / 函数名不变。

⚠️ ``app/main.py`` 依赖本包导出**名为 ``router`` 的单个 APIRouter**（不是列表）。
⚠️ ``include_router`` 顺序 = 源码顺序：``user → role → restore → permission``；
  各子 router 内保持源码顺序（``/users/options`` 先于 ``/users/{user_id}``）。
"""

from fastapi import APIRouter

from .permission_router import router as _permission_router
from .restore_router import router as _restore_router
from .role_router import router as _role_router
from .user_router import router as _user_router

router = APIRouter(prefix="/system", tags=["系统管理"])

# 按源码顺序 include；相对顺序不影响匹配（无前缀冲突），保持源码顺序便于与拆分前逐段对照。
router.include_router(_user_router)
router.include_router(_role_router)
router.include_router(_restore_router)
router.include_router(_permission_router)

__all__ = ["router"]
