/**
 * 接口类型的**唯一出口**（docs/06 §7「页面禁止手写接口 DTO」）。
 *
 * 规则：
 *   1. 需要什么类型就在这里加一个**别名**，指向 `api/schema.d.ts` 里的生成物；
 *      **不重新描述字段** —— 手写的字段表和后端分叉时没有任何东西会报错。
 *   2. 确实需要裁剪时用 `Omit` / `Pick` 组合，不写新接口。
 *   3. 页面从 `@garment/shared/types` 导入，不直接 import `schema.d.ts`
 *      —— 直接 import 的话生成路径一改，全站 import 都要跟着改。
 *
 * ⚠️ 后端还没实现的端点不在这里出现。缺类型时**先确认端点是否已存在**
 * （各模块目录下的 `router.py`），不要为了"让页面能跑"先手写一个占位 DTO。
 */

import type { components } from '../api/schema.d.ts'

type Schemas = components['schemas']

/**
 * 取 schema 里的一个具名模型。
 *
 * @example type StyleOut = ApiModel<'StyleOut'>
 *
 * ⚠️ 叫 `ApiModel` 而不是 `Schema`：全局作用域已有一个 DOM 的 `Schema` 接口，
 * 同名会让 `Schema<'X'>` 在部分解析路径下被当成模块声明而报 TS1443。
 */
export type ApiModel<Name extends keyof Schemas> = Schemas[Name]

/** 候选下拉项（docs/05 §9.5.2 `{value,label,sub?,disabled}`）。 */
export type OptionOut = ApiModel<'OptionOut'>

/** 款号详情（`GET /api/v1/styles/{style_no}`）。 */
export type StyleDetailOut = ApiModel<'StyleDetailOut'>

/** 款号列表行。 */
export type StyleListOut = ApiModel<'StyleListOut'>

/** 尺码比例查询结果（含 `hands_total` 与 `missing_size_codes`）。 */
export type RatioListOut = ApiModel<'RatioListOut'>

/** 款号工序配置行。 */
export type StyleOperationOut = ApiModel<'StyleOperationOut'>

/** 单价区间行（`is_current` 与 `rate_source` 都是派生字段）。 */
export type OperationRateOut = ApiModel<'OperationRateOut'>

/** 取价预演结果。 */
export type RateResolveOut = ApiModel<'RateResolveOut'>

/** 分页结构（docs/05 §3：所有列表统一 `{items,total,page,page_size}`）。 */
export type PageData<T> = {
  items: T[]
  total: number
  page: number
  page_size: number
}

// ---- 认证（docs/07 §1.1；T-WEB-002 起前端要用）----

/** 登录请求（`POST /api/v1/auth/login`）。 */
export type LoginRequest = ApiModel<'LoginRequest'>

/** 登录响应。⚠️ 只含 access token，refresh token 走 HttpOnly Cookie。 */
export type LoginResponse = ApiModel<'LoginResponse'>

/** 刷新响应（`POST /api/v1/auth/refresh`，成功即轮换 refresh token）。 */
export type RefreshResponse = ApiModel<'RefreshResponse'>

/** 登录态自描述（`GET /api/v1/auth/me`）：权限明细**只在这里**返回一次。 */
export type MeResponse = ApiModel<'MeResponse'>

/** 用户概要。刻意不含 `password_hash` 与 `phone`（docs/05 §3）。 */
export type UserBrief = ApiModel<'UserBrief'>

/** 角色概要。 */
export type RoleBrief = ApiModel<'RoleBrief'>

/** 修改本人口令（`PUT /api/v1/auth/password`）。 */
export type ChangePasswordRequest = ApiModel<'ChangePasswordRequest'>

/** 登出响应（`POST /api/v1/auth/logout`）：本次吊销的 refresh token 数量。 */
export type LogoutResponse = ApiModel<'LogoutResponse'>

/**
 * 数据范围枚举（docs/07 §3.2）。
 *
 * ⚠️ 后端**每次请求查库**装配权限（docs/07 §1.1），前端这份只用于控制界面，
 *    真正拦得住的是后端 403 —— 前端隐藏不是安全边界。
 */
export type DataScope = ApiModel<'DataScope'>

/** 认证渠道（`LoginRequest.channel`）。PC 端固定 `PC`。 */
export type AuthChannel = ApiModel<'AuthChannel'>

// ---- 系统管理（T-AUTH-003；T-WEB-004 的用户 / 角色两页用）----

/** 用户行。**不含** `password_hash` 与 `phone` —— 后端模型层就没有这两列。 */
export type UserOut = ApiModel<'UserOut'>

/** 新建用户请求。 */
export type UserCreate = ApiModel<'UserCreate'>

/** 局部更新用户。⚠️ `version` 是乐观锁，后端不匹配返回 10003。 */
export type UserPatch = ApiModel<'UserPatch'>

/** 整体替换用户角色。 */
export type UserRoleAssign = ApiModel<'UserRoleAssign'>

/** 停用用户（原因必填，docs/06 §5）。 */
export type DisableUserRequest = ApiModel<'DisableUserRequest'>

/** 启用用户（原因同样必填）。 */
export type EnableUserRequest = ApiModel<'EnableUserRequest'>

/** 管理员重置口令。⚠️ 新口令由**管理员填写**，不是应用生成。 */
export type PasswordResetRequest = ApiModel<'PasswordResetRequest'>

/** 角色行（含权限点与已授予用户数）。 */
export type RoleOut = ApiModel<'RoleOut'>

// ---- 基础资料（T-WEB-005 的九个资源共用）----

/**
 * 基础资料行 = `DictOut` + `ref_count`（后端 `DictRow`）。
 *
 * ⚠️ **为什么行类型要在这里拼一次**：`GET /{resource}` 的 `response_model` 是
 * `ApiResponse[dict[str, Any]]` —— 后端为了按运行时命中的资源选写入模型，收 body
 * 时用了 `dict`，列表的 response_model 也跟着退化成 `dict`（`router.py` 的注释
 * 解释了为什么不能写成 Union）。所以 openapi 里**拿不到行类型**，而
 * `DictRow` 本身也没进 `components.schemas`（只出现在 `DictOut` 上）。
 *
 * 用 `ApiModel<'DictOut'>` 而不是重新描述一遍字段：字段名与可空性仍由生成物决定，
 * 手抄一份的话后端把 `color_name` 改名成 `name`（REV-2026-10 第三批真发生过）时，
 * 前端不会有任何提示 —— 症状是表格里一整列空白。
 */
export type BaseDictRow = ApiModel<'DictOut'> & {
  /** 被引用次数；`null` = 该资源不参与引用检查（纯软删表）。 */
  readonly ref_count: number | null
}

/** 停用结果（`POST /{resource}/{code}/disables`）。原因**必填**（docs/06 §5）。 */
export type DisableOut = ApiModel<'DisableOut'>

/** 删除结果（字典表真删时 `cascaded` = 级联删掉的码表成员数）。 */
export type DeleteOut = ApiModel<'DeleteOut'>

/** 新建角色。 */
export type RoleCreate = ApiModel<'RoleCreate'>

/** 局部更新角色。⚠️ `code` 与 `is_system` 不可改。 */
export type RolePatch = ApiModel<'RolePatch'>

/** 停用角色（软删，原因必填）。 */
export type RoleDisable = ApiModel<'RoleDisable'>

/** 整体替换角色权限点。 */
export type ReplacePermissionsRequest = ApiModel<'ReplacePermissionsRequest'>

/** 按模块分组的权限点（角色勾选树）。 */
export type PermissionGroupOut = ApiModel<'PermissionGroupOut'>

/** 单个权限点。 */
export type PermissionOut = ApiModel<'PermissionOut'>

/** 用户候选（Combo 用）。 */
export type UserOptionOut = ApiModel<'UserOptionOut'>

/** 角色候选（Combo 用）。 */
export type RoleOptionOut = ApiModel<'RoleOptionOut'>

/**
 * 单据创建 / 修改请求体的公共形状（docs/05 §2：PATCH 必传 `version`）。
 *
 * ⚠️ 这里只声明**通用字段**，具体字段仍从生成物取 —— 列全字段就是手写 DTO，
 * 那正是本规范要禁掉的东西。
 */
export type Versioned = {
  version: number
}

/** 统一响应包装（docs/05 §3）。 */
export type ApiEnvelope<T> = {
  code: number
  message: string
  data: T | null
  details?: Record<string, unknown> | null
  request_id?: string | null
}

/** 排序方向（docs/05 §2：`sort_order` 只能是这两个值，不接受自由文本）。 */
export type SortOrder = 'asc' | 'desc'

/** 启停筛选（`is_active` 查询参数）。 */
export type ActiveFilter = boolean | undefined

/** 常用列表查询参数（各端点另有自己的业务筛选字段）。 */
export interface ListQuery extends Record<string, string | number | boolean | null | undefined> {
  q?: string
  page?: number
  size?: number
  sort_by?: string
  sort_order?: SortOrder
  is_active?: boolean
}

export type { components } from '../api/schema.d.ts'
export type { paths, operations } from '../api/schema.d.ts'
