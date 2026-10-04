import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { BASE_DICT_CONTRACT, PERM } from '@garment/shared'
import * as authApi from '@/api/auth'
import { http, resetHandlers } from '@/api/http'
import { REGISTRY_KEYS } from '@/api/base'
import { useAuthStore } from '@/stores/auth'
import { createAppRouter, routes } from '@/router'

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
    // ⚠️ 权限点是 `base:read` 而不是 `base:update`：款号列表是**只读**页面，
    //    而占位页时代用的是 `base:update`（那时没有真页面，权限点是随手写的）。
    //    能看款号的人就该能打开列表 —— 只读权限的人点进去却 403 是错的。
    stubMe({ permissions: [PERM.BASE_READ] })
    http.setAccessToken('token-with-base-read')
    const router = await navigate('/base/styles')

    expect(router.currentRoute.value.name).toBe('base-styles')
  })

  it('TC-W10 must_change_password → 强制改密页（即使目标页本来有权）', async () => {
    stubMe({
      permissions: [PERM.BASE_READ],
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

/**
 * 基础资料的 27 条路由（T-WEB-005 落地后新增）。
 *
 * ⚠️ 这组用例守的是「注册表加了资源、路由没加」这种漏：路由由 `baseDictRoutes()`
 *    从注册表生成，生成对了不代表**页面存在** —— `import.meta.glob` 的键对不上时
 *    `pageLoader` 要到点击那一刻才抛，而那时用户已经白屏了。
 */
describe('基础资料路由', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    resetHandlers()
    vi.restoreAllMocks()
    http.clearAccessToken()
    sessionStorage.clear()
  })

  function flatRoutes() {
    return routes.flatMap((route) => route.children ?? [route])
  }

  it('每个注册表资源都有 列表 / 新建 / 编辑 三条路由', () => {
    const names = flatRoutes().map((route) => route.name)
    for (const key of REGISTRY_KEYS) {
      expect(names, `${key} 缺列表路由`).toContain(`base-${key}`)
      expect(names, `${key} 缺新建路由`).toContain(`base-${key}-new`)
      expect(names, `${key} 缺编辑路由`).toContain(`base-${key}-edit`)
    }
  })

  it('⚠️ 每条路由的组件都能真的加载出来（glob 键对不上时是点击才白屏）', async () => {
    for (const route of flatRoutes()) {
      if (typeof route.component !== 'function') continue
      // ⚠️ 必须断言成 `() => Promise<unknown>`：vue-router 的 `RouteComponent` 联合里
      //    还有函数式组件，直接 `route.component()` 时 TS 认为它不是加载器（TS2349）
      const loader = route.component as unknown as () => Promise<unknown>
      const component = await loader()
      expect(component, `${String(route.name)} 的组件加载失败`).toBeTruthy()
    }
  })

  it('`new` 排在 `:code` 前面（反了的话 /base/colors/new 会被当成编码 "new"）', () => {
    const list = flatRoutes().filter((route) => String(route.path).startsWith('base/colors'))
    const paths = list.map((route) => route.path)
    expect(paths.indexOf('base/colors/new')).toBeLessThan(paths.indexOf('base/colors/:code'))
  })

  it('新建 / 编辑路由用的是**该资源自己的**写权限点', () => {
    const list = flatRoutes()
    for (const key of REGISTRY_KEYS) {
      const contract = BASE_DICT_CONTRACT[key]
      const createRoute = list.find((route) => route.name === `base-${key}-new`)
      const editRoute = list.find((route) => route.name === `base-${key}-edit`)
      expect(createRoute?.meta?.permission, `${key} 新建路由权限点不对`).toBe(
        contract.permissions.create,
      )
      expect(editRoute?.meta?.permission, `${key} 编辑路由权限点不对`).toBe(
        contract.permissions.update,
      )
      // 分类 / 工序的写权限不是 base:*，写错会让有权限的人点进去直接 403
      if (contract.permissions.create !== 'base:create') {
        expect(createRoute?.meta?.permission).not.toBe('base:create')
      }
    }
  })

  it('菜单里的九个入口都有对应路由（点菜单不 404）', async () => {
    const { MENU_GROUPS } = await import('@/layouts/menu')
    const names = new Set(flatRoutes().map((route) => route.name))
    for (const group of MENU_GROUPS) {
      for (const item of group.children) {
        expect(names, `菜单项 ${item.key} 没有对应路由`).toContain(item.key)
      }
    }
  })
})
