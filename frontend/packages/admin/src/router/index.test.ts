import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { PERM } from '@garment/shared'
import * as authApi from '@/api/auth'
import { http, resetHandlers } from '@/api/http'
import { useAuthStore } from '@/stores/auth'
import { createAppRouter } from '@/router'

/**
 * 路由守卫（TC-W08~TC-W10）。
 *
 * ## 为什么全部打桩而不是起一个真后端
 *
 * 守卫要判定的东西是「登录态 + 权限集合」，而这两样都由 `/auth/me` 的返回决定。
 * 打桩 `fetchMe` 让用例只依赖**守卫自己的逻辑**，失败时能直接归因到守卫；
 * 真起后端的话，登录口令过期、种子数据没跑这类问题会让用例变红，
 * 而它与「守卫有没有正确跳 403」毫无关系。
 *
 * 真链路（`pnpm dev` + 真后端）由 T-WEB-006 的 E2E 覆盖。
 */
function stubMe(me: Partial<Awaited<ReturnType<typeof authApi.fetchMe>>> | null): void {
  if (me === null) {
    vi.spyOn(authApi, 'fetchMe').mockRejectedValue(new Error('未登录'))
    return
  }
  vi.spyOn(authApi, 'fetchMe').mockResolvedValue({
    user: {
      id: '00000000-0000-0000-0000-000000000001',
      employee_no: 'A001',
      name: '张三',
      group_no: null,
      workshop_id: null,
      must_change_password: false,
      data_scope: 'FACTORY',
    },
    data_scope: 'FACTORY',
    ...me,
  } as Awaited<ReturnType<typeof authApi.fetchMe>>)
}

async function navigate(path: string) {
  const router = createAppRouter()
  await router.push(path)
  await router.isReady()
  return router
}

describe('路由守卫', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    resetHandlers()
    vi.restoreAllMocks()
    // ⚠️ 必须 `clearAccessToken()` 而不是只清 sessionStorage：
    // `createSessionTokenStorage` 在内存里也留了一份（刷新页面不丢就是靠它），
    // 所以 `sessionStorage.clear()` 之后 `http.accessToken` 仍然读得到上一个用例
    // 留下的 token —— 症状是用例之间互相污染，且只在**单个**跑时才是绿的。
    http.clearAccessToken()
    sessionStorage.clear()
  })

  it('TC-W08 未登录访问受限页 → /login 且带 redirect', async () => {
    stubMe(null)
    const router = await navigate('/base/styles')

    expect(router.currentRoute.value.name).toBe('login')
    // redirect 必须能让他登录后回到原处，否则用户每次都得重新找功能
    expect(router.currentRoute.value.query['redirect']).toBe('/base/styles')
  })

  it('TC-W09 已登录但缺权限 → /403', async () => {
    stubMe({ permissions: ['system:log:view'] })
    http.setAccessToken('token-without-base-update')
    const router = await navigate('/base/styles')

    expect(router.currentRoute.value.name).toBe('forbidden')
  })

  it('已登录且有权 → 正常进入', async () => {
    stubMe({ permissions: [PERM.BASE_UPDATE] })
    http.setAccessToken('token-with-base-update')
    const router = await navigate('/base/styles')

    expect(router.currentRoute.value.name).toBe('base-styles')
  })

  it('TC-W10 must_change_password → 强制改密页（即使目标页本来有权）', async () => {
    stubMe({
      permissions: [PERM.BASE_UPDATE],
      user: {
        id: '00000000-0000-0000-0000-000000000001',
        employee_no: 'A001',
        name: '李四',
        group_no: null,
        workshop_id: null,
        must_change_password: true,
        data_scope: 'GROUP',
      },
    })
    http.setAccessToken('token-must-change')
    const router = await navigate('/base/styles')

    expect(router.currentRoute.value.name).toBe('change-password')
  })

  it('强制改密时改密页自己可达（否则守卫把自己也拦掉 → 死循环）', async () => {
    stubMe({
      permissions: [],
      user: {
        id: '00000000-0000-0000-0000-000000000001',
        employee_no: 'A001',
        name: '李四',
        group_no: null,
        workshop_id: null,
        must_change_password: true,
        data_scope: 'GROUP',
      },
    })
    http.setAccessToken('token-must-change')
    const router = await navigate('/profile/password')

    expect(router.currentRoute.value.name).toBe('change-password')
  })

  it('强制改密排在权限判定之前：否则窄权限用户会先进 403，而 403 上没有改密入口', async () => {
    // 权限最窄的用户往往正是被要求改密的那批（临时工/试工）。
    // 若先判权限，他会卡在 403 页 —— 而 403 页上改不了密，彻底死胡同。
    stubMe({
      permissions: [],
      user: {
        id: '00000000-0000-0000-0000-000000000001',
        employee_no: 'A001',
        name: '李四',
        group_no: null,
        workshop_id: null,
        must_change_password: true,
        data_scope: 'SELF',
      },
    })
    http.setAccessToken('token-must-change')
    const router = await navigate('/')

    expect(router.currentRoute.value.name).toBe('change-password')
  })

  it('已登录还去 /login → 送回工作台', async () => {
    stubMe({ permissions: [PERM.BASE_READ] })
    http.setAccessToken('token-ok')
    const router = await navigate('/login')

    expect(router.currentRoute.value.name).toBe('home')
  })

  it('token 过期且 restore 失败 → 清本地并跳登录', async () => {
    stubMe(null)
    http.setAccessToken('stale-token')
    const router = await navigate('/')
    const auth = useAuthStore()

    expect(router.currentRoute.value.name).toBe('login')
    // ⚠️ 必须连 token 一起清：留着它的话下一次 restore 又会拿它去请求，
    // 然后又是一轮 401 —— 用户永远在「登录页闪一下又跳回来」
    expect(auth.isLoggedIn).toBe(false)
    expect(http.accessToken).toBeNull()
  })

  it('restore 只问一次后端（否则每次跳转都发一轮 me，界面会闪）', async () => {
    stubMe({ permissions: [PERM.BASE_READ] })
    http.setAccessToken('token-ok')
    const auth = useAuthStore()

    await auth.restore()
    await auth.restore()
    await auth.restore()

    expect(authApi.fetchMe).toHaveBeenCalledTimes(1)
  })

  it('无 token 时 restore 不发请求', async () => {
    stubMe(null)
    const auth = useAuthStore()

    await auth.restore()

    expect(authApi.fetchMe).not.toHaveBeenCalled()
    expect(auth.isLoggedIn).toBe(false)
  })
})
