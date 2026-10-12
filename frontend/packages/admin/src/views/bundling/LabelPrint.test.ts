/**
 * 标签打印页测试。
 *
 * mock、样例数据、mount 样板、按钮查找都在 `./test-support/labelPrintHarness` ——
 * 原因见那里。这个文件只留「用例与断言」，让该看的东西不被样板淹没。
 *
 * ⚠️ 本文件的历史教训（都已写进对应断言旁的注释）：
 *  1. 断言锚在 antd **内部 DOM** 上会静默失效（`:contains` / placeholder /
 *     `.ant-drawer-content`）；
 *  2. 只断言「调用了这个函数」不够 —— 必须断**参数**。缺了这条，
 *     「点打印必然报 10002」这个 bug 活了很久且测试全绿。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { InputNumber, Select, Spin } from 'ant-design-vue'
import { getBundlingOrder, listBundlesByOrder, exportLabels, registerLabelPrints } from '@/api/bundling'
import {
  TEST_ORDER_ID,
  findButton,
  hasButton,
  mockBundles,
  mockOrder,
  mockLabelExport,
  mountLabelPrint,
  settle,
} from './test-support/labelPrintHarness'

/* ----------------------------- 依赖 mock -----------------------------
 * ⚠️ 必须待在这个文件里，不能挪进上面的夹具：`vi.mock` 被 vitest 提升到
 * 文件最顶部执行，而它得在**导入被测模块之前**就把 mock 注册好。
 * 挪进夹具会晚一步，测试拿到的就成了真函数，`.mockResolvedValue` 抛
 * 「is not a function」。
 */
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
  default: { toDataURL: vi.fn().mockResolvedValue('data:image/png;base64,qrcode') },
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

describe('LabelPrint.vue', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getBundlingOrder).mockResolvedValue(mockOrder as never)
    vi.mocked(listBundlesByOrder).mockResolvedValue({ items: mockBundles, total: 2 } as never)
    vi.mocked(exportLabels).mockResolvedValue(mockLabelExport() as never)
    vi.mocked(registerLabelPrints).mockResolvedValue({ print_ids: ['print-1', 'print-2'], printed_count: 2 } as never)
  })

  it('renders loading state initially', () => {
    const wrapper = mountLabelPrint()
    // ⚠️ 按**组件引用**查，不按 `{ name: 'Spin' }` 的字符串查：
    // antd 的 Spin 组件没有 `name` 选项（`es/spin/index.js` 只有 `export default Spin`），
    // 按名字查会永远返回 false —— 而且组件改名/换库时它是**静默失效**的，
    // 不会像真断言那样变红，只会一直假绿。
    expect(wrapper.findComponent(Spin).exists()).toBe(true)
  })

  it('loads order and bundles on mount', async () => {
    const wrapper = mountLabelPrint()
    await wrapper.vm.$nextTick()
    await settle()

    expect(getBundlingOrder).toHaveBeenCalledWith(TEST_ORDER_ID)
    expect(listBundlesByOrder).toHaveBeenCalledWith(TEST_ORDER_ID, { status: 'ACTIVE' })
  })

  it('shows print parameters form after loading', async () => {
    const wrapper = mountLabelPrint()
    await wrapper.vm.$nextTick()
    await settle()

    // ⚠️ 断言按**组件引用 + props**，不按 antd 的内部 DOM：
    // `input[placeholder="请选择尺码"]` 永远匹配不到 —— antd `Select` 是把
    // placeholder 渲染成 `<span class="ant-select-selection-placeholder">`，
    // **不是** `<input placeholder>`；
    // `input[type="number"]` 同样匹配不到 —— antd `InputNumber` 渲染的是
    // `<input role="spinbutton" type="text">`。
    //
    // 查 props 还能顺带锁住「手数范围来自后端」这条业务约束（min/max），
    // 比「有个数字输入框」这个断言强得多。
    const select = wrapper.findComponent(Select)
    expect(select.exists()).toBe(true)
    expect(select.props('placeholder')).toBe('请选择尺码')

    const handInputs = wrapper.findAllComponents(InputNumber)
    expect(handInputs).toHaveLength(3)

    // 按 props 特征而非 DOM 顺序断言 —— 顺序是 antd 的实现细节，
    // 「起始/结束手号」的特征是 min/max 取自后端 hands_total(=2)。
    const handRange = handInputs.filter((c) => c.props('max') === 2)
    expect(handRange).toHaveLength(2)
    expect(handRange.map((c) => c.props('min'))).toEqual([1, 1])

    // 「每手打印张数」是 1~10，与手数范围无关
    const printedQty = handInputs.filter((c) => c.props('max') === 10)
    expect(printedQty).toHaveLength(1)
    expect(printedQty[0]?.props('min')).toBe(1)
  })

  it('generates preview when clicking preview button', async () => {
    const wrapper = mountLabelPrint()
    await wrapper.vm.$nextTick()
    await settle()

    await findButton(wrapper, '生成预览').trigger('click')
    await wrapper.vm.$nextTick()
    await settle()

    expect(exportLabels).toHaveBeenCalledWith(TEST_ORDER_ID, {
      from_hands: 1,
      to_hands: 2,
      size_code: 'XL',
      format: 'data',
    })
  })

  it('displays preview grid with label items', async () => {
    const wrapper = mountLabelPrint()
    await wrapper.vm.$nextTick()
    await settle()

    await findButton(wrapper, '生成预览').trigger('click')
    await wrapper.vm.$nextTick()
    await settle()

    expect(wrapper.findAll('.label-card')).toHaveLength(2)
    expect(wrapper.text()).toContain('第 1 手 / 共 2 手')
    expect(wrapper.text()).toContain('第 2 手 / 共 2 手')
    expect(wrapper.text()).toContain('60')
  })

  it('registers print for the hand range in one call', async () => {
    const wrapper = mountLabelPrint()
    await wrapper.vm.$nextTick()
    await settle()

    await findButton(wrapper, '生成预览').trigger('click')
    await wrapper.vm.$nextTick()
    await settle()

    await findButton(wrapper, '打印').trigger('click')
    await wrapper.vm.$nextTick()
    await settle()

    expect(registerLabelPrints).toHaveBeenCalledTimes(1)
    expect(registerLabelPrints).toHaveBeenCalledWith(
      TEST_ORDER_ID,
      expect.objectContaining({
        from_hands: 1,
        to_hands: 2,
        size_code: 'XL',
        is_reprint: false,
        printed_qty: 1,
      }),
    )

    // ⚠️ 下面这几条断言曾经**整条缺席**，于是「点打印必然报 10002」这个
    // bug 活了下来：`label_guard.resolve_hand_span()` 第一句就是
    //   `if hands_seq is None: raise BusinessError(10002, "必须带 hands_seq")`，
    // 而前端当时传的是 `hands_seq: undefined`。
    //
    // 只断言「调用了这个函数」是不够的 —— 必须断**参数**，
    // 否则调用参数全错、真实环境 100% 失败，测试照样绿。
    const payload = vi.mocked(registerLabelPrints).mock.calls[0]?.[1]
    expect(payload?.hands_seq).toBe(1)
    // 「共 M 手」快照只在选了尺码时传，服务端会拿它跟库内权威值核对
    expect(payload?.hands_total_of_size).toBe(2)

    // 非重打时 print_seq 这个键**不该存在**，而不是「存在且为 undefined」——
    // `expect.objectContaining({ print_seq: undefined })` 反而能通过，
    // 所以要显式断「键不存在」。
    expect(payload && 'print_seq' in payload).toBe(false)
  })

  it('requires print_seq for reprint', async () => {
    const wrapper = mountLabelPrint()
    await wrapper.vm.$nextTick()
    await settle()

    await findButton(wrapper, '生成预览').trigger('click')
    await wrapper.vm.$nextTick()
    await settle()

    // 勾选重打但不填批次序号
    const checkbox = wrapper.find('input[type="checkbox"]')
    expect(checkbox.exists()).toBe(true)
    await checkbox.setValue(true)
    await wrapper.vm.$nextTick()

    await findButton(wrapper, '打印').trigger('click')
    await wrapper.vm.$nextTick()

    // 重打且没填 print_seq 时**不应该**带上 print_seq 键 ——
    // 带上 undefined 会被序列化丢掉，后端只会看到「没传」而报 10002，
    // 与不传等价；但显式断言「键不存在」才能钉住这个约定。
    const payload = vi.mocked(registerLabelPrints).mock.calls[0]?.[1]
    expect(payload && 'print_seq' in payload).toBe(false)
    expect(registerLabelPrints).toHaveBeenCalled()
  })

  it('includes hands_text in preview items', async () => {
    const wrapper = mountLabelPrint()
    await wrapper.vm.$nextTick()
    await settle()

    await findButton(wrapper, '生成预览').trigger('click')
    await wrapper.vm.$nextTick()
    await settle()

    expect(wrapper.text()).toContain('第 1 手 / 共 2 手')
    expect(wrapper.text()).toContain('第 2 手 / 共 2 手')
  })

  it('hides print button when status is not APPROVED', async () => {
    vi.mocked(getBundlingOrder).mockResolvedValue({ ...mockOrder, status: 'DRAFT' } as never)

    const wrapper = mountLabelPrint('DRAFT')
    await wrapper.vm.$nextTick()
    await settle()

    expect(hasButton(wrapper, '打印')).toBe(false)
  })

  it('generates QR code and barcode for each preview item', async () => {
    const wrapper = mountLabelPrint()
    await wrapper.vm.$nextTick()
    await settle()

    await findButton(wrapper, '生成预览').trigger('click')
    await wrapper.vm.$nextTick()
    await settle()

    expect(wrapper.findAll('.qr-code img')).toHaveLength(2)
    expect(wrapper.findAll('.barcode img')).toHaveLength(2)
  })
})
