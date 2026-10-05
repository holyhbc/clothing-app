import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { Modal } from 'ant-design-vue'
import type { StockBatchOptionOut } from '@garment/shared'
import { baseApi } from '@/api/base'
import LineEditor from '@/views/cutting/components/LineEditor.vue'
import { balanceOf, sizeOutputOf } from '@/views/cutting/components/slotTypes'
import type { LineTree } from '@/views/cutting/components/slotTypes'

/**
 * 三层明细编辑器的组件测试（T-CUT-001c-3a，TC-CUT-E1 ~ TC-CUT-E5）。
 *
 * ## 为什么编辑器要**独立**测试，而不是只测页面
 *
 * 三层的算术（手数 × 每手件数、行余量、颜色小计）是**纯函数**（`slotTypes.ts`），
 * 而交互只有两处需要点：切换录入模式的二次确认（C27）、行内颜色的增删。
 * 绑在页面上测就必须把页面的表头、三层 Combo 的下拉、路由桩全都搭起来 ——
 * 那样一旦失败，要先判断是「算术错了」还是「下拉没点开」。
 */
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

/** 两行两色两尺码的树：行1 余量为正，行2 余量为负（用来验阻断）。 */
function tree(): LineTree {
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
          entry_mode: 'MANUAL',
          uniform_qty: null,
          size_lines: [{ size_line_no: 1, size_code: 'L', hands: 10, qty_per_hand: 3 }],
        },
      ],
    },
    {
      line_no: 2,
      stock_id: '',
      fabric_qty: '0',
      waste_qty: '0',
      output_qty: '5',
      colors: [
        {
          color_code: 'BLK',
          entry_mode: 'MANUAL',
          uniform_qty: null,
          size_lines: [{ size_line_no: 1, size_code: 'XL', hands: 10, qty_per_hand: 3 }],
        },
      ],
    },
  ]
}

async function settle(): Promise<void> {
  await nextTick()
  await nextTick()
}

function mountEditor(lines: LineTree = tree(), plyCount = 1) {
  vi.spyOn(baseApi.colors, 'options').mockResolvedValue([
    { value: 'NVY', label: 'NVY 藏青', disabled: false },
  ])
  vi.spyOn(baseApi.sizes, 'options').mockResolvedValue([
    { value: 'L', label: 'L (165/84A)', disabled: false },
  ])
  return mount(LineEditor, {
    props: {
      modelValue: lines,
      plyCount,
      fetchStockOptions: vi.fn().mockResolvedValue([STOCK]),
    },
    attachTo: document.body,
  })
}

function plain(text: string): string {
  return text.replace(/\s/g, '')
}

function text(): string {
  return plain(document.body.textContent ?? '')
}

describe('三层明细编辑器（TC-CUT-001c-3a）', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    document.body.innerHTML = ''
  })

  it('TC-CUT-E1 ★ C21 精确乘、不取整：10 手 × 3 件 = 30', () => {
    expect(sizeOutputOf({ hands: 10, qty_per_hand: 3 } as never)).toBe(30)
    expect(sizeOutputOf({ hands: 7, qty_per_hand: 13 } as never)).toBe(91)
  })

  it('TC-CUT-E2 ★ C28 人工指定的件数**优先于**手数 × 每手件数', () => {
    expect(sizeOutputOf({ hands: 10, qty_per_hand: 3, output_qty: 25 } as never)).toBe(25)
  })

  it('TC-CUT-E3 ★ C34 行余量 = 行可出件数 − Σ(颜色 Σ尺码)；负数进问题清单', () => {
    const lines = tree()
    expect(balanceOf(lines[0]!)).toBe(0) // 30 − 30
    expect(balanceOf(lines[1]!)).toBe(-25) // 5 − 30
    mountEditor(lines)
    expect(text()).toContain('行可出件数比尺码明细合计少25')
    // ⚠️ 用 `!` 断言非空而不是 `toBeDefined()`：数组下标在
    //    `noUncheckedIndexedAccess` 下是 `T | undefined`，两条用例都要判一次
  })

  it('TC-CUT-E4 ★ C27 切换录入模式**必须二次确认并写明清掉几行**', async () => {
    const wrapper = mountEditor()
    const confirmSpy = vi.spyOn(Modal, 'confirm')
    const select = document.querySelector('.layer-colors .ant-select-selector')
    expect(select, '颜色层应有录入模式下拉').not.toBeNull()
    select?.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    select?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()
    const option = [...document.querySelectorAll('.ant-select-item-option')].find((node) =>
      plain(node.textContent ?? '').includes('统一件数'),
    )
    expect(option, '模式下拉里应有「统一件数」').not.toBeNull()
    option?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()

    expect(confirmSpy).toHaveBeenCalledTimes(1)
    const config = confirmSpy.mock.calls[0]?.[0] as { content: string; onOk?: () => void }
    expect(plain(config.content)).toContain('1行尺码明细')
    expect(plain(config.content)).toContain('手数与件数全部清空')
    // 确认之前颜色明细不能被动过 —— 这是「取消」分支存在的意义
    const emitted = wrapper.emitted('update:modelValue')
    expect(emitted, '弹确认框时不应该已经改了数据').toBeUndefined()
  })

  it('TC-CUT-E5 ★ C4 铺布层数 > 1 时标注「含 N 层」（出数是多层合计）', () => {
    mountEditor(tree(), 3)
    expect(text()).toContain('含3层')
  })

  it('TC-CUT-E6 删中间行之后**重排 line_no**（不留跳号）', async () => {
    const lines = tree()
    lines.push({
      line_no: 3,
      stock_id: '',
      fabric_qty: '0',
      waste_qty: '0',
      output_qty: '0',
      colors: [],
    })
    const wrapper = mountEditor(lines)
    const removeButtons = [...document.querySelectorAll('button')].filter(
      (node) => plain(node.textContent ?? '') === '移除',
    )
    // ⚠️ 必须取**最后一个**：颜色层与尺码层的「移除」在 DOM 里排在行级操作列**之前**
    //    （嵌套表在单元格里），所以第一个「移除」是颜色级的 —— 点它只删颜色，
    //    而断言看的是 `line_no`，症状是「删了一行但号没重排」这种张冠李戴的失败。
    const lineRemove = removeButtons[removeButtons.length - 1]
    expect(lineRemove, '行级操作列应有「移除」按钮').toBeDefined()
    lineRemove?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()
    const next = wrapper.emitted('update:modelValue')?.at(-1)?.[0] as LineTree
    expect(next.map((line) => line.line_no)).toEqual([1, 2])
  })
})