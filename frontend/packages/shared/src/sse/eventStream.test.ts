/**
 * SSE 客户端测试（docs/10 §6「通用组件/composable 必须测」；覆盖 TC-SSE-FE-01~12）。
 *
 * ⚠️ **不打真实网络**：注入 `eventSourceFactory` 替身（node 环境本就没有 `EventSource`），
 * 用 fake timers 编排退避与 30s 轮询周期。真实流会让测试变慢且不可编排。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { EventStreamClient } from './eventStream.ts'
import type {
  EventSourceFactory,
  EventSourceLike,
  StreamEvent,
  StreamStatus,
} from './eventStream.ts'

class FakeEventSource implements EventSourceLike {
  static instances: FakeEventSource[] = []
  readonly listeners = new Map<string, Array<(event: Event) => void>>()
  closed = false

  constructor(readonly url: string) {
    FakeEventSource.instances.push(this)
  }

  addEventListener(type: string, listener: (event: Event) => void): void {
    const list = this.listeners.get(type) ?? []
    list.push(listener)
    this.listeners.set(type, list)
  }

  close(): void {
    this.closed = true
  }

  emit(type: string, event?: unknown): void {
    const payload = (event ?? {}) as Event
    for (const listener of this.listeners.get(type) ?? []) listener(payload)
  }
}

/** 一帧 SSE：后端发 `id:` + `data:`（原文 JSON）。 */
function frame(data: string, id?: string): { data: string; lastEventId?: string } {
  return id === undefined ? { data } : { data, lastEventId: id }
}

const factory: EventSourceFactory = (url) => new FakeEventSource(url)

interface Overrides {
  getToken?: () => string | null
  events?: readonly string[]
  onFallbackPoll?: () => void | Promise<void>
  eventSourceFactory?: EventSourceFactory
  fallbackIntervalMs?: number
  reconnectDelayMs?: number
  maxReconnectAttempts?: number
}

function makeClient(overrides: Overrides = {}) {
  const events: StreamEvent[] = []
  const statuses: StreamStatus[] = []
  const client = new EventStreamClient({
    getToken: overrides.getToken ?? (() => 'tok-1'),
    events: overrides.events ?? ['piecework_counted'],
    onEvent: (event) => events.push(event),
    onStatus: (status) => statuses.push(status),
    eventSourceFactory: overrides.eventSourceFactory ?? factory,
    fallbackIntervalMs: overrides.fallbackIntervalMs ?? 30_000,
    reconnectDelayMs: overrides.reconnectDelayMs ?? 3_000,
    maxReconnectAttempts: overrides.maxReconnectAttempts ?? 3,
    ...(overrides.onFallbackPoll === undefined ? {} : { onFallbackPoll: overrides.onFallbackPoll }),
  })
  return { client, events, statuses }
}

beforeEach(() => {
  FakeEventSource.instances = []
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('连接 URL', () => {
  it('TC-SSE-FE-01：token 走 query，events 白名单逗号拼接，默认 baseURL=/api/v1', () => {
    const { client, statuses } = makeClient()
    client.open()
    expect(FakeEventSource.instances).toHaveLength(1)
    expect(FakeEventSource.instances[0]?.url).toBe(
      '/api/v1/events/stream?token=tok-1&events=piecework_counted',
    )
    expect(statuses).toEqual(['connecting'])
    expect(client.status).toBe('connecting')
  })

  it('TC-SSE-FE-02：未给 events 时只带 token（无空 events 参数）', () => {
    const { client } = makeClient({ events: [] })
    client.open()
    expect(FakeEventSource.instances[0]?.url).toBe('/api/v1/events/stream?token=tok-1')
  })
})

describe('事件投递', () => {
  it('TC-SSE-FE-03：onopen → open；收到帧回调 onEvent 并记录 lastEventId', () => {
    const { client, events, statuses } = makeClient()
    client.open()
    const source = FakeEventSource.instances[0]!
    source.emit('open')
    expect(client.status).toBe('open')

    source.emit('piecework_counted', frame('{"bundle_no":"BD-1"}', '5-0'))
    expect(events).toEqual([
      { event: 'piecework_counted', data: '{"bundle_no":"BD-1"}', id: '5-0' },
    ])
    expect(client.lastEventId).toBe('5-0')
    // open 之后不应再出现 connecting
    expect(statuses).toEqual(['connecting', 'open'])
  })

  it('TC-SSE-FE-04：空字符串 id 视为 null，不更新续传锚点', () => {
    const { client, events } = makeClient()
    client.open()
    FakeEventSource.instances[0]!.emit('piecework_counted', frame('{}'))
    expect(events[0]?.id).toBeNull()
    expect(client.lastEventId).toBeNull()
  })

  it('TC-SSE-FE-05：data 非字符串时降级为空串（不抛）', () => {
    const { client, events } = makeClient()
    client.open()
    FakeEventSource.instances[0]!.emit('piecework_counted', { data: 42 })
    expect(events[0]?.data).toBe('')
  })
})

describe('断线重连（续传）', () => {
  it('TC-SSE-FE-06：error → reconnecting，退避后重连并带 last_event_id', () => {
    const { client, statuses } = makeClient()
    client.open()
    const first = FakeEventSource.instances[0]!
    first.emit('open')
    first.emit('piecework_counted', frame('{}', '9-0'))

    first.emit('error')
    expect(first.closed).toBe(true)
    expect(client.status).toBe('reconnecting')

    vi.advanceTimersByTime(3_000)
    expect(FakeEventSource.instances).toHaveLength(2)
    expect(FakeEventSource.instances[1]?.url).toBe(
      '/api/v1/events/stream?token=tok-1&events=piecework_counted&last_event_id=9-0',
    )

    FakeEventSource.instances[1]!.emit('open')
    expect(client.status).toBe('open')
    expect(statuses).toEqual(['connecting', 'open', 'reconnecting', 'connecting', 'open'])
  })

  it('TC-SSE-FE-07：退避按尝试次数线性增长（3000 → 6000）', () => {
    const { client } = makeClient()
    client.open()
    FakeEventSource.instances[0]!.emit('error') // 第 1 次
    vi.advanceTimersByTime(2_999)
    expect(FakeEventSource.instances).toHaveLength(1)
    vi.advanceTimersByTime(1)
    expect(FakeEventSource.instances).toHaveLength(2)

    FakeEventSource.instances[1]!.emit('error') // 第 2 次
    vi.advanceTimersByTime(5_999)
    expect(FakeEventSource.instances).toHaveLength(2)
    vi.advanceTimersByTime(1)
    expect(FakeEventSource.instances).toHaveLength(3)
  })
})

describe('降级轮询', () => {
  it('TC-SSE-FE-08：连续失败达上限 → polling，每 30s 调 onFallbackPoll', () => {
    const onFallbackPoll = vi.fn()
    const { client } = makeClient({ onFallbackPoll })
    client.open()
    FakeEventSource.instances[0]!.emit('error')
    vi.advanceTimersByTime(3_000)
    FakeEventSource.instances[1]!.emit('error')
    vi.advanceTimersByTime(6_000)
    FakeEventSource.instances[2]!.emit('error') // 第 3 次 → 降级

    expect(client.status).toBe('polling')
    expect(onFallbackPoll).toHaveBeenCalledTimes(1) // 进入即 poll 一次
    // 此路径 retryable=true：下个周期会先 poll 再重试 SSE（自愈，见 TC-11）
    vi.advanceTimersByTime(30_000)
    expect(onFallbackPoll).toHaveBeenCalledTimes(2)
  })

  it('TC-SSE-FE-09：不支持 EventSource（无 factory）→ 直接轮询，且不建连接', () => {
    const onEvent = vi.fn()
    const client = new EventStreamClient({
      getToken: () => 'tok-1',
      events: ['piecework_counted'],
      onEvent,
    })
    client.open()
    expect(FakeEventSource.instances).toHaveLength(0)
    expect(client.status).toBe('polling')
    vi.advanceTimersByTime(30_000)
    expect(FakeEventSource.instances).toHaveLength(0)
  })

  it('TC-SSE-FE-10：无 token → 不建连接，直接轮询（稳定 30s 周期）', () => {
    const onFallbackPoll = vi.fn()
    const { client } = makeClient({ getToken: () => null, onFallbackPoll })
    client.open()
    expect(FakeEventSource.instances).toHaveLength(0)
    expect(client.status).toBe('polling')
    expect(onFallbackPoll).toHaveBeenCalledTimes(1)
    vi.advanceTimersByTime(30_000)
    expect(onFallbackPoll).toHaveBeenCalledTimes(2)
    vi.advanceTimersByTime(30_000)
    expect(onFallbackPoll).toHaveBeenCalledTimes(3)
  })

  it('TC-SSE-FE-11：轮询自愈 —— 周期内 SSE 恢复则回 open 并停轮询', () => {
    const onFallbackPoll = vi.fn()
    const { client } = makeClient({ onFallbackPoll })
    client.open()
    FakeEventSource.instances[0]!.emit('error')
    vi.advanceTimersByTime(3_000)
    FakeEventSource.instances[1]!.emit('error')
    vi.advanceTimersByTime(6_000)
    FakeEventSource.instances[2]!.emit('error')
    expect(client.status).toBe('polling')

    vi.advanceTimersByTime(30_000) // 一个轮询周期：先 poll，再重试 SSE
    expect(onFallbackPoll).toHaveBeenCalledTimes(2)
    const recovered = FakeEventSource.instances[3]!
    recovered.emit('open')
    expect(client.status).toBe('open')

    vi.advanceTimersByTime(60_000) // 已回 open，轮询应已停
    expect(onFallbackPoll).toHaveBeenCalledTimes(2)
  })

  it('TC-SSE-FE-12：轮询回调 reject / 同步抛错都不冒泡（FE-INV-4）', async () => {
    const unhandled = vi.fn()
    process.on('unhandledRejection', unhandled)

    const rejecting = new EventStreamClient({
      getToken: () => 'tok-1',
      events: [],
      onEvent: () => undefined,
      onFallbackPoll: () => Promise.reject(new Error('boom')),
    })
    rejecting.open()
    expect(rejecting.status).toBe('polling')
    await Promise.resolve()
    await Promise.resolve()

    const throwing = new EventStreamClient({
      getToken: () => 'tok-1',
      events: [],
      onEvent: () => undefined,
      onFallbackPoll: () => {
        throw new Error('sync boom')
      },
    })
    expect(() => throwing.open()).not.toThrow()
    expect(throwing.status).toBe('polling')

    process.off('unhandledRejection', unhandled)
    expect(unhandled).not.toHaveBeenCalled()
  })

  it('TC-SSE-FE-13：factory 抛错走重连，达上限后降级', () => {
    const boom: EventSourceFactory = () => {
      throw new Error('构造失败')
    }
    const { client } = makeClient({ eventSourceFactory: boom })
    client.open()
    expect(client.status).toBe('reconnecting')
    vi.advanceTimersByTime(3_000)
    vi.advanceTimersByTime(6_000)
    expect(client.status).toBe('polling')
  })
})

describe('生命周期与默认 factory', () => {
  it('TC-SSE-FE-14：close() 后 status=closed；再 error 不重连', () => {
    const { client } = makeClient()
    client.open()
    const source = FakeEventSource.instances[0]!
    source.emit('open')
    client.close()
    expect(source.closed).toBe(true)
    expect(client.status).toBe('closed')

    source.emit('error')
    vi.advanceTimersByTime(60_000)
    expect(FakeEventSource.instances).toHaveLength(1)
    expect(client.status).toBe('closed')
  })

  it('TC-SSE-FE-15：open() 幂等 —— 重复调用先拆旧连接', () => {
    const { client } = makeClient()
    client.open()
    const first = FakeEventSource.instances[0]!
    client.open()
    expect(first.closed).toBe(true)
    expect(FakeEventSource.instances).toHaveLength(2)
  })

  it('TC-SSE-FE-16：不传 onStatus 也不报错', () => {
    const client = new EventStreamClient({
      getToken: () => 'tok-1',
      events: ['piecework_counted'],
      onEvent: () => undefined,
      eventSourceFactory: factory,
    })
    client.open()
    FakeEventSource.instances[0]!.emit('open')
    client.close()
    expect(client.status).toBe('closed')
  })

  it('TC-SSE-FE-18：重连等待中 close() 会清掉重连定时器（不再重连）', () => {
    const { client } = makeClient()
    client.open()
    FakeEventSource.instances[0]!.emit('error') // attempts=1 → 排重连
    expect(client.status).toBe('reconnecting')
    client.close()
    vi.advanceTimersByTime(60_000)
    expect(FakeEventSource.instances).toHaveLength(1)
    expect(client.status).toBe('closed')
  })

  it('TC-SSE-FE-17：未注入 factory 时取 globalThis.EventSource', () => {
    const globalRef = globalThis as { EventSource?: unknown }
    const original = globalRef.EventSource
    globalRef.EventSource = FakeEventSource
    try {
      const client = new EventStreamClient({
        getToken: () => 'tok-1',
        events: ['piecework_counted'],
        onEvent: () => undefined,
      })
      client.open()
      expect(FakeEventSource.instances).toHaveLength(1)
      expect(client.status).toBe('connecting')
    } finally {
      if (original === undefined) delete globalRef.EventSource
      else globalRef.EventSource = original
    }
  })
})
