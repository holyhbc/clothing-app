import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { Drawer } from 'ant-design-vue'
import type { PageData } from '@garment/shared'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { nextTick } from 'vue'
import { PERM } from '@garment/shared'
import * as bundlingApi from '@/api/bundling'
import { permission } from '@/directives/permission'
import { useAuthStore } from '@/stores/auth'
import BundlingOrderDetail from '@/views/bundling/Detail.vue'
import type { BundlingOrderOut } from '@garment/shared'

/**
 * 打菲单详情页（T-BUND-009，TC-BW-04）。
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

const ORDER = {
  id: '33333333-3333-3333-3333-333333333333',
  doc_no: 'BD-20261005-000031',
  doc_date: '2026-10-05',
  style_no: 'HB-2026-0001',
  color_code: 'WHT',
  color_group: 'WHT-GRP',
  operation_no: '03',
  workshop_id: '44444444-4444-4444-4444-444444444444',
  bundle_qty: 60,
  hands_total: 6,
  output_qty: '360',
  balance_qty: '0',
  label_print_qty: 0,
  status: 'APPROVED',
  version: 5,
  created_at: '2026-10-05T02:10:00Z',
  updated_at: '2026-10-05T02:15:00Z',
  approved_at: '2026-10-05T02:12:00Z',
  approved_by: '55555555-5555-5555-5555-555555555555',
  source_cutting_order_id: '11111111-1111-1111-1111-111111111111',
  lines: [
    {
      id: 'line-1',
      line_no: 1,
      size_code: 'XL',
      color_code: 'WHT',
      operation_no: '03',
      hands: 2,
      planned_qty: '120',
      available_qty_before: '120',
      cutting_size_line_id: 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
      group_no: 'A组',
      workstation_no: 'WS-01',
      version: 1,
      created_at: '2026-10-05T02:10:00Z',
      updated_at: '2026-10-05T02:10:00Z',
    },
  ],
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
      { path: '/base/styles/:styleNo', name: 'base-styles-detail', component: { template: '<div />' } },
      { path: '/cutting/orders/:orderId', name: 'cutting-orders-detail', component: { template: '<div />' } },
    ],
  })
}

async function mountDetail() {
  vi.spyOn(bundlingApi, 'getBundlingOrder').mockResolvedValue(ORDER as BundlingOrderOut)
  vi.spyOn(bundlingApi, 'listBundlingOrderLogs').mockResolvedValue({ items: [], total: 0, page: 1, page_size: 100 } as PageData<unknown>)

  const router = stubRouter()
  await router.push('/bundling/orders/33333333-3333-3333-3333-333333333333')
  await router.isReady()
  const wrapper = track(
    mount(BundlingOrderDetail, {
      global: { plugins: [router], directives: { can: permission } },
      attachTo: document.body,
    }),
  )
  await settle()
  return { wrapper, router }
}


describe('打菲单详情页（TC-BW-04）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
    document.body.innerHTML = ''
    grant([PERM.BUNDLING_READ])
  })

  it('TC-BW-04 详情页渲染溯源面包屑：款号 > 裁剪单 > 打菲单 > 码', async () => {
    await mountDetail()
    const text = document.body.textContent ?? ''
    expect(text).toContain('HB-2026-0001')
    expect(text).toContain('裁剪单')
    expect(text).toContain('打菲单')
    expect(text).toContain('BD-20261005-000031')
    // 「一码 = 一手 = 一个员工」提示
    expect(text).toContain('一码 = 一手 = 一个员工')
  })

  it('TC-BW-04b 表头字段按后端口径显示（不做二次计算）', async () => {
    await mountDetail()
    const text = document.body.textContent ?? ''
    expect(text).toContain('BD-20261005-000031')
    expect(text).toContain('HB-2026-0001')
    expect(text).toContain('03')
    expect(text).toContain('WHT')
    expect(text).toContain('已审核') // StatusTag
    expect(text).toContain('360') // output_qty
    expect(text).toContain('6') // hands_total
  })

  it('TC-BW-04c 明细行显示手数/计划件数/可打菲量/来源裁剪明细行', async () => {
    await mountDetail()
    const text = document.body.textContent ?? ''
    expect(text).toContain('XL')
    expect(text).toContain('2') // hands
    expect(text).toContain('120') // planned_qty
    expect(text).toContain('aaaaaaaa') // cutting_size_line_id 前 8 位
  })

  it('TC-BW-04d 已审核态显示审核/反审核/打印等操作，无 `bundling:print` 权限隐藏打印按钮', async () => {
    grant([PERM.BUNDLING_READ, PERM.BUNDLING_APPROVE, PERM.BUNDLING_REVERSE])
    await mountDetail()
    expect(findButton('反审核')).toBeDefined()
    expect(findButton('打印标签')).toBeUndefined()

    grant([PERM.BUNDLING_READ, PERM.BUNDLING_APPROVE, PERM.BUNDLING_REVERSE, PERM.BUNDLING_PRINT])
    await mountDetail()
    expect(findButton('打印标签')).toBeDefined()
  })

  it('TC-BW-04e 点「变更历史」打开 Drawer 并调用日志接口', async () => {
    const { wrapper } = await mountDetail()
    const historyBtn = findButton('变更历史')
    expect(historyBtn).toBeDefined()
    historyBtn?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()

    // 断言 Drawer 的 `open` prop，而不是 `.ant-drawer-content` 这个**内部 class**：
    // ① antd 升级会改 DOM 结构，class 断言会静默失效；
    // ② Drawer 走 Teleport，内容挂到 body 上，wrapper.find 根本看不到 ——
    //    这条断言当时是「Drawer 明明开了却报红」的原因。
    expect(wrapper.findComponent(Drawer).props('open')).toBe(true)

    // 测试名承诺了「并调用日志接口」，原断言却漏了 —— 补上，
    // 否则这个用例实际只验了「Drawer 打开」，接口挂了它照样绿。
    expect(bundlingApi.listBundlingOrderLogs).toHaveBeenCalledWith(
      expect.any(String),
      { page: 1, page_size: 100 },
    )
  })

  it('TC-BW-05 无 `bundling:read` 权限时 v-can 隐藏按钮', async () => {
    grant([])
    await mountDetail()
    // 无权限时所有需要权限的按钮都应被 v-can 隐藏
    expect(findButton('反审核')).toBeUndefined()
    expect(findButton('打印标签')).toBeUndefined()
    expect(findButton('变更历史')).toBeUndefined()
  })
})


function findButton(label: string): HTMLButtonElement | undefined {
  return [...document.querySelectorAll<HTMLButtonElement>('button')].find((node) =>
    (node.textContent ?? '').includes(label) && !node.hasAttribute('data-can-hidden'),
  )
}
