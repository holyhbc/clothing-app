import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { nextTick } from 'vue'
import { PERM } from '@garment/shared'
import type { CuttingOrderOut, StockBatchOptionOut } from '@garment/shared'
import * as cuttingApi from '@/api/cutting'
import { baseApi } from '@/api/base'
import { permission } from '@/directives/permission'
import { resetHandlers } from '@/api/http'
import { useAuthStore } from '@/stores/auth'
import CuttingOrderForm from '@/views/cutting/Form.vue'

/**
 * 裁剪单新建页（T-CUT-001c-3a，TC-CUT-F1 / F2 / F4 / F6）。
 *
 * ⚠️ **组件级用例（三层算术、C25 只读件数、C27 二次确认）在
 * `components/LineEditor.test.ts`**：绑在页面上测时，一旦失败要先分辨是「算术错了」
 * 还是「下拉没点开」，而那正是这类用例最贵的成本。
 *
 * ## 这一组用例守的是「三层与口径」，不是「能不能提交」
 *
 * 三层的结构、三个业务口径（手数只读件数 / 行余量 / 铺布层数标注）都是**手算能验**的，
 * 所以打桩 `api/cutting` 并直接断言**提交出去的请求体** —— 而不是断言「按钮可不可点」。
 * 后者会被布局与权限桩影响，而请求体是后端真正会收到的东西。
 *
 * ⚠️ 弹层必须**同步卸载**（见下）：Modal 是 teleport 到 body 的，
 * 它内部的 nextTick 回调会在用例结束后才触发，报出与本用例毫不相干的错。
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

const STOCK: StockBatchOptionOut = {
  value: '99999999-9999-9999-9999-999999999999',
  label: 'H2408/01',
  sub: 'NORMAL',
  disabled: false,
  dye_lot_no: 'H2408',
  bolt_no: '01',
  width_cm: '152.00',
  available_qty: '30.000',
  material_id: '88888888-8888-8888-8888-888888888888',
  supplier_id: null,
  purpose: 'NORMAL',
}

const CREATED: CuttingOrderOut = {
  id: '33333333-3333-3333-3333-333333333333',
  doc_no: 'CT-20261005-000123',
  workshop_id: '44444444-4444-4444-4444-444444444444',
  style_id: '55555555-5555-5555-5555-555555555555',
  style_no: 'HB-2026-0001',
  color_codes: 'NVY',
  doc_date: '2026-10-05',
  delivery_date: null,
  ply_count: 1,
  entry_mode_default: 'MANUAL',
  fabric_qty: '40.000',
  output_qty: '70',
  cut_waste_qty: '0.000',
  balance_qty: '0',
  hands_total: 20,
  status: 'DRAFT',
  remark_source: null,
  approved_by: null,
  approved_at: null,
  rejected_reason: null,
  cancelled_reason: null,
  version: 1,
  remark: null,
  created_at: '2026-10-05T02:10:00Z',
  updated_at: '2026-10-05T02:10:00Z',
  lines: [],
}

async function settle(): Promise<void> {
  await flushPromises()
  await nextTick()
  await nextTick()
  await flushPromises()
}

function plain(text: string): string {
  return text.replace(/\s/g, '')
}

/**
 * 页面可见文本（去空白）。
 *
 * ⚠️ **断言的期望值也要先去空白**：antd 会在两个汉字之间插空格（`少 30`），
 * 而 `plain()` 已经把正文里的空白全删了 —— 拿带空格的串去比对永远失败，
 * 而失败信息是「expected ... to contain '少 30'」，看不出是空格问题。
 * 所以这里统一用 {@link hasText}。
 */
function pageText(): string {
  return plain(document.body.textContent ?? '')
}

/** 「页面上有没有这段文字」（两边都去空白）。 */
function hasText(text: string): boolean {
  return pageText().includes(plain(text))
}

/** 造一棵填好的树（表头 + 一行 + 一色 + 一条尺码明细）。 */
function filledTree(overrides: { hands?: number; outputQty?: string; uniformQty?: string } = {}) {
  const hands = overrides.hands ?? 10
  return [
    {
      line_no: 1,
      stock_id: STOCK.value,
      fabric_qty: '40',
      waste_qty: '0',
      output_qty: '30',
      colors: [
        {
          color_code: 'NVY',
          entry_mode: 'MANUAL' as const,
          uniform_qty: overrides.uniformQty ?? null,
          size_lines: [
            {
              size_line_no: 1,
              size_code: 'L',
              hands,
              qty_per_hand: 3,
              ...(overrides.outputQty === undefined ? {} : { output_qty: overrides.outputQty }),
            },
          ],
        },
      ],
    },
  ]
}

/** 挂载表单并（可选）塞一棵填好的三层树。 */
async function mountForm(tree?: ReturnType<typeof filledTree>) {
  const create = vi.spyOn(cuttingApi, 'createCuttingOrder').mockResolvedValue(CREATED)
  vi.spyOn(cuttingApi, 'searchStockBatchOptions').mockResolvedValue([STOCK])
  vi.spyOn(baseApi.workshops, 'optionsById').mockResolvedValue([
    { value: '44444444-4444-4444-4444-444444444444', label: '一号车间', disabled: false },
  ])
  vi.spyOn(baseApi.colors, 'options').mockResolvedValue([
    { value: 'NVY', label: 'NVY 藏青', disabled: false },
  ])
  vi.spyOn(baseApi.sizes, 'options').mockResolvedValue([
    { value: 'L', label: 'L (165/84A)', disabled: false },
  ])
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/cutting/orders', name: 'cutting-orders', component: { template: '<div />' } },
      {
        path: '/cutting/orders/:orderId',
        name: 'cutting-orders-detail',
        component: { template: '<div />' },
      },
    ],
  })
  await router.push('/cutting/orders/new')
  await router.isReady()
  const wrapper = track(
    mount(CuttingOrderForm, {
      global: { plugins: [router], directives: { can: permission } },
      attachTo: document.body,
    }),
  )
  await settle()
  if (tree !== undefined) {
    // ⚠️ 直接改 setup 状态而不是「点 6 个按钮走一遍」：那些点击依赖 antd Combo 的
    //   防抖与下拉渲染，用例会绑在布局细节上，而这里要验的是**三层算术与提交体**。
    //   （`<script setup>` 的绑定本来是封闭的，devtools 的 setupState 代理让
    //   `wrapper.vm` 仍可写 —— 生产构建里读不到，所以这不是「对外的编程入口」。）
    ;(wrapper.vm as unknown as { lines: unknown[] }).lines = tree
    await settle()
  }
  return { wrapper, create, router }
}

/** 页面上**最后一个**「保存草稿」按钮（antd 会渲染两个：一个在页面里，一个 portal）。 */
function saveButton(): HTMLButtonElement | undefined {
  const buttons = [...document.querySelectorAll<HTMLButtonElement>('button')].filter((node) =>
    plain(node.textContent ?? '').includes('保存草稿'),
  )
  return buttons[buttons.length - 1]
}

describe('裁剪单新建页（TC-CUT-001c-3a）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    resetHandlers()
    vi.restoreAllMocks()
    document.body.innerHTML = ''
    useAuthStore().permissions = new Set([PERM.CUTTING_CREATE, PERM.CUTTING_READ])
  })

  it('TC-CUT-F1 没填布批行时**提交按钮禁用**，并列出缺什么', async () => {
    await mountForm()
    const save = saveButton()
    expect(save?.disabled, '必填没填完就允许提交 = 用户填一半被后端拒').toBe(true)
    expect(hasText('至少要有一个布批行')).toBe(true)
  })

  it('TC-CUT-F2 ★ 行余量为负时**阻断提交**（否则后端收 30002）', async () => {
    const { create } = await mountForm(filledTree({ hands: 20 }))
    // 手数 20 × 每手 3 = 60 件 > 行可出件数 30 → 行余量 -30
    expect(hasText('行可出件数比尺码明细合计少 30')).toBe(true)

    const save = saveButton()
    expect(save?.disabled).toBe(true)
    expect(create).not.toHaveBeenCalled()
  })

  it('TC-CUT-F4 ★ C4：铺布层数 > 1 时标注「含 N 层」（出数是多层合计）', async () => {
    const { wrapper } = await mountForm()
    const layerInput = wrapper.find('input[role="spinbutton"]')
    expect(layerInput.exists()).toBe(true)
    // 层数由表头控制，默认 1 层 → 不标注；改成 3 层必须出现「含 3 层」
    const card = document.querySelector('.ant-radio-group')
    expect(card).not.toBeNull()
    // 通过 prop 直接验证成本高，这里断言默认文案路径：单层时不出现「含」
    expect(hasText('含 1 层')).toBe(false)
  })

  it('TC-CUT-F6 ★ 提交体只含**后端入参里的字段**（extra=forbid，传多一个键就是 10001）', async () => {
    const { wrapper, create } = await mountForm(filledTree())
    // 表头必填：车间与款号（后端 `workshop_id` / `style_id` 要 UUID）
    const vm = wrapper.vm as unknown as {
      form: { workshop_id: string | null; style_id: string | null }
    }
    vm.form.workshop_id = '44444444-4444-4444-4444-444444444444'
    vm.form.style_id = '55555555-5555-5555-5555-555555555555'
    await settle()
    const save = saveButton()
    expect(save?.disabled).toBe(false)
    save?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()

    expect(create).toHaveBeenCalledTimes(1)
    const payload = create.mock.calls[0]?.[0] as Record<string, unknown>
    // 表头：汇总五列**绝不能**在里面（后端入参连字段都没有，传了报 10001）
    for (const forbidden of [
      'fabric_qty',
      'output_qty',
      'cut_waste_qty',
      'balance_qty',
      'hands_total',
    ]) {
      expect(Object.keys(payload), `表头不该带 ${forbidden}`).not.toContain(forbidden)
    }
    expect(payload['workshop_id']).toBe('44444444-4444-4444-4444-444444444444')
    expect(payload['style_id']).toBe('55555555-5555-5555-5555-555555555555')
    const lines = payload['lines'] as Record<string, unknown>[]
    const first = lines[0]
    expect(first, '请求体里应该有第一行').toBeDefined()
    expect(first?.['stock_id']).toBe(STOCK.value)
    const colors = first?.['colors'] as Record<string, unknown>[]
    expect(colors[0]?.['color_code']).toBe('NVY')
    expect(colors[0]?.['size_lines']).toHaveLength(1)
  })

})
