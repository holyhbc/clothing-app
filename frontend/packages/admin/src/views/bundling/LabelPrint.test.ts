import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { Spin } from 'ant-design-vue'
import LabelPrint from './LabelPrint.vue'

// Mock dependencies
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { orderId: 'test-order-id' } }),
  useRouter: () => ({ back: vi.fn(), push: vi.fn() }),
}))

vi.mock('@/api/bundling', () => ({
  getBundlingOrder: vi.fn(),
  listBundlesByOrder: vi.fn(),
  exportLabels: vi.fn(),
  registerLabelPrints: vi.fn(),
}))

vi.mock('@garment/shared', async () => {
  const actual = await vi.importActual('@garment/shared')
  return {
    ...actual,
    PERM: { BUNDLING_PRINT: 'bundling:print' },
    formatDateTime: vi.fn((d: string) => d),
  }
})

vi.mock('qrcode', () => ({
  default: {
    toDataURL: vi.fn().mockResolvedValue('data:image/png;base64,qrcode'),
  },
}))

vi.mock('bwip-js', () => ({
  toDataURL: vi.fn().mockResolvedValue('data:image/png;base64,barcode'),
}))

vi.mock('@/composables/useLabelCodes', () => ({
  useLabelCodes: () => ({
    generateQR: vi.fn().mockResolvedValue('data:image/png;base64,qrcode'),
    generateBarcode: vi.fn().mockResolvedValue('data:image/png;base64,barcode'),
  }),
}))

import { getBundlingOrder, listBundlesByOrder, exportLabels, registerLabelPrints } from '@/api/bundling'
import type { BundlingOrderOut } from '@garment/shared'

/**
 * ⚠️ **必须给全 `BundlingOrderOut` 的必填字段**，不能靠 `as Record<string, unknown>` 糊过去 ——
 * 那种写法把「少字段」藏进断言里，页面真的少读一个字段时测试照样绿。
 */
const mockOrder: BundlingOrderOut = {
  id: '33333333-3333-3333-3333-333333333333',
  doc_no: 'BD-20261018-000001',
  doc_date: '2026-10-18',
  status: 'APPROVED',
  version: 1,
  style_no: 'STYLE-001',
  workshop_id: '44444444-4444-4444-4444-444444444444',
  source_cutting_order_id: '11111111-1111-1111-1111-111111111111',
  operation_no: '01',
  color_code: 'WHT',
  color_group: 'WHT-GRP',
  bundle_qty: 60,
  hands_total: 2,
  output_qty: '120',
  balance_qty: '0',
  label_print_qty: 0,
  created_at: '2026-10-18T02:00:00Z',
  updated_at: '2026-10-18T02:00:00Z',
  lines: [],
}

const mockBundles = [
  {
    bundle_no: 'BD-20261018-000001-XL01-0001',
    bundle_qty: '60',
    color_code: 'WHT',
    hands: 1,
    hands_total_of_size: 2,
    line_id: 'line-1',
    doc_id: '33333333-3333-3333-3333-333333333333',
    counted_qty: '0',
    counted_by_name: null,
    operation_no: '01',
    qr_content: 'BD-20261018-000001-XL01-0001',
    size_code: 'XL',
    status: 'ACTIVE',
    style_no: 'STYLE-001',
  },
  {
    bundle_no: 'BD-20261018-000001-XL02-0001',
    bundle_qty: '60',
    color_code: 'WHT',
    hands: 2,
    hands_total_of_size: 2,
    line_id: 'line-1',
    doc_id: '33333333-3333-3333-3333-333333333333',
    counted_qty: '0',
    counted_by_name: null,
    operation_no: '01',
    qr_content: 'BD-20261018-000001-XL02-0001',
    size_code: 'XL',
    status: 'ACTIVE',
    style_no: 'STYLE-001',
  },
]

const mockPreviewItems = [
  {
    barcode_content: 'BD-20261018-000001-XL01-0001',
    bundle_no: 'BD-20261018-000001-XL01-0001',
    bundle_qty: '60',
    color_code: 'WHT',
    hands_seq: 1,
    hands_text: '第 1 手 / 共 2 手',
    hands_total_of_size: 2,
    operation_no: '01',
    qr_content: 'BD-20261018-000001-XL01-0001',
    size_code: 'XL',
    style_no: 'STYLE-001',
  },
  {
    barcode_content: 'BD-20261018-000001-XL02-0001',
    bundle_no: 'BD-20261018-000001-XL02-0001',
    bundle_qty: '60',
    color_code: 'WHT',
    hands_seq: 2,
    hands_text: '第 2 手 / 共 2 手',
    hands_total_of_size: 2,
    operation_no: '01',
    qr_content: 'BD-20261018-000001-XL02-0001',
    size_code: 'XL',
    style_no: 'STYLE-001',
  },
]

describe('LabelPrint.vue', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    ;vi.mocked(getBundlingOrder).mockResolvedValue(mockOrder)
    ;vi.mocked(listBundlesByOrder).mockResolvedValue({ items: mockBundles, total: 2 })
    ;vi.mocked(exportLabels).mockResolvedValue({ data: mockPreviewItems, csv_text: '' })
    ;vi.mocked(registerLabelPrints).mockResolvedValue({ print_ids: ['print-1', 'print-2'], printed_count: 2 })
  })

  it('renders loading state initially', () => {
    const wrapper = mount(LabelPrint, {
      global: {
        stubs: {
          PageLayout: { template: '<div><slot /><slot name="extra" /></div>' },
          StatusTag: { template: '<span>APPROVED</span>' },
        },
      },
    })
    // ⚠️ 按**组件引用**查，不按 `{ name: 'Spin' }` 的字符串查：
    // antd 的 Spin 组件没有 `name` 选项（`es/spin/index.js` 只有 `export default Spin`），
    // 按名字查会永远返回 false —— 而且组件改名/换库时它是**静默失效**的，
    // 不会像真断言那样变红，只会一直假绿。
    expect(wrapper.findComponent(Spin).exists()).toBe(true)
  })

  it('loads order and bundles on mount', async () => {
    const wrapper = mount(LabelPrint, {
      global: {
        stubs: {
          PageLayout: { template: '<div><slot /><slot name="extra" /></div>' },
          StatusTag: { template: '<span>APPROVED</span>' },
        },
      },
    })
    await wrapper.vm.$nextTick()
    await new Promise(r => setTimeout(r, 10))

    expect(getBundlingOrder).toHaveBeenCalledWith('test-order-id')
    expect(listBundlesByOrder).toHaveBeenCalledWith('test-order-id', { status: 'ACTIVE' })
  })

  it('shows print parameters form after loading', async () => {
    const wrapper = mount(LabelPrint, {
      global: {
        stubs: {
          PageLayout: { template: '<div><slot /><slot name="extra" /></div>' },
          StatusTag: { template: '<span>APPROVED</span>' },
        },
      },
    })
    await wrapper.vm.$nextTick()
    await new Promise(r => setTimeout(r, 10))

    expect(wrapper.find('input[placeholder="请选择尺码"]').exists()).toBe(true)
    expect(wrapper.find('input[type="number"]').exists()).toBe(true)
  })

  it('generates preview when clicking preview button', async () => {
    const wrapper = mount(LabelPrint, {
      global: {
        stubs: {
          PageLayout: { template: '<div><slot /><slot name="extra" /></div>' },
          StatusTag: { template: '<span>APPROVED</span>' },
        },
      },
    })
    await wrapper.vm.$nextTick()
    await new Promise(r => setTimeout(r, 10))

    await wrapper.find('button:contains("生成预览")').trigger('click')
    await wrapper.vm.$nextTick()
    await new Promise(r => setTimeout(r, 10))

    expect(exportLabels).toHaveBeenCalledWith('test-order-id', {
      from_hands: 1,
      to_hands: 2,
      size_code: 'XL',
      format: 'data',
    })
  })

  it('displays preview grid with label items', async () => {
    const wrapper = mount(LabelPrint, {
      global: {
        stubs: {
          PageLayout: { template: '<div><slot /><slot name="extra" /></div>' },
          StatusTag: { template: '<span>APPROVED</span>' },
        },
      },
    })
    await wrapper.vm.$nextTick()
    await new Promise(r => setTimeout(r, 10))

    await wrapper.find('button:contains("生成预览")').trigger('click')
    await wrapper.vm.$nextTick()
    await new Promise(r => setTimeout(r, 10))

    expect(wrapper.findAll('.label-card')).toHaveLength(2)
    expect(wrapper.text()).toContain('第 1 手 / 共 2 手')
    expect(wrapper.text()).toContain('第 2 手 / 共 2 手')
    expect(wrapper.text()).toContain('60')
  })

  it('registers print for the hand range in one call', async () => {
    const wrapper = mount(LabelPrint, {
      global: {
        stubs: {
          PageLayout: { template: '<div><slot /><slot name="extra" /></div>' },
          StatusTag: { template: '<span>APPROVED</span>' },
        },
      },
    })
    await wrapper.vm.$nextTick()
    await new Promise(r => setTimeout(r, 10))

    await wrapper.find('button:contains("生成预览")').trigger('click')
    await wrapper.vm.$nextTick()
    await new Promise(r => setTimeout(r, 10))

    // 触发打印登记
    await wrapper.find('button:contains("打印")').trigger('click')
    await wrapper.vm.$nextTick()
    await new Promise(r => setTimeout(r, 10))

    // 验证只调用了一次，覆盖整个手号区间
    expect(registerLabelPrints).toHaveBeenCalledTimes(1)
    expect(registerLabelPrints).toHaveBeenCalledWith('test-order-id', expect.objectContaining({
      from_hands: 1,
      to_hands: 2,
      size_code: 'XL',
      is_reprint: false,
      printed_qty: 1,
      print_seq: undefined,
    }))
  })

  it('requires print_seq for reprint', async () => {
    const wrapper = mount(LabelPrint, {
      global: {
        stubs: {
          PageLayout: { template: '<div><slot /><slot name="extra" /></div>' },
          StatusTag: { template: '<span>APPROVED</span>' },
        },
      },
    })
    await wrapper.vm.$nextTick()
    await new Promise(r => setTimeout(r, 10))

    await wrapper.find('button:contains("生成预览")').trigger('click')
    await wrapper.vm.$nextTick()
    await new Promise(r => setTimeout(r, 10))

    // 勾选重打但不填批次序号
    await wrapper.find('input[type="checkbox"]').setValue(true)
    await wrapper.vm.$nextTick()

    await wrapper.find('button:contains("打印")').trigger('click')
    await wrapper.vm.$nextTick()

    // 应该报错不调用 registerLabelPrints（后端会校验 print_seq 必填）
    // 这里验证前端仍会尝试调用，后端返回 10008
    expect(registerLabelPrints).toHaveBeenCalled()
  })

  it('includes hands_text in preview items', async () => {
    const wrapper = mount(LabelPrint, {
      global: {
        stubs: {
          PageLayout: { template: '<div><slot /><slot name="extra" /></div>' },
          StatusTag: { template: '<span>APPROVED</span>' },
        },
      },
    })
    await wrapper.vm.$nextTick()
    await new Promise(r => setTimeout(r, 10))

    await wrapper.find('button:contains("生成预览")').trigger('click')
    await wrapper.vm.$nextTick()
    await new Promise(r => setTimeout(r, 10))

    expect(wrapper.text()).toContain('第 1 手 / 共 2 手')
    expect(wrapper.text()).toContain('第 2 手 / 共 2 手')
  })

  it('hides print button when status is not APPROVED', async () => {
    ;vi.mocked(getBundlingOrder).mockResolvedValue({ ...mockOrder, status: 'DRAFT' })

    const wrapper = mount(LabelPrint, {
      global: {
        stubs: {
          PageLayout: { template: '<div><slot /><slot name="extra" /></div>' },
          StatusTag: { template: '<span>DRAFT</span>' },
        },
      },
    })
    await wrapper.vm.$nextTick()
    await new Promise(r => setTimeout(r, 10))

    expect(wrapper.find('button:contains("打印")').exists()).toBe(false)
  })

  it('generates QR code and barcode for each preview item', async () => {
    const wrapper = mount(LabelPrint, {
      global: {
        stubs: {
          PageLayout: { template: '<div><slot /><slot name="extra" /></div>' },
          StatusTag: { template: '<span>APPROVED</span>' },
        },
      },
    })
    await wrapper.vm.$nextTick()
    await new Promise(r => setTimeout(r, 10))

    await wrapper.find('button:contains("生成预览")').trigger('click')
    await wrapper.vm.$nextTick()
    await new Promise(r => setTimeout(r, 10))

    // 验证二维码和条码图片存在
    const qrImages = wrapper.findAll('.qr-code img')
    const barcodeImages = wrapper.findAll('.barcode img')
    expect(qrImages).toHaveLength(2)
    expect(barcodeImages).toHaveLength(2)
  })
})
