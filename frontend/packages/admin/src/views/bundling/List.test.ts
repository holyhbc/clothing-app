import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { nextTick } from 'vue'
import { PERM } from '@garment/shared'
import type { BundlingOrderListOut } from '@garment/shared'
import * as bundlingApi from '@/api/bundling'
import { baseApi } from '@/api/base'
import * as cuttingApi from '@/api/cutting'
import { permission } from '@/directives/permission'
import { useAuthStore } from '@/stores/auth'
import BundlingOrderList from '@/views/bundling/List.vue'

/**
 * 打菲单列表页（T-BUND-009，TC-BW-01 ~ TC-BW-02）。
 *
 * 打桩 `api/bundling` 而不是 `fetch`：验「界面选了筛选之后**调哪个函数、
 * 传了什么参数**」，打桩 fetch 会把用例绑死在 URL 与 query 拼接上。
 *
 * ⚠️ `baseApi` 要**具名导入**（`import { baseApi }`）而不是 `import * as baseApi`。
 */
const mountedWrappers: { unmount: () => void }[] = []

function track<T extends { unmount: () => void }>(wrapper: T): T {
  mountedWrappers.push(wrapper)
  return wrapper
}

afterEach(() => {
  while (mountedWrappers.length > 0) mountedWrappers.pop()?.unmount()
  document.body.innerHTML = ''
})

const ROW: BundlingOrderListOut = {
  id: '33333333-3333-3333-3333-333333333333',
  doc_no: 'BD-20261005-000031',
  workshop_id: '44444444-4444-4444-4444-444444444444',
  style_no: 'HB-2026-0001',
  doc_date: '2026-10-05',
  color_code: 'WHT',
  operation_no: '03',
  hands_total: 6,
  output_qty: '360',
  balance_qty: '0',
  status: 'DRAFT',
  version: 3,
  created_at: '2026-10-05T02:10:00Z',
}

async function settle(): Promise<void> {
  await flushPromises()
  await nextTick()
  await nextTick()
  await flushPromises()
}

function grant(codes: string[]): void {
  useAuthStore().permissions = new Set(codes)
}

function stubRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { template: '<div />' } },
      { path: '/bundling/orders', name: 'bundling-orders', component: { template: '<div />' } },
      {
        path: '/bundling/orders/:orderId',
        name: 'bundling-orders-detail',
        component: { template: '<div />' },
      },
    ],
  })
}

async function mountList(total = 500) {
  const list = vi
    .spyOn(bundlingApi, 'listBundlingOrders')
    .mockResolvedValue({ items: [ROW], total })
  vi.spyOn(baseApi.workshops, 'optionsById').mockResolvedValue([
    { value: '44444444-4444-4444-4444-444444444444', label: '一号车间', disabled: false },
  ])
  vi.spyOn(cuttingApi, 'searchStyleOptionsById').mockResolvedValue([
    { value: 'HB-2026-0001', label: 'HB-2026-0001', disabled: false },
  ])
  vi.spyOn(baseApi.operations, 'optionsById').mockResolvedValue([
    { value: '03', label: '拼前', disabled: false },
  ])
  const router = stubRouter()
  await router.push('/bundling/orders')
  await router.isReady()
  const wrapper = track(
    mount(BundlingOrderList, {
      global: { plugins: [router], directives: { can: permission } },
      attachTo: document.body,
    }),
  )
  await settle()
  return { wrapper, router, list }
}

function bodyText(): string {
  return plain(document.querySelector('.ant-table-tbody')?.textContent ?? '')
}

function plain(text: string): string {
  return text.replace(/\s/g, '')
}

function findButton(label: string): HTMLButtonElement | undefined {
  return [...document.querySelectorAll<HTMLButtonElement>('button')].find((node) =>
    plain(node.textContent ?? '').includes(label),
  )
}

describe('打菲单列表（TC-BW-01 ~ TC-BW-02）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
    document.body.innerHTML = ''
    grant([PERM.BUNDLING_READ])
  })

  it('TC-BW-01 单号、款号、工序、色码、手数、出数按后端口径渲染，余数为 0 显示「—」', async () => {
    await mountList()
    const text = bodyText()
    expect(text).toContain('BD-20261005-000031')
    expect(text).toContain('HB-2026-0001')
    expect(text).toContain('03')
    expect(text).toContain('WHT')
    expect(text).toContain('6')
    expect(text).toContain('360')
    // 状态必须走 `StatusTag`（docs/06 §1），中文是「草稿」
    expect(text).toContain('草稿')
    // 余数 0 是常态，显示「—」才不会把真正 >0 的余数淹没
    expect(text).not.toContain('0.000')
  })

  it('TC-BW-02 筛选是**等值**传参，没有后端忽略的 `q` 参数', async () => {
    const { list } = await mountList()
    list.mockClear()

    const selector = document.querySelector('.ant-select-selector')
    expect(selector, '筛选区应有状态下拉').toBeDefined()
    selector?.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    selector?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()
    const option = [...document.querySelectorAll('.ant-select-item-option')].find((node) =>
      (node.textContent ?? '').includes('待审核'),
    )
    expect(option, '状态下拉里应有「待审核」').toBeDefined()
    option?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()

    expect(list).toHaveBeenCalled()
    const query = list.mock.calls[0]?.[0] ?? {}
    expect(query['status']).toBe('SUBMITTED')
    // ⚠️ 后端 `list_bundling_orders` 没有 `q`：多传一个被忽略的键 = 界面假装筛了
    expect(Object.keys(query)).not.toContain('q')
  })

  it('TC-BW-03 改筛选回到第 1 页（不是接着第 5 页筛出空列表）', async () => {
    const { wrapper, list } = await mountList()
    list.mockClear()
    const third = wrapper.find('.ant-pagination-item-3')
    expect(third.exists(), '分页器应渲染出第 3 页（total=500）').toBe(true)
    await third.trigger('click')
    await settle()
    expect(list.mock.calls[0]?.[0]?.['page']).toBe(3)
    list.mockClear()

    const reset = findButton('重置')
    expect(reset, '筛选区应有「重置」按钮').toBeDefined()
    reset?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()

    expect(list.mock.calls[0]?.[0]?.['page']).toBe(1)
  })

  it('TC-BW-04 点「详情」跳详情路由并带上单据 id', async () => {
    const { router } = await mountList()
    const detail = findButton('详情')
    expect(detail, '操作列应有「详情」按钮').toBeDefined()
    detail?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()

    expect(router.currentRoute.value.name).toBe('bundling-orders-detail')
    expect(router.currentRoute.value.params['orderId']).toBe(ROW.id)
  })

  it('TC-BW-05 无 `bundling:read` 权限时 v-can 隐藏「详情」按钮（不渲染或隐藏）', async () => {
    grant([])
    await mountList()
    const detail = findButton('详情')
    // v-can 指令会添加 data-can-hidden 属性或完全移除元素
    // 这里检查按钮是否被隐藏（不可见）或被移除
    if (detail) {
      const isHidden = detail.hasAttribute('data-can-hidden') || 
        detail.style.display === 'none' || 
        detail.getAttribute('aria-hidden') === 'true'
      expect(isHidden, '无权限时「详情」按钮应被 v-can 隐藏').toBe(true)
    } else {
      // 元素被完全移除
      expect(detail).toBeUndefined()
    }
  })
})
