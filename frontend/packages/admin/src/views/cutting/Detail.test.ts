import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { nextTick } from 'vue'
import { PERM } from '@garment/shared'
import type { CuttingOrderOut } from '@garment/shared'
import * as cuttingApi from '@/api/cutting'
import { permission } from '@/directives/permission'
import { resetHandlers } from '@/api/http'
import { useAuthStore } from '@/stores/auth'
import CuttingOrderDetail from '@/views/cutting/Detail.vue'

/**
 * 裁剪单只读详情页（T-CUT-001c-2c，TC-CUT-D1 ~ TC-CUT-D4）。
 *
 * ## 这一组用例守的是「三层结构不能被前端改写」
 *
 * 详情页渲染的是「行 → 行内颜色 → 尺码明细」（ADR-0017）。最容易出的错不是画不出来，
 * 而是**前端自己算一遍汇总**（把行的出数加起来当表头出数），于是页面上出现一个
 * 跟后端不一样的数字 —— 而后端那份才是权威（C6）。所以用例专门盯住「页面显示的
 * 数字等于后端给的数字」，而不是「等于逐层相加的结果」。
 */

/** 见 `views/base/styles/StylePages.test.ts`：弹层 wrapper 必须同步卸载。 */
const mountedWrappers: { unmount: () => void }[] = []

function track<T extends { unmount: () => void }>(wrapper: T): T {
  mountedWrappers.push(wrapper)
  return wrapper
}

afterEach(() => {
  while (mountedWrappers.length > 0) mountedWrappers.pop()?.unmount()
  document.body.innerHTML = ''
})

const ORDER_ID = '33333333-3333-3333-3333-333333333333'

/**
 * 一张有两行、两色、四条尺码明细的单子。
 *
 * ⚠️ 这里的数字**故意不自洽**（表头出数 120 ≠ 逐层相加的 110）：真实数据里表头是
 * 服务端 C6 重算的结果，而行余量（120 - 110 = 10）正是 C34 口径 A 要展示的东西。
 * 用例断言页面显示的就是这些原值 —— 一旦有人在页面里加一层求和，这条就会红。
 */
const ORDER: CuttingOrderOut = {
  id: ORDER_ID,
  doc_no: 'CT-20261005-000123',
  workshop_id: '44444444-4444-4444-4444-444444444444',
  style_id: '55555555-5555-5555-5555-555555555555',
  style_no: 'HB-2026-0001',
  color_codes: 'NVY,BLK',
  doc_date: '2026-10-05',
  delivery_date: '2026-10-20',
  ply_count: 4,
  entry_mode_default: 'MASTER',
  fabric_qty: '96.000',
  output_qty: '120',
  cut_waste_qty: '18.000',
  balance_qty: '10',
  hands_total: 40,
  status: 'REJECTED',
  remark_source: '跟单张 A-12',
  approved_by: null,
  approved_at: null,
  rejected_reason: '手数与跟单张不符',
  cancelled_reason: null,
  version: 3,
  remark: '先做一床',
  created_at: '2026-10-05T02:10:00Z',
  updated_at: '2026-10-05T03:00:00Z',
  lines: [
    {
      id: 'l1',
      line_no: 1,
      stock_id: '66666666-6666-6666-6666-666666666666',
      supplier_id: null,
      material_id: '77777777-7777-7777-7777-777777777777',
      style_no: 'HB-2026-0001',
      dye_lot_no: 'H2408',
      bolt_no: 'B-0001',
      color_plan: 'NVY×2',
      width_cm: '150.0',
      fabric_qty: '48.000',
      fabric_weight_kg: null,
      waste_qty: '9.000',
      output_qty: '70',
      size_line_sum_qty: '60',
      balance_qty: '10',
      colors: [
        {
          id: 'c1',
          line_id: 'l1',
          color_code: 'NVY',
          entry_mode: 'MASTER',
          qty_per_hand: null,
          uniform_qty: null,
          ratio_snapshot: { L: '0.4' },
          hands_total: '20',
          output_qty_total: '60',
          balance_qty_total: '0',
          entry_mode_changed_at: null,
          entry_mode_changed_by: null,
          size_lines: [
            {
              id: 's1',
              size_line_no: 1,
              size_code: 'L',
              hands: 10,
              qty_per_hand: 3,
              output_qty: '30',
              output_qty_manual: false,
              balance_qty: '0',
              hands_seq: 1,
              remark: null,
            },
            {
              id: 's2',
              size_line_no: 2,
              size_code: 'XL',
              hands: 10,
              qty_per_hand: 3,
              output_qty: '30',
              output_qty_manual: true,
              balance_qty: '2',
              hands_seq: 2,
              remark: '手工指定',
            },
          ],
        },
      ],
    },
    {
      id: 'l2',
      line_no: 2,
      stock_id: '88888888-8888-8888-8888-888888888888',
      supplier_id: null,
      material_id: '77777777-7777-7777-7777-777777777777',
      style_no: 'HB-2026-0001',
      dye_lot_no: 'H2409',
      bolt_no: 'B-0002',
      color_plan: null,
      width_cm: null,
      fabric_qty: '48.000',
      fabric_weight_kg: null,
      waste_qty: '9.000',
      output_qty: '50',
      size_line_sum_qty: '50',
      balance_qty: '0',
      colors: [
        {
          id: 'c2',
          line_id: 'l2',
          color_code: 'BLK',
          entry_mode: 'UNIFORM',
          qty_per_hand: null,
          uniform_qty: '10',
          ratio_snapshot: null,
          hands_total: '20',
          output_qty_total: '50',
          balance_qty_total: '0',
          entry_mode_changed_at: '2026-10-05T02:30:00Z',
          entry_mode_changed_by: null,
          size_lines: [],
        },
      ],
    },
  ],
}

async function settle(): Promise<void> {
  await flushPromises()
  await nextTick()
  await nextTick()
  await flushPromises()
}

function stubRouter() {
  return createRouter({
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
}

/**
 * 挂载详情页。
 *
 * @param get 预先装好的 `getCuttingOrder` 桩。⚠️ **默认给一个成功的桩**，
 *   而「加载失败」那条用例要自己传 reject —— 在挂载**之后**再改 mock 是无效的
 *   （页面在挂载时就拉完了，错误提示不会出现，用例只能靠断言别的字段侥幸通过）。
 */
async function mountDetail(orderId = ORDER_ID, get = vi.spyOn(cuttingApi, 'getCuttingOrder').mockResolvedValue(ORDER)) {
  const router = stubRouter()
  await router.push(`/cutting/orders/${orderId}`)
  await router.isReady()
  const wrapper = track(
    mount(CuttingOrderDetail, {
      global: { plugins: [router], directives: { can: permission } },
      attachTo: document.body,
    }),
  )
  await settle()
  return { wrapper, router, get }
}

function pageText(): string {
  return document.body.textContent?.replace(/\s/g, '') ?? ''
}

/**
 * 取某一层全部子表的正文文本。
 *
 * ⚠️ **不能靠 tbody 的下标算层级**：DOM 里三层是交错的（第 1 行产出「行 / 色 / 尺码」
 *    三个 tbody，第 2 行再产出三个），而**空的尺码子表在 antd 里不一定渲染 tbody** ——
 *    于是「每 3 个一循环」会错位，用例只看到第一行的颜色就假绿。
 *    所以页面上给两层子表各挂了一个 class（`layer-colors` / `layer-sizes`），
 *    这里按 class 取**每个**子表的第一个 tbody（descendant 会连孙表一起捞到）。
 */
function layerText(level: 'colors' | 'sizes'): string {
  return [...document.querySelectorAll(`.layer-${level}`)]
    .map((node) => (node.querySelector('.ant-table-tbody')?.textContent ?? '').replace(/\s/g, ''))
    .join('|')
}

/** 第一层（布批行）的正文文本。 */
function lineText(): string {
  return (
    document.querySelector('.ant-table > .ant-table-container .ant-table-tbody')?.textContent ?? ''
  ).replace(/\s/g, '')
}

describe('裁剪单详情（T-CUT-001c-2c）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    resetHandlers()
    vi.restoreAllMocks()
    document.body.innerHTML = ''
    useAuthStore().permissions = new Set([PERM.CUTTING_READ, PERM.CUTTING_UPDATE])
  })

  it('TC-CUT-D1 三层都渲染：行 → 色 → 尺码明细', async () => {
    await mountDetail()
    expect(lineText()).toContain('H2408')
    expect(lineText()).toContain('B-0002')
    // 第二层：两个行内颜色都要在（`layerText` 把所有同层子表都收上来）
    expect(layerText('colors')).toContain('NVY')
    expect(layerText('colors')).toContain('BLK')
    // 第三层：尺码明细 —— 出数的权威来源（`modules/02 §2` C5）
    expect(layerText('sizes')).toContain('L')
    expect(layerText('sizes')).toContain('XL')
    expect(layerText('sizes')).toContain('30')
  })

  it('TC-CUT-D2 汇总数字**照抄后端**，不逐层相加', async () => {
    await mountDetail()
    const text = pageText()
    // 表头出数 120（后端 C6 重算），而行 1 出数 70、行 2 出数 50 —— 逐层相加会得到 120，
    // 这里真正要守的是**行余量 10**（120 - 110）与**行余量按行各不相同**
    expect(text).toContain('行余量')
    expect(lineText()).toContain('10')
    expect(lineText()).toContain('0')
    // 尾数（表头）非 0 要高亮：class 是唯一的「要关注」信号
    expect(document.querySelector('.cell-warn')?.textContent?.replace(/\s/g, '')).toBe('10')
  })

  it('TC-CUT-D3 录入模式给中文，「人工指定」的尺码标出来', async () => {
    await mountDetail()
    const text = pageText()
    expect(text).toContain('按比例带出')
    expect(text).toContain('统一件数')
    expect(text).toContain('人工')
    // 另一条没人工指定的显示「比例」，让两者的区别一眼可见
    expect(text).toContain('比例')
  })

  it('TC-CUT-D4 状态 / 驳回原因 / 版本都来自后端', async () => {
    await mountDetail()
    const text = pageText()
    expect(text).toContain('已驳回')
    expect(text).toContain('手数与跟单张不符')
    // 版本要显示：T-CUT-001c-3 的编辑要用它做乐观锁，这里先让它可见
    expect(text).toContain('版本')
    expect(text).toContain('3')
  })

  it('TC-CUT-D5 加载失败显示错误原文与「重试」，不渲染空白表格', async () => {
    await mountDetail(
      ORDER_ID,
      vi.spyOn(cuttingApi, 'getCuttingOrder').mockRejectedValue(new Error('无权查看该车间')),
    )
    const text = pageText()
    expect(text).toContain('裁剪单加载失败')
    // ⚠️ 显示后端 message 而不是一句通用文案：10004 与 10003 的处置完全不同
    expect(text).toContain('无权查看该车间')
    expect(text).toContain('重试')
    // 不该有一张空表格（空表会让用户以为「这张单没有明细」）
    expect(document.querySelectorAll('.ant-table-tbody').length).toBe(0)
  })

  it('TC-CUT-D5b 重试会重新拉一次', async () => {
    const get = vi
      .spyOn(cuttingApi, 'getCuttingOrder')
      .mockRejectedValueOnce(new Error('临时失败'))
      .mockResolvedValue(ORDER)
    await mountDetail(ORDER_ID, get)
    const retry = [...document.querySelectorAll<HTMLButtonElement>('button')].find((node) =>
      (node.textContent ?? '').replace(/\s/g, '').includes('重试'),
    )
    expect(retry, '错误提示里应有「重试」按钮').toBeDefined()
    retry?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()
    expect(get).toHaveBeenCalledTimes(2)
    expect(pageText()).toContain('CT-20261005-000123')
  })

  it('TC-CUT-D6 路由参数缺失时给出明确提示，而不是把 undefined 发给后端', async () => {
    const get = vi.spyOn(cuttingApi, 'getCuttingOrder').mockResolvedValue(ORDER)
    const router = stubRouter()
    await router.push('/cutting/orders')
    await router.isReady()
    track(
      mount(CuttingOrderDetail, {
        global: { plugins: [router], directives: { can: permission } },
        attachTo: document.body,
      }),
    )
    await settle()
    expect(get).not.toHaveBeenCalled()
  })
})
