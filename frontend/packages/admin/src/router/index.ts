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
import { BASE_DICT_CONTRACT, PERM } from '@garment/shared'
import AdminLayout from '@/layouts/AdminLayout.vue'
import { REGISTRY_KEYS, resourceDecl } from '@/api/base'
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
        // 款号（T-WEB-006 落地，占位页已替换）
        path: 'base/styles',
        name: 'base-styles',
        component: () => import('@/views/base/styles/List.vue'),
        meta: { title: '款号', permission: PERM.BASE_READ },
      },
      {
        path: 'base/styles/new',
        name: 'base-styles-new',
        component: () => import('@/views/base/styles/Form.vue'),
        meta: { title: '新建款号', permission: PERM.BASE_CREATE },
      },
      {
        // ⚠️ `new` 排在 `:styleNo` 前面 —— 反了的话 `/base/styles/new` 会被当成
        //    "货号叫 new 的那一行"（T-WEB-004 在系统管理那两页踩过同一个坑）
        path: 'base/styles/:styleNo',
        name: 'base-styles-detail',
        component: () => import('@/views/base/styles/Detail.vue'),
        meta: { title: '款号详情', permission: PERM.BASE_READ },
      },
      {
        path: 'base/styles/:styleNo/edit',
        name: 'base-styles-edit',
        component: () => import('@/views/base/styles/Form.vue'),
        meta: { title: '编辑款号', permission: PERM.BASE_UPDATE },
      },
      {
        path: 'base/styles/:styleNo/ratios',
        name: 'base-styles-ratios',
        component: () => import('@/views/base/styles/Ratios.vue'),
        meta: { title: '尺码比例', permission: PERM.BASE_UPDATE },
      },
      {
        path: 'base/operation-rates',
        name: 'base-operation-rates',
        component: () => import('@/views/base/operation-rates/List.vue'),
        // ⚠️ 权限点用 `piecework:rate:manage`：这是**调价**入口（设价的主操作），
        //    给 `base:read` 的话只读用户点进来什么都做不了。
        meta: { title: '工序单价', permission: PERM.PIECEWORK_RATE_MANAGE },
      },
      ...baseDictRoutes(),
      {
        // 裁剪单列表（T-CUT-001c-2）。⚠️ 只登记**真的存在**的页面：先登记再补页面，
        //    用户点进去就是 404，而 404 页会让人以为是权限问题，反复重登去找管理员。
        path: 'cutting/orders',
        name: 'cutting-orders',
        component: () => import('@/views/cutting/List.vue'),
        meta: { title: '裁剪单', permission: PERM.CUTTING_READ },
      },
      {
        // ⚠️ `new` **必须**排在 `:orderId` 前面 —— 反了的话 `/cutting/orders/new`
        //    会被当成「ID 是 new 的那一行」，后端收到非 UUID 直接 422（页面 422 白屏）。
        path: 'cutting/orders/new',
        name: 'cutting-orders-new',
        component: () => import('@/views/cutting/Form.vue'),
        meta: { title: '新建裁剪单', permission: PERM.CUTTING_CREATE },
      },
      {
        path: 'cutting/orders/:orderId',
        name: 'cutting-orders-detail',
        component: () => import('@/views/cutting/Detail.vue'),
        meta: { title: '裁剪单详情', permission: PERM.CUTTING_READ },
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

/**
 * 基础资料的 27 条路由（9 个资源 × 列表 / 新建 / 编辑），**由注册表生成**。
 *
 * ## 为什么生成而不是手写 27 条
 *
 * 手写的后果不是"多几行"，而是**注册表加了资源、路由没加** —— 那个资源有 API、
 * 有注册表声明，就是没有页面，而**没有任何东西会报错**（菜单是另一份数据，见
 * `layouts/menu.ts` 的注释）。菜单刻意手写（多一项只是多一个入口），路由生成
 * （少一条就是少一个可达页面，这种漏必须结构上不可能）。
 *
 * ## 顺序：每组三条里 `new` 必须排在 `:code` 前面
 *
 * 反了的话 `/base/colors/new` 会被 `:code` 吃掉，`code` 变成字符串 `"new"`，
 * 页面去查一个叫 new 的颜色并显示"加载失败" —— 看着像 bug，而不是路由写错。
 * （系统管理那两页踩过同一个坑，`views/system/users/Form.vue` 有注释。）
 *
 * ## 权限点逐条不同
 *
 * 列表一律 `base:read`；新建 / 编辑用**该资源自己的写权限**（分类是
 * `base:category:manage`、工序是 `base:operation:manage`）。统一写成 `base:update`
 * 会让有分类权限的人点进编辑页直接 403。
 *
 * ## ⚠️ 为什么用 `import.meta.glob` 而不是 `import(\`../views/base/${key}/List.vue\`)`
 *
 * 带变量的 `import()` **Vite 静态分析不了**，会原样留在产物里变成运行期请求
 * `../views/base/colors/List.vue` —— 构建**不报错、不警告**，首页也能正常 200
 * （闸门 5 的 web 镜像只探测首页），一进列表页就白屏。`import.meta.glob` 是
 * Vite 的一等公民，它在构建期把 glob 展开成真实的动态 import 映射。
 *
 * 顺带得到一条免费守卫：`router/index.test.ts` 断言这个映射的键与注册表一致，
 * 于是「加了资源忘了建页面」在闸门 3 就红，而不是等用户点菜单发现白屏。
 */
const DICT_LIST_PAGES = import.meta.glob('../views/base/*/List.vue')
const DICT_FORM_PAGES = import.meta.glob('../views/base/*/Form.vue')

/** 从 glob 映射里取列表页组件加载器。缺页时**当场抛**，不返回 undefined 组件。 */
function listPageLoader(dir: string): () => Promise<unknown> {
  return () => {
    const found = DICT_LIST_PAGES[`../views/base/${dir}/List.vue`]
    if (found === undefined) {
      throw new Error(`基础资料页面缺失：views/base/${dir}/List.vue`)
    }
    return found()
  }
}

function formPageLoader(dir: string): () => Promise<unknown> {
  return () => {
    const found = DICT_FORM_PAGES[`../views/base/${dir}/Form.vue`]
    if (found === undefined) {
      throw new Error(`基础资料页面缺失：views/base/${dir}/Form.vue`)
    }
    return found()
  }
}

function baseDictRoutes(): RouteRecordRaw[] {
  return REGISTRY_KEYS.flatMap((key) => {
    const decl = resourceDecl(key)
    const contract = BASE_DICT_CONTRACT[key]
    return [
      {
        path: `base/${key}`,
        name: `base-${key}`,
        component: listPageLoader(key),
        meta: { title: decl.title, permission: PERM.BASE_READ },
      },
      {
        path: `base/${key}/new`,
        name: `base-${key}-new`,
        component: formPageLoader(key),
        meta: { title: `新建${decl.itemLabel}`, permission: contract.permissions.create },
      },
      {
        path: `base/${key}/:code`,
        name: `base-${key}-edit`,
        component: formPageLoader(key),
        meta: { title: `编辑${decl.itemLabel}`, permission: contract.permissions.update },
      },
    ] satisfies RouteRecordRaw[]
  })
}

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
