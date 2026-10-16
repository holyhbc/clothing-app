/**
 * 离线队列的 flush 循环与响应式计数（T-PIECE-008）。
 *
 * ## 在线策略（设计 §6.2）
 *
 * **在线由 `useScanMachine` 直连提交；本模块只在「离线」或「在线但可重试失败」时入队**，
 * 然后负责把队列里的记录逐条回放。这样不重写 T-PIECE-007 已测的在线语义
 * （`04 §5.1` 步 8、`06 §2.5` 第 6 条）。
 *
 * ## 冲突为什么不删
 *
 * `07 §7` 要求「不静默丢弃」。一次性 toast 一闪即逝、无人可追溯；把 `32001` 的记录
 * 留成 `conflict` 并常驻计数，工人 / 组长才能看到「已由张三计件」并决定删除或保留。
 */
import { reactive } from 'vue'
import { ApiError } from '@garment/shared'
import type { PieceworkCountIn } from '@garment/shared'
import { countPiecework } from '@/api/piecework'
import {
  MAX_ATTEMPTS,
  RETRY_DELAYS_MS,
  allDue,
  counts,
  enqueue as enqueueRecord,
  getRecord,
  listByStatus,
  putRecord,
  removeRecord,
  resetSyncing,
} from './queue'
import type { QueueError, QueueRecord } from './queue'

/** 服务端「稍后自己会好」的码：限流 / 处理中 / 兜底 5xx。 */
const RETRYABLE_CODES = new Set([10003, 10004, 99999])

export type ReplayOutcome = 'retry' | 'conflict' | 'fail'

/**
 * 回放失败分类。**非 `ApiError`（`fetch` 失败 / 未知异常）一律当网络问题重试** ——
 * 断网时抛的就是原生 `TypeError`，不当可重试就等于断网期间把队列判死。
 */
export function classifyError(error: unknown): ReplayOutcome {
  if (error instanceof ApiError) {
    if (error.code === 32001) return 'conflict'
    if (RETRYABLE_CODES.has(error.code)) return 'retry'
    return 'fail'
  }
  return 'retry'
}

function toQueueError(error: unknown): QueueError {
  if (error instanceof ApiError) return { code: error.code, message: error.message }
  return { code: 0, message: error instanceof Error ? error.message : '网络异常' }
}

function conflictDetails(error: unknown): Record<string, unknown> {
  return error instanceof ApiError ? error.details : {}
}

export interface OfflineQueueDeps {
  /** 注入假时钟（测试退避用）；默认 `Date.now`。 */
  now?: () => number
  /** 注入网络状态；默认读 `navigator.onLine`。 */
  online?: () => boolean
}

/**
 * ⚠️ **模块级**的 flush 守卫（设计 INV-Q3）：一个 tab 同一时刻只有一个回放循环。
 *    放在模块级而不是实例里，是因为「两个定时器 / `online` 事件同时触发」会命中
 *    同一个模块，实例级守卫挡不住；跨标签页则由服务端同键幂等兜底（设计 §5）。
 */
let inflight: Promise<void> | null = null

export function createOfflineQueue(deps: OfflineQueueDeps = {}) {
  const now = deps.now ?? (() => Date.now())
  const readOnline = deps.online ?? (() => (typeof navigator === 'undefined' ? true : navigator.onLine))

  const state = reactive({
    pending: 0,
    conflict: 0,
    failed: 0,
    syncing: 0,
    flushing: false,
    online: readOnline(),
    /** IndexedDB 不可用（隐私模式 / 配额）——明确标记，不假装缓存成功。 */
    unavailable: false,
  })

  async function refresh(): Promise<void> {
    state.online = readOnline()
    try {
      const snapshot = await counts()
      state.pending = snapshot.pending
      state.syncing = snapshot.syncing
      state.conflict = snapshot.conflict
      state.failed = snapshot.failed
      state.unavailable = false
    } catch {
      state.unavailable = true
    }
  }

  /** 入队。返回是否真的落库（`false` = IndexedDB 不可用）。 */
  async function enqueue(payload: PieceworkCountIn, idempotencyKey: string): Promise<boolean> {
    try {
      await enqueueRecord(payload, idempotencyKey, now())
      await refresh()
      return true
    } catch {
      state.unavailable = true
      return false
    }
  }

  async function processOne(record: QueueRecord): Promise<void> {
    const claimed: QueueRecord = { ...record, status: 'syncing', attempts: record.attempts + 1 }
    await putRecord(claimed)
    try {
      await countPiecework(claimed.payload, claimed.idempotencyKey)
      await removeRecord(claimed.id)
    } catch (error) {
      const outcome = classifyError(error)
      if (outcome === 'conflict') {
        await putRecord({ ...claimed, status: 'conflict', conflict: conflictDetails(error) })
      } else if (outcome === 'retry' && claimed.attempts < MAX_ATTEMPTS) {
        const delay = RETRY_DELAYS_MS[claimed.attempts - 1] ?? 30_000
        await putRecord({
          ...claimed,
          status: 'pending',
          nextAttemptAt: now() + delay,
          lastError: toQueueError(error),
        })
      } else {
        await putRecord({ ...claimed, status: 'failed', lastError: toQueueError(error) })
      }
    }
  }

  async function run(): Promise<void> {
    if (!readOnline()) return
    state.flushing = true
    try {
      await resetSyncing()
      const due = await allDue(now())
      for (const record of due) {
        await processOne(record)
      }
      await refresh()
    } finally {
      state.flushing = false
    }
  }

  /** 回放队列。并发调用共享同一个循环（INV-Q3）。 */
  function flush(): Promise<void> {
    if (inflight !== null) return inflight
    inflight = run().finally(() => {
      inflight = null
    })
    return inflight
  }

  /** 冲突 + 失败记录（展开面板用），按入队时间升序。 */
  async function listProblems(): Promise<QueueRecord[]> {
    const [conflicts, failed] = await Promise.all([listByStatus('conflict'), listByStatus('failed')])
    return [...conflicts, ...failed].sort((a, b) => a.createdAt - b.createdAt)
  }

  /** 人工重试一条失败记录：回 `pending` 立即到期，`attempts` 保留（不清零，避免无限重试）。 */
  async function retry(id: string): Promise<void> {
    const record = await getRecord(id)
    if (record === undefined) return
    await putRecord({ ...record, status: 'pending', nextAttemptAt: now() })
    await refresh()
  }

  /** 人工删除一条冲突 / 失败记录（队列非业务数据，物理删除）。 */
  async function remove(id: string): Promise<void> {
    await removeRecord(id)
    await refresh()
  }

  return { state, refresh, enqueue, flush, listProblems, retry, remove }
}

export type OfflineQueue = ReturnType<typeof createOfflineQueue>
