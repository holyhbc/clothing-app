import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import QueuePanel from './QueuePanel.vue'
import type { QueueRecord } from './queue'

/**
 * 冲突 / 失败展开面板（T-PIECE-008）。
 *
 * ⚠️ 断言的是**渲染后的文案**与**派发的动作**：冲突项必须显示原计件人（`07 §7`
 *    「不静默丢弃」的可读形态），且「删除」是显式动作（不操作 = 保留）。
 */
function record(overrides: Partial<QueueRecord>): QueueRecord {
  return {
    id: 'r-1',
    idempotencyKey: 'KEY-1',
    payload: {
      bundle_no: 'BD-20261015-000123-M02-0001',
      employee_id: '22222222-2222-2222-2222-222222222222',
      scanned_at: '2026-10-15T09:44:00+08:00',
      station_id: 'WS-01',
      device_id: 'DEV-1',
    },
    status: 'pending',
    attempts: 1,
    nextAttemptAt: 0,
    createdAt: 0,
    lastError: null,
    conflict: null,
    ...overrides,
  }
}

describe('QueuePanel', () => {
  it('冲突项：显示原计件人 / 时间 / 件数，且「删除」为显式动作', () => {
    const conflict = record({
      status: 'conflict',
      conflict: {
        counted_by_name: '张三',
        counted_at: '2026-10-15T09:44:00+08:00',
        counted_qty: '60',
        hands: 1,
      },
    })
    const wrapper = mount(QueuePanel, { props: { records: [conflict] } })
    expect(wrapper.text()).toContain('张三')
    expect(wrapper.text()).toContain('60')
    expect(wrapper.text()).toContain('不操作即保留')
  })

  it('失败项：点「重试」派发 retry，点「删除」派发 remove', async () => {
    const failed = record({ status: 'failed', lastError: { code: 99999, message: '服务端错误' } })
    const wrapper = mount(QueuePanel, { props: { records: [failed] } })
    expect(wrapper.text()).toContain('服务端错误')

    const actions = wrapper.findAll('.queue-panel__action')
    await actions[0]?.trigger('click') // 重试
    await actions[1]?.trigger('click') // 删除
    expect(wrapper.emitted('retry')?.[0]).toEqual(['r-1'])
    expect(wrapper.emitted('remove')?.[0]).toEqual(['r-1'])
  })

  it('空列表显示空态', () => {
    const wrapper = mount(QueuePanel, { props: { records: [] } })
    expect(wrapper.text()).toContain('无待处理记录')
  })
})
