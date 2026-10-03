/**
 * 登录态 store（docs/07 §4）。
 *
 * ## 权限为什么在前端也存一份
 *
 * 后端**每个请求都查库**装配权限（docs/07 §1.1「权限查库，避免权限变更不生效」），
 * 所以前端这份**不是安全边界**，只用来控制界面显隐。真正拦得住的是后端 403 ——
 * 绕过前端直接打接口，后端照样拒。docs/03 §2.1 第 8 条把这条叫「权限控制双层」。
 *
 * ## has() 的语义必须与后端逐字一致
 *
 * 抄自 `backend/app/core/permissions.py` 的 `AuthContext.has`：只有 `*` 一个通配，
 * **没有** `base:*` 这种「按前缀通配」。两边语义分叉的代价是：前端把按钮藏起来了，
 * 后端却放行 —— 用户看到「能点的按钮被藏了」，排查半天是前端的错。
 */
import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import type { DataScope, MeResponse, RoleBrief, UserBrief } from '@garment/shared'
import * as authApi from '@/api/auth'
import { http } from '@/api/http'

/** 系统超管的通配权限点。与后端 `AuthContext.has` 一致（**只有一个**通配）。 */
const WILDCARD = '*'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<UserBrief | null>(null)
  const roles = ref<RoleBrief[]>([])
  const permissions = ref<ReadonlySet<string>>(new Set<string>())
  const dataScope = ref<DataScope | null>(null)
  const allowedWorkshopIds = ref<ReadonlySet<string>>(new Set<string>())

  /**
   * 是否已经尝试过恢复登录态。
   *
   * ⚠️ 没有它的话路由守卫会在每次跳转都去拉一次 `me`，一次跳转三个请求，
   *   而用户会看到明显的界面闪烁。
   */
  const restored = ref(false)

  const isLoggedIn = computed(() => user.value !== null)

  /** 首次登录 / 管理员重置口令后为 true，路由守卫会钉住用户必须改密。 */
  const mustChangePassword = computed(() => user.value?.must_change_password === true)

  function has(code: string): boolean {
    return permissions.value.has(WILDCARD) || permissions.value.has(code)
  }

  /** `v-can="['a','b']"` 与路由 `meta.permission` 的判定：命中任一即放行。 */
  function hasAny(codes: readonly string[]): boolean {
    if (permissions.value.has(WILDCARD)) return true
    return codes.some((code) => permissions.value.has(code))
  }

  /** 用 `GET /auth/me` 的结果覆盖本地登录态。 */
  function applyMe(me: MeResponse): void {
    user.value = me.user
    roles.value = me.roles ?? []
    permissions.value = new Set(me.permissions ?? [])
    dataScope.value = me.data_scope
    allowedWorkshopIds.value = new Set(me.allowed_workshop_ids ?? [])
  }

  /** 清空登录态并清 token。登出、登录失效、恢复失败都走这里。 */
  function clear(): void {
    user.value = null
    roles.value = []
    permissions.value = new Set<string>()
    dataScope.value = null
    allowedWorkshopIds.value = new Set<string>()
    http.clearAccessToken()
  }

  /**
   * 用内存 / sessionStorage 里的 access token 恢复登录态。
   *
   * 刷新页面后 token 还在但用户信息没了，必须问一次后端才能拿回权限 ——
   * **权限不在 token 里**（docs/07 §1.1）。失败（token 过期且 refresh 也失败）
   * 就 `clear()`，让守卫把用户送到 `/login`：不能"半登录"，有 token 没权限的话
   * 界面会渲染出一堆点不动的东西，而用户不知道自己该重新登录。
   */
  async function restore(): Promise<void> {
    if (restored.value) return
    restored.value = true
    if (http.accessToken === null) return
    try {
      applyMe(await authApi.fetchMe())
    } catch {
      clear()
    }
  }

  /**
   * 登录。
   *
   * ⚠️ **登录响应里没有权限**：`LoginResponse` 只给 `UserBrief`（docs/07 §1.1
   * 「权限明细只在 `/auth/me` 返回一次」），所以必须再拉一次 `me` 才能拿到权限点。
   * 少这一步的后果很隐蔽：登录成功、进了首页，然后所有 `v-can` 元素都不显示 ——
   * 看起来像权限全没了。
   */
  async function login(employeeNo: string, password: string): Promise<void> {
    const result = await authApi.login({ employee_no: employeeNo, password, channel: 'PC' })
    // ⚠️ 先存 token 再拉 me：me 的请求本身要带 token，顺序反了就是"刚登录就 401"
    http.setAccessToken(result.access_token)
    applyMe(await authApi.fetchMe())
    restored.value = true
  }

  /**
   * 登出。
   *
   * ⚠️ `finally` 里强制 `clear()`：吊销请求本身失败（断网 / 后端已挂）时也必须清本地，
   *   否则用户被留在"看起来登出了、后端还认这个 session"的半登录态。
   */
  async function logout(): Promise<void> {
    try {
      await authApi.logout()
    } finally {
      clear()
      restored.value = true
    }
  }

  return {
    allowedWorkshopIds,
    clear,
    dataScope,
    has,
    hasAny,
    isLoggedIn,
    login,
    logout,
    mustChangePassword,
    permissions,
    restored,
    roles,
    restore,
    user,
  }
})
