import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { nextTick } from 'vue'
import { PERM } from '@garment/shared'
import type { BundlingOrderOut } from '@garment/shared'
import * as bundlingApi from '@/api/bundling'
import { baseApi } from '@/api/base'
import * as stylesApi from '@/api/styles'
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

/** 建单接口返回的那张单（各用例共用一份，避免同一串字段抄三遍后抄歪）。 */
function createdOrder(): BundlingOrderOut {
  return {
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
    // 契约里 `label_print_qty` 是必填。曾经漏了它，`as BundlingOrderOut`
    // 是在拿一个缺字段的对象硬转 —— 转过去也就算了，但少写字段这件事被藏住了。
    label_print_qty: 0,
    lines: [],
  }
}

async function mountForm() {
  // ⚠️ **候选的 `value` 必须照真实接口的形状 mock**，一个都不能「简化」成好看的值：
  //   原先这里给款号 mock 了 `value: 'HB-2026-0001'`、给工序 mock 了 `value: '03'`，
  //   两者恰好**就是**真实接口返回的编码（05 §9.5.2）—— 但当时的实现走的是
  //   `searchStyleOptionsById` / `operations.optionsById`，那两处的 value 是
  //   **UUID**，而 mock 写的是编码，于是「传的是编码还是 UUID」这件事被 mock
  //   顺手抹掉了。真环境里传 UUID → 422 `string_too_long`，单测里永远绿。
  //   真正需要 UUID 的只有车间（`workshops.optionsById`），它下面就是 UUID。
  vi.spyOn(baseApi.workshops, 'optionsById').mockResolvedValue([
    { value: '44444444-4444-4444-4444-444444444444', label: '一号车间', disabled: false },
  ])
  vi.spyOn(stylesApi, 'searchStyleOptions').mockResolvedValue([
    { value: 'HB-2026-0001', label: 'HB-2026-0001 汗布圆领 T 恤', disabled: false },
  ])
  vi.spyOn(baseApi.operations, 'options').mockResolvedValue([
    { value: '03', label: '03 拼前', disabled: false },
  ])
  // 候选**裁剪单**（B8 + 03 §5.1：打菲单只挂一张裁剪单）
  vi.spyOn(bundlingApi, 'listSourceOrders').mockResolvedValue([
    {
      id: '11111111-1111-1111-1111-111111111111',
      doc_no: 'CT-20261005-000001',
      doc_date: '2026-10-05',
      style_no: 'HB-2026-0001',
      workshop_id: '44444444-4444-4444-4444-444444444444',
    },
  ])
  // 选定裁剪单后，该色下各尺码的可打菲来源明细
  vi.spyOn(bundlingApi, 'listSourceLines').mockResolvedValue([
    {
      cutting_size_line_id: 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
      size_code: 'XL',
      hands: 2,
      output_qty: '120',
      qty_per_hand: '60',
      available_qty: '120',
    },
  ])
  vi.spyOn(bundlingApi, 'createBundlingOrder').mockResolvedValue(createdOrder())

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

/**
 * 把整张表**真的填完**（表头 8 项 + 一行明细）。
 *
 * ⚠️ 抽出来是因为两条用例要走同一条路，且**顺序有讲究**，每一步的理由见注释：
 *   - 色码/色组之间必须 `settle()`：`HeaderFields.update()` 展开的是
 *     `props.modelValue`，而 v-model 更新要下一 tick 才传到子组件 ——
 *     紧挨着写会让后一个字段把前一个的改动覆盖回空。
 *   - 色码必须**先于**来源裁剪单填好：`listSourceLines` 要
 *     「裁剪单 + 款号 + 色码」三者齐备才拉，否则来源明细永远是空的。
 *   - 明细行必须真的填（`addLine()` 建的是全空行），否则 `blocking` 拦下提交。
 */
async function fillWholeForm(): Promise<void> {
  await pickOption(0, '一号车间')
  await pickOption(1, 'HB-2026-0001')
  typeInto(fieldInput('色码'), 'WHT')
  await settle()
  typeInto(fieldInput('色组'), 'WHT-GRP')
  await settle()
  await settle()
  await pickOption(2, '拼前')
  await pickOption(3, 'CT-20261005-000001')

  const addLineBtn = findButton('新增行')
  expect(addLineBtn).toBeDefined()
  addLineBtn?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  await settle()

  const sizeInput = [...document.querySelectorAll('input')].find((i) =>
    i.placeholder?.includes('XL / L / M'),
  )
  typeInto(sizeInput, 'XL')
  await settle()

  // 点「来源裁剪明细行」单元格进入编辑态，再从弹出的 Select 里选来源行
  const sourceCell = [...document.querySelectorAll('.code-cell')][0]
  expect(sourceCell).toBeDefined()
  sourceCell?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  await settle()
  await pickOption(4, 'XL · 手数:2')
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
    await fillWholeForm()

    // 点击保存
    const saveBtn = findButton('保存草稿')
    expect(saveBtn).toBeDefined()
    saveBtn?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()

    // 验证是否调用了创建接口
    expect(bundlingApi.createBundlingOrder).toHaveBeenCalled()
    expect(router.currentRoute.value.name).toBe('bundling-orders-detail')
  })

  /**
   * 提交体的**字段形状**（E2E 挖出来的坑的回归护栏）。
   *
   * ⚠️ 只断言 `createBundlingOrder` 被调用过是不够的 —— 单测全程 mock 掉后端，
   *   所以「传的是款号还是 UUID」「行上的色码/工序号是不是空串」这两类错误
   *   在单测里**永远看不见**，而它们在真实环境里分别表现为 422 与空色码落库。
   */
  it('TC-BW-03c 提交体传款号/工序号编码，且行上色码与工序号跟表头一致', async () => {
    await mountForm()
    await fillWholeForm()

    findButton('保存草稿')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()

    // 从 mock 的调用记录里取回**真正传出去的那个对象**。
    // ⚠️ 别用 `let sent = null` + 在 mock 里赋值：赋值发生在回调里，
    //   TS 的控制流分析看不见它，出断言处会把 `sent` 收窄成 `null`，
    //   于是每个 `sent?.x` 都报「`x` 不存在于 `never`」。
    const calls = vi.mocked(bundlingApi.createBundlingOrder).mock.calls
    expect(calls).toHaveLength(1)
    const sent = calls[0]?.[0]
    // 款号是**字符串**（`max_length=32`）。传 UUID 会被 422 `string_too_long` 挡下。
    expect(sent?.style_no).toBe('HB-2026-0001')
    // 工序号同理（`max_length=16`）；车间才是 UUID。
    expect(sent?.operation_no).toBe('03')
    expect(sent?.workshop_id).toBe('44444444-4444-4444-4444-444444444444')
    // 明细行：色码/工序号必须**继承表头**。空串落库会让审核生成的码色码为空（B2/B3）。
    expect(sent?.lines).toHaveLength(1)
    expect(sent?.lines[0]?.color_code).toBe('WHT')
    expect(sent?.lines[0]?.operation_no).toBe('03')
    // 明细行本体（尺码 / 来源行）也要真的带上
    expect(sent?.lines[0]?.size_code).toBe('XL')
    expect(sent?.lines[0]?.cutting_size_line_id).toBe('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
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
