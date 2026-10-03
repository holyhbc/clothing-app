/**
 * `v-can` —— 按权限点控制元素**显示**（docs/07 §4.1）。
 *
 * ```vue
 * <a-button v-can="'cutting:approve'">审核</a-button>
 * <a-button v-can="['payroll:approve', 'payroll:pay']">工资发放</a-button>
 * ```
 *
 * ## 为什么用 `display:none` 而不是规范示例里的 `el.remove()`
 *
 * docs/07 §4.1 的示例写的是 `el.remove()`。照抄会引入一个很难查的缺陷：
 * **权限是异步到达的**。刷新页面时路由守卫先 `await restore()`（拉 `/auth/me`），
 * 之后组件才首次渲染，所以大多时候权限已就绪；但只要组件在权限到达前渲染过一次
 * （例如守卫放行的是 `public` 页面、或 `me` 请求失败后重试成功），`remove()` 就把
 * 元素永久摘掉了 —— 它**不可逆**，而 Vue 还持有它的 vnode，后续 patch 也未必补回来。
 * 症状是「登录后按钮一直不出现，刷新也没用」。
 *
 * `display:none` 是可逆的：权限一到就恢复显示。代价是元素仍在 DOM 里 ——
 * 对「隐藏一个操作按钮」这个场景没有实际差别（用户看不到、也点不到）。
 *
 * ## 边界
 *
 * **前端隐藏不是安全边界**（docs/03 §2.1 第 8 条）。真正的拦截在服务端：
 * 即便有人用 devtools 把 `display` 改回来，后端每个接口仍独立校验权限并返回 403。
 */
import type { Directive } from 'vue'
import { useAuthStore } from '@/stores/auth'

/** 打上这个属性是为了「恢复显示」时能确认是自己藏的，而不是页面自己写的 display:none。 */
const HIDDEN_ATTR = 'data-can-hidden'

function requiredCodes(value: string | string[]): string[] {
  return Array.isArray(value) ? value : [value]
}

function apply(el: HTMLElement, value: string | string[]): void {
  const auth = useAuthStore()
  const allowed = requiredCodes(value).some((code) => auth.has(code))
  if (allowed) {
    // ⚠️ 只撤销**自己**设的 display：无差别 removeProperty 会把页面自己写的
    //   `style="display:none"` 一起清掉，症状是该灰的按钮变成可点。
    if (el.hasAttribute(HIDDEN_ATTR)) {
      el.removeAttribute(HIDDEN_ATTR)
      el.style.removeProperty('display')
    }
    return
  }
  el.setAttribute(HIDDEN_ATTR, '')
  el.style.setProperty('display', 'none')
}

export const permission: Directive<HTMLElement, string | string[]> = {
  mounted(el, binding) {
    apply(el, binding.value)
  },
  // ⚠️ `updated` 必须**无条件**重算，不能加 `binding.value === binding.oldValue` 的
  //    提前返回：变的是**权限集合**不是绑定值，典型的「先渲染、后拿到权限」里
  //    绑定值一直没变 —— 加上这行就等于把这条路径整个关掉，元素永远藏着。
  //    代价只是每次组件更新多几次 Set 查询，可以忽略。
  updated(el, binding) {
    apply(el, binding.value)
  },
}
