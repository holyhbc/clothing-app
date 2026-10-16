import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@garment/shared'
import type { PieceworkCountIn } from '@garment/shared'
import { countPiecework } from '@/api/piecework'
import { classifyError, createOfflineQueue } from './useOfflineQueue'
import {
  MAX_ATTEMPTS,
  __resetQueueForTest,
  enqueue,
  listByStatus,
  openQueueDb,
  putRecord,
} from './queue'
import type { QueueRecord } from './queue'

/**
 * 离线队列（T-PIECE-008）。
 *
 * ⚠️ 打桩 `@/api/piecework`（验「重试带的是不是同一把键、失败怎么归类」）而不是
 *    打桩 `fetch`：本卡要守的是**队列的状态迁移**，不是 HTTP 细节。
 */
vi.mock('@/api/piecework', () => ({
  countPiecework: vi.fn(),
  listPieceworkOperators: vi.fn(() => Promise.resolve([])),
}))

const PAYLOAD: PieceworkCountIn = {
  bundle_no: 'BD-20261015-000123-M02-0001',
  employee_id: '22222222-2222-2222-2222-222222222222',
  scanned_at: '2026-10-15T09:44:00+08:00',
  station_id: 'WS-01',
  device_id: 'DEV-1',
}

/** 假时钟 + 可注入网络状态，让退避序列不必真等 1s/2s/…。 */
function makeClock() {
  let value = 0
  return {
    now: () => value,
    advance: (ms: number) => {
      value += ms
    },
  }
}

beforeEach(async () => {
  vi.mocked(countPiecework).mockReset()
  await __resetQueueForTest()
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('classifyError', () => {
  it('32001 → conflict；可重试码与网络错 → retry；其余业务拒 → fail', () => {
    expect(classifyError(new ApiError(32001, '已被他人计件'))).toBe('conflict')
    expect(classifyError(new ApiError(10003, '处理中'))).toBe('retry')
    expect(classifyError(new ApiError(10004, '限流'))).toBe('retry')
    expect(classifyError(new ApiError(99999, '服务端错误'))).toBe('retry')
    expect(classifyError(new TypeError('Failed to fetch'))).toBe('retry')
    expect(classifyError(new ApiError(32006, '超产'))).toBe('fail')
    expect(classifyError(new ApiError(20004, '当日无单价'))).toBe('fail')
  })
})

describe('enqueue / 持久化', () => {
  it('入队即可按状态查到，且重开库仍在（持久化）', async () => {
    const clock = makeClock()
    await enqueue(PAYLOAD, 'KEY-1', clock.now())
    expect((await listByStatus('pending')).length).toBe(1)

    // 重新开一次库（模拟刷新页面）：记录仍在
    const db = await openQueueDb()
    const rows = await db.getAll('pending_counts')
    expect(rows.length).toBe(1)
    expect(rows[0]?.idempotencyKey).toBe('KEY-1')
    expect(rows[0]?.attempts).toBe(0)
    expect(rows[0]?.payload.scanned_at).toBe(PAYLOAD.scanned_at)
  })
})

describe('flush：成功与幂等', () => {
  it('回放成功即删除记录', async () => {
    const clock = makeClock()
    const queue = createOfflineQueue({ now: clock.now, online: () => true })
    vi.mocked(countPiecework).mockResolvedValue(makeOut())
    await queue.enqueue(PAYLOAD, 'KEY-OK')
    await queue.flush()
    expect(countPiecework).toHaveBeenCalledWith(PAYLOAD, 'KEY-OK')
    expect(await listByStatus('pending')).toEqual([])
  })

  it('幂等键与 scanned_at 在多次重试间冻结不变', async () => {
    const clock = makeClock()
    const queue = createOfflineQueue({ now: clock.now, online: () => true })
    vi.mocked(countPiecework)
      .mockRejectedValueOnce(new TypeError('net'))
      .mockRejectedValueOnce(new TypeError('net'))
      .mockResolvedValue(makeOut())
    await queue.enqueue(PAYLOAD, 'KEY-FROZEN')

    await queue.flush() // attempts=1 → pending(延迟 1s)
    clock.advance(1000)
    await queue.flush() // attempts=2 → pending(延迟 2s)
    clock.advance(2000)
    await queue.flush() // 成功

    const calls = vi.mocked(countPiecework).mock.calls
    expect(calls.length).toBe(3)
    for (const [payload, key] of calls) {
      expect(key).toBe('KEY-FROZEN')
      expect(payload.scanned_at).toBe(PAYLOAD.scanned_at)
    }
    expect(await listByStatus('pending')).toEqual([])
  })
})

describe('flush：退避与上限', () => {
  it('退避序列 1/2/4/8/30s；第 8 次失败进入 failed 且保留', async () => {
    const clock = makeClock()
    const queue = createOfflineQueue({ now: clock.now, online: () => true })
    vi.mocked(countPiecework).mockRejectedValue(new TypeError('net'))
    await queue.enqueue(PAYLOAD, 'KEY-RETRY')

    const delays = [1000, 2000, 4000, 8000, 30000, 30000, 30000, 30000]
    for (let i = 0; i < MAX_ATTEMPTS; i += 1) {
      const before = clock.now()
      await queue.flush()
      const record = (await listByStatus('pending'))[0]
      if (i < MAX_ATTEMPTS - 1) {
        expect(record?.attempts).toBe(i + 1)
        expect(record?.nextAttemptAt).toBe(before + (delays[i] ?? 0))
        clock.advance(delays[i] ?? 0)
      } else {
        expect(record).toBeUndefined()
      }
    }
    const failed = await listByStatus('failed')
    expect(failed.length).toBe(1)
    expect(failed[0]?.attempts).toBe(MAX_ATTEMPTS)
    expect(failed[0]?.lastError?.message).toContain('net')
  })
})

describe('flush：冲突保留', () => {
  it('32001 → conflict，含原计件人 details，且不被删除', async () => {
    const clock = makeClock()
    const queue = createOfflineQueue({ now: clock.now, online: () => true })
    vi.mocked(countPiecework).mockRejectedValue(
      new ApiError(32001, '已被他人计件', {
        details: { counted_by_name: '张三', counted_at: '2026-10-15T09:44:00+08:00', counted_qty: '60' },
      }),
    )
    await queue.enqueue(PAYLOAD, 'KEY-CONFLICT')
    await queue.flush()

    const conflicts = await listByStatus('conflict')
    expect(conflicts.length).toBe(1)
    expect(conflicts[0]?.conflict?.['counted_by_name']).toBe('张三')
    // 再次 flush 不应把它重新提交（不再自动重试）
    await queue.flush()
    expect(vi.mocked(countPiecework).mock.calls.length).toBe(1)
    expect((await listByStatus('conflict')).length).toBe(1)
  })
})

describe('flush：并发与崩溃恢复', () => {
  it('同一时刻只有一个 flush 循环，每条恰好提交一次', async () => {
    const clock = makeClock()
    const queue = createOfflineQueue({ now: clock.now, online: () => true })
    vi.mocked(countPiecework).mockResolvedValue(makeOut())
    for (let i = 0; i < 20; i += 1) {
      await enqueue({ ...PAYLOAD, bundle_no: `BD-20261015-0001${String(i).padStart(2, '0')}-M02-0001` }, `KEY-${i}`, clock.now())
    }
    await Promise.all([queue.flush(), queue.flush(), queue.flush()])
    expect(vi.mocked(countPiecework).mock.calls.length).toBe(20)
    expect(await listByStatus('pending')).toEqual([])
  })

  it('遗留 syncing 记录在 flush 时复位并按同一键提交', async () => {
    const clock = makeClock()
    const queue = createOfflineQueue({ now: clock.now, online: () => true })
    vi.mocked(countPiecework).mockResolvedValue(makeOut())
    const stale: QueueRecord = {
      id: 'stale-1',
      idempotencyKey: 'KEY-STALE',
      payload: PAYLOAD,
      status: 'syncing',
      attempts: 3,
      nextAttemptAt: 0,
      createdAt: 0,
      lastError: null,
      conflict: null,
    }
    await putRecord(stale)
    await queue.flush()
    expect(countPiecework).toHaveBeenCalledWith(PAYLOAD, 'KEY-STALE')
    expect(await listByStatus('pending')).toEqual([])
    expect(await listByStatus('syncing')).toEqual([])
  })
})

describe('人工处置', () => {
  it('失败记录可人工重试；冲突 / 失败可人工删除', async () => {
    const clock = makeClock()
    const queue = createOfflineQueue({ now: clock.now, online: () => true })
    await enqueue(PAYLOAD, 'KEY-M1', clock.now())
    const [pending] = await listByStatus('pending')
    expect(pending).toBeDefined()
    await putRecord({ ...(pending as QueueRecord), status: 'failed' })

    await queue.retry(pending?.id ?? '')
    expect((await listByStatus('pending')).length).toBe(1)

    await queue.remove(pending?.id ?? '')
    expect((await listByStatus('pending')).length).toBe(0)
  })
})

function makeOut() {
  return {
    log_id: '11111111-1111-1111-1111-111111111111',
    bundle_no: PAYLOAD.bundle_no,
    style_no: 'HB-2026-0001',
    operation_no: '03',
    color_name: '藏青',
    size_name: '中',
    hands: 1,
    hands_total_of_size: 3,
    qty: '60',
    bundle_qty: '60',
    unit_price: '0.420000',
    amount: '25.2000',
    work_date: '2026-10-15',
    idempotent: false,
    today_hands: 1,
    today_qty: '60',
    today_amount: '25.2000',
  }
}
