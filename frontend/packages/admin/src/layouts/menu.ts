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
/**
 * ⚠️ T-WEB-004 原本在这里有「系统管理」（用户管理 / 角色权限）两项，已**移除**：
 * 后端没有 `users` / `roles` 的任何端点（`openapi.json` 里与账号相关的只有
 * `auth` 那六个），页面无处可接。菜单里留一个点进去是 404 的入口比没有更糟。
 * 端点与页面都到位后再加回来 —— 见 `docs/tasks/T-AUTH-003-用户与角色管理接口.md`。
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
    ],
  },
]
