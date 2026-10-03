import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { PERM } from '@garment/shared'
import * as authApi from '@/api/auth'
import { http, resetHandlers } from '@/api/http'
import { useAuthStore } from '@/stores/auth'

/**
 * store 的 `has()` / `hasAny()`（TC-W12）与登录登出流程。
 *
 * `has()` 是**权限双层控制的前端那一层**，它与后端 `AuthContext.has` 的语义一旦
 * 分叉就会出现很难查的症状：前端把按钮藏了、后端却放行（用户报"按钮少了"），
 * 或者反过来（用户点了却撞 403）。所以这里逐条钉住语义。
 */
describe('useAuthStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    resetHandlers()
    vi.restoreAllMocks()
    http.clearAccessToken()
    sessionStorage.clear()
  })

  function grant(codes: string[]): void {
    useAuthStore().permissions = new Set(codes)
  }

  it('TC-W12 精确匹配：有该权限点 → true', () => {
    grant([PERM.BASE_READ, PERM.BASE_UPDATE])
    expect(useAuthStore().has(PERM.BASE_READ)).toBe(true)
  })

  it('TC-W12 精确匹配：没有该权限点 → false', () => {
    grant([PERM.BASE_READ])
    // ⚠️ 不能写成前缀匹配 / startsWith。后端 `AuthContext.has` 只做**全等**，
    //   只有 '*' 一个通配；前端若"聪明"地支持 base:*，就会出现前端放行、
    //   后端 403 的错位。
    expect(useAuthStore().has(PERM.BASE_DELETE)).toBe(false)
  })

  it('TC-W12 has("*") 即通配：* 在集合里 → 任意权限点都 true', () => {
    grant(['*'])
    const auth = useAuthStore()

    expect(auth.has(PERM.CUTTING_APPROVE).valueOf()).toBe(true)
    expect(auth.has(PERM.PAYROLL_PAY).valueOf()).toBe(true)
    expect(auth.has('never:registered:code')).toBe(true)
  })

  it('hasAny 命中任一即 true，全不命中才 false', () => {
    grant([PERM.BASE_READ])
    const auth = useAuthStore()

    expect(auth.hasAny([PERM.BASE_UPDATE, PERM.BASE_READ])).toBe(true)
    expect(auth.hasAny([PERM.BASE_UPDATE, PERM.BASE_CREATE])).toBe(false)
  })

  it('hasAny 空数组 → false（不能因为"任一命中"而对空集返回 true）', () => {
    grant([])
    expect(useAuthStore().hasAny([])).toBe(false)
  })

  it('未登录时 has() 全 false', () => {
    const auth = useAuthStore()
    expect(auth.isLoggedIn).toBe(false)
    expect(auth.has('*')).toBe(false)
    expect(auth.has(PERM.BASE_READ)).toBe(false)
  })

  it('clear() 同时清用户、权限与 token', () => {
    const auth = useAuthStore()
    grant([PERM.BASE_READ])
    auth.user = {
      id: '00000000-0000-0000-0000-000000000001',
      employee_no: 'A001',
      name: '张三',
      group_no: null,
      workshop_id: null,
      must_change_password: false,
      data_scope: 'SELF',
    }
    auth.dataScope = 'SELF'
    http.setAccessToken('token')

    auth.clear()

    expect(auth.user).toBeNull()
    expect(auth.permissions.size).toBe(0)
    expect(auth.dataScope).toBeNull()
    // ⚠️ 只清界面不清 token 的话，用户刷新页面又被 restore() 拉回登录态 ——
    // "我明明点了退出"
    expect(http.accessToken).toBeNull()
  })

  it('login 先存 token 再拉 me（顺序反了就是"刚登录就 401"）', async () => {
    const order: string[] = []
    vi.spyOn(authApi, 'login').mockImplementation(async () => {
      order.push('login')
      expect(http.accessToken).toBeNull()
      return {
        access_token: 'fresh-token',
        expires_in: 900,
        refresh_expires_at: '2026-10-04T10:00:00Z',
        token_type: 'bearer',
        user: {
          id: '00000000-0000-0000-0000-000000000001',
          employee_no: 'A001',
          name: '张三',
          group_no: null,
          workshop_id: null,
          must_change_password: false,
          data_scope: 'GROUP',
        },
      }
    })
    vi.spyOn(authApi, 'fetchMe').mockImplementation(async () => {
      order.push('me')
      // me 请求发出时 token 必须已经在位
      expect(http.accessToken).toBe('fresh-token')
      return {
        user: {
          id: '00000000-0000-0000-0000-000000000001',
          employee_no: 'A001',
          name: '张三',
          group_no: null,
          workshop_id: null,
          must_change_password: false,
          data_scope: 'GROUP',
        },
        data_scope: 'GROUP',
        permissions: [PERM.BASE_READ],
        roles: [{ code: 'admin', name: '系统管理员', data_scope: 'FACTORY' }],
        allowed_workshop_ids: [],
      }
    })

    const auth = useAuthStore()
    await auth.login('A001', 'Abcd1234')

    expect(order).toEqual(['login', 'me'])
    expect(auth.permissions.has(PERM.BASE_READ)).toBe(true)
    expect(auth.roles).toHaveLength(1)
  })

  it('logout 即使吊销请求失败也清本地（否则停在"看起来登出了"的半登录态）', async () => {
    vi.spyOn(authApi, 'logout').mockRejectedValue(new Error('网络中断'))
    const auth = useAuthStore()
    grant([PERM.BASE_READ])
    http.setAccessToken('token')

    await expect(auth.logout()).rejects.toThrow('网络中断')
    // 抛错也要清干净 —— 这才是 finally 的意义
    expect(auth.isLoggedIn).toBe(false)
    expect(http.accessToken).toBeNull()
  })
})
