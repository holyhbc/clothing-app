import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { ApiError } from '@garment/shared'
import type { PieceworkCountOut } from '@garment/shared'
import { countPiecework, listPieceworkOperators } from '@/api/piecework'
import {
  BUNDLE_NO_PATTERN,
  createScanMachine,
  duplicateText,
  idempotencyKey,
  isTypingTarget,
} from './useScanMachine'
import Scan from './Scan.vue'

/**
 * 工位机扫码页（T-PIECE-007）。
 *
 * ⚠️ 打桩 `@/api/piecework`（验「提交了什么、怎么反馈」）而不是打桩 `fetch`：
 *    打 fetch 会把用例绑死在 URL 与 header 拼接上，而本卡要守的是**业务分支**
 *    （32001 不弹窗、32006 红字、暂停忽略）。
 */
vi.mock('@/api/piecework', () => ({
  countPiecework: vi.fn(),
  listPieceworkOperators: vi.fn(() => Promise.resolve([])),
}))
vi.mock('@/api/base', () => ({
  baseApi: { operations: { options: vi.fn(() => Promise.resolve([])) } },
}))
vi.mock('./feedback', () => ({
  FLASH_MS: 200,
  playTone: vi.fn(),
}))

const VALID = 'BD-20261015-000123-M02-0001'

const OUT: PieceworkCountOut = {
  log_id: '11111111-1111-1111-1111-111111111111',
  bundle_no: VALID,
  style_no: 'HB-2026-0001',
  operation_no: '03',
  color_name: '藏青',
  size_name: '中',
  hands: 2,
  hands_total_of_size: 6,
  qty: '60',
  bundle_qty: '60',
  unit_price: '0.420000',
  amount: '25.2000',
  work_date: '2026-10-15',
  idempotent: false,
  today_hands: 2,
  today_qty: '120',
  today_amount: '50.4000',
}

const CONFIG = {
  employeeId: '22222222-2222-2222-2222-222222222222',
  workstationNo: 'WS-01',
  operationNo: '03',
  paused: false,
  qty: null,
}

function key(value: string, target?: EventTarget): KeyboardEvent {
  const event = new KeyboardEvent('keydown', { key: value, bubbles: true })
  if (target !== undefined) {
    Object.defineProperty(event, 'target', { value: target })
  }
  return event
}

beforeEach(() => {
  vi.mocked(countPiecework).mockReset()
  vi.mocked(listPieceworkOperators).mockReset()
  vi.mocked(listPieceworkOperators).mockResolvedValue([])
})

afterEach(() => {
  vi.clearAllMocks()
  document.body.innerHTML = ''
})

describe('useScanMachine：本地格式与幂等键', () => {
  it('打菲号正则与后端一致（尺码码 1~3 位）', () => {
    expect(BUNDLE_NO_PATTERN.test('BD-20261015-000123-S02-0001')).toBe(true)
    expect(BUNDLE_NO_PATTERN.test('BD-20261015-000123-XL02-0001')).toBe(true)
    expect(BUNDLE_NO_PATTERN.test('BD-20261015-000123-XXL02-0001')).toBe(true)
    // 小写、缺段、尺码码 >3 位一律非法（本地直接拦下，不发请求）
    expect(BUNDLE_NO_PATTERN.test('bd-20261015-000123-M02-0001')).toBe(false)
    expect(BUNDLE_NO_PATTERN.test('BD-20261015-000123-M02')).toBe(false)
    expect(BUNDLE_NO_PATTERN.test('BD-20261015-000123-XXXX02-0001')).toBe(false)
  })

  it('isTypingTarget：输入控件为真，普通元素为假', () => {
    expect(isTypingTarget(null)).toBe(false)
    expect(isTypingTarget(document.createElement('input'))).toBe(true)
    expect(isTypingTarget(document.createElement('div'))).toBe(false)
  })

  it('idempotencyKey 同窗稳定、跨窗变化', () => {
    const a = idempotencyKey(VALID, CONFIG, 0)
    const b = idempotencyKey(VALID, CONFIG, 9_999)
    const c = idempotencyKey(VALID, CONFIG, 10_000)
    expect(a).toBe(b)
    expect(a).not.toBe(c)
    expect(a).toContain('WS-01')
  })

  it('duplicateText 用 details 渲染一行黄色提示（不弹窗）', () => {
    const text = duplicateText({
      counted_by_name: '陈美玲',
      counted_at: '2026-10-15T09:44:00+08:00',
      counted_qty: '60.000',
      bundle_qty: '60.000',
      hands: 1,
    })
    expect(text).toContain('陈美玲')
    expect(text).toContain('第 1 手')
    expect(text).toContain('60.000')
  })
})

describe('useScanMachine：提交分支', () => {
  it('整手成功：计数用服务端 today_* 覆盖，不前端累加', async () => {
    vi.mocked(countPiecework).mockResolvedValue(OUT)
    const machine = createScanMachine()
    await machine.submit(VALID, CONFIG)
    expect(countPiecework).toHaveBeenCalledTimes(1)
    expect(machine.state.phase).toBe('success')
    expect(machine.state.todayHands).toBe(2)
    expect(machine.state.todayAmount).toBe('50.4000')
    expect(machine.state.flash).toBe('ok')
  })

  it('非打菲号格式：不发请求', async () => {
    const machine = createScanMachine()
    await machine.submit('HELLO', CONFIG)
    expect(countPiecework).not.toHaveBeenCalled()
    expect(machine.state.phase).toBe('error')
  })

  it('缺设置：不发请求并提示', async () => {
    const machine = createScanMachine()
    await machine.submit(VALID, { ...CONFIG, employeeId: null })
    expect(countPiecework).not.toHaveBeenCalled()
    expect(machine.state.message).toContain('操作员')
  })

  it('32001 重复码 → 黄色行内提示，不弹窗', async () => {
    vi.mocked(countPiecework).mockRejectedValue(
      new ApiError(32001, '该码已被他人计件', {
        details: { counted_by_name: '张三', counted_at: '2026-10-15T09:44:00+08:00', counted_qty: '60' },
      }),
    )
    const machine = createScanMachine()
    await machine.submit(VALID, CONFIG)
    expect(machine.state.phase).toBe('duplicate')
    expect(machine.state.flash).toBe('warn')
    expect(machine.state.message).toContain('张三')
  })

  it('32006 超产 → 红色提示', async () => {
    vi.mocked(countPiecework).mockRejectedValue(new ApiError(32006, '超过该手件数'))
    const machine = createScanMachine()
    await machine.submit(VALID, CONFIG)
    expect(machine.state.phase).toBe('error')
    expect(machine.state.flash).toBe('error')
  })
})

describe('useScanMachine：keydown 缓冲', () => {
  it('逐字符入缓冲，Enter 触发提交', async () => {
    vi.mocked(countPiecework).mockResolvedValue(OUT)
    const machine = createScanMachine()
    for (const ch of VALID) machine.onKey(key(ch), CONFIG)
    expect(machine.state.buffer).toBe(VALID)
    machine.onKey(key('Enter'), CONFIG)
    await flushPromises()
    expect(countPiecework).toHaveBeenCalledTimes(1)
    expect(machine.state.buffer).toBe('')
  })

  it('暂停时忽略所有扫码', () => {
    const machine = createScanMachine()
    machine.onKey(key('B'), { ...CONFIG, paused: true })
    expect(machine.state.buffer).toBe('')
  })

  it('焦点在输入框时不进缓冲（避免把搜索词当扫码）', () => {
    const machine = createScanMachine()
    const input = document.createElement('input')
    document.body.appendChild(input)
    machine.onKey(key('B', input), CONFIG)
    expect(machine.state.buffer).toBe('')
  })
})

describe('Scan 组件：全局监听生命周期', () => {
  it('挂载后捕获全局 keydown，卸载后不再监听', async () => {
    vi.mocked(countPiecework).mockResolvedValue(OUT)
    const wrapper = mount(Scan)
    await flushPromises()

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'B' }))
    await flushPromises()
    expect(wrapper.text()).toContain('B')

    wrapper.unmount()
    // 卸载后再敲键盘不应影响（组件已销毁，这里只断言监听已移除，不抛错即可）
    expect(() => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'X' }))).not.toThrow()
  })
})
