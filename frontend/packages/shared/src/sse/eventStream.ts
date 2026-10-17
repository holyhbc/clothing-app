/**
 * 计件事件流的前端订阅端（docs/05 §8：订阅 + 失败降级 30s 轮询）。
 *
 * 为什么放在 `@garment/shared` 而不是 admin/mobile 的 `composables/`：
 * 它是**纯逻辑、框架无关**（与 `ApiClient` 同层），PC 看板与员工端首页都要用，
 * 而 shared 的约定是「不依赖 Vue」。将来在某一端要 `useEventStream` 时，包一层即可。
 *
 * ## 四条由这一层统一处理、页面里各写一遍必然出错的规则
 *
 * 1. **token 走 query**：浏览器的 `EventSource` 不能自定义请求头，后端为此额外接受
 *    `?token=`（docs/05 §8）。这里从 `getToken()` 现取。
 * 2. **断线续传**：重连 URL 必须带 `last_event_id`（docs/05 §8「断线重连带
 *    Last-Event-ID」）。**不复用原生自动重连** —— 它会沿用旧 URL/旧 token，而 access
 *    token 只有 15 分钟（docs/07 §1.1），断线时多半已过期。
 * 3. **失败降级**：不支持 `EventSource` / 无 token / 连续失败达上限 → 30s 轮询。
 *    轮询**哪个接口**由宿主 `onFallbackPoll` 决定（shared 不认识业务资源）。
 * 4. **回调不阻断**：轮询回调抛错 / reject 只吞掉，绝不让它冒成未处理拒绝。
 *
 * ⚠️ 事件是**实时提示**不是真值来源（docs/05 §8）：`data` 只递原始 JSON 文本，
 * 本层不解析、不写任何 store；真值仍以 `POST /piecework/count` 的响应为准。
 */

import { buildQuery } from '../api/client.ts'

/** 客户端连接状态，供界面显示「实时 / 已降级」。 */
export type StreamStatus = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'polling' | 'closed'

/** 一帧 SSE 事件。`data` 是原文，是否 `JSON.parse` 由调用方决定。 */
export interface StreamEvent {
  /** 事件名，如 `piecework_counted`（docs/05 §8：`<资源>_<动作>`）。 */
  readonly event: string
  /** 原始 `data:` 文本（后端是 JSON 字符串）。 */
  readonly data: string
  /** 服务端 `id:`（Redis Stream ID），续传锚点；无则 `null`。 */
  readonly id: string | null
}

/** 只描述本客户端用到的 `EventSource` 子集，便于注入替身（node 环境没有它）。 */
export type EventSourceListener = (event: Event) => void

export interface EventSourceLike {
  addEventListener(type: string, listener: EventSourceListener): void
  close(): void
}

export type EventSourceFactory = (url: string) => EventSourceLike

export interface EventStreamOptions {
  /** 取当前 access token；为 `null` 时不建连接、直接降级轮询。 */
  getToken: () => string | null
  /** 收到事件。`data` 为原文，本层不解析。 */
  onEvent: (event: StreamEvent) => void
  /** 事件流地址前缀，默认 `/api/v1`（与 `ApiClient` 一致）。 */
  baseURL?: string
  /** 要订阅的事件名；同时作为服务端 `events` 白名单（逗号拼接）。 */
  events?: readonly string[]
  /** 状态变化回调。 */
  onStatus?: (status: StreamStatus) => void
  /**
   * 降级为轮询时每个周期回调一次（默认 30s，docs/05 §8）。
   * 由宿主决定轮询哪个接口 —— shared 不认识业务资源。
   */
  onFallbackPoll?: () => void | Promise<void>
  /** `EventSource` 构造器；默认取 `globalThis.EventSource`，测试注入替身。 */
  eventSourceFactory?: EventSourceFactory
  /** 降级轮询周期，默认 30000ms（docs/05 §8）。 */
  fallbackIntervalMs?: number
  /** 连续失败（含首连）达该次数即降级；默认 3。 */
  maxReconnectAttempts?: number
  /** 首次重连延迟，默认 3000ms；按尝试次数线性退避，封顶 `fallbackIntervalMs`。 */
  reconnectDelayMs?: number
}

const DEFAULT_BASE_URL = '/api/v1'
const DEFAULT_FALLBACK_INTERVAL_MS = 30_000
const DEFAULT_MAX_RECONNECT_ATTEMPTS = 3
const DEFAULT_RECONNECT_DELAY_MS = 3_000

/** 默认工厂：node / 老浏览器没有 `EventSource` 时返回 `undefined`（→ 直接降级轮询）。 */
function defaultEventSourceFactory(): EventSourceFactory | undefined {
  const ctor: unknown = globalThis.EventSource
  if (typeof ctor !== 'function') return undefined
  const EventSourceCtor = ctor as new (url: string) => EventSourceLike
  return (url) => new EventSourceCtor(url)
}

export class EventStreamClient {
  private readonly baseURL: string
  private readonly getToken: () => string | null
  private readonly events: readonly string[]
  private readonly onEvent: (event: StreamEvent) => void
  private readonly onStatus: ((status: StreamStatus) => void) | undefined
  private readonly onFallbackPoll: (() => void | Promise<void>) | undefined
  private readonly factory: EventSourceFactory | undefined
  private readonly fallbackIntervalMs: number
  private readonly maxReconnectAttempts: number
  private readonly reconnectDelayMs: number

  private source: EventSourceLike | null = null
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private pollTimer: ReturnType<typeof setInterval> | null = null
  private attempts = 0
  private lastSeenId: string | null = null
  private closed = false
  private current: StreamStatus = 'idle'

  constructor(options: EventStreamOptions) {
    this.baseURL = options.baseURL ?? DEFAULT_BASE_URL
    this.getToken = options.getToken
    this.events = options.events ?? []
    this.onEvent = options.onEvent
    this.onStatus = options.onStatus
    this.onFallbackPoll = options.onFallbackPoll
    this.factory = options.eventSourceFactory ?? defaultEventSourceFactory()
    this.fallbackIntervalMs = options.fallbackIntervalMs ?? DEFAULT_FALLBACK_INTERVAL_MS
    this.maxReconnectAttempts = options.maxReconnectAttempts ?? DEFAULT_MAX_RECONNECT_ATTEMPTS
    this.reconnectDelayMs = options.reconnectDelayMs ?? DEFAULT_RECONNECT_DELAY_MS
  }

  get status(): StreamStatus {
    return this.current
  }

  /** 最近一次收到的事件 `id`，即下次重连用的续传锚点。 */
  get lastEventId(): string | null {
    return this.lastSeenId
  }

  /** 建立订阅。重复调用会先拆掉旧连接（幂等）。 */
  open(): void {
    this.closed = false
    this.attempts = 0
    this._clearTimers()
    this._closeSource()
    this._connect()
  }

  /** 拆掉连接与所有定时器（终态）。 */
  close(): void {
    this.closed = true
    this._clearTimers()
    this._closeSource()
    this._setStatus('closed')
  }

  private _connect(): void {
    this._clearTimers()
    if (this.factory === undefined) {
      // 不可逆：浏览器根本不支持 SSE，重试没有意义
      this._enterPolling(false)
      return
    }
    const token = this.getToken()
    if (token === null) {
      // 无 token：现在连也是 401；等宿主经轮询把登录态理清（自愈仍会重试）
      this._enterPolling(false)
      return
    }

    this._setStatus('connecting')
    const url = `${this.baseURL}/events/stream${buildQuery({
      token,
      events: this.events.length > 0 ? this.events.join(',') : null,
      last_event_id: this.lastSeenId,
    })}`

    let source: EventSourceLike
    try {
      source = this.factory(url)
    } catch {
      this._handleError()
      return
    }
    this.source = source

    source.addEventListener('open', () => {
      if (this.closed) return
      this.attempts = 0
      this._setStatus('open')
    })
    source.addEventListener('error', () => {
      this._handleError()
    })
    for (const name of this.events) {
      source.addEventListener(name, (event) => {
        this._handleMessage(name, event)
      })
    }
  }

  private _handleMessage(name: string, event: Event): void {
    if (this.closed) return
    const message = event as MessageEvent
    const data = typeof message.data === 'string' ? message.data : ''
    const rawId: unknown = message.lastEventId
    const id = typeof rawId === 'string' && rawId !== '' ? rawId : null
    if (id !== null) this.lastSeenId = id
    this.onEvent({ event: name, data, id })
  }

  private _handleError(): void {
    this._closeSource()
    if (this.closed) return
    this.attempts += 1
    if (this.attempts >= this.maxReconnectAttempts) {
      this._enterPolling(true)
      return
    }
    this._setStatus('reconnecting')
    const delay = Math.min(this.reconnectDelayMs * this.attempts, this.fallbackIntervalMs)
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null
      this._connect()
    }, delay)
  }

  /**
   * 进入降级轮询。
   *
   * @param retryable `true` 时每个轮询周期顺带重试一次 SSE（自愈）；
   *   `false` 用于不可逆情形（不支持 `EventSource` / 无 token），只轮询。
   */
  private _enterPolling(retryable: boolean): void {
    this._clearTimers()
    this._closeSource()
    this._setStatus('polling')
    this._poll()
    this.pollTimer = setInterval(() => {
      this._poll()
      if (retryable) {
        this.attempts = 0
        // `_connect()` 会 `_clearTimers()`，即停掉本定时器；连上后不再轮询
        this._connect()
      }
    }, this.fallbackIntervalMs)
  }

  private _poll(): void {
    if (this.closed) return
    try {
      const result = this.onFallbackPoll?.()
      if (result !== undefined) {
        // 轮询失败由宿主自己的请求层提示；这里只保证不冒成未处理拒绝
        void Promise.resolve(result).catch(() => undefined)
      }
    } catch {
      // 宿主回调同步抛错同样不该影响订阅端
    }
  }

  private _clearTimers(): void {
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    if (this.pollTimer !== null) {
      clearInterval(this.pollTimer)
      this.pollTimer = null
    }
  }

  private _closeSource(): void {
    if (this.source !== null) {
      this.source.close()
      this.source = null
    }
  }

  private _setStatus(status: StreamStatus): void {
    this.current = status
    this.onStatus?.(status)
  }
}
