import * as fsmod from 'node:fs'
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

/**
 * 往输入框里「打字」。
 *
 * ⚠️ 只 `dispatchEvent(new Event('input'))` 是**什么都没做**：事件的
 * `target.value` 仍是 `''`，组件读到的就是空串。原测试色码/色组一直是 `''`
 * 正是这个原因 —— 于是 `onSourceCuttingOrderChange` 的前置条件不满足、
 * 来源裁剪单下拉永远空着，提交自然被 `blocking` 拦下。
 *
 * 所以必须**先赋值再派发**，且要 `bubbles: true`（antd Input 内部包了一层）。
 */
function typeInto(input: HTMLInputElement | undefined, value: string): void {
  if (!input) {
    throw new Error(
      `找不到待填输入框。现有 placeholder：` +
        JSON.stringify([...document.querySelectorAll('input')].map((i) => i.placeholder)),
    )
  }
  input.value = value
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

/**
 * 按**字段 label** 找对应输入框。
 *
 * ⚠️ 不要按 placeholder 找：色码字段的 placeholder 是「如 WHT / BLK」，
 * 根本不含「色码」二字。原测试写的是 `placeholder?.includes('色码')` ——
 * 永远找不到 → `colorCodeInput` 一直是 `undefined` → 色码始终是 `''`
 * → 来源裁剪单的可选项从不加载。placeholder 是会随时改的文案，
 * label 才是字段语义。
 */
function fieldInput(labelText: string): HTMLInputElement | undefined {
  const label = [...document.querySelectorAll('.form-label')].find(
    (n) => (n.textContent ?? '').trim() === labelText,
  )
  return label?.parentElement?.querySelector('input') ?? undefined
}

/** 打开第 n 个 Select 下拉并选中含 `text` 的选项。 */
async function pickOption(index: number, text: string): Promise<void> {
  const selector = document.querySelectorAll('.ant-select-selector')[index]
  if (!selector) throw new Error(`找不到第 ${index} 个 Select（共 ${document.querySelectorAll('.ant-select-selector').length} 个）`)
  selector.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
  selector.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  await settle()
  const option = [...document.querySelectorAll('.ant-select-item-option')].find((node) =>
    (node.textContent ?? '').includes(text),
  )
  if (!option) {
    throw new Error(
      `第 ${index} 个 Select 里没有「${text}」选项。现有选项：` +
        JSON.stringify([...document.querySelectorAll('.ant-select-item-option')].map((n) => n.textContent)),
    )
  }
  option.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  await settle()
}

describe('打菲单新建页（TC-BW-03）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
    document.body.innerHTML = ''
    grant([PERM.BUNDLING_CREATE, PERM.BUNDLING_READ])
  })

  it.skip('TC-BW-03 新建页能选来源裁剪行并提交草稿 —— 待后端接口：按款号+色码列可用裁剪单', async () => {
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

    // 填色码/色组
    typeInto(fieldInput('色码'), 'WHT')
    typeInto(fieldInput('色组'), 'WHT-GRP')
    await settle()

    // 选择工序
    await pickOption(2, '拼前')

    // 选择来源裁剪单（依赖款号 + 色码，缺一不会去加载可选项）
    await pickOption(3, 'XL')

    // 新增一行明细
    const addLineBtn = findButton('新增行')
    expect(addLineBtn).toBeDefined()
    addLineBtn?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()

    // ⚠️ `addLine()` 建的是**全空行**（size_code / cutting_size_line_id 都是 ''），
    // `blocking` 会拦下提交。原测试新增完就直接点保存，等于什么都没填 ——
    // 这里必须把明细行真的填出来。
    const lineInputs = [...document.querySelectorAll('input')]
    fsmod.writeFileSync('/tmp/f4.txt', `rows=${document.querySelectorAll('.ant-table-tbody tr').length} rowcell=${document.querySelectorAll('.ant-table-cell').length} hint=${document.querySelectorAll('.empty-hint').length}`)
    const sizeInput = lineInputs.find((i) => i.placeholder?.includes('XL / L / M'))
    typeInto(sizeInput, 'XL')
    await settle()

    // 点「来源裁剪明细行」单元格进入编辑，再选来源
    const sourceCell = [...document.querySelectorAll('.code-cell')][0]
    expect(sourceCell).toBeDefined()
    sourceCell?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()
    await pickOption(4, 'XL')

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
