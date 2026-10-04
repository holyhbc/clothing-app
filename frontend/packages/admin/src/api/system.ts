/**
 * 系统管理接口封装（`/system/*`，T-AUTH-003 落地）。
 *
 * ⚠️ **页面不许直接调 `http`**（docs/03 §2.1 第 5 条）：页面调这里的方法，
 *    这里调 `http`。好处是路径与请求形状集中一处，后端改路由时只改一个文件。
 *
 * ⚠️ 类型全部来自 `@garment/shared`（由 `openapi.json` 生成），
 *    **不手写 DTO**（docs/06 §7）。
 */
import type {
  DisableUserRequest,
  EnableUserRequest,
  PasswordResetRequest,
  PermissionGroupOut,
  ReplacePermissionsRequest,
  RoleCreate,
  RoleDisable,
  RoleOptionOut,
  RoleOut,
  RolePatch,
  UserCreate,
  UserOptionOut,
  UserOut,
  UserPatch,
  UserRoleAssign,
} from '@garment/shared'
import { http } from './http'

// ------------------------------------------------------------------- 用户

export interface UserListQuery {
  q?: string
  workshop_id?: string
  is_active?: boolean
  page?: number
  size?: number
}

export async function listUsers(query: UserListQuery = {}): Promise<{
  items: UserOut[]
  total: number
}> {
  const result = await http.get<{
    items: UserOut[]
    total: number
    page: number
    page_size: number
  }>('/system/users', { query: { ...query } })
  return { items: result.items, total: result.total }
}

export function getUser(userId: string): Promise<UserOut> {
  return http.get<UserOut>(`/system/users/${userId}`)
}

export function createUser(payload: UserCreate): Promise<UserOut> {
  return http.post<UserOut>('/system/users', payload)
}

export function patchUser(userId: string, payload: UserPatch): Promise<UserOut> {
  return http.patch<UserOut>(`/system/users/${userId}`, payload)
}

/** 整体替换用户角色 —— 不是增删（授权界面天然是"勾选哪些"的全量语义）。 */
export function assignUserRoles(userId: string, payload: UserRoleAssign): Promise<UserOut> {
  return http.put<UserOut>(`/system/users/${userId}/roles`, payload)
}

export function disableUser(userId: string, payload: DisableUserRequest): Promise<UserOut> {
  return http.post<UserOut>(`/system/users/${userId}/disables`, payload)
}

export function enableUser(userId: string, payload: EnableUserRequest): Promise<UserOut> {
  return http.post<UserOut>(`/system/users/${userId}/enables`, payload)
}

/**
 * 管理员重置口令。
 *
 * ⚠️ 新口令由**管理员填写**：后端不生成随机口令（没有送达通道，
 *    `auth/sms/send-code` 是 P2 未启用）。后端会置 `must_change_password`
 * 并吊销该用户全部 refresh token。
 */
export function resetUserPassword(userId: string, payload: PasswordResetRequest): Promise<null> {
  return http.post<null>(`/system/users/${userId}/password-resets`, payload)
}

export function searchUserOptions(keyword: string): Promise<UserOptionOut[]> {
  return http.get<UserOptionOut[]>('/system/users/options', { query: { q: keyword, size: 20 } })
}

// ------------------------------------------------------------------- 角色

export async function listRoles(keyword?: string): Promise<RoleOut[]> {
  return http.get<RoleOut[]>('/system/roles', { query: { q: keyword } })
}

export function createRole(payload: RoleCreate): Promise<RoleOut> {
  return http.post<RoleOut>('/system/roles', payload)
}

export function patchRole(roleId: string, payload: RolePatch): Promise<RoleOut> {
  return http.patch<RoleOut>(`/system/roles/${roleId}`, payload)
}

/** 整体替换角色权限点。后端会写 `document_logs` 且**立刻生效**。 */
export function replaceRolePermissions(
  roleId: string,
  payload: ReplacePermissionsRequest,
): Promise<RoleOut> {
  return http.put<RoleOut>(`/system/roles/${roleId}/permissions`, payload)
}

/** 停用角色（后端实现为软删）。⚠️ 内置角色会被拒绝。 */
export function disableRole(roleId: string, payload: RoleDisable): Promise<RoleOut> {
  return http.post<RoleOut>(`/system/roles/${roleId}/disables`, payload)
}

export function searchRoleOptions(keyword: string): Promise<RoleOptionOut[]> {
  return http.get<RoleOptionOut[]>('/system/roles/options', { query: { q: keyword, size: 20 } })
}

/**
 * 权限点（按模块分组）。
 *
 * ⚠️ 权限声明的是 `system:role:manage`，而 `roles/options` 声明的是
 * `system:user:manage` —— 后端**故意**这么分：建号时要选角色，
 * 而车间主管没有角色管理权。
 */
export function listPermissionGroups(): Promise<PermissionGroupOut[]> {
  return http.get<PermissionGroupOut[]>('/system/permissions')
}
