/**
 * 打菲明细行编辑器的回归测试。
 *
 * ⚠️ 本文件的存在理由是**一条已经发生过的生产缺陷**：
 *
 *   antd `Table` 的 `#bodyCell` 插槽提供的是 `{ text, value, record, index, column }`
 *   —— **没有 `rowIndex`**（见 antd-vue `Table.d.ts`）。而本组件当时写的是
 *   `{ column, record, rowIndex }`，于是：
 *
 *   1. 行号列渲染成 `NaN`（`undefined + 1`）；
 *   2. 所有 `onXxxChange(rowIndex, v)` 传进去的是 `undefined`，
 *      `next[undefined] = {...undefined, size_code}` 只在数组上挂了一个
 *      名字叫 `"undefined"` 的垃圾属性，**真实行纹丝不动** ——
 *      用户改尺码、改来源明细行，保存后什么都不会变。
 *
 *   这个 bug 能溜过去，是因为当时 `<a-select>` 用了 kebab 全局标签（没渲染出来），
 *   vue-tsc 拿不到模板的类型信息。修好标签之后类型检查才**照出**这个洞。
 *
 * 所以下面每条断言都锚在「用户看到/改到什么」，而不是组件内部实现。
 */
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import LineEditor from './LineEditor.vue'

interface Row {
  line_no: number
  size_code: string
  color_code: string
  operation_no: string
  cutting_size_line_id: string
  hands: number
  planned_qty: null
}

function makeRows(): Row[] {
  return [
    { line_no: 1, size_code: 'XL', color_code: '', operation_no: '', cutting_size_line_id: 'CUT-1', hands: 2, planned_qty: null },
    { line_no: 2, size_code: 'L', color_code: '', operation_no: '', cutting_size_line_id: 'CUT-2', hands: 1, planned_qty: null },
  ]
}

function mountEditor(rows: Row[] = makeRows()) {
  return mount(LineEditor, {
    props: {
      modelValue: rows,
      sourceOptions: [
        { value: 'CUT-9', label: 'M · 手数:3 · 出数:180 · 每手:60' },
      ],
      loadingSources: false,
    },
  })
}

/** 取最后一次 emit 出去的明细。 */
function lastEmitted(wrapper: ReturnType<typeof mountEditor>): Row[] | undefined {
  return wrapper.emitted('update:modelValue')?.at(-1)?.[0] as Row[] | undefined
}

describe('打菲明细行编辑器（bundling/LineEditor）', () => {
  it('行号列按顺序显示 1、2…，不是 NaN', () => {
    const wrapper = mountEditor()
    // 按「每行的第一个单元格」取，别假设列数 —— 列数是表格结构，会变
    const firstColumn = wrapper
      .findAll('.ant-table-tbody tr.ant-table-row')
      .map((row) => row.findAll('.ant-table-cell')[0]?.text())
    expect(firstColumn).toEqual(['1', '2'])
  })

  it('改第 1 行尺码只动第 1 行', async () => {
    const wrapper = mountEditor()
    await wrapper.findAll('input')[0]?.setValue('XXL')
    expect(lastEmitted(wrapper)?.map((r) => r.size_code)).toEqual(['XXL', 'L'])
  })

  it('改第 2 行尺码只动第 2 行（索引不能串行）', async () => {
    const wrapper = mountEditor()
    await wrapper.findAll('input')[1]?.setValue('LL')
    expect(lastEmitted(wrapper)?.map((r) => r.size_code)).toEqual(['XL', 'LL'])
  })

  it('emit 里不留 any 名字为 undefined 的垃圾属性', async () => {
    // 这条是 `rowIndex` 为 undefined 时最隐蔽的症状：
    // 数组长度没变、`map` 也没报错，只是多挂了一个 `undefined` 键。
    const wrapper = mountEditor()
    await wrapper.findAll('input')[0]?.setValue('XXL')
    const emitted = lastEmitted(wrapper) ?? []
    expect(Object.keys(emitted[0] ?? {})).not.toContain('undefined')
  })

  it('改手数写回对应的行（需先点进编辑态）', async () => {
    const wrapper = mountEditor()
    // 手数单元格默认是 `<span>{{ record.hands }}</span>`，点一下才换成 InputNumber
    const handCells = wrapper
      .findAll('.ant-table-tbody tr.ant-table-row')
      .map((row) => row.findAll('.ant-table-cell')[3])
    expect(handCells[1]?.text()).toBe('1')

    await handCells[1]?.find('span').trigger('click')
    const spinbutton = wrapper.findAll('input[role="spinbutton"]')[0]
    expect(spinbutton).toBeDefined()
    await spinbutton?.setValue('5')
    await spinbutton?.trigger('change')
    expect(lastEmitted(wrapper)?.map((r) => r.hands)).toEqual([2, 5])
  })

  it('「来源裁剪明细行」列渲染出来的是可点的单元格，不是原生下拉', () => {
    // `<a-select>` kebab 全局标签曾让这一列整个渲染不出来
    const wrapper = mountEditor()
    expect(wrapper.findAll('.code-cell').length).toBeGreaterThan(0)
    expect(wrapper.text()).toContain('CUT-1')
    expect(wrapper.text()).toContain('CUT-2')
  })
})
