/**
 * 请求层的测试（docs/10 §6「通用组件/composable 必须测」；本文件覆盖 TC-W04~W07）。
 *
 * ⚠️ **不打真实网络**：注入 `fetchImpl` 替身。真实网络会让测试变慢、变脆，
 * 而且"401 → refresh → 重放"这条链路的每一步都要能被精确编排才测得出来。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  ApiClient,
  ApiError,
  buildQuery,
  createSessionTokenStorage,
  isPageResult,
} from './client.ts'
import type { ClientHooks, TokenStorage } from './client.ts'

/** 内存令牌存储，避免测试碰 `sessionStorage`（node 环境没有）。 */
function memoryStorage(initial: string | null = null): TokenStorage {
  let value = initial
  return {
    get: () => value,
    set: (token) => {
      value = token
    },
    clear: () => {
      value = null
    },
  }
}

interface Recorded {
  url: string
  init: RequestInit
}

/** 构造一个按脚本回包的 fetch 替身。 */
function stubFetch(handlers: Array<(url: string, init: RequestInit) => Response>) {
  const calls: Recorded[] = []
  let index = 0
  const impl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString()
    calls.push({ url, init: init ?? {} })
    const handler = handlers[index]
    index += 1
    if (!handler) throw new Error(`未编排的第 ${index} 次请求：${url}`)
    return handler(url, init ?? {})
  })
  return { impl: impl as unknown as typeof fetch, calls }
}

function jsonResponse(
  body: Record<string, unknown>,
  status: number,
  headers: Record<string, string> = {},
): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  })
}

const ok = (data: unknown) => jsonResponse({ code: 0, message: 'ok', data }, 200)

function makeClient(options: {
  fetchImpl: typeof fetch
  storage?: TokenStorage
  hooks?: Partial<ClientHooks>
  onAuthExpired?: () => void
  onError?: ClientHooks['onError']
  refresh?: ClientHooks['refresh']
}) {
  const onAuthExpired = vi.fn(options.onAuthExpired ?? (() => {}))
  const onError = vi.fn(options.onError ?? (() => {}))
  const refresh = vi.fn(options.refresh ?? (async () => null))
  const client = new ApiClient({
    baseURL: '/api/v1',
    fetchImpl: options.fetchImpl,
    storage: options.storage ?? memoryStorage('token-old'),
    hooks: { onAuthExpired, onError, refresh },
  })
  return { client, onAuthExpired, onError, refresh }
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('buildQuery', () => {
  it('跳过 null / undefined / 空串，其余按类型转字符串', () => {
    expect(buildQuery({ a: 1, b: 'x', c: true })).toBe('?a=1&b=x&c=true')
    expect(buildQuery({ a: null, b: undefined, c: '' })).toBe('')
    expect(buildQuery(undefined)).toBe('')
  })

  it('参数值做 URL 编码（款号里可能有 & 或空格）', () => {
    expect(buildQuery({ q: 'HB 2026 &AB' })).toBe('?q=HB+2026+%26AB')
  })
})

describe('ApiClient 响应解包', () => {
  beforeEach(() => {
    // 占位，真正的 stub 在各用例里
  })

  it('code=0 时返回 data', async () => {
    const fetchStub = stubFetch([() => ok({ style_no: 'HB-2026-0001' })])
    const { client } = makeClient({ fetchImpl: fetchStub.impl })
    await expect(client.get('/styles')).resolves.toEqual({ style_no: 'HB-2026-0001' })
    expect(fetchStub.calls[0]?.url).toBe('/api/v1/styles')
  })

  it('TC-W06：业务错误抛 ApiError，带 code / message / details', async () => {
    const fetchStub = stubFetch([
      () =>
        jsonResponse(
          {
            code: 32002,
            message: '该码已被他人计件',
            data: null,
            details: { bundle_no: 'HB-2026-0001-XL01-0001' },
            request_id: '01JBREQ1',
          },
          409,
        ),
    ])
    const { client } = makeClient({ fetchImpl: fetchStub.impl })
    const error = await client.get('/piecework/logs').catch((caught: unknown) => caught)
    expect(error).toBeInstanceOf(ApiError)
    const apiError = error as ApiError
    expect(apiError.code).toBe(32002)
    expect(apiError.message).toBe('该码已被他人计件')
    expect(apiError.details['bundle_no']).toBe('HB-2026-0001-XL01-0001')
    expect(apiError.requestId).toBe('01JBREQ1')
    expect(apiError.status).toBe(409)
    expect(apiError.isParamError).toBe(false)
    expect(apiError.isPermissionDenied).toBe(false)
  })

  it('参数错误不弹全局 toast（界面走行内红字，避免同一条信息出现两次）', async () => {
    const fetchStub = stubFetch([
      () =>
        jsonResponse(
          {
            code: 10001,
            message: '请求参数不合法，请检查标红字段',
            data: null,
            details: { fields: [{ field: 'style_no' }] },
          },
          422,
        ),
    ])
    const { client, onError } = makeClient({ fetchImpl: fetchStub.impl })
    await expect(client.post('/styles', {})).rejects.toBeInstanceOf(ApiError)
    expect(onError).not.toHaveBeenCalled()
  })

  it('TC-W07：403 走全局提示并带上 request_id', async () => {
    const fetchStub = stubFetch([
      () =>
        jsonResponse(
          { code: 12001, message: '无权限执行该操作', data: null, request_id: '01JBDENY' },
          403,
        ),
    ])
    const { client, onError } = makeClient({ fetchImpl: fetchStub.impl })
    const error = await client.get('/operation-rates').catch((caught: unknown) => caught)
    expect((error as ApiError).isPermissionDenied).toBe(true)
    expect(onError).toHaveBeenCalledWith({
      message: '无权限执行该操作',
      level: 'error',
      requestId: '01JBDENY',
    })
  })

  it('非 JSON 响应（网关 502 / 维护页）按"系统内部错误"处理，不当业务错误', async () => {
    const fetchStub = stubFetch([
      () => new Response('<html>502 Bad Gateway</html>', { status: 502 }),
    ])
    const { client, onError } = makeClient({ fetchImpl: fetchStub.impl })
    const error = await client.get('/styles').catch((caught: unknown) => caught)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).message).toContain('非预期')
    expect(onError).toHaveBeenCalledTimes(1)
  })

  it('HTTP 403 但 code=0（网关层拦截）也提示，不静默吞掉', async () => {
    const fetchStub = stubFetch([() => jsonResponse({ code: 0, message: 'ok', data: null }, 403)])
    const { client, onError } = makeClient({ fetchImpl: fetchStub.impl })
    await client.get('/styles')
    expect(onError).toHaveBeenCalledWith({
      message: '无权限执行该操作',
      level: 'error',
      requestId: null,
    })
  })

  it('data 为 null 时返回 null 而不是 undefined（页面判空不必写两种）', async () => {
    const fetchStub = stubFetch([() => ok(null)])
    const { client } = makeClient({ fetchImpl: fetchStub.impl })
    await expect(client.delete('/styles/X')).resolves.toBeNull()
  })

  it('分页结构可被 isPageResult 识别', async () => {
    const fetchStub = stubFetch([
      () => ok({ items: [{ style_no: 'HB-2026-0001' }], total: 1, page: 1, page_size: 20 }),
    ])
    const { client } = makeClient({ fetchImpl: fetchStub.impl })
    const data = await client.get('/styles')
    expect(isPageResult(data)).toBe(true)
    expect(isPageResult({ style_no: 'HB-2026-0001' })).toBe(false)
    expect(isPageResult(null)).toBe(false)
  })
})

describe('ApiClient 请求头', () => {
  it('带 Authorization 与查询参数', async () => {
    const fetchStub = stubFetch([() => ok([])])
    const { client } = makeClient({ fetchImpl: fetchStub.impl })
    await client.get('/styles', { query: { page: 2, q: 'HB' } })
    const headers = fetchStub.calls[0]?.init.headers as Record<string, string>
    expect(headers['Authorization']).toBe('Bearer token-old')
    expect(fetchStub.calls[0]?.url).toBe('/api/v1/styles?page=2&q=HB')
  })

  it('auth=false 的请求不带 Authorization（候选 / 健康检查）', async () => {
    const fetchStub = stubFetch([() => ok([])])
    const { client } = makeClient({ fetchImpl: fetchStub.impl })
    await client.get('/styles/options', { auth: false })
    const headers = fetchStub.calls[0]?.init.headers as Record<string, string>
    expect(headers['Authorization']).toBeUndefined()
  })

  it('POST 带 JSON body 与 Content-Type', async () => {
    const fetchStub = stubFetch([() => jsonResponse({ code: 0, message: 'ok', data: {} }, 201)])
    const { client } = makeClient({ fetchImpl: fetchStub.impl })
    await client.post('/styles', { style_no: 'HB-2026-0001' })
    expect(fetchStub.calls[0]?.init.body).toBe('{"style_no":"HB-2026-0001"}')
    const headers = fetchStub.calls[0]?.init.headers as Record<string, string>
    expect(headers['Content-Type']).toBe('application/json')
  })

  it('动词辅助方法各自带对 method', async () => {
    const fetchStub = stubFetch([
      () => ok({}),
      () => jsonResponse({ code: 0, message: 'ok', data: {} }, 201),
      () => ok({}),
      () => ok({}),
      () => ok({}),
    ])
    const { client } = makeClient({ fetchImpl: fetchStub.impl })
    await client.get('/a')
    await client.post('/a', {})
    await client.put('/a', {})
    await client.patch('/a', {})
    await client.delete('/a')
    expect(fetchStub.calls.map((call) => call.init.method)).toEqual([
      'GET',
      'POST',
      'PUT',
      'PATCH',
      'DELETE',
    ])
  })

  it('没有 crypto.randomUUID 时幂等键降级为时间戳 + 随机数（老浏览器 / 非安全上下文）', async () => {
    // ⚠️ http 访问（内网 IP）时 crypto.randomUUID 不可用，此时必须降级而不是抛错
    const original = globalThis.crypto
    vi.stubGlobal('crypto', { ...original, randomUUID: undefined })
    try {
      const fetchStub = stubFetch([() => ok({})])
      const { client } = makeClient({ fetchImpl: fetchStub.impl })
      await client.post('/bundling/scan', {}, { idempotent: true })
      const headers = fetchStub.calls[0]?.init.headers as Record<string, string>
      expect(headers['Idempotency-Key']).toMatch(/^k-/)
    } finally {
      vi.stubGlobal('crypto', original)
    }
  })

  it('宿主可覆盖 Idempotency-Key 生成器（重放同一请求要带同一个键）', async () => {
    const fetchStub = stubFetch([() => ok({})])
    const onError = vi.fn()
    const client = new ApiClient({
      baseURL: '/api/v1',
      fetchImpl: fetchStub.impl,
      storage: memoryStorage('t'),
      hooks: {
        onAuthExpired: () => {},
        onError,
        refresh: async () => null,
        newIdempotencyKey: () => 'fixed-key',
      },
    })
    await client.post('/bundling/scan', {}, { idempotent: true })
    const headers = fetchStub.calls[0]?.init.headers as Record<string, string>
    expect(headers['Idempotency-Key']).toBe('fixed-key')
  })

  it('idempotent=true 时注入 Idempotency-Key（docs/05 §5）', async () => {
    const fetchStub = stubFetch([() => ok({})])
    const { client } = makeClient({ fetchImpl: fetchStub.impl })
    await client.post('/bundling/scan', { code: 'X' }, { idempotent: true })
    const headers = fetchStub.calls[0]?.init.headers as Record<string, string>
    expect(headers['Idempotency-Key']).toBeTruthy()
  })
})

describe('ApiClient 401 处理', () => {
  it('TC-W04：401 → refresh 成功 → 用新令牌重放，且只重放一次', async () => {
    const fetchStub = stubFetch([
      () => jsonResponse({ code: 11001, message: '未登录', data: null }, 401),
      () => ok({ style_no: 'HB-2026-0001' }),
    ])
    const { client, onAuthExpired, refresh } = makeClient({
      fetchImpl: fetchStub.impl,
      storage: memoryStorage('token-expired'),
      refresh: async () => 'token-new',
    })
    await expect(client.get('/styles')).resolves.toEqual({ style_no: 'HB-2026-0001' })
    expect(refresh).toHaveBeenCalledTimes(1)
    expect(fetchStub.calls).toHaveLength(2)
    // 重放必须带**新**令牌
    const replayHeaders = fetchStub.calls[1]?.init.headers as Record<string, string>
    expect(replayHeaders['Authorization']).toBe('Bearer token-new')
    expect(client.accessToken).toBe('token-new')
    expect(onAuthExpired).not.toHaveBeenCalled()
  })

  it('TC-W05：refresh 也失败 → 清 token 跳登录，**不重放、不死循环**', async () => {
    const fetchStub = stubFetch([
      () => jsonResponse({ code: 11001, message: '未登录', data: null }, 401),
    ])
    const { client, onAuthExpired, refresh } = makeClient({
      fetchImpl: fetchStub.impl,
      storage: memoryStorage('token-expired'),
      refresh: async () => null,
    })
    const error = await client.get('/styles').catch((caught: unknown) => caught)
    expect((error as ApiError).code).toBe(11001)
    expect(refresh).toHaveBeenCalledTimes(1)
    expect(onAuthExpired).toHaveBeenCalledTimes(1)
    expect(client.accessToken).toBeNull()
    // ⚠️ 只有 1 次请求：没有重放，所以不可能死循环
    expect(fetchStub.calls).toHaveLength(1)
  })

  it('refresh 抛异常也按"登录失效"处理，不把异常抛给页面', async () => {
    const fetchStub = stubFetch([
      () => jsonResponse({ code: 11001, message: '未登录', data: null }, 401),
    ])
    const { client, onAuthExpired } = makeClient({
      fetchImpl: fetchStub.impl,
      storage: memoryStorage('token-expired'),
      refresh: async () => {
        throw new Error('network down')
      },
    })
    const error = await client.get('/styles').catch((caught: unknown) => caught)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).code).toBe(11001)
    expect(onAuthExpired).toHaveBeenCalledTimes(1)
  })

  it('重放后仍是 401 → 直接抛错，**不再 refresh 第二次**', async () => {
    const fetchStub = stubFetch([
      () => jsonResponse({ code: 11001, message: '未登录', data: null }, 401),
      () => jsonResponse({ code: 11004, message: '账号已停用', data: null }, 401),
    ])
    const { client, refresh } = makeClient({
      fetchImpl: fetchStub.impl,
      storage: memoryStorage('token-expired'),
      refresh: async () => 'token-new',
    })
    const error = await client.get('/styles').catch((caught: unknown) => caught)
    expect((error as ApiError).code).toBe(11004)
    // ⚠️ 只 refresh 过一次：第二次 401 走「直接抛错」分支
    expect(refresh).toHaveBeenCalledTimes(1)
    expect(fetchStub.calls).toHaveLength(2)
  })

  it('并发 401 只发一次 refresh（串行化，否则后到者会用已轮换的 refresh_token）', async () => {
    let served = 0
    const impl = vi.fn(async () => {
      served += 1
      // 前两次（两个并发请求）都 401；第三次起放行
      if (served <= 2) {
        return jsonResponse({ code: 11001, message: '未登录', data: null }, 401)
      }
      return ok({ ok: true })
    })
    let refreshCalls = 0
    const { client } = makeClient({
      fetchImpl: impl as unknown as typeof fetch,
      storage: memoryStorage('token-expired'),
      refresh: async () => {
        refreshCalls += 1
        // ⚠️ 故意加一点延迟：让第二个请求的 401 与第一个的 refresh **交错**，
        // 这才是要防的场景（两个 refresh 各发一次，后到者用已轮换的
        // refresh_token 再换一次 → 被后端判 401 → 明明已登录却被踢到登录页）
        await new Promise((resolve) => setTimeout(resolve, 5))
        return 'token-new'
      },
    })
    await Promise.all([client.get('/styles'), client.get('/operation-rates')])
    expect(refreshCalls).toBe(1)
  })
})

describe('ApiClient 防御分支', () => {
  it('method 省略时默认 GET', async () => {
    const fetchStub = stubFetch([() => ok({})])
    const { client } = makeClient({ fetchImpl: fetchStub.impl })
    await client.request('/styles')
    expect(fetchStub.calls[0]?.init.method).toBe('GET')
  })

  it('signal 透传给 fetch（页面切路由时要能中断在途请求）', async () => {
    const fetchStub = stubFetch([() => ok({})])
    const { client } = makeClient({ fetchImpl: fetchStub.impl })
    const controller = new AbortController()
    await client.get('/styles', { signal: controller.signal })
    expect(fetchStub.calls[0]?.init.signal).toBe(controller.signal)
  })

  it('空响应体按"非预期内容"处理', async () => {
    const fetchStub = stubFetch([() => new Response('', { status: 200 })])
    const { client } = makeClient({ fetchImpl: fetchStub.impl })
    const error = await client.get('/styles').catch((caught: unknown) => caught)
    expect((error as ApiError).message).toContain('非预期')
  })

  it('JSON 但不是对象（数组 / 字符串 / null）同样按非预期处理', async () => {
    for (const payload of ['[]', '"ok"', 'null']) {
      const fetchStub = stubFetch([
        () =>
          new Response(payload, { status: 200, headers: { 'Content-Type': 'application/json' } }),
      ])
      const { client } = makeClient({ fetchImpl: fetchStub.impl })
      const error = await client.get('/styles').catch((caught: unknown) => caught)
      expect((error as ApiError).message).toContain('非预期')
    }
  })

  it('code 缺失或不是数字 → 判为非预期内容，**绝不**当成成功', async () => {
    // ⚠️ 兜底成 code=0 看着宽容，实际是把畸形响应当成功 —— 界面空白且无从排查
    for (const payload of [
      { data: { style_no: 'HB-2026-0001' } },
      { code: '0', data: { style_no: 'HB-2026-0001' } },
    ]) {
      const fetchStub = stubFetch([
        () =>
          new Response(JSON.stringify(payload), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          }),
      ])
      const { client } = makeClient({ fetchImpl: fetchStub.impl })
      const error = await client.get('/styles').catch((caught: unknown) => caught)
      expect((error as ApiError).message).toContain('非预期')
    }
  })

  it('未注入 storage 且无 window 时降级到纯内存（SSR / node 环境）', () => {
    const storage = createSessionTokenStorage()
    storage.set('tok')
    expect(storage.get()).toBe('tok')
    storage.clear()
    expect(storage.get()).toBeNull()
  })
})

describe('ApiClient 令牌与默认值', () => {
  it('accessToken 读写清', () => {
    const fetchStub = stubFetch([() => ok({})])
    const { client } = makeClient({ fetchImpl: fetchStub.impl, storage: memoryStorage(null) })
    expect(client.accessToken).toBeNull()
    client.setAccessToken('manual-token')
    expect(client.accessToken).toBe('manual-token')
    client.clearAccessToken()
    expect(client.accessToken).toBeNull()
  })

  it('不传 baseURL / fetchImpl 时用默认值（/api/v1 + globalThis.fetch）', async () => {
    const fetchStub = stubFetch([() => ok({ ok: true })])
    const originalFetch = globalThis.fetch
    vi.stubGlobal('fetch', fetchStub.impl)
    try {
      const client = new ApiClient({
        storage: memoryStorage('t'),
        hooks: { onAuthExpired: () => {}, onError: () => {}, refresh: async () => null },
      })
      await expect(client.get('/healthz')).resolves.toEqual({ ok: true })
      expect(fetchStub.calls[0]?.url).toBe('/api/v1/healthz')
    } finally {
      vi.stubGlobal('fetch', originalFetch)
    }
  })
})

describe('createSessionTokenStorage', () => {
  it('内存与传入的 Storage 双写，清除时两边都清', () => {
    const items = new Map<string, string>()
    const store = {
      getItem: (key: string) => items.get(key) ?? null,
      setItem: (key: string, value: string) => void items.set(key, value),
      removeItem: (key: string) => void items.delete(key),
      clear: () => items.clear(),
      key: () => null,
      length: 0,
    } as unknown as Storage
    const storage = createSessionTokenStorage(store)
    storage.set('abc')
    expect(storage.get()).toBe('abc')
    expect(items.get('garment.access_token')).toBe('abc')
    storage.clear()
    expect(storage.get()).toBeNull()
    expect(items.has('garment.access_token')).toBe(false)
  })

  it('Storage 抛异常（无痕模式 / 配额满）时降级到内存，不让请求失败', () => {
    const store = {
      getItem: () => {
        throw new Error('SecurityError')
      },
      setItem: () => {
        throw new Error('QuotaExceededError')
      },
      removeItem: () => {
        throw new Error('SecurityError')
      },
    } as unknown as Storage
    const storage = createSessionTokenStorage(store)
    expect(() => storage.set('abc')).not.toThrow()
    expect(storage.get()).toBe('abc')
    expect(() => storage.clear()).not.toThrow()
  })
})
