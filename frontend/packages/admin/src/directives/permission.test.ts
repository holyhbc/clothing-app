import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'
import { defineComponent, nextTick, ref } from 'vue'
import { PERM } from '@garment/shared'
import { permission } from '@/directives/permission'
import { useAuthStore } from '@/stores/auth'

/**
 * 挂一个带 `v-can` 的按钮。
 *
 * `tick` 是为了能可靠地触发一次 re-render：指令的 `updated` 钩子只在组件真的
 * 更新过之后才会跑，而一个没有任何响应式依赖的组件调 `setProps({})` 不一定更新，
 * 用例就会"什么都没发生却通过"。
 */
function mountWithDirective(value: string | string[], style?: string) {
  const tick = ref(0)
  const wrapper = mount(
    defineComponent({
      directives: { can: permission },
      setup: () => ({ tick, value }),
      template: `<button v-can="value" :data-tick="tick"${style === undefined ? '' : ` style="${style}"`}>审核</button>`,
    }),
  )
  return {
    async refresh() {
      tick.value += 1
      await nextTick()
    },
    button: () => wrapper.get('button').element as HTMLElement,
  }
}

describe('v-can 权限指令', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  function grant(codes: string[]): void {
    useAuthStore().permissions = new Set(codes)
  }

  it('TC-W11 有权 → 元素保留且可见', () => {
    grant([PERM.BASE_UPDATE])
    const view = mountWithDirective(PERM.BASE_UPDATE)

    expect(view.button().style.display).not.toBe('none')
    expect(view.button().hasAttribute('data-can-hidden')).toBe(false)
  })

  it('TC-W11 无权 → 元素被隐藏', () => {
    grant([])
    const view = mountWithDirective(PERM.BASE_UPDATE)

    expect(view.button().style.display).toBe('none')
    expect(view.button().hasAttribute('data-can-hidden')).toBe(true)
  })

  it('数组语义是「命中任一即放行」（对齐后端 has_any）', () => {
    grant([PERM.BASE_READ])
    const view = mountWithDirective([PERM.BASE_UPDATE, PERM.BASE_READ])

    expect(view.button().style.display).not.toBe('none')
  })

  it('数组一个都不命中 → 隐藏', () => {
    grant([PERM.SYSTEM_LOG_VIEW])
    const view = mountWithDirective([PERM.BASE_UPDATE, PERM.BASE_CREATE])

    expect(view.button().style.display).toBe('none')
  })

  it('超管的 * 通配放行一切', () => {
    grant(['*'])
    const view = mountWithDirective('cutting:approve')

    expect(view.button().style.display).not.toBe('none')
  })

  it('权限在元素渲染之后才到达 → updated 重新判定（可逆，不是 remove()）', async () => {
    grant([])
    const view = mountWithDirective(PERM.BASE_UPDATE)
    expect(view.button().style.display).toBe('none')

    // 模拟「先渲染、后拿到权限」：刷新页面时 restore() 重试成功就是这条路径。
    // 如果指令按 docs/07 §4.1 的示例用 el.remove()，元素就被永久摘掉了 ——
    // 症状是「登录后按钮一直不出现，刷新也没用」。
    grant([PERM.BASE_UPDATE])
    await view.refresh()

    expect(view.button().style.display).not.toBe('none')
    expect(view.button().hasAttribute('data-can-hidden')).toBe(false)
  })

  it('只撤销「自己藏的」display：页面自己写的 display:none 不被改成可见', async () => {
    // 无差别 removeProperty('display') 会把页面的正常样式一起清掉，
    // 症状是某个该灰的按钮变成可点 —— 比多显示一个按钮更危险。
    grant([PERM.BASE_UPDATE])
    const view = mountWithDirective(PERM.BASE_UPDATE, 'display: none')
    expect(view.button().style.display).toBe('none')

    await view.refresh()

    expect(view.button().style.display).toBe('none')
  })
})
