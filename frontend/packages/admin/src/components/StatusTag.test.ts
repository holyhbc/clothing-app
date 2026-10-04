import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { DOCUMENT_STATUSES, statusColor } from '@garment/shared'
import StatusTag from '@/components/StatusTag.vue'

/**
 * `StatusTag`（TC-W19）。
 *
 * ## 断言的是「色 token」，不是色值
 *
 * 断言 `var(--color-success)` 而不是 `#1f9e5a`：token 是事实来源，
 * 断言色值等于把 token 的具体数值复制一份到测试里 —— 将来调色（docs/06 §1 允许
 * 改 token 的值）就得回来改测试，而"忘了改"就会报一个与真实缺陷无关的红。
 *
 * 同时断言「**未知状态不空白也不错映射**」：后端加一档而前端没跟上时，
 * 空白标签会让用户以为系统坏了，错映射成「草稿」会让人以为单据还没提交。
 */

function tag(status: string) {
  return mount(StatusTag, { props: { status } })
}

describe('StatusTag 状态标签', () => {
  it('TC-W19 五状态：文案与色 token 与 docs/06 §1 一致', () => {
    const expectations: Record<string, { text: string; color: string }> = {
      DRAFT: { text: '草稿', color: 'var(--color-text-second)' },
      SUBMITTED: { text: '待审核', color: 'var(--color-primary)' },
      APPROVED: { text: '已审核', color: 'var(--color-success)' },
      REJECTED: { text: '已驳回', color: 'var(--color-danger)' },
      CANCELLED: { text: '已作废', color: 'var(--color-info)' },
    }

    for (const [status, expected] of Object.entries(expectations)) {
      const wrapper = tag(status)
      const el = wrapper.get('span')

      expect(el.text(), `${status} 文案`).toBe(expected.text)
      expect((el.element as HTMLElement).style.color, `${status} 色 token`).toBe(expected.color)
    }
  })

  it('已作废带删除线，其余不带（docs/06 §1 表）', () => {
    expect(tag('CANCELLED').get('span > span').classes()).toContain('is-struck')
    for (const status of ['DRAFT', 'SUBMITTED', 'APPROVED', 'REJECTED', 'PAID']) {
      expect(tag(status).get('span > span').classes()).not.toContain('is-struck')
    }
  })

  it('每个状态都能渲染，不漏任何一档（后端枚举全遍历）', () => {
    for (const status of DOCUMENT_STATUSES) {
      const wrapper = tag(status)
      expect(wrapper.text().length, `${status} 不该是空标签`).toBeGreaterThan(0)
      // 未知标记为 false
      expect(wrapper.get('span').attributes('data-unknown')).toBeUndefined()
    }
  })

  it('未知状态原样显示 + 标出是未知，不空白也不错映射成已知状态', () => {
    const wrapper = tag('SOMETHING_NEW')

    expect(wrapper.text()).toBe('SOMETHING_NEW')
    expect(wrapper.get('span').attributes('data-unknown')).toBe('true')
    // 关键：绝不能显示成某个已知文案（那会让人以为单据处于那个状态）
    expect(wrapper.text()).not.toBe('草稿')
    expect(wrapper.get('span').attributes('title')).toContain('未知状态')
  })

  it('已知状态的 title 是中文文案（hover 就能看懂）', () => {
    expect(tag('APPROVED').get('span').attributes('title')).toBe('已审核')
  })

  it('色值与 shared 的 statusColor 完全一致（组件没自己写映射）', () => {
    for (const status of DOCUMENT_STATUSES) {
      const el = tag(status).get('span').element as HTMLElement
      expect(el.style.color).toBe(statusColor(status))
    }
  })
})
