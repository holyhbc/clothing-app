/**
 * 路由表与守卫（docs/07 §4.1「路由守卫：菜单项按权限过滤；直接访问无权限路由跳 403」）。
 *
 * ## 守卫的判定顺序是有讲究的
 *
 * ```
 * 未登录            → /login（带 redirect 回跳）
 * must_change_password → /profile/password（**在权限判定之前**）
 * 缺 meta.permission → /403
 * ```
 *
 * 强制改密排在权限判定**之前**：被钉住的用户往往正是权限最窄的那个（临时工、试工），
 * 先判权限会把他扔进 403，而 403 页上又没有改密入口 —— 用户彻底走不出死胡同。
 */
import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'
import { PERM } from '@garment/shared'
import AdminLayout from '@/layouts/AdminLayout.vue'
import { useAuthStore } from '@/stores/auth'

declare module 'vue-router' {
  interface RouteMeta {
    /** 免登录可访问（登录页、403、改密页）。 */
    public?: boolean
    /** 需要的权限点；数组表示命中任一即放行（对齐后端 `has_any`）。 */
    permission?: string | string[]
    /** 文档标题与面包屑用。 */
    title?: string
    /** 强制改密期间唯一可访问的页面（守卫据此放行它自己，避免自我跳转死循环）。 */
    forcePassword?: boolean
  }
}

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/Login.vue'),
    meta: { public: true, title: '登录' },
  },
  {
    path: '/403',
    name: 'forbidden',
    component: () => import('@/views/Error403.vue'),
    meta: { public: true, title: '无权限' },
  },
  {
    path: '/profile/password',
    name: 'change-password',
    component: () => import('@/views/Password.vue'),
    meta: { public: true, title: '修改密码', forcePassword: true },
  },
  {
    path: '/',
    component: AdminLayout,
    children: [
      {
        path: '',
        name: 'home',
        component: () => import('@/views/HomeView.vue'),
        meta: { title: '工作台', permission: PERM.BASE_READ },
      },
      {
        // 占位：T-WEB-005 落地基础资料页后替换。留在这里是为了让「无权限跳 403」
        // 与「有权限进入」两条路径现在就能真跑起来，而不是只在测试里成立。
        path: 'base/styles',
        name: 'base-styles',
        component: () => import('@/views/PlaceholderView.vue'),
        meta: { title: '款号', permission: PERM.BASE_UPDATE },
      },
      {
        path: 'system/users',
        name: 'system-users',
        component: () => import('@/views/system/users/List.vue'),
        meta: { title: '用户管理', permission: PERM.SYSTEM_USER_MANAGE },
      },
      {
        path: 'system/users/new',
        name: 'system-user-create',
        component: () => import('@/views/system/users/Form.vue'),
        meta: { title: '新建用户', permission: PERM.SYSTEM_USER_MANAGE },
      },
      {
        // ⚠️ 路由是 `system/users/new` 与 `system/users/:id` 两条，**顺序不能反** ——
        // 反了的话 `/system/users/new` 会被 `:id` 吃掉，`id` 变成字符串 "new"，
        // 于是页面去查一个不存在的用户，界面显示"加载失败"而看不出是路由写错了。
        path: 'system/users/:id',
        name: 'system-user-edit',
        component: () => import('@/views/system/users/Form.vue'),
        meta: { title: '编辑用户', permission: PERM.SYSTEM_USER_MANAGE },
      },
      {
        path: 'system/roles',
        name: 'system-roles',
        component: () => import('@/views/system/roles/List.vue'),
        meta: { title: '角色权限', permission: PERM.SYSTEM_ROLE_MANAGE },
      },
      {
        path: 'system/roles/new',
        name: 'system-role-create',
        component: () => import('@/views/system/roles/Form.vue'),
        meta: { title: '新建角色', permission: PERM.SYSTEM_ROLE_MANAGE },
      },
      {
        path: 'system/roles/:id',
        name: 'system-role-permissions',
        component: () => import('@/views/system/roles/Form.vue'),
        meta: { title: '配置权限点', permission: PERM.SYSTEM_ROLE_MANAGE },
      },
      {
        // `system-role-edit` 与 `system-role-permissions` 指向同一个组件，
        // 只是标题不同 —— 后端"改名称"和"改权限"是两个端点，但从一个页面提交。
        path: 'system/roles/:id/meta',
        name: 'system-role-edit',
        component: () => import('@/views/system/roles/Form.vue'),
        meta: { title: '编辑角色', permission: PERM.SYSTEM_ROLE_MANAGE },
      },
    ],
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: () => import('@/views/Error404.vue'),
    meta: { public: true, title: '页面不存在' },
  },
]

export function createAppRouter() {
  const router = createRouter({
    history: createWebHistory(),
    routes,
    scrollBehavior: () => ({ top: 0 }),
  })

  router.beforeEach(async (to) => {
    const auth = useAuthStore()

    // 刷新页面后先恢复登录态，否则第二次跳转的判定会基于「未登录」做出错误决定
    await auth.restore()

    if (to.meta.public === true) {
      // 已登录还去登录页 → 送回首页（否则用户改完密码想看看首页却卡在登录页）
      if (to.name === 'login' && auth.isLoggedIn) return { name: 'home' }
      return true
    }

    if (!auth.isLoggedIn) {
      // 带上 redirect，登录后能回到他本来要去的地方；
      // ⚠️ 只带 path（不带 query/hash 与整串 URL），避免把外部地址透传进站内
      return { name: 'login', query: { redirect: to.fullPath } }
    }

    if (auth.mustChangePassword && to.meta.forcePassword !== true) {
      return { name: 'change-password' }
    }

    const required = to.meta.permission
    if (required !== undefined) {
      const codes = typeof required === 'string' ? [required] : required
      if (!auth.hasAny(codes)) return { name: 'forbidden' }
    }

    return true
  })

  return router
}

export { routes }
