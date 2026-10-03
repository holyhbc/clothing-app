/**
 * 统一请求层（docs/05 §3 响应包装、§5 幂等、docs/07 §1 令牌）。
 *
 * 四条必须由这一层统一处理、页面里各自写一遍必然出错的规则：
 *
 * 1. **统一响应解包**：`{code, message, data, details, request_id}`。`code !== 0`
 *    一律抛 `ApiError`，页面只 catch 一次。
 * 2. **401 静默 refresh 一次后重放**；refresh 也 401 → 清 token 跳登录，**只重试一次**
 *    （两个请求同时 401 时若各自都去 refresh，会互相把对方的 refresh 结果冲掉）。
 * 3. **`X-Request-ID` 透传**：后端给每个响应都带，界面上提示"请把请求编号提供给
 *    技术支持"时用户不用自己去日志里找（docs/06 §5）。
 * 4. **`Idempotency-Key` 按需注入**：扫码枪连扫 / 网络重试必须幂等（docs/05 §5）。
 *
 * ⚠️ **业务错误一律抛 `ApiError`，不返回 `null`**：让调用方显式处理，
 * 漏处理时是"没 catch 到"这种能被测试发现的问题，而不是界面默默显示空白。
 */

import type { components, operations, paths } from './schema.d.ts'

/** 后端统一响应包装（docs/05 §3）。 */
export type ApiEnvelope<T> = {
  code: number
  message: string
  data: T | null
  details?: Record<string, unknown> | null
  request_id?: string | null
}

export type SchemaComponents = components['schemas']

/** openapi-typescript 生成的 `operations` 表：operationId → 方法定义。 */
export type SchemaOperations = operations

/** openapi-typescript 生成的 `paths` 表：路径 → 方法。 */
export type SchemaPaths = paths

/**
 * 业务错误。
 *
 * ⚠️ `code` 是**业务错误码**（docs/05 §4），不是 HTTP 状态码：同一个 `422` 可能是
 * `10001 参数不合法` 也可能是 `11006 缺少原因`。界面按 `code` 分支，不看 HTTP。
 */
export class ApiError extends Error {
  readonly code: number
  readonly details: Record<string, unknown>
  readonly requestId: string | null
  /** HTTP 状态码。用于「是接口问题还是业务问题」的粗判与埋点。 */
  readonly status: number

  constructor(
    code: number,
    message: string,
    options: {
      details?: Record<string, unknown> | null
      requestId?: string | null
      status?: number
    } = {},
  ) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.details = options.details ?? {}
    this.requestId = options.requestId ?? null
    this.status = options.status ?? 0
  }

  /** 权限类错误（12001 无操作权限 / 12002 无数据权限），界面提示文案不同。 */
  get isPermissionDenied(): boolean {
    return this.code === 12001 || this.code === 12002
  }

  /** 登录态失效（11001/11002/11004），需要跳登录页。 */
  get isAuthFailure(): boolean {
    return this.code === 11001 || this.code === 11002 || this.code === 11004
  }

  /**
   * 参数类错误（`10xxx` 通用段），界面应做**行内红字**而不是全局 toast。
   *
   * ⚠️ 上界取 `12000` 而不是 `20000`：`12xxx` 是**权限**段（12001/12002），
   * 把它当参数错误处理会让"无权限"只显示一行红字 —— 用户根本不知道自己缺哪个
   * 权限点，而那正是他要去后台申请的东西。
   */
  get isParamError(): boolean {
    return this.code >= 10001 && this.code < 12000
  }
}

/** 令牌存取。抽成接口是为了测试可注入，也为了将来换 cookie 方案只改这一处。 */
export interface TokenStorage {
  get(): string | null
  set(token: string): void
  clear(): void
}

/**
 * ⚠️ **access token 放内存 + `sessionStorage` 双写**（docs/07 §1.1）。
 *
 * 只放内存的话刷新页面就丢登录态；只放 `localStorage` 的话 XSS 能长期窃取。
 * 双写的取舍是：`sessionStorage` 关掉标签页即失效，内存那份负责刷新页面后恢复，
 * 两者都不会跨浏览器重启存活。
 */
export function createSessionTokenStorage(storage?: Storage): TokenStorage {
  const KEY = 'garment.access_token'
  let memory: string | null = null
  const fallback = (): Storage | null => {
    if (storage) return storage
    try {
      return typeof window === 'undefined' ? null : window.sessionStorage
    } catch {
      // Safari 无痕模式访问 sessionStorage 会抛 SecurityError
      return null
    }
  }
  return {
    get: () => {
      if (memory !== null) return memory
      const store = fallback()
      try {
        memory = store?.getItem(KEY) ?? null
      } catch {
        memory = null
      }
      return memory
    },
    set: (token) => {
      memory = token
      const store = fallback()
      try {
        store?.setItem(KEY, token)
      } catch {
        // 配额满 / 无痕模式：内存那份仍然有效，不因此让请求失败
      }
    },
    clear: () => {
      memory = null
      const store = fallback()
      try {
        store?.removeItem(KEY)
      } catch {
        // 同上，忽略
      }
    },
  }
}

/** 宿主回调：由 admin / mobile 包注入（shared 不依赖 Vue，也就不依赖 UI 库）。 */
export interface ClientHooks {
  /** 登录态彻底失效时调用（清 token + 跳登录）。 */
  onAuthExpired: () => void
  /** 全局提示。`level=error` 时界面应把 `requestId` 一并展示。 */
  onError: (info: { message: string; level: 'warn' | 'error'; requestId: string | null }) => void
  /** refresh 端点。**shared 不硬编码 URL 形状**，由宿主按后端路由提供。 */
  refresh: () => Promise<string | null>
  /** 生成 `Idempotency-Key`；默认用 `crypto.randomUUID`。 */
  newIdempotencyKey?: () => string
}

export type QueryValue = string | number | boolean | null | undefined

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  query?: Record<string, QueryValue>
  /** 请求体。⚠️ `undefined` 的键会被丢掉（PATCH 的部分更新语义，docs/05 §2）。 */
  body?: unknown
  /** 需要登录态（默认 `true`）。候选 / 健康检查之类传 `false`。 */
  auth?: boolean
  /** 带 `Idempotency-Key`（docs/05 §5）。 */
  idempotent?: boolean
  signal?: AbortSignal
  headers?: Record<string, string>
}

interface InternalOptions extends RequestOptions {
  /** 内部用：标记"这是 refresh 之后的重放"，用来保证只重试一次。 */
  isReplay?: boolean
}

/** 把查询参数对象序列化成 `?a=1&b=2`，跳过 `null` / `undefined` / 空串。 */
export function buildQuery(query: Record<string, QueryValue> | undefined): string {
  if (!query) return ''
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value === null || value === undefined) continue
    if (typeof value === 'string' && value === '') continue
    search.append(key, String(value))
  }
  const text = search.toString()
  return text === '' ? '' : `?${text}`
}

function randomKey(): string {
  const cryptoRef = typeof globalThis === 'undefined' ? undefined : globalThis.crypto
  if (cryptoRef && typeof cryptoRef.randomUUID === 'function') return cryptoRef.randomUUID()
  return `k-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

export interface ClientOptions {
  baseURL?: string
  fetchImpl?: typeof fetch
  storage?: TokenStorage
  hooks: ClientHooks
}

export class ApiClient {
  private readonly baseURL: string
  private readonly fetchImpl: typeof fetch
  private readonly tokens: TokenStorage
  private readonly hooks: ClientHooks
  /** 串行化 refresh：并发 401 只发一次 refresh（docs/07 §1.1「权限查库」的同源问题）。 */
  private refreshing: Promise<string | null> | null = null

  constructor(options: ClientOptions) {
    this.baseURL = options.baseURL ?? '/api/v1'
    // ⚠️ 兜底 `globalThis.fetch`：node 18+ 有，但显式取一次比在别处假设它存在更清楚
    this.fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis)
    this.tokens = options.storage ?? createSessionTokenStorage()
    this.hooks = options.hooks
  }

  get accessToken(): string | null {
    return this.tokens.get()
  }

  setAccessToken(token: string): void {
    this.tokens.set(token)
  }

  clearAccessToken(): void {
    this.tokens.clear()
  }

  async request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    return this.send<T>(path, options)
  }

  get<T>(path: string, options: Omit<RequestOptions, 'method' | 'body'> = {}): Promise<T> {
    return this.request<T>(path, { ...options, method: 'GET' })
  }

  post<T>(path: string, body?: unknown, options: Omit<RequestOptions, 'method'> = {}): Promise<T> {
    return this.request<T>(path, { ...options, method: 'POST', body })
  }

  put<T>(path: string, body?: unknown, options: Omit<RequestOptions, 'method'> = {}): Promise<T> {
    return this.request<T>(path, { ...options, method: 'PUT', body })
  }

  patch<T>(path: string, body?: unknown, options: Omit<RequestOptions, 'method'> = {}): Promise<T> {
    return this.request<T>(path, { ...options, method: 'PATCH', body })
  }

  delete<T>(path: string, options: Omit<RequestOptions, 'method'> = {}): Promise<T> {
    return this.request<T>(path, { ...options, method: 'DELETE' })
  }

  /** 发一次请求并解包响应；401 时 refresh + 重放一次。 */
  private async send<T>(path: string, options: InternalOptions): Promise<T> {
    const auth = options.auth ?? true
    const token = auth ? this.tokens.get() : null
    const headers: Record<string, string> = { Accept: 'application/json', ...options.headers }
    if (auth && token !== null) headers['Authorization'] = `Bearer ${token}`
    if (options.body !== undefined) headers['Content-Type'] = 'application/json'
    if (options.idempotent === true) {
      const factory = this.hooks.newIdempotencyKey ?? randomKey
      headers['Idempotency-Key'] = factory()
    }

    const url = `${this.baseURL}${path}${buildQuery(options.query)}`
    const response = await this.fetchImpl(url, {
      method: options.method ?? 'GET',
      headers,
      ...(options.body === undefined ? {} : { body: JSON.stringify(options.body) }),
      ...(options.signal === undefined ? {} : { signal: options.signal }),
    })

    const requestId = response.headers.get('X-Request-ID')
    const envelope = await readEnvelope(response, requestId)

    if (response.status === 401 && auth && options.isReplay !== true) {
      // ⚠️ **只重放一次**：refresh 也 401 时走下面的失败分支跳登录，
      // 绝不回到这里 —— 否则两个并发 401 会各自 refresh 再各自重放，
      // 最坏情况是无限循环（TC-W04 / TC-W05）。
      const fresh = await this.refreshOnce()
      if (fresh !== null) {
        return this.send<T>(path, { ...options, isReplay: true })
      }
    }

    if (envelope === null) {
      // 非 JSON（网关 502 / nginx 维护页）：文案必须说清"不是系统内部错误"
      const error = new ApiError(0, '服务器返回了非预期的内容，请稍后重试', {
        requestId,
        status: response.status,
      })
      this.reportError(error)
      throw error
    }

    if (envelope.code !== 0) {
      const error = new ApiError(envelope.code, envelope.message, {
        details: envelope.details ?? {},
        requestId: envelope.request_id ?? requestId,
        status: response.status,
      })
      // ⚠️ 参数错误走行内红字（isParamError），不在这里弹全局 toast ——
      // 全局弹一次 + 行内再标一次，用户被同一条信息轰炸两次
      if (!error.isParamError) this.reportError(error)
      throw error
    }

    if (response.status === 403) {
      // HTTP 403 但 code=0 理论上不该发生；真发生了说明网关层拦的，
      // 仍然要让用户看到"请求编号"以便排查
      this.hooks.onError({
        message: envelope.message === 'ok' ? '无权限执行该操作' : envelope.message,
        level: 'error',
        requestId: envelope.request_id ?? requestId,
      })
    }

    return (envelope.data ?? null) as T
  }

  /**
   * 串行化的 refresh。
   *
   * ⚠️ 为什么要串行：页面初始化常并发发 3~5 个请求，全部 401 时若各自去 refresh，
   * 后到的 refresh 会用**旧 refresh_token**（已被前一个轮换掉）再换一次 →
   * 后端返回 401 → 明明已登录却被踢到登录页。这里把并发请求合并成一次。
   */
  private async refreshOnce(): Promise<string | null> {
    if (this.refreshing === null) {
      this.refreshing = this.hooks
        .refresh()
        .then((token) => {
          if (token === null) {
            this.tokens.clear()
            return null
          }
          this.tokens.set(token)
          return token
        })
        .catch(() => {
          this.tokens.clear()
          return null
        })
        .finally(() => {
          this.refreshing = null
        })
    }
    const token = await this.refreshing
    if (token === null) {
      // ⚠️ 走到这里说明登录态真的没了。**只在此时**通知宿主跳登录，
      // 且不重试 —— 防止「跳登录 → 立刻又被 401 → 再跳」的抖动。
      this.hooks.onAuthExpired()
    }
    return token
  }

  private reportError(error: ApiError): void {
    this.hooks.onError({
      message: error.message,
      level: 'error',
      requestId: error.requestId,
    })
  }
}

/**
 * 读统一响应包装。
 *
 * ⚠️ 后端所有失败路径都返回同一形状（docs/05 §3），但**网关的 502/504 与 nginx
 * 维护页不是**。返回 `null` 让调用方区分「系统内部错误」与「业务错误」——
 * 前者提示"稍后重试"，后者提示具体改哪个字段。
 */
async function readEnvelope(
  response: Response,
  requestId: string | null,
): Promise<ApiEnvelope<unknown> | null> {
  const text = await response.text()
  if (text === '') return null
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    return null
  }
  if (typeof parsed !== 'object' || parsed === null || !('code' in parsed)) return null
  const candidate = parsed as Partial<ApiEnvelope<unknown>>
  // ⚠️ `code` 必须是**数字**：缺失或类型不对一律判为"非预期内容"。
  // 反过来兜底成 `code: 0` 看着更宽容，实际是把一个畸形响应当成成功 ——
  // 界面显示空白数据，排查时从头找不到原因（后端确实返回过 `code: "0"` 这种）。
  if (typeof candidate.code !== 'number') return null
  return {
    code: candidate.code,
    message: typeof candidate.message === 'string' ? candidate.message : 'ok',
    data: candidate.data ?? null,
    details: candidate.details ?? null,
    request_id: candidate.request_id ?? requestId,
  }
}

/**
 * 带分页结构的解包。
 *
 * ⚠️ 后端列表统一返回 `{items, total, page, page_size}`（docs/05 §3），
 * 这里从**生成的 schema** 取类型而不是手写 —— 手写就会与后端分页上限、
 * 字段名悄悄分叉（docs/06 §7 禁止手写 DTO）。
 */
export type PageResult<T> = {
  items: T[]
  total: number
  page: number
  page_size: number
}

/** 判别一个 `data` 是不是分页结构（用于泛型收窄，不做任何业务判断）。 */
export function isPageResult(value: unknown): value is PageResult<unknown> {
  if (typeof value !== 'object' || value === null) return false
  const candidate = value as Partial<PageResult<unknown>>
  return (
    Array.isArray(candidate.items) &&
    typeof candidate.total === 'number' &&
    typeof candidate.page === 'number' &&
    typeof candidate.page_size === 'number'
  )
}

/**
 * 取某个端点的**响应体**类型（docs/06 §7：类型来自生成物，页面禁止手写 DTO）。
 *
 * @example
 * type StyleOut = ApiResponseOf<'/api/v1/styles/{style_no}'>
 * type Created = ApiMutationResponseOf<'/api/v1/styles', 'post'>
 *
 * ⚠️ 逐层 `infer` 剥掉「操作定义 → responses → 2xx → content → data」。任何一层
 * 形状变了就退化成 `never` —— 那正是我们想要的：宁可编译报错，也不要一个静默
 * 变成 `any` 的类型。
 */

/** 成功响应体（201 优先，其次 200 —— FastAPI 的 POST 默认 201、其余 200）。 */
type SuccessBody<R> = R extends { 201: { content: { 'application/json': infer B } } }
  ? B
  : R extends { 200: { content: { 'application/json': infer B } } }
    ? B
    : never

/** 从统一响应包装里取出 `data`。 */
type UnwrapData<B> = B extends { data: infer D } ? D : never

/** GET 端点的响应体类型。 */
export type ApiResponseOf<Path extends keyof SchemaPaths> = SchemaPaths[Path] extends {
  get: { responses: infer R }
}
  ? UnwrapData<SuccessBody<R>>
  : never

/** 写操作（POST / PUT / PATCH / DELETE）的响应体类型。 */
export type ApiMutationResponseOf<
  Path extends keyof SchemaPaths,
  Method extends 'post' | 'put' | 'patch' | 'delete',
> = SchemaPaths[Path] extends { [K in Method]: { responses: infer R } }
  ? UnwrapData<SuccessBody<R>>
  : never

/** 分页查询参数（docs/05 §9.5.4：`page` 从 1 起，`size` 上限 200）。 */
export interface PageQuery extends Record<string, QueryValue> {
  page?: number
  size?: number
}
