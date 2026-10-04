import { beforeEach, describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { PERM } from '@garment/shared'
import EmptyState from '@/components/EmptyState.vue'
import PageLayout from '@/components/PageLayout.vue'
import TableToolbar from '@/components/TableToolbar.vue'
import { permission } from '@/directives/permission'
import { useAuthStore } from '@/stores/auth'

/**
 * `PageLayout` / `EmptyState` / `TableToolbar`。
 *
 * 这三个组件的价值全在**结构约束**上（主操作只能有一个、已选数量必须显示、
 * 空态必须给下一步动作），所以测试盯的就是结构，而不是渲染出来的像素。
 */

/**
 * 辅助函数的入参用 `Partial`，但 `mount` 要的是完整 props 类型 ——
 * `exactOptionalPropertyTypes` 下这两者不兼容（`Partial` 会让可选 prop 变成
 * `T | undefined`，而 props 不接受 `undefined`）。转换只在挂载这一处发生。
 */
type PropsOf<T extends abstract new () => { $props: object }> = Partial<InstanceType<T>['$props']>

describe('PageLayout 页面容器', () => {
  function layout(props: PropsOf<typeof PageLayout> = {}, slots: Record<string, string> = {}) {
    return mount(PageLayout, {
      props: props as InstanceType<typeof PageLayout>['$props'],
      slots,
    })
  }

  it('渲染标题，20px/600（docs/06 §2.1）', () => {
    const wrapper = layout({ title: '用户管理' })

    expect(wrapper.get('h1').text()).toBe('用户管理')
    expect(wrapper.get('h1').classes()).toContain('page-title')
  })

  it('description 给了才渲染；不给就不留空行', () => {
    expect(layout({ title: 'X' }).find('.page-description').exists()).toBe(false)
    expect(layout({ title: 'X', description: '共 128 条' }).get('.page-description').text()).toBe(
      '共 128 条',
    )
  })

  it('筛选区与工具栏的容器**按插槽存在与否**渲染（不留空气壳）', () => {
    const bare = layout({ title: 'X' })
    expect(bare.find('.page-filter').exists()).toBe(false)
    expect(bare.find('.page-toolbar').exists()).toBe(false)

    const full = layout(
      { title: 'X' },
      { filter: '<div class="f" />', toolbar: '<div class="t" />' },
    )
    expect(full.find('.page-filter').exists()).toBe(true)
    expect(full.find('.page-toolbar').exists()).toBe(true)
  })

  it('主操作区是独立插槽 —— 结构上只允许一个 primary（docs/06 §2.1）', () => {
    const wrapper = layout({ title: 'X' }, { extra: '<button class="primary">新建</button>' })

    // 只有一个 extra 容器；想放第二个 primary 必须先改组件
    expect(wrapper.findAll('.page-header-extra')).toHaveLength(1)
    expect(wrapper.get('.page-header-extra').text()).toBe('新建')
  })

  it('filter 插槽拿得到 collapsed（页面据此决定要不要默认展开）', () => {
    const wrapper = mount(PageLayout, {
      props: { title: 'X' },
      slots: {
        filter: `<span class="probe">{{ params.collapsed ? '折叠' : '展开' }}</span>`,
      },
    })

    // 默认 filterCollapsed=true → 页面把"更多条件"收起来
    expect(wrapper.get('.probe').text()).toBe('折叠')
  })

  it('filterCollapsed=false 时插槽参数为 false', () => {
    const wrapper = mount(PageLayout, {
      props: { title: 'X', filterCollapsed: false },
      slots: { filter: `<span class="probe">{{ params.collapsed ? '折叠' : '展开' }}</span>` },
    })

    expect(wrapper.get('.probe').text()).toBe('展开')
  })
})

describe('EmptyState 空态', () => {
  function empty(props: PropsOf<typeof EmptyState> = {}) {
    return mount(EmptyState, { props: props as InstanceType<typeof EmptyState>['$props'] })
  }

  it('必须给"下一步动作"而不是只说"暂无数据"', () => {
    const wrapper = empty({ title: '还没有款号', hint: '点右上角「新建」录入第一个款号' })

    expect(wrapper.get('.empty-title').text()).toBe('还没有款号')
    // ⚠️ `.empty-hint` 拿不到不是组件坏了：antd 的 `Empty` 把 `#description`
    //    插槽包在自己的容器里，而**只有传了 `description` prop 时才渲染该插槽**。
    //    所以 `EmptyState` 必须传 `:description="null"` 之外的显式空串 ——
    //    否则 hint / title 整段被丢掉，页面上只有一个空 Empty 图标。
    expect(wrapper.find('.empty-hint').exists()).toBe(true)
  })

  it('hint 不给时不渲染空 hint（避免出现空行）', () => {
    expect(empty({ title: '还没有款号' }).find('.empty-hint').exists()).toBe(false)
  })

  it('actionText 给了才渲染按钮；点击抛 action 事件', async () => {
    const wrapper = empty({ title: 'x', actionText: '新建款号' })
    const button = wrapper.findAll('button').find((b) => b.text() === '新建款号')

    expect(button).toBeDefined()
    await button?.trigger('click')
    expect(wrapper.emitted('action')).toHaveLength(1)
  })

  it('没给 actionText 就不渲染按钮（空态不该摆一个点不动的按钮）', () => {
    expect(empty({ title: 'x' }).findAll('button')).toHaveLength(0)
  })

  it('辅助操作（清空筛选）单独一个事件', async () => {
    const wrapper = empty({ title: '没有匹配结果', secondaryActionText: '清空筛选条件' })
    const button = wrapper.findAll('button').find((b) => b.text() === '清空筛选条件')

    await button?.trigger('click')
    expect(wrapper.emitted('secondaryAction')).toHaveLength(1)
  })
})

describe('TableToolbar 列表工具栏', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  function grant(codes: string[]): void {
    useAuthStore().permissions = new Set(codes)
  }

  /**
   * 按钮文案去掉所有空白再比对。
   *
   * ⚠️ antd 的 `Button` 会在两个汉字之间插一个空格（class
   * `ant-btn-two-chinese-characters`），textContent 里真的有那个空格。
   *    直接比 `'导出'` 会挂 —— 挂的是一个 antd 的排版细节，与被测行为无关。
   */
  function buttonTexts(wrapper: ReturnType<typeof toolbar>): string[] {
    return wrapper.findAll('button').map((b) => b.text().replace(/\s/g, ''))
  }

  function toolbar(props: PropsOf<typeof TableToolbar> = {}) {
    return mount(TableToolbar, {
      props: { selectedCount: 0, ...props } as InstanceType<typeof TableToolbar>['$props'],
      // ⚠️ 必须注册 `v-can`：指令是在 `main.ts` 里 `app.directive('can', …)` 注册的，
      //    单测直接 `mount` 时没有这一步 —— 不注册的话 `v-can` 根本不执行，
      //    "无权时按钮被移除"这条断言会**假绿**（因为按钮压根没被处理过）。
      global: { directives: { can: permission } },
    })
  }

  it('未选中时不显示"已选 N 项"（避免用户数错）', () => {
    const wrapper = toolbar({ selectedCount: 0 })

    expect(wrapper.find('.table-toolbar-count').exists()).toBe(false)
    expect(wrapper.findAll('button').map((b) => b.text())).not.toContain('清空选择')
  })

  it('选中后显示数量；总数更大时显示成"N / 总数"', () => {
    expect(toolbar({ selectedCount: 3 }).get('.table-toolbar-count').text()).toContain('3')
    const withTotal = toolbar({ selectedCount: 3, total: 128 })
    expect(withTotal.get('.table-toolbar-count').text()).toContain('3 / 128')
  })

  it('清空选择抛事件', async () => {
    const wrapper = toolbar({ selectedCount: 2 })
    const button = wrapper.findAll('button').find((b) => b.text() === '清空选择')

    await button?.trigger('click')
    expect(wrapper.emitted('clearSelection')).toHaveLength(1)
  })

  it('不给 exportPermission 就不渲染导出按钮（docs/05 §9.1 要求列表页有导出，但不该给没权限的人一个点下去就 403 的按钮）', () => {
    expect(buttonTexts(toolbar({ selectedCount: 0 }))).not.toContain('导出')
  })

  it('给了 exportPermission 时导出按钮受 v-can 控制（无权时不可见）', async () => {
    /**
     * ⚠️ 断言的是「不可见」而不是「DOM 里没有」。
     * `v-can` 刻意用 `display:none` 而不是 `el.remove()`（见 `directives/permission.ts`
     * 的注释：权限是异步到达的，`remove()` 不可逆 —— 元素被摘掉后就再也回不来）。
     * 所以"无权"的表现是按钮仍在 DOM 里但不可见。
     */
    const exportButton = (wrapper: ReturnType<typeof toolbar>) =>
      wrapper.findAll('button').find((b) => b.text().replace(/\s/g, '') === '导出')

    grant([])
    const denied = exportButton(
      toolbar({ selectedCount: 0, exportPermission: PERM.SYSTEM_EXPORT_MANAGE }),
    )
    expect(denied).toBeDefined()
    expect((denied?.element as HTMLElement).style.display).toBe('none')

    grant([PERM.SYSTEM_EXPORT_MANAGE])
    const allowed = exportButton(
      toolbar({ selectedCount: 0, exportPermission: PERM.SYSTEM_EXPORT_MANAGE }),
    )
    expect((allowed?.element as HTMLElement).style.display).not.toBe('none')
  })

  it('危险批量操作在 `...` 下拉里，且未选中时禁用（docs/06 §2.2「破坏性操作不用红色文字按钮」）', () => {
    const wrapper = toolbar({ selectedCount: 0, dangerActionText: '批量停用' })

    // 直显的按钮里没有"批量停用" —— 它在 `...` 下拉里
    expect(buttonTexts(wrapper)).not.toContain('批量停用')
    expect(buttonTexts(wrapper)).toContain('…')
  })
})
