/**
 * 认证相关端点（docs/07 §1.1）。
 *
 * ⚠️ **页面不许直接调 `http`**（docs/03 §2.1 第 5 条）：页面调这里的方法，
 *    这里调 `http`。好处是路径与请求形状集中一处，后端改路由时只改一个文件，
 *    而且每个方法名自带语义（`changePassword` 比 `put('/auth/password', ...)` 可读）。
 */
import type {
  ChangePasswordRequest,
  LoginRequest,
  LoginResponse,
  LogoutResponse,
  MeResponse,
} from '@garment/shared'
import { http } from './http'

/** 登录。成功返回体里只有 access token，refresh token 走 HttpOnly Cookie。 */
export function login(payload: LoginRequest): Promise<LoginResponse> {
  // auth: false —— 此刻按定义就没有 token
  return http.post<LoginResponse>('/auth/login', payload, { auth: false })
}

/**
 * 拉登录态自描述：用户 + 权限点 + 数据范围（docs/07 §1.1「权限查库」）。
 *
 * 刷新页面时靠它把权限重新装回 store —— 权限**不在 token 里**，所以每个新标签页
 * 都必须问一次后端。
 */
export function fetchMe(): Promise<MeResponse> {
  return http.get<MeResponse>('/auth/me')
}

/** 登出：吊销该用户**全部** refresh token。 */
export function logout(): Promise<LogoutResponse> {
  return http.post<LogoutResponse>('/auth/logout')
}

/**
 * 修改本人口令。`must_change_password` 的用户只能通过它解除强制改密
 * （路由守卫会把这类用户钉在 `/profile/password`）。
 */
export function changePassword(payload: ChangePasswordRequest): Promise<null> {
  return http.put<null>('/auth/password', payload)
}
