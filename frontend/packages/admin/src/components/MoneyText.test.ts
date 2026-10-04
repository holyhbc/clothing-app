import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import MoneyText from '@/components/MoneyText.vue'
import { adminSrc } from '@/test-support/paths'

/**
 * `MoneyText`（TC-W20）。
 *
 * TC-W20 要求「右对齐 + 等宽数字」—— 这两件事都在 CSS 里，**JS 断言不了**。
 * 所以这里做两件事：
 *  1. 断言**类名/结构**没变（`money-text` 是样式的挂载点，改了就得同步改测试）；
 *  2. 另有一个「样式不许回退」的检查放在 `styles/tokens.test.ts` 同级的思路里 ——
 *     这里直接读编译产物太重，改为断言组件源码里**必须出现**这两个 CSS 属性。
 *     读源码比读样式表轻，且能在有人删样式时立刻报红。
 */

function money(value: string | number | null | undefined, places?: number) {
  return mount(MoneyText, { props: places === undefined ? { value } : { value, places } })
}

describe('MoneyText 金额文本', () => {
  it('TC-W20 挂载点带 money-text 类（样式的唯一入口）', () => {
    expect(money('1').classes()).toContain('money-text')
  })

  it('右对齐 + 等宽数字这两个属性必须留在样式里', () => {
    const source = readFileSync(join(adminSrc(), 'components', 'MoneyText.vue'), 'utf8')
    expect(source).toContain('text-align: right')
    expect(source).toContain('font-variant-numeric: tabular-nums')
  })

  it('复用 shared 的 formatMoney，不自己 toFixed', () => {
    expect(money('1234.5').text()).toBe('¥1,234.50')
    expect(money(0.1 + 0.2).text()).toBe('¥0.30')
  })

  it('接受字符串入参（后端把 numeric 序列化成字符串）', () => {
    expect(money('1234.5000').text()).toBe('¥1,234.50')
  })

  it('单价用 4 位小数（写死 2 位会让 0.378000 显示成 0.38）', () => {
    expect(money('0.3780', 4).text()).toBe('¥0.3780')
    // 不传 places 就是 2 位，与 docs/05 §3「金额响应一律字符串，界面统一 2 位」一致
    expect(money('0.3780').text()).toBe('¥0.38')
  })

  it('空值显示 `-`（与 shared 的 formatMoney 一致，不另造一种"空"）', () => {
    expect(money(null).text()).toBe('-')
    expect(money(undefined).text()).toBe('-')
    expect(money('').text()).toBe('-')
  })

  it('负数保留负号且千分位不吞负号', () => {
    expect(money('-1234.5').text()).toBe('-¥1,234.50')
  })

  it('title 与显示文本一致（金额太长时 hover 能看全）', () => {
    expect(money('1234.5').attributes('title')).toBe('¥1,234.50')
  })
})
