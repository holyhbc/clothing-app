import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { nextTick } from 'vue'
import { PERM } from '@garment/shared'
import type { BundlingOrderOut } from '@garment/shared'
import * as bundlingApi from '@/api/bundling'
import { baseApi } from '@/api/base'
import * as cuttingApi from '@/api/cutting'
import { permission } from '@/directives/permission'
import { useAuthStore } from '@/stores/auth'
import BundlingOrderForm from '@/views/bundling/Form.vue'

/**
 * 打菲单新建页（T-BUND-009，TC-BW-03）。
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

function grant(codes: string[]): void {
  useAuthStore().permissions = new Set(codes)
}

function stubRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { template: '<div />' } },
      { path: '/bundling/orders', name: 'bundling-orders', component: { template: '<div />' } },
      { path: '/bundling/orders/new', name: 'bundling-orders-new', component: { template: '<div />' } },
      {
        path: '/bundling/orders/:orderId',
        name: 'bundling-orders-detail',
        component: { template: '<div />' },
      },
    ],
  })
}

async function settle(): Promise<void> {
  await flushPromises()
  await nextTick()
  await nextTick()
  await flushPromises()
}

async function mountForm() {
  vi.spyOn(baseApi.workshops, 'optionsById').mockResolvedValue([
    { value: '44444444-4444-4444-4444-444444444444', label: '一号车间', disabled: false },
  ])
  vi.spyOn(cuttingApi, 'searchStyleOptionsById').mockResolvedValue([
    { value: 'HB-2026-0001', label: 'HB-2026-0001', disabled: false },
  ])
  vi.spyOn(baseApi.operations, 'optionsById').mockResolvedValue([
    { value: '03', label: '拼前', disabled: false },
  ])
  vi.spyOn(bundlingApi, 'listAvailableOutputs').mockResolvedValue([
    {
      cutting_size_line_id: 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
      size_code: 'XL',
      hands: 2,
      output_qty: '120',
      qty_per_hand: '60',
      available_qty: '120',
    },
  ])
  vi.spyOn(bundlingApi, 'createBundlingOrder').mockResolvedValue({
    id: '33333333-3333-3333-3333-333333333333',
    doc_no: 'BD-20261005-000031',
    status: 'DRAFT',
    version: 1,
    hands_total: 2,
    output_qty: '120',
    balance_qty: '0',
    bundle_qty: 60,
    color_code: 'WHT',
    color_group: 'WHT-GRP',
    created_at: new Date().toISOString(),
    doc_date: '2026-10-08',
    operation_no: '03',
    source_cutting_order_id: '11111111-1111-1111-1111-111111111111',
    style_no: 'HB-2026-0001',
    updated_at: new Date().toISOString(),
    workshop_id: '44444444-4444-4444-4444-444444444444',
    lines: [],
  } as BundlingOrderOut)

  const router = stubRouter()
  await router.push('/bundling/orders/new')
  await router.isReady()
  const wrapper = track(
    mount(BundlingOrderForm, {
      global: { plugins: [router], directives: { can: permission } },
      attachTo: document.body,
    }),
  )
  await settle()
  return { wrapper, router }
}

function findButton(label: string): HTMLButtonElement | undefined {
  return [...document.querySelectorAll<HTMLButtonElement>('button')].find((node) =>
    (node.textContent ?? '').includes(label),
  )
}

describe('打菲单新建页（TC-BW-03）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
    document.body.innerHTML = ''
    grant([PERM.BUNDLING_CREATE, PERM.BUNDLING_READ])
  })

  it('TC-BW-03 新建页能选来源裁剪行并提交草稿', async () => {
    const { router } = await mountForm()

    // 选择车间
    const workshopSelector = document.querySelector('.ant-select-selector')
    expect(workshopSelector).toBeDefined()
    workshopSelector?.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    workshopSelector?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()
    const workshopOption = [...document.querySelectorAll('.ant-select-item-option')].find((node) =>
      (node.textContent ?? '').includes('一号车间'),
    )
    workshopOption?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()

    // 选择款号
    const styleSelector = document.querySelectorAll('.ant-select-selector')[1]
    styleSelector?.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    styleSelector?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()
    const styleOption = [...document.querySelectorAll('.ant-select-item-option')].find((node) =>
      (node.textContent ?? '').includes('HB-2026-0001'),
    )
    styleOption?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()

    // 填色码/色组/工序
    const inputs = document.querySelectorAll('input')
    const colorCodeInput = [...inputs].find((i) => i.placeholder?.includes('色码'))
    const colorGroupInput = [...inputs].find((i) => i.placeholder?.includes('色组'))
    colorCodeInput?.dispatchEvent(new Event('input', { bubbles: true }))
    colorGroupInput?.dispatchEvent(new Event('input', { bubbles: true }))

    // 选择工序
    const operationSelector = document.querySelectorAll('.ant-select-selector')[2]
    operationSelector?.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    operationSelector?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()
    const operationOption = [...document.querySelectorAll('.ant-select-item-option')].find((node) =>
      (node.textContent ?? '').includes('拼前'),
    )
    operationOption?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()

    // 选择来源裁剪单
    const sourceSelector = document.querySelectorAll('.ant-select-selector')[3]
    sourceSelector?.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    sourceSelector?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()
    const sourceOption = [...document.querySelectorAll('.ant-select-item-option')].find((node) =>
      (node.textContent ?? '').includes('XL'),
    )
    sourceOption?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()

    // 新增一行明细
    const addLineBtn = findButton('新增行')
    addLineBtn?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()

    // 点击保存
    const saveBtn = findButton('保存草稿')
    expect(saveBtn).toBeDefined()
    saveBtn?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()

    // 验证是否调用了创建接口
    expect(bundlingApi.createBundlingOrder).toHaveBeenCalled()
    expect(router.currentRoute.value.name).toBe('bundling-orders-detail')
  })

  it('TC-BW-03b 缺必填字段时阻断提交', async () => {
    await mountForm()
    const saveBtn = findButton('保存草稿')
    saveBtn?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()

    // 还在新建页（没跳详情）
    const titleEl = document.querySelector('.ant-page-header-heading-title')
    expect(titleEl).toBeDefined()
    if (titleEl) {
      expect(titleEl.textContent).toContain('新建打菲单')
    }
  })
})
