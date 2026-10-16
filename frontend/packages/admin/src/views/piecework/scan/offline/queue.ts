/**
 * 工位机离线队列的 IndexedDB 封装（T-PIECE-008，`07 §7`）。
 *
 * ## 为什么单独一层而不把 IndexedDB 调用散进 `useOfflineQueue`
 *
 * 「库里存了什么、怎么查」与「什么时候 flush、失败了怎么办」是两件事：前者要能被
 * `fake-indexeddb` 直接测（升库、索引、按状态计数），后者要能注入假时钟与假网络。
 * 混在一个文件里会让「重试次数算错」和「索引建错」看起来是同一个问题。
 *
 * ⚠️ 队列记录**不是业务数据**：真正的业务事实是服务端落库的 `piecework_logs` +
 *    `bundles.counted_*`。所以这里用物理删除（`db.delete`），不受 INV-7 软删约束。
 */
import { openDB, type DBSchema, type IDBPDatabase } from 'idb'
import type { PieceworkCountIn } from '@garment/shared'

export type QueueStatus = 'pending' | 'syncing' | 'conflict' | 'failed'

export interface QueueError {
  code: number
  message: string
}

/** 队列中的一条待提交扫码。字段与 `07 §7`「离线缓存必须记 device_id + scanned_at」对齐。 */
export interface QueueRecord {
  /** `keyPath`；入队生成。 */
  id: string
  /** 入队时生成、重试间不变（05 §5 幂等键）。 */
  idempotencyKey: string
  /** 直接作为 `POST /piecework/count` 请求体；`scanned_at` 入队时固定。 */
  payload: PieceworkCountIn
  status: QueueStatus
  /** 已尝试次数，`0..MAX_ATTEMPTS`（INV-Q5）。 */
  attempts: number
  /** epoch ms；`status='pending'` 时生效。 */
  nextAttemptAt: number
  createdAt: number
  lastError: QueueError | null
  /** `32001` 的 `details`（原计件人 / 时间 / 件数）。 */
  conflict: Record<string, unknown> | null
}

/** `07 §7`：指数退避 1s/2s/4s/8s/30s，最多 8 次。超限进入 `failed` 并保留。 */
export const RETRY_DELAYS_MS = [1000, 2000, 4000, 8000, 30000, 30000, 30000, 30000] as const
export const MAX_ATTEMPTS = RETRY_DELAYS_MS.length

/** ⚠️ 只存设备标识，**不存队列**（队列在 IndexedDB，见 ADR-0034）。 */
export const DEVICE_KEY = 'piecework.device_id'

const DB_NAME = 'garment-erp-piecework-offline'
const STORE = 'pending_counts'
const VERSION = 1

interface QueueDB extends DBSchema {
  pending_counts: {
    key: string
    value: QueueRecord
    indexes: { by_status: QueueStatus; by_next_attempt_at: number }
  }
}

let dbPromise: Promise<IDBPDatabase<QueueDB>> | null = null

/** 开库（首次调用建 store + 两个索引）；失败不缓存 rejected promise，允许下次重试。 */
export function openQueueDb(): Promise<IDBPDatabase<QueueDB>> {
  if (dbPromise === null) {
    dbPromise = openDB<QueueDB>(DB_NAME, VERSION, {
      upgrade(db) {
        const store = db.createObjectStore(STORE, { keyPath: 'id' })
        store.createIndex('by_status', 'status')
        store.createIndex('by_next_attempt_at', 'nextAttemptAt')
      },
    }).catch((error: unknown) => {
      dbPromise = null
      throw error
    })
  }
  return dbPromise
}

function uid(): string {
  const cryptoObj = globalThis.crypto
  if (cryptoObj !== undefined && typeof cryptoObj.randomUUID === 'function') {
    return cryptoObj.randomUUID()
  }
  return `q-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

/** 设备号：首次生成后持久化（`07 §7` 离线归属；清缓存会换新，不影响计件正确性）。 */
export function getDeviceId(): string {
  try {
    const saved = localStorage.getItem(DEVICE_KEY)
    if (saved !== null && saved !== '') return saved
    const id = uid()
    localStorage.setItem(DEVICE_KEY, id)
    return id
  } catch {
    // localStorage 不可用（隐私模式）：返回临时值，不阻断扫码。
    return uid()
  }
}

export async function enqueue(
  payload: PieceworkCountIn,
  idempotencyKey: string,
  now = Date.now(),
): Promise<QueueRecord> {
  const record: QueueRecord = {
    id: uid(),
    idempotencyKey,
    payload,
    status: 'pending',
    attempts: 0,
    nextAttemptAt: now,
    createdAt: now,
    lastError: null,
    conflict: null,
  }
  const db = await openQueueDb()
  await db.add(STORE, record)
  return record
}

/** flush 取「到期待提交」，按入队时间升序（先扫先提交）。 */
export async function allDue(now = Date.now()): Promise<QueueRecord[]> {
  const db = await openQueueDb()
  const pending = await db.getAllFromIndex(STORE, 'by_status', 'pending')
  return pending.filter((record) => record.nextAttemptAt <= now).sort((a, b) => a.createdAt - b.createdAt)
}

export async function listByStatus(status: QueueStatus): Promise<QueueRecord[]> {
  const db = await openQueueDb()
  const rows = await db.getAllFromIndex(STORE, 'by_status', status)
  return rows.sort((a, b) => a.createdAt - b.createdAt)
}

export interface QueueCounts {
  pending: number
  syncing: number
  conflict: number
  failed: number
}

export async function counts(): Promise<QueueCounts> {
  const db = await openQueueDb()
  const [pending, syncing, conflict, failed] = await Promise.all([
    db.countFromIndex(STORE, 'by_status', 'pending'),
    db.countFromIndex(STORE, 'by_status', 'syncing'),
    db.countFromIndex(STORE, 'by_status', 'conflict'),
    db.countFromIndex(STORE, 'by_status', 'failed'),
  ])
  return { pending, syncing, conflict, failed }
}

export async function getRecord(id: string): Promise<QueueRecord | undefined> {
  const db = await openQueueDb()
  return db.get(STORE, id)
}

export async function putRecord(record: QueueRecord): Promise<void> {
  const db = await openQueueDb()
  await db.put(STORE, record)
}

export async function removeRecord(id: string): Promise<void> {
  const db = await openQueueDb()
  await db.delete(STORE, id)
}

/** 页面加载时把上次残留的 `syncing` 复位为 `pending`（提交中被关页 / 崩溃恢复）。 */
export async function resetSyncing(): Promise<number> {
  const db = await openQueueDb()
  const stale = await db.getAllFromIndex(STORE, 'by_status', 'syncing')
  for (const record of stale) {
    await db.put(STORE, { ...record, status: 'pending' })
  }
  return stale.length
}

/** 仅供测试：关闭连接并删库，避免用例间串味（不能靠删单条，索引残留也要清）。 */
export async function __resetQueueForTest(): Promise<void> {
  if (dbPromise !== null) {
    const db = await dbPromise
    db.close()
    dbPromise = null
  }
  await new Promise<void>((resolve) => {
    const request = indexedDB.deleteDatabase(DB_NAME)
    request.onsuccess = () => resolve()
    request.onerror = () => resolve()
    request.onblocked = () => resolve()
  })
}
