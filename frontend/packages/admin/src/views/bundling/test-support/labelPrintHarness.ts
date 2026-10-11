/**
 * `LabelPrint.vue` 的测试夹具：mock、样例数据、mount 样板。
 *
 * ⚠️ 为什么要单独抽出来：这个测试文件里有 **10 个用例**，而每个用例都要写一遍
 * `mount(LabelPrint, { global: { stubs: { PageLayout, StatusTag } } })`，
 * 光这段样板就 ~100 行重复。样板一多，真正该看的断言就被淹了 ——
 * 而且改一处 stub 要同步改 10 处，漏一处就只在那一个用例里诡异失败。
 *
 * 只被 `*.test.ts` 引用，不进生产构建。
 */
import { mount } from '@vue/test-utils'
import LabelPrint from '../LabelPrint.vue'

/*
 * ⚠️ 这里**不放** `vi.mock(...)`：`vi.mock` 会被 vitest 提升到文件最顶部执行，
 * 而 `vi.mocked(...).mockResolvedValue` 这类用法要求 mock 注册表在**导入被测模块
 * 之前**就已就位。写在夹具里会晚一步，于是测试拿到的 `getBundlingOrder`
 * 是**真函数**，`.mockResolvedValue` 直接抛
 * 「is not a function」。mock 声明留在 `LabelPrint.test.ts` 顶层。
 */

/* ----------------------------- 样例数据 ----------------------------- */

export const TEST_ORDER_ID = 'test-order-id'

export const mockOrder = {
  id: '33333333-3333-3333-3333-333333333333',
  doc_no: 'BD-20261018-000001',
  doc_date: '2026-10-18',
  status: 'APPROVED',
  version: 1,
  style_no: 'HB-2026-0001',
  workshop_id: '44444444-4444-4444-4444-444444444444',
  source_cutting_order_id: '11111111-1111-1111-1111-111111111111',
  operation_no: '01',
  color_code: 'WHT',
  color_group: 'G',
  bundle_qty: 60,
  hands_total: 2,
  output_qty: '120',
  balance_qty: '0',
  label_print_qty: 0,
  created_at: '2026-10-18T00:00:00Z',
  updated_at: '2026-10-18T00:00:00Z',
  lines: [],
}

export const mockBundles = [
  { id: 'a', bundle_no: 'B-1', size_code: 'XL', hands: 1, bundle_qty: 60, status: 'ACTIVE' },
  { id: 'b', bundle_no: 'B-2', size_code: 'XL', hands: 2, bundle_qty: 60, status: 'ACTIVE' },
]

export const mockPreviewItems = [
  {
    barcode_content: 'BD-20261018-000001-XL01-0001',
    bundle_no: 'BD-20261018-000001-XL01-0001',
    bundle_qty: '60',
    color_code: 'WHT',
    hands_seq: 1,
    hands_text: '第 1 手 / 共 2 手',
    style_no: 'HB-2026-0001',
    operation_no: '01',
    qr_content: 'BD-20261018-000001-XL01-0001',
    size_code: 'XL',
    hands_total_of_size: 2,
  },
  {
    barcode_content: 'BD-20261018-000001-XL01-0002',
    bundle_no: 'BD-20261018-000001-XL01-0002',
    bundle_qty: '60',
    color_code: 'WHT',
    hands_seq: 2,
    hands_text: '第 2 手 / 共 2 手',
    style_no: 'HB-2026-0001',
    operation_no: '01',
    qr_content: 'BD-20261018-000001-XL01-0002',
    size_code: 'XL',
    hands_total_of_size: 2,
  },
]

/* ----------------------------- 按钮查找 ----------------------------- */

/** 归一化按钮文本：剥掉 antd 为两字中文插入的空格。 */
function buttonLabel(button: { text: () => string }): string {
  return button.text().replace(/\s+/g, '')
}

/**
 * 按可见文本找按钮；找不到就抛出**带候选清单**的错误。
 *
 * ⚠️ 为什么不用 `find('button:contains("打印")')`：两个坑叠加，都会**静默**
 * 给出空 DOMWrapper（然后报「Cannot call trigger on an empty DOMWrapper」，
 * 看不出真正原因）：
 *  1. **jsdom 不支持 `:contains()`** —— 那是 jQuery 私有扩展，不是标准 CSS；
 *  2. **antd 会给「两字中文」按钮自动插空格**：`<Button>打印</Button>` 实际
 *     渲染成 `<span>打 印</span>`。
 *
 * 断言要锚在「用户看到什么」上，而不是第三方库的 DOM 实现上
 * ——后者会随 antd 版本静默失效。
 */
export function findButton(wrapper: ReturnType<typeof mount>, label: string) {
  const buttons = wrapper.findAll('button')
  const match = buttons.find((b) => buttonLabel(b).includes(label))
  if (!match) {
    throw new Error(
      `找不到按钮「${label}」。实际渲染的按钮：${JSON.stringify(buttons.map(buttonLabel))}`,
    )
  }
  return match
}

/** 同上，但只回答「在不在」。 */
export function hasButton(wrapper: ReturnType<typeof mount>, label: string): boolean {
  return wrapper.findAll('button').some((b) => buttonLabel(b).includes(label))
}

/* ----------------------------- mount 样板 ----------------------------- */

/** 挂载标签打印页。`status` 可覆盖，用于测「非 APPROVED 时不显示打印按钮」。 */
export function mountLabelPrint(status = 'APPROVED') {
  return mount(LabelPrint, {
    global: {
      stubs: {
        PageLayout: { template: '<div><slot /><slot name="extra" /></div>' },
        StatusTag: { template: `<span>${status}</span>` },
      },
    },
  })
}

/** 等组件里的 `await` 链（loadOrder / loadPreview 等）跑完。 */
export async function settle(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 10))
}
