/**
 * 菜单定义 —— **与路由表分开**，因为两者回答的是不同问题：
 *
 * - `router/index.ts` 回答「哪些 URL 允许访问」（守卫用，判定错了是安全问题）
 * - 这里回答「给这个用户看哪些入口」（菜单用，判定错了是体验问题）
 *
 * 两者用同一份权限点，但**故意不共用同一份数据**：菜单多一项只是多一个入口，
 * 路由多一项就多一个可达页面 —— 共用一份会让「加个菜单」的改动同时放宽了访问控制。
 *
 * 过滤在 `AdminLayout` 里按 `auth.has()` 做（docs/07 §4.1「菜单项按权限过滤」）。
 */
import { PERM } from '@garment/shared'
import type { PermissionCode } from '@garment/shared'

export interface MenuItem {
  /** 路由 name（不是 path）：用 name 跳转，重构路径时菜单不用改。 */
  readonly key: string
  readonly title: string
  /** 需要的权限点；数组表示命中任一即可（对齐后端 `has_any`）。 */
  readonly permission: PermissionCode | readonly PermissionCode[]
}

export interface MenuGroup {
  readonly key: string
  readonly title: string
  readonly children: readonly MenuItem[]
}

/**
 * P0 阶段的菜单。T-WEB-004 起每落一个页面就在这里加一项。
 *
 * ⚠️ 权限点一律用 `PERM.*` 具名常量，不写裸字符串 —— 裸字符串打错了要等用户点进去
 * 撞 403 才发现，具名常量打错了编译就红。
 *
 * ⚠️ **只有真的有页面才登记**（T-WEB-004 移除系统管理两项时定下的规矩）：
 *  菜单里出现一个点进去是 404 的入口，比没有这个入口更糟 —— 用户会以为权限有问题、
 *  反复重登、找管理员。端点与页面都到位了再加。
 */

export const MENU_GROUPS: readonly MenuGroup[] = [
  {
    key: 'base',
    title: '基础资料',
    children: [
      {
        key: 'base-styles',
        title: '款号',
        permission: PERM.BASE_READ,
      },
      // T-WEB-006：款号详情里的「设价 / 调价」是独立页面，所以进菜单。
      // ⚠️ 权限点用 `piecework:rate:manage`（调价）而不是 `base:read` —— 单价页的
      // 主操作就是调价，只读权限的人看到入口却什么都做不了。
      {
        key: 'base-operation-rates',
        title: '工序单价',
        permission: PERM.PIECEWORK_RATE_MANAGE,
      },
      // ⚠️ 九项**手写**而不是由注册表生成 —— 与路由表刻意不共用数据（见文件头）。
      //   这里共用会让「注册表加一个资源」同时多出一个可达页面：菜单多一项只是多一
      //   个入口（点不进去是 404，用户知道是自己没权限之外的原因），路由多一项就是
      //   多一个真的能打开的页面。菜单用 `base:read` 即可 —— 写操作按按钮逐个 `v-can`。
      { key: 'base-workshops', title: '车间', permission: PERM.BASE_READ },
      { key: 'base-workshop-groups', title: '组别', permission: PERM.BASE_READ },
      { key: 'base-warehouses', title: '仓库', permission: PERM.BASE_READ },
      { key: 'base-uom-units', title: '计量单位', permission: PERM.BASE_READ },
      { key: 'base-product-categories', title: '商品分类', permission: PERM.BASE_READ },
      { key: 'base-colors', title: '颜色', permission: PERM.BASE_READ },
      { key: 'base-sizes', title: '尺码', permission: PERM.BASE_READ },
      { key: 'base-size-groups', title: '尺码模板', permission: PERM.BASE_READ },
      { key: 'base-operations', title: '工序', permission: PERM.BASE_READ },
    ],
  },
  // T-CUT-001c-2 / T-BUND-009：裁剪与打菲是 P1 的主链路（裁剪 → 打菲）。
  // ⚠️ 权限点用 `cutting:read` / `bundling:read` —— 菜单与路由用**同一个**判据，
  //    不会出现「能进列表但按钮全灰」这种半吊子状态。
  {
    key: 'production',
    title: '生产管理',
    children: [
      { key: 'cutting-orders', title: '裁剪单', permission: PERM.CUTTING_READ },
      { key: 'bundling-orders', title: '打菲单', permission: PERM.BUNDLING_READ },
    ],
  },
  // T-WEB-004 曾把这一组删掉过（后端没有端点，菜单里点进去是 404）；
  // T-AUTH-003 补齐端点后按上面的规矩加回来。
  {
    key: 'system',
    title: '系统管理',
    children: [
      {
        key: 'system-users',
        title: '用户管理',
        permission: PERM.SYSTEM_USER_MANAGE,
      },
      {
        key: 'system-roles',
        title: '角色权限',
        permission: PERM.SYSTEM_ROLE_MANAGE,
      },
    ],
  },
]
